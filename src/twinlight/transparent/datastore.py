"""Datastores, edits and transactions of the transparent controller.

Four datastores, after NMDA (RFC 8342 / RFC 8527):

* **operational** — the live twin: every leaf, config and state. ``/data``
  serves this view (RFC 8040's combined view), filtered by ``content``.
* **running** — the configuration in force: config leaves at their live
  values. Writing it changes the network immediately.
* **candidate** — running plus edits staged by clients and not yet
  committed. Shared by all clients, as NETCONF's is; ``commit`` applies
  it to running in one atomic step, ``discard-changes`` drops it.
* **startup** — the configuration the twin started with: element values as
  the engine loaded them, twin config as the YAML set it. Read-only;
  deleting a leaf in running or candidate puts it back to this value.

An *edit* turns a request into a change set: an ordered
``{LeafKey: value | REVERT}``. Every change set — a PUT, a PATCH, a YANG
Patch, a DELETE, a commit — is validated in full before anything is
written, and an error names every offending leaf. Application goes through
``TapiContext`` so baselines, override records, snapshots and the running
revision (the ETag) follow.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from twinlight.physics.backend import element_kind_name
from twinlight.transparent.tree import (
    CANONICAL_ROOTS,
    MODULE,
    Leaf,
    LeafKey,
    Node,
    TreeBuilder,
    TreeError,
    config_leaves,
)

if TYPE_CHECKING:
    from twinlight.state.context import TapiContext


class _Revert:
    """Marker value: put the leaf back to its startup value."""

    def __repr__(self) -> str:
        return "<startup>"


REVERT: Any = _Revert()

DATASTORES = ("running", "candidate", "startup", "operational")
CONVENTIONAL = ("running", "candidate", "startup")


def datastore_name(raw: str) -> str:
    """``ietf-datastores:running`` or ``running`` → ``running``."""
    name = raw.removeprefix("ietf-datastores:")
    if name not in DATASTORES:
        raise TreeError(
            f"Unknown datastore {raw!r}; known: "
            f"{', '.join('ietf-datastores:' + d for d in DATASTORES)}",
            tag="invalid-value", status=404,
        )
    return name


# -- Values per datastore -------------------------------------------------------


def running_explicit(ctx: TapiContext, key: LeafKey) -> bool:
    """True if ``key`` has been set in running (i.e. differs from startup)."""
    if key.scope == "element":
        if key.name == "failed":
            return key.owner in ctx.get_failed_links()
        return key.name in ctx.get_element_overrides().get(key.owner, {})
    return key.name in ctx.get_twin_overrides()


def startup_value(ctx: TapiContext, key: LeafKey) -> Any:
    from twinlight.transparent.tree import jsonable

    if key.scope == "element":
        return jsonable(ctx.startup_element_value(key.owner, key.name))
    return jsonable(ctx.startup_twin_value(key.name))


@dataclass
class CandidateDatastore:
    """Edits staged against running and not yet committed."""

    edits: dict[LeafKey, Any] = field(default_factory=dict)
    revision: int = 0
    last_modified: datetime = field(default_factory=lambda: datetime.now(UTC))
    # Running revision when the first edit was staged: lets a commit report
    # that running moved underneath the candidate in the meantime.
    base_revision: int | None = None

    def stage(self, changes: dict[LeafKey, Any], running_revision: int) -> None:
        if not changes:
            return
        if not self.edits:
            self.base_revision = running_revision
        for key, value in changes.items():
            self.edits.pop(key, None)  # a later edit moves to the end
            self.edits[key] = value
        self._touch()

    def discard(self) -> None:
        self.edits.clear()
        self.base_revision = None
        self._touch()

    def _touch(self) -> None:
        self.revision += 1
        self.last_modified = datetime.now(UTC)


class ValueSource:
    """How one datastore answers "what is this leaf's value?"."""

    name = "operational"

    def __init__(self, ctx: TapiContext) -> None:
        self.ctx = ctx

    async def value(self, leaf: Leaf) -> Any:
        return await leaf.read()

    def explicit(self, key: LeafKey) -> bool:
        return running_explicit(self.ctx, key)

    def startup(self, key: LeafKey) -> Any:
        return startup_value(self.ctx, key)


class RunningSource(ValueSource):
    name = "running"


class CandidateSource(ValueSource):
    name = "candidate"

    async def value(self, leaf: Leaf) -> Any:
        edits = self.ctx.candidate.edits
        if leaf.key is not None and leaf.key in edits:
            staged = edits[leaf.key]
            return self.startup(leaf.key) if staged is REVERT else staged
        return await leaf.read()

    def explicit(self, key: LeafKey) -> bool:
        edits = self.ctx.candidate.edits
        if key in edits:
            return edits[key] is not REVERT
        return running_explicit(self.ctx, key)


class StartupSource(ValueSource):
    name = "startup"

    async def value(self, leaf: Leaf) -> Any:
        if leaf.key is None:
            return await leaf.read()
        return self.startup(leaf.key)

    def explicit(self, key: LeafKey) -> bool:
        return False


def source_for(ctx: TapiContext, name: str) -> ValueSource:
    return {
        "operational": ValueSource,
        "running": RunningSource,
        "candidate": CandidateSource,
        "startup": StartupSource,
    }[name](ctx)


# -- Rendering -------------------------------------------------------------------


@dataclass
class RenderOptions:
    """RESTCONF retrieval parameters (RFC 8040 §4.8, RFC 6243)."""

    source: ValueSource
    depth: int | None = None            # None = unbounded
    content: str = "all"                # all | config | nonconfig
    with_defaults: str = "report-all"   # report-all | explicit | trim

    def includes(self, leaf: Leaf) -> bool:
        # Conventional datastores hold configuration only (RFC 8342 §5.1).
        if self.content == "config" or self.source.name in CONVENTIONAL:
            return leaf.key is not None
        if self.content == "nonconfig":
            return leaf.key is None
        return True


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, float) and isinstance(b, (int, float)) and not isinstance(b, bool):
        return math.isclose(a, float(b), rel_tol=1e-9, abs_tol=1e-12)
    return bool(a == b)


def _children_for(node: Node, opts: RenderOptions) -> list[tuple[str, Node]]:
    children = node.children()
    if node.kind == "root" and opts.source.name in CONVENTIONAL:
        return [(n, children[n]) for n in CANONICAL_ROOTS]
    return list(children.items())


async def _render(node: Node, opts: RenderOptions, level: int) -> tuple[bool, Any]:
    if isinstance(node, Leaf):
        if not opts.includes(node):
            return False, None
        value = await opts.source.value(node)
        if node.key is not None:
            if opts.with_defaults == "explicit" and not opts.source.explicit(node.key):
                return False, None
            if opts.with_defaults == "trim" and _same(value, opts.source.startup(node.key)):
                return False, None
        return True, value
    if opts.depth is not None and level >= opts.depth:
        return True, {}
    out: dict[str, Any] = {}
    for name, child in _children_for(node, opts):
        present, value = await _render(child, opts, level + 1)
        if present:
            out[name] = value
    if not out and level > 1:
        return False, None
    return True, out


async def render_tree(nodes: list[Node], opts: RenderOptions) -> dict[str, Any]:
    """The matched nodes as one tree rooted at ``/`` (casts dropped).

    The keys of the result are path segments, so the JSON pointer to any
    value is its path: a GET's output can be edited and PATCHed back.
    """
    result: dict[str, Any] = {}
    for node in nodes:
        present, value = await _render(node, opts, 1)
        if not present:
            continue
        if not node.path:
            result.update(value)
            continue
        cursor = result
        for name in node.path[:-1]:
            cursor = cursor.setdefault(name, {})
        last = node.path[-1]
        if isinstance(value, dict) and isinstance(cursor.get(last), dict):
            cursor[last].update(value)
        else:
            cursor[last] = value
    return result


async def render_leaves(nodes: list[Node], opts: RenderOptions) -> list[dict[str, Any]]:
    """The matched nodes' leaves as a flat list — ns-3's ``LookupMatches``.

    Each entry's ``path`` names exactly that leaf and can be written back;
    config leaves also carry their ``canonical`` path (when reached through
    another branch), whether they are ``explicit``ly set, and their
    ``startup`` value.
    """
    out: list[dict[str, Any]] = []

    async def walk(node: Node, level: int) -> None:
        if isinstance(node, Leaf):
            present, value = await _render(node, opts, level)
            if not present:
                return
            entry: dict[str, Any] = {"path": node.path_text, "value": value}
            entry.update(node.spec.describe())
            if node.key is not None:
                if node.key.path != entry["path"]:
                    entry["canonical"] = node.key.path
                entry["explicit"] = opts.source.explicit(node.key)
                entry["startup"] = opts.source.startup(node.key)
            out.append(entry)
            return
        if opts.depth is not None and level >= opts.depth:
            return
        for _name, child in _children_for(node, opts):
            await walk(child, level + 1)

    for node in nodes:
        await walk(node, 1)
    return out


# -- Building change sets from requests ----------------------------------------------


def _read_only(leaf: Leaf) -> TreeError:
    return TreeError(
        f"{leaf.path_text} is read-only (state data). Writable leaves are "
        f"listed with config=true by GET …?format=leaves&content=config",
        tag="access-denied", status=403, path=leaf.path_text,
    )


def _unwrap(node: Node, body: Any) -> Any:
    """Accept a RESTCONF-style body wrapped in its target's name."""
    name = node.path[-1] if node.path else "data"
    if isinstance(body, dict) and len(body) == 1:
        ((key, value),) = body.items()
        bare = key.removeprefix(f"{MODULE}:")
        if bare == name and (isinstance(node, Leaf) or name not in node.children()):
            return value
    return body


