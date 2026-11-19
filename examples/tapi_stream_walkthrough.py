#!/usr/bin/env python3
"""Walkthrough: the twin's T-API performance-measurement stream over gNMI.

TR-548 (the T-API reference implementation agreement) §6 defines how a
client gets periodic performance measurements from an optical domain
controller: discover a stream over RESTCONF, subscribe to it over gNMI, decode
protobuf ``MeasurementDetails`` records, and resume from a timestamp after a
lost connection. This script does each of those steps in the open, printing
what it sends and what comes back:

 1. ST-0.1  credentials — the bearer token, checked with GET /internal/whoami;
 2.         RESTCONF discovery — the root from /.well-known/host-meta;
 3. ST-0.2  stream discovery — the supported-stream-type and available-stream
            in tapi-streaming:stream-context, and where to connect;
 4.         OAM jobs — one per connectivity service; a record's gNMI path names
            the job, and the job names the service it measures;
 5.         gNMI Capabilities — models and encoding;
 6.         what the twin refuses — a call without a token, a subscription
            outside TR-548 §6.3.4 (the error names the offending field);
 7. ST-6.2  subscribe with the history extension: replay the last seconds of
            the log, then a sync_response, then live records;
 8.         one record, decoded field by field — normalized and native
            measurement types, units, value qualifiers;
 9. ST-6.4  drop the subscription on purpose, wait, and resume from the last
            record's timestamp: the records logged meanwhile arrive, none twice;
10. ST-6.3  (with --fail-link) cut a fiber on the service's path and watch every
            measure turn MISSING; the fiber is restored afterwards;
11.         (with --follow) hand over to ``TapiStreamClient.follow``, the
            library loop that does step 9 automatically, until Ctrl-C.

Steps 5–9 use the raw gNMI stub so the protocol is visible; the library
(``twinlight_client.streaming``) wraps the same calls.

Run it against a twin with at least one connectivity service
(``python examples/demo_services.py`` creates some)::

    python examples/tapi_stream_walkthrough.py
    python examples/tapi_stream_walkthrough.py --rest-url http://host:8080 \\
        --gnmi-target localhost:50051 --fail-link --follow

Against a twin with API tokens, set TWINLIGHT_API_TOKEN (environment or
./.env) or pass --token. It needs the ``twinlight`` package installed.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import sys
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import grpc
import httpx

from twinlight.streaming.proto import gnmi_pb2, gnmi_pb2_grpc
from twinlight.streaming.proto import tapi_streaming_performance_pb2 as tapi
from twinlight_client.credentials import add_token_argument, bearer_headers, resolve_token
from twinlight_client.restconf import discover_root
from twinlight_client.streaming import (
    MeasurementRecord,
    StreamEndpoint,
    StreamUnreachableError,
    TapiStreamClient,
    decode_record,
    select_stream,
    subscribe_request,
    unreachable_message,
)

TIMEOUT_S = 15.0


# -- Presentation -------------------------------------------------------------------


def heading(n: int, text: str) -> None:
    print(f"\n{'=' * 78}\n{n:2}. {text}\n{'=' * 78}", flush=True)


def clock(ns: int) -> str:
    """A nanosecond timestamp as local wall-clock time, to the millisecond."""
    return dt.datetime.fromtimestamp(ns / 1e9, tz=dt.UTC).astimezone().strftime("%H:%M:%S.%f")[:-3]


def short(identity: str) -> str:
    """``tapi-streaming:STREAM_STATE_ACTIVE`` → ``ACTIVE``."""
    return identity.rsplit(":", 1)[-1].rsplit("_", 1)[-1] if identity else "-"


def measure_name(q: tapi.QualifiedMeasurement) -> tuple[str, str]:
    """(name, kind): the normalized enum name when there is one, else native."""
    if q.normalized_measurement_type:
        return tapi.NormalizedMeasurementType.Name(q.normalized_measurement_type), "normalized"
    return q.native_measurement_type, "native"


def describe(details: tapi.MeasurementDetails) -> None:
    """Print one MeasurementDetails the way a TR-548 client reads it."""
    print(f"   time-measurement-was-sampled  {details.time_measurement_was_sampled} ms "
          f"({clock(details.time_measurement_was_sampled * 1_000_000)})")
    print(f"   sample-interval               {details.sample_interval} ns")
    print(f"   native-resource-id            {details.native_resource_id}  (the receiver)")
    print("   direction-of-measured-signal  "
          f"{tapi.DirectionOfMeasuredSignal.Name(details.direction_of_measured_signal)}")
    print(f"   qualified-measurement ×{len(details.qualified_measurement)}:")
    for q in details.qualified_measurement:
        name, kind = measure_name(q)
        v = q.qualified_measured_value
        qualifier = tapi.ValueQualifier.Name(v.value_qualifier)
        units = "" if v.units in ("", "1") else f" {v.units}"  # "1": dimensionless
        value = f"{v.value:.6g}{units}" if qualifier == "OK" else "—"
        print(f"     [{q.list_index:2}] {kind:10} {name:24} {value:>22}  {qualifier}")


COLUMNS = (("osnr-db", "OSNR dB", ".2f"), ("gsnr-db", "GSNR dB", ".2f"),
           ("q-factor-db", "Q dB", ".2f"), ("pre-fec-ber", "pre-FEC BER", ".2e"),
           ("post-fec-ber-hd", "post-FEC HD", ".2e"))


def table_header() -> str:
    return f"   {'logged':12}  {'sampled':12}  " + "  ".join(f"{h:>11}" for _, h, _ in COLUMNS)


def table_row(r: MeasurementRecord) -> str:
    cells = []
    for key, _, fmt in COLUMNS:
        v = r.measurements.get(key)
        cells.append(f"{'MISSING' if v is None else format(v, fmt):>11}")
    return f"   {clock(r.logged_ns)}  {clock(r.sampled_ms * 1_000_000)}  " + "  ".join(cells)


# -- A raw gNMI subscription ----------------------------------------------------------


@dataclass
class Subscription:
    """One gNMI Subscribe call: records of one job, and where the sync fell.

    gNMI Subscribe is bidirectional: the client sends one SubscribeRequest and
    keeps its send side open for as long as it wants records. Closing it (or
    cancelling the call) ends the subscription.
    """

    call: Any
    job_uuid: str
    synced: bool = False
    replayed: list[MeasurementRecord] = field(default_factory=list)

    async def next_records(self, n: int) -> list[MeasurementRecord]:
        """The next ``n`` records of the job; earlier ones before the sync
        response are collected in ``replayed`` instead."""
        out: list[MeasurementRecord] = []

        async def read() -> None:
            async for response in self.call:
                if response.sync_response:
                    self.synced = True
                    continue
                for record in decode_record(response):
                    if record.job_uuid != self.job_uuid:
                        continue  # the stream carries every job; keep ours
                    (out if self.synced else self.replayed).append(record)
                    if len(out) == n:
                        return

        await asyncio.wait_for(read(), TIMEOUT_S)
        return out

    def close(self) -> None:
        self.call.cancel()


def subscribe(
    stub: gnmi_pb2_grpc.gNMIStub, metadata: Any, job_uuid: str, start_ns: int | None = None
) -> Subscription:
    async def requests() -> AsyncIterator[gnmi_pb2.SubscribeRequest]:
        yield subscribe_request(start_ns)
        await asyncio.Event().wait()  # hold the send side open

    return Subscription(stub.Subscribe(requests(), metadata=metadata), job_uuid)


# -- The walkthrough ----------------------------------------------------------------


@dataclass
class Options:
    token: str | None = None
    gnmi_target: str | None = None
    service: str | None = None
    history_s: float = 5.0
    live: int = 3
    fail_link: bool = False
    follow: bool = False


async def run(http: httpx.AsyncClient, opts: Options) -> int:
    metadata = (("authorization", f"Bearer {opts.token}"),) if opts.token else None

    heading(1, "ST-0.1 Credentials")
    resp = await http.get("/internal/whoami")
    if resp.status_code == 401:
        print(f"   {resp.headers.get('www-authenticate')}: the twin requires an API token; "
              "set TWINLIGHT_API_TOKEN or pass --token")
        return 1
    resp.raise_for_status()
    who = resp.json()
    print(f"   authentication: {who['authentication']}"
          + (f", authenticated as {who['principal']!r}" if who["principal"] else ""))

    heading(2, "RESTCONF discovery (RFC 8040 §3.1)")
    root = await discover_root(http)
    print(f"   GET /.well-known/host-meta → RESTCONF root {root!r}")
    context = f"{root}/data/tapi-common:context"

    heading(3, "ST-0.2 Stream discovery")
    resp = await http.get(f"{context}/tapi-streaming:stream-context")
    resp.raise_for_status()
    stream_context = resp.json()["tapi-streaming:stream-context"]
    for sst in stream_context.get("supported-stream-type", []):
        proto = sst.get("connection-protocol-details", {})
        print(f"   supported-stream-type {sst['uuid']}")
        print(f"     content {', '.join(short(c) for c in sst.get('stream-type-content', []))}"
              f" · trigger {short(sst.get('record-trigger', ''))}"
              f" · log {short(sst.get('log-storage-strategy', ''))}"
              f" (record-retention {sst.get('record-retention')} min)")
        print(f"     protocols {', '.join(short(p) for p in proto.get('allowed-connection-protocols', []))}"
              f" · encoding {short(proto.get('encoding-format', ''))}")
    endpoint: StreamEndpoint = select_stream(stream_context)
    print(f"   available-stream {endpoint.uuid}: {short(endpoint.stream_state)} "
          f"at {endpoint.connection_address}")
    target = opts.gnmi_target or endpoint.target
    print(f"   connecting to {target}"
          + (" (--gnmi-target overrides the advertised address)" if opts.gnmi_target else ""))

    heading(4, "OAM jobs: what each record measures")
    resp = await http.get(f"{context}/tapi-oam:oam-context/oam-job")
    resp.raise_for_status()
    jobs = resp.json().get("tapi-oam:oam-job", [])
    resp = await http.get(f"{context}/tapi-connectivity:connectivity-context/connectivity-service")
    resp.raise_for_status()
    services = {
        s["uuid"]: next((n["value"] for n in s.get("name", [])), s["uuid"])
        for s in resp.json()["tapi-connectivity:connectivity-service"]
    }
    if not jobs:
        print("   no oam-jobs: create a connectivity service first "
              "(python examples/demo_services.py)")
        return 1
    for job in jobs:
        ref = job["connectivity-service-end-point"]
        svc = ref["connectivity-service-uuid"]
        print(f"   oam-job {job['uuid']}  {short(job['oam-job-state'])}  → service "
              f"{services.get(svc, svc)} ({ref['connectivity-service-end-point-local-id']})")
    chosen = next(
        (j for j in jobs if j["connectivity-service-end-point"]["connectivity-service-uuid"]
         == opts.service), None) if opts.service else jobs[0]
    if chosen is None:
        print(f"   no oam-job for service {opts.service}")
        return 1
    job_uuid = chosen["uuid"]
    service_uuid = chosen["connectivity-service-end-point"]["connectivity-service-uuid"]
    print(f"   following {services.get(service_uuid, service_uuid)} → oam-job {job_uuid}")

    async with grpc.aio.insecure_channel(target) as channel:
        stub = gnmi_pb2_grpc.gNMIStub(channel)

        heading(5, "gNMI Capabilities")
        try:
            caps = await stub.Capabilities(gnmi_pb2.CapabilityRequest(), metadata=metadata)
        except grpc.aio.AioRpcError as exc:
            if exc.code() != grpc.StatusCode.UNAVAILABLE:
                raise
            # The first gNMI call: the address is wrong, not the connection lost.
            advertised = None if opts.gnmi_target else endpoint.connection_address
            print(unreachable_message(StreamUnreachableError(target, exc), advertised),
                  file=sys.stderr, flush=True)
            return 1
        print(f"   gNMI {caps.gNMI_version}; encodings "
              f"{[gnmi_pb2.Encoding.Name(e) for e in caps.supported_encodings]}")
        for m in caps.supported_models:
            print(f"   model {m.name} {m.version} ({m.organization})")

        heading(6, "What the twin refuses")
        if opts.token:
            try:
                await stub.Capabilities(gnmi_pb2.CapabilityRequest())
            except grpc.aio.AioRpcError as exc:
                print(f"   no token        → {exc.code().name}: {exc.details()}")
        else:
            print("   (authentication is off: any client may call)")
        bad = subscribe_request()
        bad.subscribe.mode = gnmi_pb2.SubscriptionList.ONCE
        call = stub.Subscribe(iter([bad]), metadata=metadata)
        try:
            async for _ in call:
                pass
        except grpc.aio.AioRpcError as exc:
            print(f"   mode=ONCE       → {exc.code().name}: {exc.details()}")

        heading(7, f"ST-6.2 Subscribe, replaying the last {opts.history_s:g} s (history extension)")
        start_ns = time.time_ns() - int(opts.history_s * 1e9)
        print(f"   SubscribeRequest: prefix target 'tapi', path '/', STREAM, TARGET_DEFINED, "
              f"updates_only, history range.start={start_ns}")
        sub = subscribe(stub, metadata, job_uuid, start_ns)
        live = await sub.next_records(opts.live)
        print(f"   replayed {len(sub.replayed)} record(s) of this job from the log"
              + (f", {clock(sub.replayed[0].logged_ns)} … {clock(sub.replayed[-1].logged_ns)}"
                 if sub.replayed else ""))
        print("   ── sync_response: the replay is over; live records follow ──")
        print(table_header())
        for record in live:
            print(table_row(record))

        heading(8, "One record, decoded (tapi.MeasurementDetails in proto_bytes)")
        print(f"   gNMI path oam-job[uuid={live[-1].job_uuid}], "
              f"notification timestamp {live[-1].logged_ns} ns (the resume key)")
        describe(live[-1].details)

        heading(9, "ST-6.4 Resume after a lost connection")
        last_ns = live[-1].logged_ns
        interval_s = live[-1].details.sample_interval / 1e9
        sub.close()
        print(f"   subscription dropped after the record logged at {clock(last_ns)}")
        await asyncio.sleep(2.5 * interval_s)
        print(f"   {2.5 * interval_s:g} s later: resubscribing with range.start={last_ns}")
        resumed = subscribe(stub, metadata, job_uuid, last_ns)
        await resumed.next_records(1)
        resumed.close()
        missed = [r for r in resumed.replayed if r.logged_ns > last_ns]
        duplicates = len(resumed.replayed) - len(missed)
        print(f"   replay: {len(resumed.replayed)} record(s); {duplicates} at {clock(last_ns)} "
              "already seen (the replay is inclusive) — dropped")
        for record in missed:
            print(f"   recovered {clock(record.logged_ns)}")
        print(f"   → {len(missed)} record(s) logged while disconnected, none lost, none twice")

        if opts.fail_link:
            await fail_link_step(http, stub, metadata, job_uuid, service_uuid)

    if opts.follow:
        heading(11, "TapiStreamClient.follow — the library loop (Ctrl-C to stop)")
        print(table_header())
        async with TapiStreamClient(target, opts.token) as client:
            async for record in client.follow(job_uuid=job_uuid):
                print(table_row(record), flush=True)
    return 0


async def fail_link_step(
    http: httpx.AsyncClient, stub: gnmi_pb2_grpc.gNMIStub, metadata: Any,
    job_uuid: str, service_uuid: str,
) -> None:
    heading(10, "ST-6.3 Value qualifiers: a fiber cut on the service's path")
    resp = await http.get(f"/transparent/data/ServiceList/{service_uuid}")
    resp.raise_for_status()
    service = resp.json()["twinlight-transparent:data"]["ServiceList"][service_uuid]
    elements = service.get("ElementList", {})  # empty under the mock backend
    fiber = next((uid for uid, el in elements.items() if el.get("type") == "Fiber"), None)
    if fiber is None:
        print("   the service crosses no fiber; skipping")
        return
    print(f"   path {' → '.join(service.get('path', []))}; failing fiber {fiber!r}")
    resp = await http.post("/config/set", json={"devices": {fiber: {"failed": True}}})
    resp.raise_for_status()
    try:
        sub = subscribe(stub, metadata, job_uuid)
        # Two ticks: the first may have been sampled just before the cut.
        record = (await sub.next_records(2))[-1]
        sub.close()
        print(table_header())
        print(table_row(record))
        describe(record.details)
    finally:
        resp = await http.post("/config/set", json={"devices": {fiber: {"failed": False}}})
        resp.raise_for_status()
        print(f"   fiber {fiber!r} restored")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--rest-url", default="http://localhost:8080",
                        help="the twin's REST base URL (default: %(default)s)")
    parser.add_argument("--gnmi-target", default=None, metavar="HOST:PORT",
                        help="override the advertised connection-address when it is not "
                             "reachable from here (Compose from the host, NAT, port maps)")
    parser.add_argument("--service", default=None, metavar="UUID",
                        help="connectivity service to follow (default: the first)")
    parser.add_argument("--history", type=float, default=5.0, metavar="SECONDS",
                        help="how far back to replay the log on subscribe (default: %(default)s)")
    parser.add_argument("--live", type=int, default=3, metavar="N",
                        help="live records to print in step 7 (default: %(default)s)")
    parser.add_argument("--fail-link", action="store_true",
                        help="cut a fiber on the service's path, show MISSING, restore it")
    parser.add_argument("--follow", action="store_true",
                        help="finish by following the service until Ctrl-C")
    add_token_argument(parser)
    args = parser.parse_args(argv)
    token = resolve_token(args.token)
    opts = Options(token=token, gnmi_target=args.gnmi_target, service=args.service,
                   history_s=args.history, live=max(1, args.live), fail_link=args.fail_link,
                   follow=args.follow)

    async def go() -> int:
        async with httpx.AsyncClient(
            base_url=args.rest_url, headers=bearer_headers(token), timeout=30.0
        ) as http:
            return await run(http, opts)

    try:
        return asyncio.run(go())
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0
    except grpc.aio.AioRpcError as exc:
        print(f"gNMI error: {exc.code().name}: {exc.details()}", file=sys.stderr)
        return 1
    except (httpx.HTTPError, TimeoutError) as exc:
        print(f"error: {exc!r}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
