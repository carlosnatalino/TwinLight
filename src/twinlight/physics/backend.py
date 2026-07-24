"""Physical-layer backend Protocol.

The twin can compute QoT baselines via more than one propagation engine
— today GNPy is the only implementation; M4 adds an EGN backend from
``optical-networking-gym``. Both engines plug in through this Protocol
so ``TapiContext`` stays backend-agnostic and the transient layer
(`physics/transients/*`) is reused unchanged.

Backends own:
* their propagation engine + the network/uid-map state it needs,
* per-element parameter read/write (so the /config plane can mutate
  whichever engine is selected),
* a per-backend re-design hook (GNPy: ``designed_network``; EGN: no-op),
* per-backend graph mutation for fiber-link failure (the routing
  graph in ``TopologyGraph`` is mutated by ``TapiContext`` itself —
  the backend only needs to invalidate its own caches / state).

This module deliberately stays small. The Protocol is structural —
implementations don't have to inherit from it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    import networkx as nx

    from twinlight.physics.gnpy_adapter import OpmBaseline


class PhysicalBackend(Protocol):
    """Common surface for GNPy / EGN / future QoT backends."""

    # Short stable identifier used in CLI/YAML/snapshot. "gnpy" or "egn".
    name: str
    # True if the backend's underlying engine loaded successfully. When
    # False, OPM falls back to the sinusoidal mock — same behaviour as
    # today when GNPy fails to import.
    available: bool
    # uid → element-like object for every node in the topology. GNPy's
    # values are the real ``gnpy.core.elements`` objects; EGN's are
    # lightweight stand-ins (a dict or a typed namespace) carrying at
    # least the ``.uid`` and class-name semantics that the rest of the
    # twin reads. Used by topology serialization, ``services_for_element``,
    # and the /config plane's "what type is this UID?" checks.
    uid_map: dict[str, Any]
    # GNPy uses a NetworkX DiGraph of element objects for path
    # computation; the EGN backend leaves this ``None`` (path computation
    # falls back to the TopologyGraph-based NetworkX k-shortest).
    network: nx.DiGraph | None

    async def compute_baseline(
        self,
        path_uids: list[str],
        modulation_format: str,
    ) -> OpmBaseline | None:
        """Return a static QoT baseline for the path, or ``None`` if
        propagation is not possible (engine unavailable, missing element).
        Returning ``None`` puts the OPM endpoint into mock-data fallback.
        """

    def read_attribute(self, uid: str, attr: str) -> Any:
        """Read a /config-mutable attribute as a JSON-friendly user value."""

    def write_attribute(self, uid: str, attr: str, value: Any) -> None:
        """Write a /config-mutable attribute. Raises ``ParamValidationError``
        if the attribute is not supported on this element/backend.
        """

    def read_all_attributes(self, uid: str) -> dict[str, Any]:
        """Return every allow-listed attribute for the element."""

    def supported_attributes(
        self,
    ) -> dict[str, dict[str, dict[str, Any]]]:
        """Self-describing schema: per element class name → attr → spec."""

    def redesign(self) -> None:
        """Re-equalise the network's design state (GNPy: ``designed_network``).
        Backends without a redesign step raise ``NotImplementedError`` and
        the /config router converts that to a 501.
        """

    def is_fiber(self, uid: str) -> bool:
        """True if ``uid`` is a fiber span. Used by ``TapiContext.set_link_failed``
        to validate the input without backend-specific isinstance checks."""

    def notify_fiber_failed(self, uid: str, failed: bool) -> None:
        """Called after ``TapiContext`` mutates the routing graphs, so a
        backend can update any failure state it keeps internally.
        GNPy backend has none — graph-edge removal is sufficient. The
        EGN backend tracks a set used by ``compute_baseline`` to
        short-circuit to ``status="link-failed"``."""


def element_kind_name(el: Any) -> str:
    """Return ``"Fiber"`` / ``"Edfa"`` / ``"Roadm"`` / ``"Transceiver"`` for
    either a real GNPy element object (via ``type(el).__name__``) or an
    EGN ``_UidStub`` (which carries the kind in its ``.type`` attribute).

    Several call sites in ``TapiContext`` and the API layer key off the
    element's kind name. The GNPy backend's uid_map holds real GNPy
    element instances; the EGN backend's uid_map holds lightweight
    stand-ins (no real Fiber/Edfa class hierarchy). This helper hides
    that difference.
    """
    if el is None:
        return ""
    cls_name = type(el).__name__
    if cls_name == "_UidStub":
        return getattr(el, "type", "")
    return cls_name


def build_backend(config: TwinConfig) -> PhysicalBackend:
    """Construct the configured backend. Imported lazily to keep optional
    deps (the EGN library) off the import path when they're not used.

    Raises ``ValueError`` if the configured backend name is unknown, or
    ``ImportError`` if the EGN extra was selected without installing it.
    """
    name = config.physics.backend
    if name == "gnpy":
        from twinlight.physics.gnpy_backend import GnpyBackend

        return GnpyBackend(config)
    if name == "egn":
        try:
            # EgnBackend lands in M4 of the egn_backend branch; until then
            # this import always fails. The error path below surfaces a
            # clear message to the user.
            from twinlight.physics.egn_backend import EgnBackend
        except ImportError as exc:
            raise ImportError(
                "Physics backend 'egn' selected but the EGN library is "
                "not installed. Install with: pip install -e \".[egn]\""
            ) from exc
        return EgnBackend(config)
    raise ValueError(f"Unknown physics backend: {name!r}")


if TYPE_CHECKING:
    from twinlight.config import TwinConfig