def _merge_into(node: Node, obj: dict[str, Any], changes: dict[LeafKey, Any]) -> None:
    children = node.children()
    for name, sub in obj.items():
        child = children.get(name)
        if child is None:
            raise TreeError(
                f"{node.path_text or '/'}: no child named {name!r}. "
                f"Available: {sorted(children)}",
                tag="unknown-element", path=node.path_text or "/",
            )
        if isinstance(child, Leaf):
            if child.key is None:
                raise _read_only(child)
            _set(changes, child.key, sub)
        else:
            if not isinstance(sub, dict):
                raise TreeError(
                    f"{child.path_text}: expected a JSON object of its children",
                    path=child.path_text,
                )
            _merge_into(child, sub, changes)


def _set(changes: dict[LeafKey, Any], key: LeafKey, value: Any) -> None:
    changes.pop(key, None)
    changes[key] = value


def _revert_all(node: Node, changes: dict[LeafKey, Any]) -> None:
    """Revert every config leaf under ``node``, each once, in tree order
    (a leaf reached again through another view keeps its first place)."""
    for leaf in config_leaves(node):
        assert leaf.key is not None
        changes.setdefault(leaf.key, REVERT)


def build_edit(nodes: list[Node], operation: str, body: Any = None) -> dict[LeafKey, Any]:
    """The change set an operation on ``nodes`` makes.

    * ``merge`` — set the leaves named in ``body`` (RESTCONF PATCH);
    * ``replace`` — set the leaves named in ``body``, and put every other
      config leaf under the target back to startup (RESTCONF PUT);
    * ``remove`` / ``delete`` — put every config leaf under the target back
      to startup. Nothing is ever deleted from the network: services
      belong to T-API, elements to the topology.

    The same relative ``body`` applies to each matched node, so
    ``PATCH /ElementList/*/$Edfa {"gain_target": 18}`` sets every amplifier.
    """
    changes: dict[LeafKey, Any] = {}
    for node in nodes:
        if operation in ("remove", "delete"):
            if isinstance(node, Leaf) and node.key is None:
                raise _read_only(node)
            _revert_all(node, changes)
            continue
        if operation not in ("merge", "replace"):
            raise TreeError(
                f"Operation {operation!r} is not supported: the tree's lists "
                f"mirror the network and cannot gain or lose entries",
                tag="operation-not-supported", status=501,
            )
        value = _unwrap(node, body)
        if isinstance(node, Leaf):
            if node.key is None:
                raise _read_only(node)
            _set(changes, node.key, value)
            continue
        if not isinstance(value, dict):
            raise TreeError(
                f"{node.path_text or '/'}: expected a JSON object of its children, "
                f"got {type(value).__name__}", path=node.path_text or "/",
            )
        if operation == "replace":
            _revert_all(node, changes)
        _merge_into(node, value, changes)
    return changes


