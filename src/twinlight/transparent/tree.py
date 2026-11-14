"""The data tree the transparent controller's paths walk.

The tree is built on demand from the live ``TapiContext`` — it is a *view*,
never a copy, so it cannot go stale. Its shape::

    /
    ├── ElementList/<uid>            every element of the physical backend's
    │     uid, type, … (state)       network: transceivers, ROADMs, fibers,
    │     loss_coef, … (config)      amplifiers (GNPy's boosters and fiber
    │                                sub-spans included)
    ├── NodeList/<uid>               the terminals (ROADMs, transceivers) —
    │                                ns-3's NodeList; same entries as above
    ├── LinkList/<from>/<to>         a directional OMS between two terminals
    │     from, to, length_km, …
    │     ElementList/<uid>          its inline elements, in propagation order
    ├── ServiceList/<uuid>           connectivity services (read-only here:
    │     path, spectrum, …          their lifecycle belongs to T-API)
    │     qot/gsnr_db, …             static QoT baseline of the allocated path
    │     ElementList/<uid>          the elements on the allocated path
    ├── ModulationList/<format>      transceiver mode table (read-only)
    └── Config/<section>/…/<field>   the twin's own model configuration

Leaves are *config* (writable) or *state* (read-only), as in YANG. Only two
kinds of config leaf exist, whatever path reaches them:

* an element attribute — ``LeafKey("element", uid, attr)``, from the
  backend's allow-list, plus ``failed`` on fibers;
* a twin-config field — ``LeafKey("config", "", "transients.….tau_ms")``,
  from ``state.twin_overrides.TWIN_ALLOWED``.

So ``/LinkList/a/z/ElementList/x/loss_coef`` and
``/ElementList/x/loss_coef`` are one leaf with one canonical path, and a
write through either is the same write.
"""

from __future__ import annotations

import enum
import functools
import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import TYPE_CHECKING, Any

from pydantic import SecretStr

from twinlight.physics.backend import element_kind_name
from twinlight.transparent.path import Segment, format_path

if TYPE_CHECKING:
    from pydantic.fields import FieldInfo

    from twinlight.state.context import TapiContext

MODULE = "twinlight-transparent"
SCHEMA_REVISION = "2026-11-02"

# Element kinds a ``$`` cast may name. Known statically so a misspelt cast
# (``$Fibre``) is an error even when the topology has no element of the
# intended kind.
ELEMENT_KINDS = ("Transceiver", "Roadm", "Fiber", "Edfa", "Fused")
TERMINAL_KINDS = ("Roadm", "Transceiver")
# Where each config leaf lives canonically. The other root branches are
# views onto the same leaves, so a conventional datastore read at "/"
# shows only these — once each.
CANONICAL_ROOTS = ("ElementList", "Config")
INLINE_KINDS = ("Fiber", "Edfa")


class TreeError(ValueError):
    """A path or edit that does not fit the tree.

    Carries the RESTCONF error-tag and HTTP status it maps to.
    """

    def __init__(self, message: str, tag: str = "invalid-value",
                 status: int = 400, path: str | None = None) -> None:
        super().__init__(message)
        self.tag = tag
        self.status = status
        self.path = path


# -- Leaves --------------------------------------------------------------------


