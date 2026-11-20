"""The T-API v2.6.0 data-node tree, for checking request bodies against it.

A RESTCONF edit is a NETCONF ``<edit-config>`` (RFC 8040 §1.4), and NETCONF
refuses a body that carries a member the schema does not define:
``unknown-element``, "An unexpected element is present" (RFC 6241 Appendix
A). The twin's request models name only what the twin acts on, so this module
holds the whole tree of ``/tapi-common:context`` as the modules the twin
implements define it — every container, list, leaf and leaf-list with its
``config`` statement and list keys, augments included — generated from the
YANG (``tapi_schema_tree.py``, ``tools/gen_tapi_schema.py``).

It answers three questions about a JSON body:

* which members the schema does not define (:func:`unknown_members`), and
  how each is wrong — not defined there, qualified with a module the server
  does not implement, or qualified where RFC 7951 §4 requires the simple
  form;
* what the body says once the members that are not configuration are left
  out (:func:`without_state`): "Data nodes representing state data are not
  part of configuration datastores" (RFC 7950 §7.21.1), so an edit neither
  sets nor checks them;
* whether two bodies carry the same configuration (:func:`config_view`),
  which decides whether a ``PUT`` leaves a node the client may not write
  unaltered.
"""

from __future__ import annotations

import functools
import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any

from twinlight.models.tapi_schema_tree import MODULES, TREE


@dataclass(eq=False)
class SchemaNode:
    """One data node: a container, list, leaf or leaf-list."""

    module: str
    name: str
    config: bool
    kind: str  # "container", "list", "leaf" or "leaf-list"
    keys: tuple[str, ...] = ()
    children: dict[tuple[str, str], SchemaNode] = field(default_factory=dict)

    @property
    def has_children(self) -> bool:
        return self.kind in ("container", "list")

    def member_name(self, child: SchemaNode) -> str:
        """``child``'s JSON member name under this node (RFC 7951 §4):
        module-qualified where the module changes, simple otherwise."""
        return child.name if child.module == self.module else f"{child.module}:{child.name}"

    def child(self, member: str) -> SchemaNode | None:
        """The child a correctly spelled member name designates, or None."""
        prefix, _, name = member.rpartition(":")
        if prefix == self.module:
            return None  # RFC 7951 §4: the simple form "MUST be used" here
        return self.children.get((prefix or self.module, name))

    def iter_children(self) -> Iterator[tuple[str, SchemaNode]]:
        """Each child with its member name under this node."""
        for child in self.children.values():
            yield self.member_name(child), child


def _parse(tree: str) -> SchemaNode:
    root = SchemaNode("", "", True, "container")
    stack: list[tuple[int, SchemaNode]] = [(-1, root)]
    for line in tree.splitlines():
        depth = len(line) - len(line.lstrip(" "))
        flag, spec = line.strip().split(" ", 1)
        member, _, kind = spec.partition(" ")
        while stack[-1][0] >= depth:
            stack.pop()
        parent = stack[-1][1]
        prefix, _, name = member.rpartition(":")
        node = SchemaNode(
            module=prefix or parent.module,
            name=name,
            config=flag == "w",
            kind=(
                "container" if kind == "{" else "leaf-list" if kind == "*"
                else "list" if kind.startswith("[") else "leaf"
            ),
            keys=tuple(kind.strip("[]").split()) if kind.startswith("[") else (),
        )
        parent.children[(node.module, node.name)] = node
        stack.append((depth, node))
    return root


@functools.cache
def _root() -> SchemaNode:
    return _parse(TREE)


def node(path: str) -> SchemaNode:
    """The schema node at a data path, e.g.
    ``/tapi-common:context/tapi-connectivity:connectivity-context/connectivity-service``
    (member names as RFC 7951 §4 spells them, without list keys).

    Raises:
        KeyError: No such node.
    """
    current = _root()
    for member in path.strip("/").split("/"):
        found = current.child(member)
        if found is None:
            raise KeyError(f"{path}: no data node {member!r} under {current.name!r}")
        current = found
    return current


@dataclass(frozen=True)
class MemberError:
    """A member of a request body the schema does not admit where it is.

    ``error_tag`` is the RFC 6241 Appendix A tag: ``unknown-element`` (no
    such data node there), ``unknown-namespace`` (qualified with a module the
    server does not implement; ``namespace`` names it) or
    ``malformed-message`` (a defined node spelled against RFC 7951 §4).
    ``parent_path`` is the instance-identifier of the node holding the
    member, ``member`` the member as sent.
    """

    error_tag: str
    parent_path: str
    member: str
    reason: str
    namespace: str | None = None

    @property
    def path(self) -> str:
        return f"{self.parent_path}/{self.member}"


def _quote(value: Any) -> str:
    text = str(value)
    return f'"{text}"' if "'" in text else f"'{text}'"


def entry_path(path: str, schema: SchemaNode, entry: Any) -> str:
    """The instance-identifier of a list entry (RFC 7950 §9.13): the list's
    path with a predicate for each key the entry carries."""
    if not isinstance(entry, dict):
        return path
    return path + "".join(
        f"[{key}={_quote(entry[key])}]" for key in schema.keys if key in entry
    )


def _entries(value: Any) -> list[Any]:
    """A list's entries: RFC 7951 §5.4 encodes them as an array; a bare
    object is accepted as one entry, as the request parsers accept it."""
    return value if isinstance(value, list) else [value]