# -- Validation and application ----------------------------------------------------


def validate_changes(ctx: TapiContext, changes: dict[LeafKey, Any]) -> list[TreeError]:
    """Every problem with a change set, without writing anything."""
    from twinlight.state.twin_overrides import TwinOverrideError
    from twinlight.state.twin_overrides import validate as validate_twin

    errors: list[TreeError] = []
    backend = ctx._backend
    schema = backend.supported_attributes() if backend.available else {}
    twin: dict[str, Any] = {}
    for key, value in changes.items():
        if value is REVERT:
            continue
        if key.scope == "config":
            twin[key.name] = value
            continue
        el = backend.uid_map.get(key.owner) if backend.available else None
        if el is None:
            errors.append(TreeError(
                f"{key.path}: element {key.owner!r} does not exist",
                tag="invalid-value", status=404, path=key.path,
            ))
            continue
        kind = element_kind_name(el)
        if key.name == "failed":
            if not isinstance(value, bool):
                errors.append(TreeError(
                    f"{key.path}: expected a boolean, got {type(value).__name__}",
                    path=key.path,
                ))
            elif kind != "Fiber":
                errors.append(TreeError(
                    f"{key.path}: only fibers can fail", path=key.path,
                ))
            continue
        spec = (schema.get(kind) or {}).get(key.name)
        if spec is None:
            errors.append(TreeError(
                f"{key.path}: {kind} has no writable {key.name!r}",
                tag="unknown-element", path=key.path,
            ))
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(TreeError(
                f"{key.path}: expected a number, got {type(value).__name__}",
                path=key.path,
            ))
            continue
        lo, hi = spec.get("min"), spec.get("max")
        if (lo is not None and value < lo) or (hi is not None and value > hi):
            errors.append(TreeError(
                f"{key.path}={value:g} outside [{lo}, {hi}] {spec.get('unit', '')}".rstrip(),
                path=key.path,
            ))
    if twin:
        try:
            validate_twin(twin, ctx._config)
        except TwinOverrideError as exc:
            for message in str(exc).split("; "):
                dotted = next((k for k in twin if message.startswith(k)), None)
                path = LeafKey("config", "", dotted).path if dotted else "/Config"
                errors.append(TreeError(message, path=path))
    return errors