@dataclass(frozen=True)
class LeafSpec:
    """What a leaf is: the schema half, independent of any instance."""

    name: str
    type: str
    config: bool
    unit: str = ""
    description: str = ""
    minimum: float | None = None
    maximum: float | None = None
    bounds: dict[str, float] = field(default_factory=dict)
    choices: tuple[Any, ...] | None = None
    note: str = ""

    def describe(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": self.type, "config": self.config}
        if self.unit:
            out["unit"] = self.unit
        if self.minimum is not None:
            out["min"] = self.minimum
        if self.maximum is not None:
            out["max"] = self.maximum
        if self.bounds:
            out["bounds"] = dict(self.bounds)
        if self.choices is not None:
            out["choices"] = list(self.choices)
        if self.description:
            out["description"] = self.description
        if self.note:
            out["note"] = self.note
        return out


@dataclass(frozen=True)
class LeafKey:
    """The identity of a writable leaf, whichever path reached it."""

    scope: str  # "element" | "config"
    owner: str  # element UID; "" for config
    name: str   # attribute, or dotted config key

    @property
    def names(self) -> tuple[str, ...]:
        if self.scope == "element":
            return ("ElementList", self.owner, self.name)
        return ("Config", *self.name.split("."))

    @property
    def path(self) -> str:
        return format_path(self.names)


# -- Nodes ---------------------------------------------------------------------


class Node:
    """A node of the tree. ``path`` is its canonical location (no casts)."""

    kind = "node"
    element_kind: str | None = None

    def __init__(self, path: tuple[str, ...]) -> None:
        self.path = path

    @property
    def path_text(self) -> str:
        return format_path(self.path)

    def children(self) -> dict[str, Node]:
        return {}

    def aliases(self, name: str) -> tuple[str, ...]:
        """Other names a list entry may be selected by (service names)."""
        return ()


class Leaf(Node):
    kind = "leaf"

    def __init__(
        self, path: tuple[str, ...], spec: LeafSpec,
        reader: Callable[[], Any], key: LeafKey | None = None,
    ) -> None:
        super().__init__(path)
        self.spec = spec
        self._reader = reader
        self.key = key if spec.config else None

    async def read(self) -> Any:
        value = self._reader()
        if inspect.isawaitable(value):
            value = await value
        return jsonable(value)


class Branch(Node):
    """A container, list or list entry whose children are built lazily."""

    def __init__(
        self, path: tuple[str, ...], kind: str,
        factory: Callable[[], dict[str, Node]],
        element_kind: str | None = None,
        aliases: Callable[[str], tuple[str, ...]] | None = None,
    ) -> None:
        super().__init__(path)
        self.kind = kind
        self._factory = factory
        self._children: dict[str, Node] | None = None
        self.element_kind = element_kind
        self._aliases = aliases

    def children(self) -> dict[str, Node]:
        if self._children is None:
            self._children = self._factory()
        return self._children

    def aliases(self, name: str) -> tuple[str, ...]:
        return self._aliases(name) if self._aliases else ()


def jsonable(value: Any) -> Any:
    """Make a leaf value JSON-friendly (enums, paths, numpy scalars).

    A ``SecretStr`` (the streaming auth token) renders as its mask: the
    controller is transparent about the twin's model, not its credentials.
    """
    if isinstance(value, SecretStr):
        return str(value)
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, PurePath):
        return str(value)
    if isinstance(value, (list, tuple, set)):
        return [jsonable(v) for v in value]
    if hasattr(value, "item") and callable(value.item):  # numpy scalar
        return value.item()
    return value


# -- Schema: leaf specs --------------------------------------------------------


def _state(name: str, type_: str, description: str, unit: str = "") -> LeafSpec:
    return LeafSpec(name=name, type=type_, config=False, unit=unit,
                    description=description)


_ELEMENT_COMMON = (
    _state("uid", "string", "Element UID in the physical backend's network"),
    _state("type", "string", "Element kind"),
)
_GNPY_TYPE_VARIETY = _state(
    "type_variety", "string", "Equipment-library variety (GNPy)",
)
_INLINE_STATE = (
    _state("upstream", "string", "Nearest ROADM or transceiver upstream"),
    _state("downstream", "string", "Nearest ROADM or transceiver downstream"),
)
_SERVICES_STATE = _state(
    "services", "string-list",
    "Connectivity services whose allocated path traverses the element",
)
_FIBER_STATE = (_state("length_km", "number", "Span length", "km"),)
_TERMINAL_STATE = (
    _state("tapi_node_uuid", "string", "Cross-reference: the T-API node"),
    _state("sips", "string-list",
           "Cross-reference: T-API service-interface points mapped here"),
)
FAILED_SPEC = LeafSpec(
    name="failed", type="boolean", config=True,
    description="Fiber cut: removes the span from routing and reports "
                "link-failed on every service across it",
)

