"""``twinlight-transparent``: drive the transparent controller from a shell.

Every command takes the controller's ns-3-style paths as plain strings
(quote them for the shell)::

    # Browse
    twinlight-transparent get /LinkList --depth 3
    twinlight-transparent get '/LinkList/roadm A/roadm B/ElementList/*' --leaves
    twinlight-transparent get / --ds running --with-defaults explicit

    # Change running now, in one transaction (values are JSON)
    twinlight-transparent set '/ElementList/*/$Fiber/con_in=1.0' '/Config/rmsa/qot_margin_db=2'
    twinlight-transparent revert '/ElementList/*/$Fiber'

    # Stage, check, commit
    twinlight-transparent set --candidate '/ElementList/*/$Edfa/p_max=18'
    twinlight-transparent validate --qot
    twinlight-transparent compare running candidate
    twinlight-transparent commit            # or: discard

``set`` and ``revert`` send one YANG Patch, so several paths land together
or not at all. ``--if-match ETAG`` makes a write conditional on the
datastore not having changed (``get`` prints the current ETag with
``--etag``).
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import httpx

from twinlight_client.transparent import TransparentClient, TransparentError


def _parse_assignment(text: str) -> tuple[str, Any]:
    """``PATH=VALUE`` → (path, JSON value). The *last* ``=`` outside quotes
    splits, so a quoted UID may contain ``=``."""
    in_quotes = False
    split_at = -1
    for i, ch in enumerate(text):
        if ch == '"' and (i == 0 or text[i - 1] != "\\"):
            in_quotes = not in_quotes
        elif ch == "=" and not in_quotes:
            split_at = i
    if split_at <= 0:
        raise argparse.ArgumentTypeError(f"expected PATH=VALUE, got {text!r}")
    path, raw = text[:split_at], text[split_at + 1:]
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        value = raw  # a bare word is a string: ase_distribution=rx
    return path, value


def _print_json(data: Any) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def _print_leaves(leaves: list[dict[str, Any]]) -> None:
    for leaf in leaves:
        unit = f" {leaf['unit']}" if leaf.get("unit") else ""
        mark = " *" if leaf.get("explicit") else ""
        print(f"{leaf['path']} = {json.dumps(leaf['value'], ensure_ascii=False)}{unit}{mark}")


def _print_changes(changes: list[dict[str, Any]]) -> None:
    for c in changes:
        unit = f" {c['unit']}" if c.get("unit") else ""
        verb = "revert" if c["operation"] == "remove" else "set"
        print(f"{verb:6} {c['path']}: {c['previous']} -> {c['value']}{unit}")


def _print_qot(rows: list[dict[str, Any]]) -> None:
    if not rows:
        print("no service is affected")
        return
    print(f"{'service':38} {'current':>9} {'predicted':>10} {'delta':>7}  verdict")
    for row in rows:
        cur = (row["current"] or {}).get("gsnr_db")
        pred = row["predicted"] or {}
        if pred.get("status") == "link-failed":
            pred_text, verdict = "cut", "LINK FAILED"
        else:
            pred_text = f"{pred.get('gsnr_db', float('nan')):.2f}"
            verdict = (
                "ok" if row["meets_admission_margin"]
                else "below admission margin" if row["above_required"]
                else "BELOW REQUIRED GSNR"
            )
        delta = row["delta_gsnr_db"]
        print(
            f"{row['name'] or row['service']:38} "
            f"{cur if cur is None else f'{cur:.2f}':>9} {pred_text:>10} "
            f"{'' if delta is None else f'{delta:+.2f}':>7}  {verdict}"
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="twinlight-transparent",
        description="Read and write any parameter of a TwinLight twin through "
        "its transparent controller (/transparent).",
    )
    parser.add_argument("--rest-url", default="http://localhost:8080",
                        help="TwinLight REST base URL (default: %(default)s)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("get", help="read a path")
    p.add_argument("path", nargs="?", default="/")
    p.add_argument("--ds", choices=["running", "candidate", "startup", "operational"])
    p.add_argument("--depth", type=int)
    p.add_argument("--content", choices=["all", "config", "nonconfig"])
    p.add_argument("--with-defaults", choices=["report-all", "explicit", "trim"])
    p.add_argument("--leaves", action="store_true", help="one line per leaf")
    p.add_argument("--etag", action="store_true", help="print the datastore ETag")

    for name, help_ in (("set", "set leaves (one transaction)"),
                        ("revert", "put leaves back to startup (one transaction)")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("items", nargs="+", metavar="PATH=VALUE" if name == "set" else "PATH")
        p.add_argument("--candidate", action="store_true", help="stage instead of applying")
        p.add_argument("--if-match")

    p = sub.add_parser("commit", help="apply the candidate to running")
    p.add_argument("--if-match")
    sub.add_parser("discard", help="drop the candidate's edits")
    p = sub.add_parser("validate", help="check the candidate")
    p.add_argument("--qot", action="store_true", help="predict affected services' QoT")
    p = sub.add_parser("compare", help="edits turning one datastore into another")
    p.add_argument("source", nargs="?", default="running")
    p.add_argument("target", nargs="?", default="candidate")
    sub.add_parser("schema", help="print the schema")
    sub.add_parser("redesign", help="re-run the backend's network design")
    return parser


def run_command(args: argparse.Namespace, odc: TransparentClient) -> int:
    cmd = args.command
    if cmd == "get":
        if args.etag:
            print(odc.etag(args.ds or "running"))
            return 0
        ds = None if args.ds in (None, "operational") else args.ds
        if args.leaves:
            _print_leaves(odc.leaves(args.path, datastore=ds, content=args.content,
                                     with_defaults=args.with_defaults))
        else:
            _print_json(odc.get(args.path, datastore=ds, depth=args.depth,
                                content=args.content, with_defaults=args.with_defaults))
        return 0
    if cmd in ("set", "revert"):
        if cmd == "set":
            edits = [
                {"operation": "merge", "target": path, "value": value}
                for path, value in map(_parse_assignment, args.items)
            ]
        else:
            edits = [{"operation": "remove", "target": path} for path in args.items]
        status = odc.yang_patch(
            edits, datastore="candidate" if args.candidate else "running",
            if_match=args.if_match,
        )
        result = status.get("twinlight-transparent:edit-result", {})
        _print_changes(result.get("changes", []))
        if result.get("invalidated-services"):
            print("invalidated:", ", ".join(result["invalidated-services"]))
        return 0
    if cmd == "commit":
        out = odc.commit(if_match=args.if_match)
        _print_changes(out["changes"])
        print(f"committed {out['committed-edits']} edit(s)")
        return 0
    if cmd == "discard":
        print(f"discarded {odc.discard()['discarded-edits']} edit(s)")
        return 0
    if cmd == "validate":
        out = odc.validate(with_qot_impact=args.qot)
        _print_changes(out["changes"])
        if args.qot:
            _print_qot(out["qot-impact"])
        return 0
    if cmd == "compare":
        _print_json(odc.compare(args.source, args.target))
        return 0
    if cmd == "schema":
        _print_json(odc.schema())
        return 0
    _print_json(odc.redesign())
    return 0


def main(argv: list[str] | None = None, client: httpx.Client | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        with TransparentClient(args.rest_url, client=client) as odc:
            return run_command(args, odc)
    except TransparentError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except httpx.HTTPError as exc:
        print(f"error: cannot reach {args.rest_url}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
