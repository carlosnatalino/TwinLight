"""GNPy implementation of ``PhysicalBackend``.

Encapsulates the gnpy 2.x network handle, the UID→element map, and the
equipment dict that today live directly on ``TapiContext``. Splitting
them out lets a second backend (EGN, M4) plug in cleanly and lets
``TapiContext`` stop importing ``gnpy.*`` directly.

No new behaviour here vs. the previous in-context code — this is a pure
refactor that M1 introduces so subsequent milestones have a contract
to satisfy.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from tapi_twin.config import TwinConfig

if TYPE_CHECKING:
    import networkx as nx

    from tapi_twin.physics.gnpy_adapter import OpmBaseline


logger = logging.getLogger(__name__)


class GnpyBackend:
    """GNPy-backed QoT propagation + element mutation."""

    name = "gnpy"

    def __init__(self, config: TwinConfig) -> None:
        self.available: bool = False
        self.uid_map: dict[str, Any] = {}
        self.network: "nx.DiGraph | None" = None
        # Retained for ``redesign()`` so we don't re-read the equipment
        # JSON from disk on every override.
        self.equipment: Any = None

        if config.gnpy.equipment is None:
            logger.info("No GNPy equipment file configured — using mock OPM data")
            return

        try:
            from tapi_twin.physics.gnpy_adapter import (
                build_gnpy_network,
                build_uid_map,
            )

            network, equipment = build_gnpy_network(
                config.gnpy.topology, config.gnpy.equipment
            )
            self.network = network
            self.equipment = equipment
            self.uid_map = build_uid_map(network)
            self.available = True
            logger.info("GNPy network loaded: %d elements", len(self.uid_map))
        except ImportError:
            logger.warning(
                "gnpy package not found — OPM will use sinusoidal mock data"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "GNPy network load failed (%s) — using mock data", exc
            )

    # -- QoT propagation -------------------------------------------------

    async def compute_baseline(
        self,
        path_uids: list[str],
        modulation_format: str,
    ) -> "OpmBaseline | None":
        if not self.available:
            return None
        from tapi_twin.physics.gnpy_adapter import compute_path_baseline

        try:
            return compute_path_baseline(
                self.uid_map, path_uids, modulation_format,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("GNPy baseline computation failed: %s", exc)
            return None

    # -- /config plane: per-element parameter reads/writes ---------------

    def read_attribute(self, uid: str, attr: str) -> Any:
        from tapi_twin.physics.element_params import read_attr

        el = self.uid_map.get(uid)
        if el is None:
            raise KeyError(f"Unknown GNPy element UID: {uid!r}")
        return read_attr(el, attr)

    def write_attribute(self, uid: str, attr: str, value: Any) -> None:
        from tapi_twin.physics.element_params import write_attr

        el = self.uid_map.get(uid)
        if el is None:
            raise KeyError(f"Unknown GNPy element UID: {uid!r}")
        write_attr(el, attr, value)

    def read_all_attributes(self, uid: str) -> dict[str, Any]:
        from tapi_twin.physics.element_params import read_all

        el = self.uid_map.get(uid)
        if el is None:
            raise KeyError(f"Unknown GNPy element UID: {uid!r}")
        return read_all(el)

    def supported_attributes(self) -> dict[str, dict[str, dict[str, Any]]]:
        from tapi_twin.physics.element_params import schema

        return schema()

    # -- Element kind queries (backend-neutral surface) -------------------

    def is_fiber(self, uid: str) -> bool:
        from gnpy.core.elements import Fiber

        return isinstance(self.uid_map.get(uid), Fiber)

    def notify_fiber_failed(self, uid: str, failed: bool) -> None:
        """Called by ``TapiContext.set_link_failed`` after the routing
        graphs are mutated. The GNPy backend learns about failure
        through the DiGraph edge removal that ``TapiContext`` does
        directly on ``self.network``, so this hook is a no-op here —
        kept so the Protocol surface stays uniform across backends."""

    # -- Re-design (opt-in equalisation after parameter changes) ---------

    def redesign(self) -> None:
        """Re-run ``gnpy.tools.worker_utils.designed_network`` and refresh
        the local uid_map/network handles to whatever it returns.
        """
        if not self.available:
            return
        from gnpy.tools.worker_utils import designed_network

        from tapi_twin.physics.gnpy_adapter import build_uid_map

        network, _ref_req, _ref_chan = designed_network(
            self.equipment, self.network,
        )
        self.network = network
        self.uid_map = build_uid_map(network)