LINK_LEAVES = (
    _state("from", "string", "Upstream terminal"),
    _state("to", "string", "Downstream terminal"),
    _state("length_km", "number", "Total fiber length", "km"),
    _state("failed", "boolean", "True if any fiber of the link is failed"),
    _state("tapi_link_uuid", "string", "Cross-reference: the T-API link"),
)

SERVICE_LEAVES = (
    _state("uuid", "string", "Connectivity-service UUID"),
    _state("name", "string", "service-name, if one was given"),
    _state("modulation_format", "string", "Modulation format"),
    _state("path", "string-list", "Allocated path, element UIDs in order"),
    _state("start_slot", "integer", "First allocated spectrum slot"),
    _state("num_slots", "integer", "Allocated slots, guard band included"),
    _state("central_frequency_thz", "number", "Centre frequency", "THz"),
    _state("slot_width_ghz", "number", "Allocated bandwidth", "GHz"),
    _state("required_gsnr_db", "number",
           "GSNR the format needs (before the admission margin)", "dB"),
)
QOT_LEAVES = (
    _state("status", "string", "'ok', or 'link-failed' when a fiber on the path is cut"),
    _state("gsnr_db", "number", "Static GSNR of the allocated path", "dB"),
    _state("osnr_db", "number", "ASE-only OSNR in the signal bandwidth", "dB"),
    _state("margin_db", "number", "gsnr_db - required_gsnr_db", "dB"),
    _state("cd_ps_nm", "number", "Accumulated chromatic dispersion", "ps/nm"),
    _state("pmd_ps", "number", "Accumulated PMD", "ps"),
    _state("latency_ms", "number", "Propagation latency", "ms"),
    _state("total_fiber_km", "number", "Fiber length of the path", "km"),
)
MODULATION_LEAVES = (
    _state("baud_rate_gbaud", "number", "Symbol rate", "GBd"),
    _state("spacing_ghz", "number", "Channel spacing", "GHz"),
    _state("tx_power_dbm", "number", "Launch power per channel", "dBm"),
    _state("req_gsnr_db", "number", "Required GSNR for admission", "dB"),
    _state("bits_per_symbol", "integer", "Bits per symbol per polarization"),
)


def element_attr_spec(name: str, raw: dict[str, Any]) -> LeafSpec:
    """A backend allow-list entry as a config leaf spec."""
    return LeafSpec(
        name=name, type="number", config=True,
        unit=raw.get("unit", ""),
        minimum=raw.get("min"), maximum=raw.get("max"),
        description=raw.get("description", ""),
        note=raw.get("note", ""),
    )


def element_leaf_specs(ctx: TapiContext, kind: str) -> list[LeafSpec]:
    """Every leaf an element of ``kind`` has under the running backend."""
    specs: list[LeafSpec] = list(_ELEMENT_COMMON)
    if ctx._backend.name == "gnpy":
        specs.append(_GNPY_TYPE_VARIETY)
    if kind in INLINE_KINDS:
        specs += _INLINE_STATE
    if kind == "Fiber":
        specs += _FIBER_STATE
    if kind in TERMINAL_KINDS:
        specs += _TERMINAL_STATE
    specs.append(_SERVICES_STATE)
    attrs = ctx._backend.supported_attributes().get(kind) or {}
    specs += [element_attr_spec(n, raw) for n, raw in attrs.items()]
    if kind == "Fiber":
        specs.append(FAILED_SPEC)
    return specs


_CONFIG_TYPES = {bool: "boolean", int: "integer", float: "number", str: "string"}


