"""Regenerate ``src/twinlight/models/tapi_schema_tree.py`` from the T-API YANG.

The twin checks every request body against the data-node tree of the T-API
v2.6.0 modules it implements (``twinlight.models.tapi_schema``). That tree is
generated, not hand-written, from the ``pyang -f tree`` output of the modules:

    git clone --branch v2.6.0 \\
        https://github.com/Open-Network-Models-and-Interfaces-ONMI/TAPI
    cd TAPI/YANG
    for m in tapi-common tapi-topology tapi-connectivity tapi-path-computation \\
             tapi-equipment tapi-oam tapi-streaming tapi-gnmi-streaming \\
             tapi-photonic-media; do
        pyang -f tree --tree-line-length 1000 -p . $m.yang > /tmp/trees/$m.tree
    done
    python tools/gen_tapi_schema.py /tmp/trees src/twinlight/models/tapi_schema_tree.py

Only the modules the twin implements (``api/restconf_resources.IMPLEMENTED``)
and the features it advertises contribute: a node of ``tapi-notification``
(import-only) or under the ``tapi-streaming-admin`` feature is not part of
the server's schema, so a request that names one is refused like any other
undefined member.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

LINE = re.compile(r"^(?P<indent>[ |]*)[+xo]--(?P<flags>\S+)\s+(?P<name>\S+)(?P<rest>.*)$")
CASE = re.compile(r"^(?P<indent>[ |]*)[+xo]--:\((?P<name>[^)]+)\)")
AUGMENT = re.compile(r"^\s+augment (\S+):$")
SECTION_END = re.compile(
    r"^\s+(rpcs|notifications|grouping|identities|typedef|structure|yang-data)\b.*:$"
)
FEATURE = re.compile(r"\{([a-z0-9-]+)\}\?")

IMPLEMENTED = {
    "tapi-common", "tapi-topology", "tapi-connectivity", "tapi-path-computation",
    "tapi-equipment", "tapi-oam", "tapi-streaming", "tapi-gnmi-streaming",
    "tapi-photonic-media",
}
FEATURES = {"tapi-streaming"}


class Node:
    def __init__(self, module: str, name: str) -> None:
        self.module, self.name = module, name
        self.kind = ""  # set where the node is defined; augment targets start empty
        self.keys: list[str] = []
        self.config = True
        self.excluded = False
        self.children: dict[tuple[str, str], Node] = {}

    def child(self, module: str, name: str) -> Node:
        return self.children.setdefault((module, name), Node(module, name))


def parse(directory: Path) -> Node:
    root = Node("", "")
    for path in sorted(directory.glob("tapi-*.tree")):
        module = path.stem
        target: Node | None = None
        stack: list[tuple[int, Node | None]] = []
        for raw in path.read_text().splitlines():
            if raw.startswith("module:"):
                target, stack = root, []
                continue
            if m := AUGMENT.match(raw):
                target = root
                for segment in m.group(1).strip("/").split("/"):
                    prefix, name = segment.split(":")
                    target = target.child(prefix, name)
                stack = []
                continue
            if SECTION_END.match(raw):
                target = None
                continue
            if target is None or not raw.strip():
                continue
            if c := CASE.match(raw):  # a case: transparent in the data tree
                depth = len(c.group("indent"))
                while stack and stack[-1][0] >= depth:
                    stack.pop()
                stack.append((depth, stack[-1][1] if stack else target))
                continue
            m = LINE.match(raw)
            if m is None:
                raise SystemExit(f"{path}: cannot parse {raw!r}")
            depth = len(m.group("indent"))
            while stack and stack[-1][0] >= depth:
                stack.pop()
            parent = stack[-1][1] if stack else target
            name, flags, rest = m.group("name"), m.group("flags"), m.group("rest").strip()
            if parent is None or flags not in ("rw", "ro"):
                stack.append((depth, None))  # a notification or action: not data
                continue
            if name.startswith("("):  # a choice: transparent in the data tree
                stack.append((depth, parent))
                continue
            node = parent.child(module, name.rstrip("?*!"))
            node.config = flags == "rw"
            feature = FEATURE.search(rest)
            if feature and feature.group(1) not in FEATURES:
                node.excluded = True
            rest = FEATURE.sub("", rest).strip()
            if name.endswith("*"):
                node.kind = "list" if rest.startswith("[") else "leaf-list"
                if node.kind == "list":
                    node.keys = rest[1:rest.index("]")].split()
            else:
                node.kind = "leaf" if rest else "container"
            stack.append((depth, node))
    return root


def emit(node: Node, parent_module: str, depth: int, out: list[str]) -> None:
    for (module, name), child in node.children.items():
        if child.excluded or module not in IMPLEMENTED:
            continue
        if not child.kind:
            raise SystemExit(f"{module}:{name} is augmented but never defined")
        member = name if module == parent_module else f"{module}:{name}"
        kind = {
            "container": " {",
            "leaf": "",
            "leaf-list": " *",
            "list": " [" + " ".join(child.keys) + "]",
        }[child.kind]
        out.append(" " * depth + ("w " if child.config else "r ") + member + kind)
        emit(child, module, depth + 1, out)


HEADER = '''"""The data-node tree of /tapi-common:context in T-API v2.6.0 (generated).

Generated by ``tools/gen_tapi_schema.py`` from the ``pyang -f tree`` output
of the T-API v2.6.0 modules the twin implements; do not edit by hand. Read by
``twinlight.models.tapi_schema``, which documents the format: one data node
per line, indented one space per level, ``w`` for config true and ``r`` for
config false, the member name as RFC 7951 section 4 spells it, then ``{`` for
a container, ``[keys]`` for a list, ``*`` for a leaf-list, nothing for a leaf.
"""

'''


def main() -> None:
    source, target = Path(sys.argv[1]), Path(sys.argv[2])
    root = parse(source)
    context = root.children[("tapi-common", "context")]
    lines = ["w tapi-common:context {"]
    emit(context, "tapi-common", 1, lines)
    modules = "".join(f'    "{m}",\n' for m in sorted(IMPLEMENTED))
    target.write_text(
        HEADER
        + "#: The modules the tree was generated from (conformance-type implement).\n"
        + "MODULES = frozenset({\n" + modules + "})\n\n"
        + 'TREE = """\\\n' + "\n".join(lines) + '\n"""\n'
    )
    print(f"{len(lines)} data nodes written to {target}")


if __name__ == "__main__":
    main()
