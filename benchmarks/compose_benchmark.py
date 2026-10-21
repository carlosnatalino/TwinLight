"""Benchmark a TwinLight Docker Compose deployment through its REST API.

Times what a deployment costs a client: startup until the physics is loaded,
the latency of each kind of request, throughput under concurrent load, how
responsive the twin stays while it propagates, and the containers' memory and
CPU. Run it once per deployment, then compare the two result files:

    python benchmarks/compose_benchmark.py run --label before \\
        --compose "docker compose -p tl-before -f docker-compose.yml" \\
        --snapshot-dir ./snapshots --containers twinlight --out before.json
    python benchmarks/compose_benchmark.py compare before.json after.json

The deployment must serve the CORONET CONUS scenario (the compose default) on
--url. The ``run`` command restarts it with ``docker compose down`` / ``up``
and clears --snapshot-dir between starts, so point both at a deployment whose
state you can lose. Request sequences are seeded, so two runs ask the same
questions in the same order.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import shlex
import shutil
import statistics
import subprocess
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

TAPI_SIPS = "/data/tapi-common:context/service-interface-point"
TAPI_SERVICES = "/data/tapi-connectivity:connectivity-context/connectivity-service"
TRANSIENT_MODELS = ("edfa_reservoir", "polarization", "phase_noise", "environmental")
LOG_TIME = re.compile(r"^(?:\S+\s+\|\s+)?(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3})")


# -- helpers -----------------------------------------------------------------

def stats(samples_ms: list[float]) -> dict[str, float]:
    ordered = sorted(samples_ms)
    return {
        "n": len(ordered),
        "median": statistics.median(ordered),
        "p95": ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))],
        "mean": statistics.fmean(ordered),
        "min": ordered[0],
        "max": ordered[-1],
    }


def timed(call: Callable[[], httpx.Response]) -> tuple[float, httpx.Response]:
    start = time.perf_counter()
    response = call()
    return (time.perf_counter() - start) * 1000.0, response


def check(response: httpx.Response) -> httpx.Response:
    if response.status_code >= 300:
        raise RuntimeError(f"{response.request.url} -> {response.status_code}: {response.text}")
    return response


def ok(response: httpx.Response) -> Any:
    return check(response).json()


def log_time(line: str) -> datetime | None:
    """Timestamp of a twin log line; containers log in UTC."""
    match = LOG_TIME.match(line)
    if not match:
        return None
    return datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S,%f").replace(tzinfo=UTC)


class ResourceSampler(threading.Thread):
    """Samples `docker stats` for the given containers until stopped."""

    def __init__(self, containers: list[str]) -> None:
        super().__init__(daemon=True)
        self.containers = containers
        self.samples: list[dict[str, Any]] = []
        self._halt = threading.Event()

    def run(self) -> None:
        while not self._halt.is_set():
            out = subprocess.run(
                ["docker", "stats", "--no-stream", "--format", "{{json .}}", *self.containers],
                capture_output=True, text=True, check=False,
            ).stdout
            for line in out.splitlines():
                row = json.loads(line)
                self.samples.append({
                    "t": time.time(),
                    "name": row["Name"],
                    "cpu_pct": float(row["CPUPerc"].rstrip("%")),
                    "mem_mib": _mib(row["MemUsage"].split("/")[0].strip()),
                })

    def stop(self) -> dict[str, dict[str, float]]:
        self._halt.set()
        self.join()
        summary: dict[str, dict[str, float]] = {}
        for name in self.containers:
            rows = [s for s in self.samples if s["name"] == name]
            if rows:
                summary[name] = {
                    "mem_peak_mib": max(r["mem_mib"] for r in rows),
                    "mem_last_mib": rows[-1]["mem_mib"],
                    "cpu_peak_pct": max(r["cpu_pct"] for r in rows),
                }
        return summary


def _mib(value: str) -> float:
    units = {"KiB": 1 / 1024, "MiB": 1.0, "GiB": 1024.0, "B": 1 / 1024 / 1024}
    for unit, factor in units.items():
        if value.endswith(unit):
            return float(value[: -len(unit)]) * factor
    return float(value)


# -- the benchmark ---------------------------------------------------------------

class Benchmark:
    def __init__(self, args: argparse.Namespace) -> None:
        self.url = args.url
        self.compose = shlex.split(args.compose)
        self.snapshot_dir = args.snapshot_dir
        self.containers = args.containers
        self.twin_container = args.containers[0]
        self.http = httpx.Client(base_url=self.url, timeout=300)
        self.rng = random.Random(args.seed)
        self.args = args
        self.results: dict[str, Any] = {"label": args.label, "latency_ms": {}, "raw_ms": {}}

    def record(self, name: str, samples: list[float]) -> None:
        self.results["raw_ms"][name] = samples
        self.results["latency_ms"][name] = stats(samples)
        s = self.results["latency_ms"][name]
        print(f"  {name:<34} median {s['median']:9.2f} ms   p95 {s['p95']:9.2f} ms   n={s['n']}")

    def repeat(self, name: str, n: int, call: Callable[[], httpx.Response]) -> None:
        samples = []
        for _ in range(n):
            ms, response = timed(call)
            check(response)
            samples.append(ms)
        self.record(name, samples)

    # -- startup -------------------------------------------------------------

    def compose_run(self, *args: str) -> None:
        subprocess.run([*self.compose, *args], check=True, capture_output=True)

    def wait_for(self, path: str, deadline_s: float = 600.0) -> float:
        end = time.perf_counter() + deadline_s
        while time.perf_counter() < end:
            try:
                if self.http.get(path, timeout=5).status_code == 200:
                    return time.perf_counter()
            except httpx.HTTPError:
                pass
            time.sleep(0.05)
        raise TimeoutError(f"{path} not ready within {deadline_s}s")

    def started_at(self, container: str) -> datetime:
        raw = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.StartedAt}}", container],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        # 2026-10-21T08:00:00.123456789Z, truncated to microseconds
        return datetime.strptime(raw[:26], "%Y-%m-%dT%H:%M:%S.%f").replace(tzinfo=UTC)

    def startup(self) -> None:
        print(f"startup ({self.args.startup_reps} cold starts of the stack)")
        runs = []
        for _ in range(self.args.startup_reps):
            self.compose_run("down")
            if self.snapshot_dir:
                shutil.rmtree(self.snapshot_dir, ignore_errors=True)
                self.snapshot_dir.mkdir(parents=True)
            t0 = time.perf_counter()
            self.compose_run("up", "-d", "--no-build", "twin")
            t_health = self.wait_for("/health")
            t_ready = self.wait_for("/config/get")
            run = {
                "up_to_health_s": t_health - t0,
                "up_to_physics_ready_s": t_ready - t0,
                **self.startup_phases(),
            }
            runs.append(run)
            print("  " + "  ".join(f"{k} {v:.2f}" for k, v in run.items()))
        self.results["startup"] = {
            "runs": runs,
            "median": {k: statistics.median(r[k] for r in runs) for k in runs[0]},
        }

    def startup_phases(self) -> dict[str, float]:
        """Phases from container timestamps and the twin's own log lines."""
        logs = subprocess.run(
            [*self.compose, "logs", "--no-color", "--no-log-prefix", "twin"],
            capture_output=True, text=True, check=True,
        ).stdout.splitlines()
        started = next(log_time(line) for line in logs if "Starting TwinLight" in line)
        loaded = next(log_time(line) for line in logs if "GNPy network loaded" in line)
        twin_start = self.started_at(self.twin_container)
        phases = {
            "container_to_main_s": (started - twin_start).total_seconds(),
            "physics_load_s": (loaded - started).total_seconds(),
        }
        for container in self.containers[1:]:
            phases["dependency_gate_s"] = (twin_start - self.started_at(container)).total_seconds()
        return phases

    # -- request latencies ---------------------------------------------------

    def discover(self) -> None:
        sips = ok(self.http.get(TAPI_SIPS))["tapi-common:context"]["service-interface-point"]
        self.sip_of = {
            n["value"]: sip["uuid"]
            for sip in sips for n in sip["name"] if n["value-name"] == "node-name"
        }
        trx = sorted(self.sip_of)
        pairs = [tuple(self.rng.sample(trx, 2)) for _ in range(400)]
        unique = list(dict.fromkeys(pairs))
        self.info_pairs = unique[: self.args.path_pairs]
        self.admit_pairs = unique[self.args.path_pairs: self.args.path_pairs + self.args.services]

    def path_info(self, pair: tuple[str, str]) -> httpx.Response:
        a, z = pair
        return self.http.get("/internal/path-info",
                             params={"sip_a": self.sip_of[a], "sip_z": self.sip_of[z]})

    def latencies(self) -> None:
        print("request latency (single client)")
        self.repeat("health", 200, lambda: self.http.get("/health"))
        self.repeat("tapi_sip_list", 50, lambda: self.http.get(TAPI_SIPS))

        cold, warm, km = [], [], {}
        for pair in self.info_pairs:
            ms, response = timed(lambda pair=pair: self.path_info(pair))
            cold.append(ms)
            km[" -> ".join(pair)] = ok(response)["total-fiber-km"]
        for _ in range(self.args.path_reps):
            for pair in self.info_pairs:
                ms, response = timed(lambda pair=pair: self.path_info(pair))
                ok(response)
                warm.append(ms)
        self.record("path_info_first_call", cold)
        self.record("path_info_route_cached", warm)
        self.results["path_km"] = km

        admit_ms, admitted = [], {}
        for i, (a, z) in enumerate(self.admit_pairs):
            body = {"tapi-connectivity:connectivity-service": {
                "name": [{"value-name": "service-name", "value": f"bench-{i}"}],
                "end-point": [
                    {"local-id": "a", "service-interface-point":
                        {"service-interface-point-uuid": self.sip_of[a]}},
                    {"local-id": "z", "service-interface-point":
                        {"service-interface-point-uuid": self.sip_of[z]}},
                ],
            }}
            ms, response = timed(lambda body=body: self.http.post(TAPI_SERVICES, json=body))
            admit_ms.append(ms)
            if response.status_code == 201:
                uuid = response.json()["tapi-connectivity:connectivity-service"]["uuid"]
                admitted[f"{a} -> {z}"] = uuid
            elif response.status_code != 409:
                ok(response)
        self.record("service_admission", admit_ms)
        self.results["admitted"] = sorted(admitted)
        print(f"  admitted {len(admitted)} of {len(self.admit_pairs)} services (rest refused on QoT)")
        self.services = list(admitted.values())
        self.service_pairs = list(admitted)

        cycle = iter(self.services * 50)
        self.repeat("opm_one_service", 200,
                    lambda: self.http.get(f"/internal/opm/{next(cycle)}"))
        self.repeat("opm_all_services", 20, lambda: self.http.get("/internal/opm"))
        self.repeat("config_get_all_devices", 20, lambda: self.http.get("/config/get"))
        self.repeat("prometheus_scrape", 20, lambda: self.http.get("/metrics"))

        self.mutations()

    def fiber_of(self, service_pair: str) -> str:
        """A fiber span on the route of an admitted service."""
        a, z = service_pair.split(" -> ")
        hops = ok(self.path_info((a, z)))["hops"]
        roadms = [h["uid"].removeprefix("roadm ") for h in hops if h["type"] == "Roadm"]
        prefix = f"fiber ({roadms[0]} → {roadms[1]})"
        devices = ok(self.http.get("/config/get"))["devices"]
        return next(d["uid"] for d in devices if d["type"] == "Fiber" and d["uid"].startswith(prefix))

    def mutations(self) -> None:
        print("runtime changes (/config/set)")
        fiber = self.fiber_of(self.service_pairs[0])
        service = self.services[0]
        write_ms, reopm_ms = [], []
        for i in range(20):
            ms, response = timed(lambda i=i: self.http.post("/config/set", json={
                "devices": {fiber: {"loss_coef": 0.21 + 0.01 * (i % 2)}}}))
            assert service in ok(response)["invalidated_services"][fiber]
            write_ms.append(ms)
            ms, response = timed(lambda: self.http.get(f"/internal/opm/{service}"))
            ok(response)
            reopm_ms.append(ms)
        self.record("param_write", write_ms)
        self.record("opm_after_write_recompute", reopm_ms)

        fail_ms = []
        for _ in range(10):
            for failed in (True, False):
                ms, response = timed(lambda failed=failed: self.http.post(
                    "/config/set", json={"devices": {fiber: {"failed": failed}}}))
                ok(response)
                fail_ms.append(ms)
        self.record("fiber_fail_or_restore", fail_ms)
        self.repeat("redesign", self.args.redesign_reps,
                    lambda: self.http.post("/config/set", json={"redesign": True}))

    # -- load ----------------------------------------------------------------

    def load(self) -> None:
        print(f"load ({self.args.load_s:.0f} s per scenario)")
        pairs = self.info_pairs

        def hammer(stop: threading.Event, sink: list[float]) -> None:
            rng = random.Random(threading.get_ident())
            with httpx.Client(base_url=self.url, timeout=300) as client:
                while not stop.is_set():
                    a, z = rng.choice(pairs)
                    start = time.perf_counter()
                    ok(client.get("/internal/path-info",
                                  params={"sip_a": self.sip_of[a], "sip_z": self.sip_of[z]}))
                    sink.append((time.perf_counter() - start) * 1000.0)

        for workers in (1, 8):
            stop, sink = threading.Event(), []
            with ThreadPoolExecutor(workers) as pool:
                start = time.perf_counter()
                futures = [pool.submit(hammer, stop, sink) for _ in range(workers)]
                time.sleep(self.args.load_s)
                stop.set()
                for future in futures:
                    future.result()  # re-raise a failed request
            elapsed = time.perf_counter() - start
            self.results[f"throughput_path_info_c{workers}_rps"] = len(sink) / elapsed
            print(f"  path-info throughput, {workers} client(s): {len(sink) / elapsed:7.2f} req/s")
            self.record(f"path_info_under_load_c{workers}", sink)

        # How long a cheap request waits while the twin propagates for others.
        stop, sink = threading.Event(), []
        with ThreadPoolExecutor(4) as pool:
            futures = [pool.submit(hammer, stop, sink) for _ in range(4)]
            probe = []
            end = time.perf_counter() + self.args.load_s
            while time.perf_counter() < end:
                ms, response = timed(lambda: self.http.get("/health"))
                ok(response)
                probe.append(ms)
                time.sleep(0.05)
            stop.set()
            for future in futures:
                future.result()
        self.record("health_while_propagating", probe)

    # -- driver ----------------------------------------------------------------

    def run(self) -> dict[str, Any]:
        self.startup()
        sampler = ResourceSampler(self.containers)
        sampler.start()
        # Transient models off: their cost is the same in every deployment and
        # would only blur the part that differs.
        ok(self.http.post("/config/set", json={"twin": {"transients": {
            model: {"enabled": False} for model in TRANSIENT_MODELS}}}))
        self.discover()
        self.latencies()
        self.load()
        self.results["resources"] = sampler.stop()
        self.results["images"] = {
            name: subprocess.run(
                ["docker", "inspect", "-f", "{{.Config.Image}}", name],
                capture_output=True, text=True, check=True,
            ).stdout.strip()
            for name in self.containers
        }
        for name, image in self.results["images"].items():
            arch, size = subprocess.run(
                ["docker", "image", "inspect", "-f", "{{.Architecture}} {{.Size}}", image],
                capture_output=True, text=True, check=True,
            ).stdout.split()
            self.results["images"][name] = {"image": image, "arch": arch, "size_mb": int(size) / 1e6}
        for name, r in self.results["resources"].items():
            print(f"  {name}: peak {r['mem_peak_mib']:.0f} MiB, cpu peak {r['cpu_peak_pct']:.0f}%")
        return self.results