def config_leaf_spec(key: str, info: FieldInfo) -> LeafSpec:
    """A ``TwinConfig`` field as a leaf spec (config iff runtime-mutable)."""
    from typing import get_origin

    from twinlight.state.twin_overrides import (
        INERT,
        TWIN_ALLOWED,
        _unwrap_optional,
        leaf_bounds,
        leaf_choices,
    )

    annotation = _unwrap_optional(info.annotation)
    choices = leaf_choices(info)
    if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        choices = [m.value for m in annotation]
    if choices is not None:
        type_ = "enumeration"
    elif get_origin(annotation) is list:
        type_ = "string-list"
    elif isinstance(annotation, type) and issubclass(annotation, PurePath):
        type_ = "string"
    else:
        type_ = _CONFIG_TYPES.get(annotation, "string")
    writable = key in TWIN_ALLOWED
    note = ""
    if not writable:
        note = INERT.get(key, "restart-only: set in the YAML file")
    bounds = leaf_bounds(info)
    return LeafSpec(
        name=key.rsplit(".", 1)[-1], type=type_, config=writable,
        description=info.description or "",
        minimum=bounds.get("ge"), maximum=bounds.get("le"),
        bounds={k: v for k, v in bounds.items() if k in ("gt", "lt")},
        choices=tuple(choices) if choices is not None else None,
        note=note,
    )


# -- Building the tree from the live twin --------------------------------------


