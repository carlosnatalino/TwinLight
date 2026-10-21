# Benchmarks

`compose_benchmark.py` measures a running Docker Compose deployment through the
twin's REST API, the way clients use it:

* **startup**: cold `up` until `/health`, and until `/config/get` answers, which
  needs the physics loaded; plus phases read from the containers and the twin's log;
* **request latency** with a single client: T-API reads, path estimates (route and
  propagation), service admission, OPM reads, `/config/get`, Prometheus scrapes,
  parameter writes, fiber failures, redesign;
* **load**: path-estimate throughput with 1 and 8 clients, and the latency of
  `/health` while four clients keep the twin propagating;
* **resources**: peak memory and CPU of each container, image sizes.

Transient models are switched off for the run: their cost does not depend on
where GNPy runs and would only blur the comparison. Request sequences are seeded,
so two runs ask the same questions; `compare` also checks that both deployments
admitted the same services over the same routes.

```bash
# the script restarts the stack and clears --snapshot-dir: use a deployment
# whose state you can lose (own project name, ports and snapshot directory)
python benchmarks/compose_benchmark.py run --label before \
    --compose "docker compose -p tl-before -f docker-compose.yml -f before.yml" \
    --containers twinlight --url http://localhost:8080 \
    --snapshot-dir ./bench-snapshots --out before.json
python benchmarks/compose_benchmark.py compare before.json after.json
```

`--containers` lists the twin first, then the containers it depends on.

## Reference results: GNPy in the twin vs GNPy in gnpy-api

Measured on 2026-10-21 on an Apple M3 Max (Docker Desktop, 16 CPUs). linux/amd64
containers run under Rosetta 2. CORONET CONUS scenario, two rounds in A-B-A-B
order; the rounds agreed within a few percent, the table gives round 1.

* **before**: `main` (97812cf), twin image linux/amd64 with GNPy in-process,
  pinned to gnpy 2.14.1. Unpinned, a fresh build resolves gnpy 3.0.1, whose
  `Transceiver.__call__` needs a `role` argument: every propagation fails and the
  twin serves mock OPM data.
* **after**: this branch, twin image native arm64 without GNPy, plus the gnpy-api
  service (linux/amd64, gnpy 2.14.1).

Both deployments admitted the same 31 of 40 services over the same routes.

| | before | after | after/before |
|---|---:|---:|---:|
| cold start until physics loaded | 4.45 s | 4.89 s | 1.10 |
| … twin process, container start to `main()` | 2.79 s | 1.16 s | 0.42 |
| … waiting for gnpy-api to report healthy | — | 2.56 s | |
| path estimate, route cached (median) | 35.6 ms | 38.8 ms | 1.09 |
| service admission (median) | 33.7 ms | 41.8 ms | 1.24 |
| redesign (median) | 382 ms | 442 ms | 1.16 |
| path-estimate throughput, 8 clients | 24.7 req/s | 24.3 req/s | 0.99 |
| OPM read, one service (median) | 1.16 ms | 0.85 ms | 0.74 |
| `/config/get`, all devices (median) | 11.0 ms | 3.1 ms | 0.29 |
| Prometheus scrape (median) | 58.6 ms | 26.6 ms | 0.45 |
| parameter write (median) | 2.3 ms | 3.2 ms | 1.39 |
| `/health` while propagating (median / p95) | 99 / 171 ms | 1.8 / 2.6 ms | 0.02 |
| peak memory | 184 MiB | 97 + 150 MiB | 1.34 |
| images | 498 MB | 474 + 797 MB | 2.55 |

One propagation on the same 30 routes (median): 32.5 ms in-process under
Rosetta, 34.6 ms inside gnpy-api without HTTP, 36.8 ms through HTTP from the
twin, 16.0 ms natively on the arm64 host. Emulation, not the service split, is
the main cost of GNPy here.