class ChangeError(Exception):
    """A change set was refused; nothing was written."""

    def __init__(self, errors: list[TreeError]) -> None:
        super().__init__("; ".join(str(e) for e in errors))
        self.errors = errors


def effective_changes(
    ctx: TapiContext, changes: dict[LeafKey, Any], datastore: str = "running",
) -> dict[LeafKey, Any]:
    """Drop reverts that change nothing in ``datastore``: leaves already at
    their startup value (and, for the candidate, with no staged edit)."""
    staged = ctx.candidate.edits if datastore == "candidate" else {}
    return {
        k: v for k, v in changes.items()
        if v is not REVERT or running_explicit(ctx, k) or k in staged
    }



def apply_changes(ctx: TapiContext, changes: dict[LeafKey, Any]) -> list[str]:
    """Validate and write a change set; return the invalidated services.

    Element writes go through ``apply_element_overrides_atomic`` (which
    rolls back on a failed write), reverts through the context's revert
    methods, twin-config writes through ``apply_twin_overrides``. Everything
    is validated first, so the later steps cannot fail on input.
    """
    errors = validate_changes(ctx, changes)
    if errors:
        raise ChangeError(errors)
    changes = effective_changes(ctx, changes)
    element_sets: dict[str, dict[str, Any]] = {}
    element_reverts: list[tuple[str, str]] = []
    twin_sets: dict[str, Any] = {}
    twin_reverts: list[str] = []
    for key, value in changes.items():
        if key.scope == "element":
            if value is REVERT:
                element_reverts.append((key.owner, key.name))
            else:
                element_sets.setdefault(key.owner, {})[key.name] = value
        elif value is REVERT:
            twin_reverts.append(key.name)
        else:
            twin_sets[key.name] = value

    invalidated: set[str] = set()
    if element_sets:
        for services in ctx.apply_element_overrides_atomic(element_sets).values():
            invalidated |= services
    if element_reverts:
        for services in ctx.revert_element_overrides(element_reverts).values():
            invalidated |= services
    if twin_sets:
        ctx.apply_twin_overrides(twin_sets)
    if twin_reverts:
        ctx.revert_twin_overrides(twin_reverts)
    return sorted(invalidated)