class TreeBuilder:
    """Builds :class:`Node` views over one ``TapiContext``.

    One builder serves one request: QoT baselines read during it are
    memoised so a service's ``qot`` leaves share a single computation.
    """

    def __init__(self, ctx: TapiContext) -> None:
        self.ctx = ctx
        self._schema = ctx._backend.supported_attributes() if ctx._backend.available else {}
        self._baselines: dict[str, Any] = {}
        self._uid_to_sips: dict[str, list[str]] = {}
        for sip_uuid, uid in ctx._sip_to_gnpy_uid.items():
            self._uid_to_sips.setdefault(uid, []).append(sip_uuid)

    # -- root ------------------------------------------------------------

    def root(self) -> Branch:
        return Branch((), "root", lambda: {
            "ElementList": self.element_list(("ElementList",), self._all_uids()),
            "NodeList": self.element_list(("NodeList",), self._terminal_uids()),
            "LinkList": self.link_list(),
            "ServiceList": self.service_list(),
            "ModulationList": self.modulation_list(),
            "Config": self.config_container(),
        })

    def _uid_map(self) -> dict[str, Any]:
        backend = self.ctx._backend
        return backend.uid_map if backend.available else {}

    def _all_uids(self) -> list[str]:
        return sorted(self._uid_map())

    def _terminal_uids(self) -> list[str]:
        m = self._uid_map()
        return sorted(u for u, el in m.items() if element_kind_name(el) in TERMINAL_KINDS)

    # -- elements --------------------------------------------------------

    def element_list(self, path: tuple[str, ...], uids: list[str]) -> Branch:
        """A list of element entries, in the order given."""
        return Branch(path, "list", lambda: {
            uid: self.element_entry(path + (uid,), uid) for uid in uids
            if uid in self._uid_map()
        })

    def element_entry(self, path: tuple[str, ...], uid: str) -> Branch:
        kind = element_kind_name(self._uid_map()[uid])
        return Branch(
            path, "entry", lambda: self._element_leaves(path, uid, kind),
            element_kind=kind,
        )

    def _element_leaves(
        self, path: tuple[str, ...], uid: str, kind: str,
    ) -> dict[str, Node]:
        ctx, backend = self.ctx, self.ctx._backend
        el = self._uid_map()[uid]
        a, z = ctx.element_terminals(uid)
        state: dict[str, Callable[[], Any]] = {
            "uid": lambda: uid,
            "type": lambda: kind,
            "type_variety": lambda: getattr(el, "type_variety", None),
            "upstream": lambda: a,
            "downstream": lambda: z,
            "length_km": lambda: self._fiber_length_km(uid),
            "tapi_node_uuid": lambda: (ctx._gnpy_uid_to_node_ref.get(uid) or (None, None))[1],
            "sips": lambda: sorted(self._uid_to_sips.get(uid, [])),
            "services": lambda: sorted(ctx.services_for_element(uid)),
        }
        leaves: dict[str, Node] = {}
        for spec in element_leaf_specs(ctx, kind):
            leaf_path = path + (spec.name,)
            if not spec.config:
                leaves[spec.name] = Leaf(leaf_path, spec, state[spec.name])
            elif spec.name == "failed":
                leaves["failed"] = Leaf(
                    leaf_path, spec, lambda: uid in ctx.get_failed_links(),
                    LeafKey("element", uid, "failed"),
                )
            else:
                attr = spec.name
                leaves[attr] = Leaf(
                    leaf_path, spec,
                    functools.partial(backend.read_attribute, uid, attr),
                    LeafKey("element", uid, attr),
                )
        return leaves

    def _fiber_length_km(self, uid: str) -> float | None:
        from twinlight.state.context import _fiber_length_km

        parsed = getattr(self.ctx._topo_graph._gnpy_topo, "elements_by_uid", {})
        el = self._uid_map().get(uid)
        gnpy_el = el if hasattr(el, "params") else None
        km = _fiber_length_km(gnpy_el, parsed.get(uid))
        return None if km is None else round(km, 6)

    # -- links -----------------------------------------------------------

    def link_list(self) -> Branch:
        links = self.ctx.link_elements()
        froms = sorted({a for a, _ in links})

        def to_list(a: str) -> Branch:
            tos = sorted(z for (x, z) in links if x == a)
            return Branch(("LinkList", a), "list", lambda: {
                z: self.link_entry(a, z, links[(a, z)]) for z in tos
            })

        return Branch(("LinkList",), "list", lambda: {a: to_list(a) for a in froms})

    def link_entry(self, a: str, z: str, uids: list[str]) -> Branch:
        path = ("LinkList", a, z)
        ctx = self.ctx

        def length() -> float:
            total = 0.0
            for uid in uids:
                km = self._fiber_length_km(uid) if self._is_fiber(uid) else None
                total += km or 0.0
            return round(total, 6)

        def tapi_link() -> str | None:
            ref = ctx._gnpy_pair_to_link_ref.get((a, z)) or ctx._gnpy_pair_to_link_ref.get((z, a))
            return ref[1] if ref else None

        readers: dict[str, Callable[[], Any]] = {
            "from": lambda: a,
            "to": lambda: z,
            "length_km": length,
            "failed": lambda: any(u in ctx.get_failed_links() for u in uids),
            "tapi_link_uuid": tapi_link,
        }

        def children() -> dict[str, Node]:
            out: dict[str, Node] = {
                s.name: Leaf(path + (s.name,), s, readers[s.name]) for s in LINK_LEAVES
            }
            out["ElementList"] = self.element_list(path + ("ElementList",), uids)
            return out

        return Branch(path, "entry", children)

    def _is_fiber(self, uid: str) -> bool:
        return element_kind_name(self._uid_map().get(uid)) == "Fiber"

    # -- services --------------------------------------------------------

    def _service_name(self, uuid: str) -> str | None:
        svc = self.ctx.get_service(uuid)
        if svc is None:
            return None
        return next(
            (n.value for n in svc.name or [] if n.value_name == "service-name"), None,
        )

    def service_list(self) -> Branch:
        uuids = [s.uuid for s in self.ctx.get_services()]

        def aliases(uuid: str) -> tuple[str, ...]:
            name = self._service_name(uuid)
            return (name,) if name else ()

        return Branch(("ServiceList",), "list", lambda: {
            u: self.service_entry(u) for u in uuids
        }, aliases=aliases)

    async def _baseline(self, uuid: str) -> Any:
        if uuid not in self._baselines:
            svc = self.ctx.get_service(uuid)
            self._baselines[uuid] = (
                await self.ctx.get_or_compute_baseline(svc) if svc else None
            )
        return self._baselines[uuid]

    def service_entry(self, uuid: str) -> Branch:
        from twinlight.physics.modulation import get_params

        ctx = self.ctx
        path = ("ServiceList", uuid)
        svc = ctx.get_service(uuid)
        assert svc is not None
        required = get_params(svc.modulation_format).req_gsnr_db
        alloc = ctx._service_allocation.get(uuid)
        spectrum = ctx.get_service_spectrum(uuid) or {}
        allocated = ctx._allocated_path_uids(uuid) or []

        readers: dict[str, Callable[[], Any]] = {
            "uuid": lambda: uuid,
            "name": lambda: self._service_name(uuid),
            "modulation_format": lambda: svc.modulation_format.value,
            "path": lambda: list(allocated),
            "start_slot": lambda: alloc[1] if alloc else None,
            "num_slots": lambda: alloc[2] if alloc else None,
            "central_frequency_thz": lambda: spectrum.get("nominal-central-frequency"),
            "slot_width_ghz": lambda: spectrum.get("slot-width"),
            "required_gsnr_db": lambda: required,
        }

        def qot_reader(name: str) -> Callable[[], Awaitable[Any]]:
            async def read() -> Any:
                b = await self._baseline(uuid)
                if b is None:
                    return None
                failed = b.status == "link-failed"
                if name == "status":
                    return b.status or "ok"
                if failed:
                    return None
                values = {
                    "gsnr_db": b.gsnr_db, "osnr_db": b.osnr_ase_db,
                    "margin_db": b.gsnr_db - required, "cd_ps_nm": b.cd_ps_nm,
                    "pmd_ps": b.pmd_ps, "latency_ms": b.latency_ms,
                    "total_fiber_km": b.total_fiber_km,
                }
                return round(float(values[name]), 6)
            return read

        def children() -> dict[str, Node]:
            out: dict[str, Node] = {
                s.name: Leaf(path + (s.name,), s, readers[s.name]) for s in SERVICE_LEAVES
            }
            qot_path = path + ("qot",)
            out["qot"] = Branch(qot_path, "container", lambda: {
                s.name: Leaf(qot_path + (s.name,), s, qot_reader(s.name))
                for s in QOT_LEAVES
            })
            out["ElementList"] = self.element_list(
                path + ("ElementList",), list(dict.fromkeys(allocated)),
            )
            return out

        return Branch(path, "entry", children)

    # -- modulation table --------------------------------------------------

    def modulation_list(self) -> Branch:
        from twinlight.physics.modulation import MODULATION_TABLE

        def entry(fmt: str) -> Branch:
            p = MODULATION_TABLE[fmt]
            values = {
                "baud_rate_gbaud": p.baud_rate_hz / 1e9,
                "spacing_ghz": p.spacing_hz / 1e9,
                "tx_power_dbm": p.tx_power_dbm,
                "req_gsnr_db": p.req_gsnr_db,
                "bits_per_symbol": p.bits_per_symbol,
            }
            path = ("ModulationList", fmt)
            return Branch(path, "entry", lambda: {
                s.name: Leaf(path + (s.name,), s, functools.partial(values.__getitem__, s.name))
                for s in MODULATION_LEAVES
            })

        formats = [jsonable(f) for f in MODULATION_TABLE]
        return Branch(("ModulationList",), "list", lambda: {f: entry(f) for f in formats})

    # -- twin configuration ------------------------------------------------

    def config_container(self) -> Branch:
        from twinlight.config import TwinConfig

        return self._config_branch(TwinConfig, ("Config",), "")

    def _config_branch(self, model: Any, path: tuple[str, ...], prefix: str) -> Branch:
        from twinlight.state.twin_overrides import read_key, section_model

        ctx = self.ctx

        def children() -> dict[str, Node]:
            out: dict[str, Node] = {}
            for name, info in model.model_fields.items():
                if info.exclude:
                    continue
                key = f"{prefix}.{name}" if prefix else name
                sub = section_model(model, name)
                if sub is not None:
                    out[name] = self._config_branch(sub, path + (name,), key)
                    continue
                spec = config_leaf_spec(key, info)
                out[name] = Leaf(
                    path + (name,), spec,
                    functools.partial(read_key, ctx._config, key),
                    LeafKey("config", "", key),
                )
            return out

        return Branch(path, "container", children)