# -- comparison ----------------------------------------------------------------

def compare(before_path: Path, after_path: Path) -> None:
    before, after = (json.loads(p.read_text()) for p in (before_path, after_path))
    b_label, a_label = before["label"], after["label"]
    print(f"| startup (median s) | {b_label} | {a_label} | {a_label}/{b_label} |")
    print("|---|---:|---:|---:|")
    for key, value in before["startup"]["median"].items():
        other = after["startup"]["median"].get(key)
        if other is not None:
            print(f"| {key} | {value:.2f} | {other:.2f} | {other / value:.2f} |")
    print(f"\n| request (ms) | {b_label} median | {a_label} median | ratio "
          f"| {b_label} p95 | {a_label} p95 | ratio |")
    print("|---|---:|---:|---:|---:|---:|---:|")
    for name, b in before["latency_ms"].items():
        a = after["latency_ms"][name]
        print(f"| {name} | {b['median']:.2f} | {a['median']:.2f} | {a['median'] / b['median']:.2f} "
              f"| {b['p95']:.2f} | {a['p95']:.2f} | {a['p95'] / b['p95']:.2f} |")
    print("\n| throughput (req/s) | " + f"{b_label} | {a_label} | ratio |")
    print("|---|---:|---:|---:|")
    for key in (k for k in before if k.startswith("throughput")):
        print(f"| {key} | {before[key]:.2f} | {after[key]:.2f} | {after[key] / before[key]:.2f} |")
    same = before["admitted"] == after["admitted"] and before["path_km"] == after["path_km"]
    print(f"\nsame admission decisions and route lengths: {same}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="benchmark one deployment")
    run.add_argument("--label", required=True)
    run.add_argument("--compose", required=True, help='e.g. "docker compose -p tl -f a.yml"')
    run.add_argument("--containers", nargs="+", required=True,
                     help="twin container first, then any it depends on")
    run.add_argument("--url", default="http://localhost:8080")
    run.add_argument("--snapshot-dir", type=Path, default=None)
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--seed", type=int, default=20261021)
    run.add_argument("--startup-reps", type=int, default=5)
    run.add_argument("--path-pairs", type=int, default=30)
    run.add_argument("--path-reps", type=int, default=4)
    run.add_argument("--services", type=int, default=40)
    run.add_argument("--redesign-reps", type=int, default=3)
    run.add_argument("--load-s", type=float, default=20.0)
    cmp = sub.add_parser("compare", help="compare two result files")
    cmp.add_argument("before", type=Path)
    cmp.add_argument("after", type=Path)
    args = parser.parse_args()

    if args.command == "compare":
        compare(args.before, args.after)
        return
    results = Benchmark(args).run()
    args.out.write_text(json.dumps(results, indent=2))
    print(f"results written to {args.out}")


if __name__ == "__main__":
    main()
