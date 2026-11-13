#!/usr/bin/env python3
"""Scenario: TwinLight as a fully transparent optical domain controller.

An *abstracted* optical domain controller shows its northbound clients
service-interface points, nodes, links and services (T-API), and keeps the
physical layer to itself. A *fully transparent* one also exposes every
element and every parameter it models, so an upper orchestrator can plan,
check and apply physical-layer changes itself. TwinLight serves both: T-API
under ``/restconf/data``, and the transparent view under ``/transparent``.

This script plays the orchestrator through one maintenance window:

 1. discover the controller (root resource, datastores, schema);
 2. inventory the network through the transparent tree — no T-API objects;
 3. provision a lightpath through T-API, the abstracted interface;
 4. look at that lightpath, and every parameter of its first link,
    through the transparent tree;
 5. stage a maintenance change in the *candidate* datastore — span ageing
    (+1 dB over a link), a degraded amplifier pump, a stricter admission
    margin;
 6. validate it with a QoT what-if, and compare candidate with running;
 7. commit it, guarded by an ETag — first losing a race to a concurrent
    write made through the legacy /config plane, then retrying;
 8. check the committed result against the prediction;
 9. ask what a fiber cut would do, without cutting anything;
10. list everything that differs from startup, then roll it all back.

Run it against a twin (``twinlight --config examples/twin_config.yaml``)::

    python examples/transparent_odc_scenario.py
    python examples/transparent_odc_scenario.py --base-url http://host:8080 --keep

It needs the ``twinlight`` package installed (it uses
``twinlight_client.TransparentClient``); the topology's UIDs are discovered,
not assumed, so it runs on the two-site example and on CORONET alike.
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from typing import Any

import httpx

from twinlight_client.transparent import TransparentClient, TransparentError

CONNECTIVITY = "/restconf/data/tapi-connectivity:connectivity-context"
OTSIA = "tapi-photonic-media:otsia-connectivity-service-end-point-spec"


def heading(n: int, text: str) -> None:
    print(f"\n{'=' * 78}\n{n:2}. {text}\n{'=' * 78}")


def show(label: str, data: Any) -> None:
    text = json.dumps(data, indent=2, ensure_ascii=False)
    lines = text.splitlines()
    if len(lines) > 40:
        text = "\n".join(lines[:40] + [f"   … ({len(lines) - 40} more lines)"])
    print(f"--- {label}\n{text}")


def quoted(uid: str) -> str:
    return '"' + uid.replace("\\", "\\\\").replace('"', '\\"') + '"'


def print_qot(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        cur, pred = row["current"] or {}, row["predicted"] or {}
        print(
            f"   {row['name'] or row['service']}: GSNR {cur.get('gsnr_db')} dB -> "
            f"{pred.get('gsnr_db') if pred.get('status') == 'ok' else pred.get('status')}"
            f" (delta {row['delta_gsnr_db']}), required {row['required_gsnr_db']} dB, "
            f"margin {row['qot_margin_db']} dB: "
            f"above required={row['above_required']}, "
            f"admissible={row['meets_admission_margin']}"
        )


def create_lightpath(http: httpx.Client, sip_a: str, sip_z: str) -> str | None:
    def end_point(local_id: str, sip: str) -> dict[str, Any]:
        return {
            "local-id": local_id,
            "service-interface-point": {"service-interface-point-uuid": sip},
            "layer-protocol-constraint": [{"local-id": "otsi", OTSIA: {"otsi-config": [
                {"local-id": "1", "modulation": {"standard-modulation-technique": "MT_DP-QPSK"}},
            ]}}],
        }

    body = {"tapi-connectivity:connectivity-service": {
        "name": [{"value-name": "service-name", "value": "odc-scenario"}],
        "end-point": [end_point("a", sip_a), end_point("z", sip_z)],
    }}
    r = http.post(f"{CONNECTIVITY}/connectivity-service", json=body)
    if r.status_code == 409:  # admission refused: no path, spectrum or QoT
        return None
    r.raise_for_status()
    uuid: str = r.json()["tapi-connectivity:connectivity-service"]["uuid"]
    return uuid


def run(http: httpx.Client, keep: bool) -> None:
    odc = TransparentClient(client=http)

    heading(1, "Discover the transparent controller")
    root = odc.root()
    show("GET /transparent", root)
    schema = odc.schema()["tree"]
    for kind, leaves in schema["ElementList"]["entry-by-kind"].items():
        writable = [n for n, spec in leaves.items() if spec["config"]]
        print(f"   {kind:12} writable: {', '.join(writable) or '-'}")
    twin_writable = sum(
        1 for leaf in odc.leaves("/Config", content="config")
    )
    print(f"   Config       writable: {twin_writable} model parameters")

    heading(2, "Inventory through the transparent tree (no T-API objects)")
    nodes = odc.get("/NodeList", depth=2)["NodeList"]
    print(f"   {len(nodes)} terminals: {', '.join(list(nodes)[:6])}{' …' if len(nodes) > 6 else ''}")
    links = odc.leaves("/LinkList/*/*/length_km")
    print(f"   {len(links)} directional link(s), e.g.")
    for leaf in links[:4]:
        print(f"     {leaf['path']} = {leaf['value']} km")

    heading(3, "Provision a lightpath through T-API (the abstracted view)")
    sips = [leaf["value"][0] for leaf in odc.leaves("/NodeList/*/$Transceiver/sips")
            if leaf["value"]]
    # Admission may refuse a pair (a long path can miss the GSNR threshold),
    # so try endpoint pairs until one is admitted, as demo_services.py does.
    pairs = [(a, z) for i, a in enumerate(sips) for z in sips[i + 1:]]
    uuid = None
    for attempt, (sip_a, sip_z) in enumerate(pairs[:200], start=1):
        uuid = create_lightpath(http, sip_a, sip_z)
        if uuid:
            break
    if uuid is None:
        raise SystemExit("no transceiver pair could be admitted")
    print(f"   T-API created connectivity-service {uuid} (attempt {attempt})")

    heading(4, "The same lightpath, and its first link, seen transparently")
    service = odc.get(f"/ServiceList/{uuid}")["ServiceList"][uuid]
    show("ServiceList entry", {k: v for k, v in service.items() if k != "ElementList"})
    # The path's terminals (ROADMs, transceivers) in order; the first
    # consecutive pair that is a link is the first span section crossed.
    terminals = [
        n["value"] for n in odc.leaves(f"/ServiceList/{uuid}/ElementList/*/$Roadm|Transceiver/uid")
    ]
    order = service["path"]
    terminals.sort(key=order.index)
    link_pairs = odc.get("/LinkList", depth=3).get("LinkList", {})
    a, z = next(
        (x, y) for x, y in itertools.pairwise(terminals)
        if y in link_pairs.get(x, {})
    )
    link = f"/LinkList/{quoted(a)}/{quoted(z)}"
    print(f"   first link on the path: {a} -> {z}")
    params = odc.leaves(f"{link}/ElementList/*", content="config")
    shown = [
        leaf for leaf in params
        if leaf["path"].rsplit("/", 1)[-1]
        in ("loss_coef", "con_in", "gain_target", "p_max", "nf0")
    ]
    print(f"   it has {len(params)} writable parameters, among them:")
    for leaf in shown[:8]:
        print(f"     {leaf['path']} = {leaf['value']} {leaf.get('unit', '')}")
    if len(shown) > 8:
        print(f"     … and {len(shown) - 8} more")

    heading(5, "Stage a maintenance change in the candidate datastore")
    # Span ageing: 1 dB more loss across the link, spread over its fibers in
    # proportion to their length; a degraded pump on its last amplifier; and
    # a stricter admission margin network-wide. Under GNPy the pump loses
    # 6 dB of output-power ceiling (p_max), which saturates the amplifier.
    # The EGN backend exposes only the noise figure, so there the same fault
    # shows as a 2 dB higher nf0.
    edfa_schema = schema["ElementList"]["entry-by-kind"].get("Edfa", {})
    pump_attr, pump_delta = (
        ("p_max", -6.0) if edfa_schema.get("p_max", {}).get("config") else ("nf0", 2.0)
    )
    fibers = odc.leaves(f"{link}/ElementList/*/$Fiber/loss_coef")
    amps = odc.leaves(f"{link}/ElementList/*/$Edfa/{pump_attr}")
    link_km = odc.value(f"{link}/length_km")
    edits: list[dict[str, Any]] = [
        {"operation": "replace", "target": f["canonical"],
         "value": {"loss_coef": round(f["value"] + 1.0 / link_km, 6)}}
        for f in fibers
    ]
    edits.append({"operation": "replace", "target": amps[-1]["canonical"],
                  "value": {pump_attr: amps[-1]["value"] + pump_delta}})
    edits.append({"operation": "merge", "target": "/Config/rmsa",
                  "value": {"qot_margin_db": 2.0}})
    status = odc.yang_patch(edits, patch_id="maintenance-window", datastore="candidate")
    for c in status["twinlight-transparent:edit-result"]["changes"]:
        print(f"   staged {c['path']}: {c['previous']} -> {c['value']} {c.get('unit', '')}")
    print("   running is untouched:",
          odc.value(fibers[0]["canonical"]), "dB/km on", fibers[0]["canonical"])

    heading(6, "Validate with a QoT what-if; compare candidate with running")
    report = odc.validate(with_qot_impact=True)
    print_qot(report["qot-impact"])
    for edit in odc.compare("running", "candidate"):
        print(f"   {edit['operation']:8} {edit['target']}: "
              f"{edit['source-value']} -> {edit.get('value', '(startup)')}")

    broken = [r for r in report["qot-impact"] if not r["meets_admission_margin"]]
    if broken:
        print(f"   {len(broken)} lightpath(s) would fall below the admission margin: an "
              f"orchestrator would stop here. The scenario commits anyway, to show it.")

    heading(7, "Commit, guarded by the running datastore's ETag")
    etag = odc.etag()
    print(f"   read running ETag {etag}")
    http.post("/config/set", json={"twin": {"rmsa": {"k_shortest_paths": 4}}})
    print("   meanwhile, another operator changes rmsa.k_shortest_paths via /config/set")
    try:
        odc.commit(if_match=etag)
    except TransparentError as exc:
        print(f"   commit refused: {exc}")
    etag = odc.etag()
    out = odc.commit(if_match=etag)
    print(f"   retried with {etag}: committed {out['committed-edits']} edits; "
          f"invalidated {out['invalidated-services']}; "
          f"running moved since staging: {out['running-changed-since-staged']}")

    heading(8, "Check the committed result against the prediction")
    gsnr = odc.value(f"/ServiceList/{uuid}/qot/gsnr_db")
    predicted = report["qot-impact"][0]["predicted"]["gsnr_db"]
    print(f"   predicted {predicted} dB, now {gsnr} dB")
    opm = http.get(f"/internal/opm/{uuid}").json()["measurements"]
    print(f"   live OPM (static baseline + transients): GSNR {opm['gsnr-db']:.2f} dB, "
          f"pre-FEC BER {opm['pre-fec-ber']:.2e}")

    heading(9, "What would a cut of the link's first fiber do? (nothing is cut)")
    cut = odc.validate(with_qot_impact=True, edits=[
        {"operation": "merge", "target": fibers[0]["canonical"].rsplit("/", 1)[0],
         "value": {"failed": True}},
    ])
    print_qot(cut["qot-impact"])
    print("   failed fibers in running:", odc.leaves("/ElementList/*/$Fiber/failed",
                                                    with_defaults="explicit") or "none")

    heading(10, "Everything that differs from startup, then roll back")
    for edit in odc.compare("running", "startup"):
        [value] = edit["source-value"].values()
        startup = odc.value(edit["target"], datastore="startup")
        print(f"   {edit['target']}: {value} (startup: {startup})")
    if keep:
        print("   --keep: leaving the changes and the lightpath in place")
        return
    reverted = odc.revert("/")
    print(f"   DELETE /transparent/data reverted {len(reverted['changes'])} leaves")
    print("   running vs startup now:", odc.compare("running", "startup") or "identical")
    http.delete(f"{CONNECTIVITY}/connectivity-service={uuid}")
    print(f"   T-API deleted {uuid}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--keep", action="store_true",
                        help="leave the changes and the lightpath in place")
    args = parser.parse_args()
    try:
        with httpx.Client(base_url=args.base_url, timeout=60.0) as http:
            run(http, args.keep)
    except TransparentError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except httpx.HTTPError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