def unknown_members(value: Any, schema: SchemaNode, path: str) -> list[MemberError]:
    """Every member of ``value`` (the JSON of ``schema``'s node, at instance
    path ``path``) that the schema does not admit there, at any depth.

    Values are not checked, nor the JSON type of a member (a leaf sent as an
    object, say): that is the request models' job, which report it with the
    error the value calls for. Only names are.
    """
    errors: list[MemberError] = []
    if schema.kind == "list":
        for entry in _entries(value):
            errors.extend(_check_object(entry, schema, entry_path(path, schema, entry)))
    elif schema.kind == "container":
        errors.extend(_check_object(value, schema, path))
    return errors


def _check_object(value: Any, schema: SchemaNode, path: str) -> list[MemberError]:
    if not isinstance(value, dict):
        return []
    errors: list[MemberError] = []
    for member, child_value in value.items():
        prefix, _, name = member.rpartition(":")
        child = schema.child(member)
        if child is not None:
            errors.extend(unknown_members(child_value, child, f"{path}/{member}"))
            continue
        if prefix and prefix not in MODULES:
            errors.append(MemberError(
                "unknown-namespace", path, member,
                f"{prefix!r} is not a module this server implements", namespace=prefix,
            ))
        elif prefix == schema.module and (prefix, name) in schema.children:
            errors.append(MemberError(
                "malformed-message", path, member,
                f"{name!r} is defined in {prefix}, the module of its parent, so RFC "
                f"7951 §4 requires the simple form {name!r}",
            ))
        else:
            errors.append(MemberError("unknown-element", path, member, _hint(schema, name)))
    return errors


def _hint(schema: SchemaNode, name: str) -> str:
    """Why a member is not defined here, naming its right spelling if any."""
    elsewhere = [
        schema.member_name(child) for (module, child_name), child in schema.children.items()
        if child_name == name
    ]
    if elsewhere:
        return f"not defined here; the data node of that name is {elsewhere[0]!r}"
    return "not defined here by T-API v2.6.0"


def without_state(value: Any, schema: SchemaNode) -> Any:
    """``value`` without its config-false members, at any depth.

    Members the schema does not define are kept: :func:`unknown_members`
    reports them.
    """
    if schema.kind == "list":
        if isinstance(value, list):
            return [_object_without_state(entry, schema) for entry in value]
        return _object_without_state(value, schema)
    if schema.kind == "container":
        return _object_without_state(value, schema)
    return value


def _object_without_state(value: Any, schema: SchemaNode) -> Any:
    if not isinstance(value, dict):
        return value
    kept: dict[str, Any] = {}
    for member, child_value in value.items():
        child = schema.child(member)
        if child is None:
            kept[member] = child_value
        elif child.config:
            kept[member] = without_state(child_value, child)
    return kept


def config_view(value: Any, schema: SchemaNode) -> Any:
    """The configuration ``value`` carries, in a form two bodies compare by.

    Config-false members are left out. A list becomes a mapping from its
    keys to its entries and a leaf-list a sorted tuple, since no T-API list
    is ``ordered-by user`` (the order "has no meaning", RFC 7950 §7.7.7). A
    container with no configuration in it is left out of its parent: a
    non-presence container "does not have a meaning" of its own (§7.5.1),
    so omitting one and sending it empty say the same. Numbers are compared
    by their decimal text, as RFC 7951 §6.1 lets a 64-bit value be a string.
    """
    if schema.kind == "leaf":
        return _scalar(value)
    if schema.kind == "leaf-list":
        values = value if isinstance(value, list) else [value]
        return tuple(sorted((_scalar(v) for v in values), key=repr))
    if schema.kind == "list":
        view: dict[Any, Any] = {}
        for entry in _entries(value):
            entry_view = _object_view(entry, schema)
            if isinstance(entry, dict) and all(k in entry for k in schema.keys):
                key: Any = tuple(_scalar(entry[k]) for k in schema.keys)
            else:
                key = repr(entry_view)
            view[key] = entry_view
        return view
    return _object_view(value, schema)


def _object_view(value: Any, schema: SchemaNode) -> Any:
    if not isinstance(value, dict):
        return _scalar(value)
    view: dict[str, Any] = {}
    for member, child_value in value.items():
        child = schema.child(member)
        if child is None or not child.config:
            continue
        child_view = config_view(child_value, child)
        if child.kind != "leaf" and child_view in ({}, ()):
            continue
        view[schema.member_name(child)] = child_view
    return view


def _scalar(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True)


#: What a server acts on below a node: each member it implements, mapped to
#: what it implements below that member, or to None where it takes the whole
#: subtree (a leaf, or a subtree another parser checks).
type Supported = Mapping[str, Supported | None]


def unsupported_members(
    value: Any, schema: SchemaNode, supported: Supported, at: str = "",
) -> list[str]:
    """The configuration members of ``value`` that are not in ``supported``.

    Each is a member the schema defines (the members it does not are
    :func:`unknown_members`) as configuration but the server does not act
    on, named by a path relative to ``value`` with each list entry's first
    key, e.g. ``end-point[z]/role``. Config-false members are left out: an
    edit does not set them.
    """
    found: list[str] = []
    if not isinstance(value, dict):
        return found
    for member, child_value in value.items():
        child = schema.child(member)
        if child is None or not child.config:
            continue
        if member not in supported:
            found.append(f"{at}{member}")
            continue
        below = supported[member]
        if below is None:
            continue
        if child.kind == "list":
            for entry in _entries(child_value):
                key = entry.get(child.keys[0]) if isinstance(entry, dict) and child.keys else None
                found.extend(unsupported_members(entry, child, below, f"{at}{member}[{key}]/"))
        else:
            found.extend(unsupported_members(child_value, child, below, f"{at}{member}/"))
    return found