# -- Resolution ----------------------------------------------------------------


def resolve(root: Node, segments: list[Segment]) -> list[Node]:
    """Every node ``segments`` selects, in tree order.

    Raises :class:`TreeError` for a path that cannot be right whatever the
    network holds — a cast to an unknown kind, a spelled-out child name that
    exists on none of the nodes reached, an attribute named after a cast
    whose kind lacks it, a path continuing below a leaf. A path that is
    well-formed but matches nothing returns ``[]``.
    """
    current: list[tuple[Node, bool]] = [(root, False)]
    walked: list[str] = []
    for seg in segments:
        walked.append(str(seg))
        where = "/" + "/".join(walked)
        nxt: list[tuple[Node, bool]] = []
        if seg.cast:
            names = seg.selector.exact_names or []
            unknown = [n for n in names if n not in ELEMENT_KINDS]
            if unknown:
                raise TreeError(
                    f"{where}: unknown element kind(s) {unknown}; "
                    f"known: {list(ELEMENT_KINDS)}", tag="unknown-element",
                )
            for node, _strict in current:
                if node.element_kind is None:
                    raise TreeError(
                        f"{where}: a $ cast follows an element entry, not "
                        f"{node.path_text or '/'}", tag="unknown-element",
                    )
                if seg.selector.matches(node.element_kind):
                    nxt.append((node, True))
            current = nxt
            continue

        exact = seg.selector.exact_names
        container_names: set[str] = set()
        seen_container = False
        for node, strict in current:
            if node.kind == "leaf":
                raise TreeError(
                    f"{where}: {node.path_text} is a leaf; the path cannot "
                    f"continue below it", tag="unknown-element",
                )
            children = node.children()
            if node.kind == "list":
                for pos, (name, child) in enumerate(children.items()):
                    if seg.selector.matches(name, pos) or any(
                        seg.selector.matches(alias) for alias in node.aliases(name)
                    ):
                        nxt.append((child, False))
                continue
            seen_container = True
            container_names |= set(children)
            if strict and exact:
                missing = [n for n in exact if n not in children]
                if missing:
                    raise TreeError(
                        f"{where}: {node.element_kind} has no {missing[0]!r} "
                        f"under the running backend. "
                        f"Available: {sorted(children)}", tag="unknown-element",
                    )
            for name, child in children.items():
                if seg.selector.matches(name):
                    nxt.append((child, False))
        if exact and seen_container and not (set(exact) & container_names):
            raise TreeError(
                f"{where}: no child named {exact[0]!r}. "
                f"Available: {sorted(container_names)}", tag="unknown-element",
            )
        current = nxt
    unique: dict[tuple[str, ...], Node] = {}
    for node, _ in current:
        unique.setdefault(node.path, node)
    return list(unique.values())


def config_leaves(node: Node) -> list[Leaf]:
    """Every config leaf at or under ``node`` (synchronous: no reads)."""
    if isinstance(node, Leaf):
        return [node] if node.key is not None else []
    out: list[Leaf] = []
    for child in node.children().values():
        out.extend(config_leaves(child))
    return out