def _find_leaf(builder: TreeBuilder, key: LeafKey) -> Leaf | None:
    node: Node = builder.root()
    for name in key.names:
        child = node.children().get(name)
        if child is None:
            return None
        node = child
    return node if isinstance(node, Leaf) else None


async def describe_changes(
    ctx: TapiContext, changes: dict[LeafKey, Any],
) -> list[dict[str, Any]]:
    """Each change with the running value it replaces."""
    builder = TreeBuilder(ctx)
    out: list[dict[str, Any]] = []
    for key, value in changes.items():
        leaf = _find_leaf(builder, key)
        previous = await leaf.read() if leaf is not None else None
        entry: dict[str, Any] = {
            "path": key.path,
            "operation": "remove" if value is REVERT else "replace",
            "previous": previous,
            "value": startup_value(ctx, key) if value is REVERT else value,
        }
        if leaf is not None and leaf.spec.unit:
            entry["unit"] = leaf.spec.unit
        out.append(entry)
    return out


async def preview_changes(
    ctx: TapiContext, changes: dict[LeafKey, Any],
) -> list[dict[str, Any]]:
    """Predicted QoT of every service the change set would touch."""
    element_values: dict[str, dict[str, Any]] = {}
    element_reverts: list[tuple[str, str]] = []
    margin: float | None = None
    for key, value in effective_changes(ctx, changes).items():
        if key.scope == "element":
            if value is REVERT:
                element_reverts.append((key.owner, key.name))
            else:
                element_values.setdefault(key.owner, {})[key.name] = value
        elif key.name == "rmsa.qot_margin_db":
            margin = float(startup_value(ctx, key) if value is REVERT else value)
    return await ctx.preview_qot(element_values, element_reverts, margin)


# -- Compare (RFC 9144-inspired) ---------------------------------------------------


def _interesting_keys(ctx: TapiContext, names: tuple[str, str]) -> list[LeafKey]:
    keys: dict[LeafKey, None] = {}
    if any(n in ("running", "operational", "startup") for n in names) or "candidate" in names:
        for uid, attrs in ctx.get_element_overrides().items():
            for attr in attrs:
                keys[LeafKey("element", uid, attr)] = None
        for uid in sorted(ctx.get_failed_links()):
            keys[LeafKey("element", uid, "failed")] = None
        for dotted in ctx.get_twin_overrides():
            keys[LeafKey("config", "", dotted)] = None
    if "candidate" in names:
        for key in ctx.candidate.edits:
            keys[key] = None
    return sorted(keys, key=lambda k: k.path)


async def compare(ctx: TapiContext, source: str, target: str) -> list[dict[str, Any]]:
    """The edits that turn ``source`` into ``target``, as YANG-Patch edits.

    ``value`` is the target's value and ``source-value`` the source's; an
    edit whose target is the startup value is a ``remove``, so applying the
    result to running with YANG Patch reproduces the target exactly,
    override records included.
    """
    builder = TreeBuilder(ctx)
    src, tgt = source_for(ctx, source), source_for(ctx, target)
    edits: list[dict[str, Any]] = []
    for key in _interesting_keys(ctx, (source, target)):
        leaf = _find_leaf(builder, key)
        if leaf is None:
            continue
        sv, tv = await src.value(leaf), await tgt.value(leaf)
        if _same(sv, tv) and src.explicit(key) == tgt.explicit(key):
            continue
        edit: dict[str, Any] = {
            "edit-id": str(len(edits) + 1),
            "operation": "replace" if tgt.explicit(key) else "remove",
            "target": key.path,
            "source-value": {leaf.spec.name: sv},
        }
        if tgt.explicit(key):
            edit["value"] = {leaf.spec.name: tv}
        edits.append(edit)
    return edits
