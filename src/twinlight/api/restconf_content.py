"""The ``content`` query parameter (RFC 8040 §4.8.1) over the twin's bodies.

    config     Return only configuration descendant data nodes
    nonconfig  Return only non-configuration descendant data nodes
    all        Return all descendant data nodes

A filter over the JSON a GET handler produced, so every data resource gets
it, one added later included, and the handlers stay as they are. Which node
is configuration comes from the ``config`` statements of the T-API v2.6.0
YANG (``api/yang_config.py``) and, for the datastore's IETF members, from
their RFCs: ``ietf-yang-library:yang-library`` and ``modules-state`` (RFC
8525) and ``ietf-restconf-monitoring:restconf-state`` (RFC 8040 §9) are
``config false``.

What a filtered body holds:

- ``config``: the target with its configuration descendants. A target that is
  itself ``config false`` has no instance in the configuration, so it is
  ``404`` ``invalid-value`` (RFC 8040 §4.3), decided before the handler runs.
- ``nonconfig``: the target with its non-configuration descendants, the
  configuration ancestors that place them and the keys of each list entry
  on the way ("configuration ancestors (if any) and list key leafs (if any)
  are also returned", RFC 8040 Appendix B.3.1, Example 3). A configuration
  container or entry with no non-configuration data below it is left out;
  the target itself never is, so an empty result is the target with only
  its keys (``{"m:container": {}}``, ``{"m:list": [{"uuid": "…"}]}``).

The filter looks at the shape of the body only (objects, arrays, the schema
node of each member), never at a value's JSON type, so it does not care
whether a 64-bit number is encoded as a number or a string.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote

from twinlight.api.yang_config import TREE

logger = logging.getLogger(__name__)

#: The datastore resource's own node (RFC 8040 §3.4).
DATASTORE = "ietf-restconf:data"

#: The datastore's top-level members besides the T-API context: all
#: ``config false`` in their modules.
_IETF_STATE = (
    "ietf-yang-library:yang-library",
    "ietf-yang-library:modules-state",
    "ietf-restconf-monitoring:restconf-state",
)


@dataclass
class SchemaNode:
    """A data node: ``kind`` is container, list, leaf or state (a ``config
    false`` node of any kind, whose whole subtree is non-configuration)."""

    name: str  # module-qualified
    kind: str
    keys: tuple[str, ...] = ()
    children: dict[str, SchemaNode] = field(default_factory=dict)

    @property
    def module(self) -> str:
        return self.name.split(":", 1)[0]

    @property
    def config(self) -> bool:
        return self.kind != "state"

    def child(self, member: str) -> SchemaNode | None:
        """The child a JSON member names (RFC 7951 §4: unqualified members
        are in the parent's module)."""
        return self.children.get(member if ":" in member else f"{self.module}:{member}")


def _qualify(name: str, module: str) -> str:
    return name if ":" in name else f"{module}:{name}"


def parse_tree(text: str) -> SchemaNode:
    """The ``TREE`` format of ``api/yang_config.py`` as ``SchemaNode``s,
    under a datastore node that also holds the IETF members."""
    root = SchemaNode(DATASTORE, "container")
    for name in _IETF_STATE:
        root.children[name] = SchemaNode(name, "state")
    stack: list[tuple[int, SchemaNode]] = [(-1, root)]
    for line in text.splitlines():
        if not line.strip():
            continue
        depth = len(line) - len(line.lstrip(" "))
        body = line.strip()
        if body[0] in "=;":
            # Leaves (=) or config-false children (;) of the node above.
            parent = stack[-1][1]
            for name in body[1:].split():
                qualified = _qualify(name, parent.module)
                kind = "leaf" if body[0] == "=" else "state"
                parent.children[qualified] = SchemaNode(qualified, kind)
            continue
        while stack[-1][0] >= depth:
            stack.pop()
        parent = stack[-1][1]
        name, _, keys = body.partition(" [")
        qualified = _qualify(name, parent.module)
        node = SchemaNode(
            qualified, "list" if keys else "container",
            keys=tuple(keys.rstrip("]").split()),
        )
        parent.children[qualified] = node
        stack.append((depth, node))
    return root


SCHEMA = parse_tree(TREE)

#: A member the table does not know: treated as server state, so a config
#: view never claims it. ``unclassified`` finds these for the tests.
_UNKNOWN = SchemaNode("unknown:unknown", "state")


@dataclass(frozen=True)
class Target:
    """The schema node a data-resource URL names, and whether the URL names
    one list entry (``…/list=key``) rather than the whole list."""

    node: SchemaNode
    entry: bool


def target_of(raw_path: str, data_root: str) -> Target | None:
    """The target of ``raw_path`` (still percent-encoded, so an encoded
    ``/`` or ``=`` stays inside its key), below ``data_root`` (``{root}/data``).

    Below a ``config false`` node every node is ``config false``, so the walk
    stops descending there. ``None`` for a path the schema does not have.
    """
    if raw_path == data_root:
        return Target(SCHEMA, False)
    if not raw_path.startswith(data_root + "/"):
        return None
    node = SCHEMA
    entry = False
    for segment in raw_path[len(data_root) + 1:].split("/"):
        name, eq, _key = segment.partition("=")
        name = unquote(name)
        entry = bool(eq)
        if not node.config:
            continue  # inside a config-false subtree
        child = node.child(name) if ":" in name or node is not SCHEMA else None
        if child is None:
            return None
        node = child
    return Target(node, entry)


_MISSING = object()


def _filter(node: SchemaNode, value: Any, mode: str) -> Any:
    """``value`` (the instance of ``node``) reduced to ``mode``, or
    ``_MISSING`` when nothing of it belongs there."""
    if not node.config:
        return value if mode == "nonconfig" else _MISSING
    if node.kind == "leaf":
        return value if mode == "config" else _MISSING
    if node.kind == "list" and isinstance(value, list):
        entries = [
            out for entry in value
            if (out := _filter_entry(node, entry, mode, keep_empty=False)) is not _MISSING
        ]
        return entries if entries or mode == "config" else _MISSING
    if isinstance(value, dict):
        out = _members(node, value, mode)
        return out if out or mode == "config" else _MISSING
    return value if mode == "config" else _MISSING


def _members(node: SchemaNode, obj: dict, mode: str) -> dict:
    out: dict[str, Any] = {}
    for member, value in obj.items():
        child = node.child(member)
        if child is None:
            logger.warning("content=%s: %s/%s is not in the YANG table", mode, node.name, member)
            child = _UNKNOWN
        kept = _filter(child, value, mode)
        if kept is not _MISSING:
            out[member] = kept
    return out


def _filter_entry(node: SchemaNode, entry: Any, mode: str, *, keep_empty: bool) -> Any:
    """A list entry: in a ``nonconfig`` view its keys go with whatever
    non-configuration data it holds. An entry left with nothing for the
    view is dropped, unless it is the target (``keep_empty``)."""
    if not isinstance(entry, dict):
        return entry if mode == "config" else _MISSING
    out = _members(node, entry, mode)
    if not out and not keep_empty:
        return _MISSING
    if mode == "config":
        return out
    keys = {k: entry[k] for k in node.keys if k in entry and k not in out}
    return {**keys, **out}


def filter_body(target: Target, body: dict, mode: str) -> dict:
    """A GET body (``{"module:target": value}``) reduced to ``mode``.

    The target itself is always kept (see the module docstring); only its
    descendants are filtered.
    """
    node = target.node
    out: dict[str, Any] = {}
    for member, value in body.items():
        if not node.config or (node.kind == "list" and not target.entry):
            kept = _filter(node, value, mode)
            out[member] = [] if kept is _MISSING else kept
        elif node.kind == "list" and isinstance(value, list):
            out[member] = [_filter_entry(node, e, mode, keep_empty=True) for e in value]
        elif isinstance(value, dict):
            out[member] = _members(node, value, mode)
        else:
            out[member] = value
    return out


def unclassified(target: Target, body: dict) -> list[str]:
    """Every member of ``body`` below a configuration node that the table
    does not classify, as a path. Empty for a body the filter fully
    understands; the tests require that of every GET body the twin serves."""
    found: list[str] = []

    def walk(node: SchemaNode, value: Any, at: str) -> None:
        if not node.config or node.kind == "leaf":
            return
        items = value if isinstance(value, list) else [value]
        for item in items:
            if not isinstance(item, dict):
                continue
            for member, child_value in item.items():
                child = node.child(member)
                if child is None:
                    found.append(f"{at}/{member}")
                else:
                    walk(child, child_value, f"{at}/{member}")

    for member, value in body.items():
        walk(target.node, value, member)
    return found
