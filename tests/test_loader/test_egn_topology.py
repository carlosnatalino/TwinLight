"""Tests for the GnpyTopology → EGN converter (M3).

The converter walks the GNPy element/connection graph and emits an
EGN-shaped (link, span) view plus a GNPy-UID → EGN-location index
that the /config plane will use to route mutations.

Tests are deliberately offline — no ``optical_networking_gym`` import
— so M3 is a self-contained step.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from twinlight.loader.egn_topology import (
    EgnLinkData,
    EgnSpanData,
    EgnTopologyData,
    convert,
)
from twinlight.loader.gnpy_topology import load_gnpy_topology

FIXTURES = Path(__file__).parent.parent / "fixtures"


@pytest.fixture(scope="module")
def egn(tmp_path_factory) -> EgnTopologyData:
    gnpy_topo = load_gnpy_topology(FIXTURES / "edfa_example_network.json")
    return convert(gnpy_topo)


class TestNodeAndLinkShape:
    def test_node_names_include_all_terminals(self, egn: EgnTopologyData) -> None:
        # Fixture has 2 ROADMs + 2 Transceivers.
        assert set(egn.node_names) == {
            "trx Brest_1", "roadm Brest", "roadm Morlaix", "trx Morlaix_1",
        }

    def test_one_link_per_directed_terminal_pair(
        self, egn: EgnTopologyData
    ) -> None:
        # Brest↔Morlaix is unidirectional in GNPy, so we get 2 links.
        link_pairs = {(lnk.source_name, lnk.target_name) for lnk in egn.links}
        assert link_pairs == {
            ("roadm Brest", "roadm Morlaix"),
            ("roadm Morlaix", "roadm Brest"),
        }

    def test_each_link_has_exactly_one_span(
        self, egn: EgnTopologyData
    ) -> None:
        for lnk in egn.links:
            assert len(lnk.spans) == 1

    def test_link_length_matches_sum_of_spans(
        self, egn: EgnTopologyData
    ) -> None:
        for lnk in egn.links:
            assert lnk.length_km == sum(s.length_km for s in lnk.spans)

    def test_link_id_is_dense_zero_based(
        self, egn: EgnTopologyData
    ) -> None:
        ids = sorted(lnk.id for lnk in egn.links)
        assert ids == list(range(len(ids)))

    def test_link_by_endpoints_resolves_each_terminal_pair(
        self, egn: EgnTopologyData
    ) -> None:
        # Every link is reachable from its ordered terminal pair — this is
        # how a route is resolved to spans, without matching fiber UIDs.
        for lnk in egn.links:
            link_id = egn.link_by_endpoints[(lnk.source_name, lnk.target_name)]
            assert link_id == lnk.id

    def test_link_by_endpoints_is_bidirectional(
        self, egn: EgnTopologyData
    ) -> None:
        # A route may traverse a fiber pair either way; both directions must
        # resolve (to the same spans) so no leg is silently dropped.
        assert (
            egn.link_by_endpoints[("roadm Brest", "roadm Morlaix")]
            in {lnk.id for lnk in egn.links}
        )
        assert (
            egn.link_by_endpoints[("roadm Morlaix", "roadm Brest")]
            in {lnk.id for lnk in egn.links}
        )


class TestSpanContent:
    def _eastbound(self, egn: EgnTopologyData) -> EgnLinkData:
        return next(
            lnk for lnk in egn.links
            if (lnk.source_name, lnk.target_name) == (
                "roadm Brest", "roadm Morlaix",
            )
        )

    def test_span_carries_fiber_length(self, egn: EgnTopologyData) -> None:
        # Fixture fiber length is 80 km.
        span = self._eastbound(egn).spans[0]
        assert span.length_km == pytest.approx(80.0)

    def test_span_carries_loss_coef(self, egn: EgnTopologyData) -> None:
        # Fixture loss_coef is 0.2 dB/km.
        span = self._eastbound(egn).spans[0]
        assert span.attenuation_db_per_km == pytest.approx(0.2)

    def test_span_carries_edfa_nf(self, egn: EgnTopologyData) -> None:
        # std_medium_gain has no nf0 (uses nf_min/nf_max) — the helper
        # falls back to the average. Just check we got *something*
        # numeric and in a reasonable range, not a hard literal.
        span = self._eastbound(egn).spans[0]
        assert 3.0 <= span.noise_figure_db <= 12.0

    def test_span_records_originating_uids(
        self, egn: EgnTopologyData
    ) -> None:
        span: EgnSpanData = self._eastbound(egn).spans[0]
        assert span.fiber_uid == "fiber (Brest → Morlaix)-F"
        assert span.edfa_uid == "east edfa in Brest to Morlaix"


class TestUidIndex:
    def test_fiber_index_resolves_to_link_and_span(
        self, egn: EgnTopologyData
    ) -> None:
        entry = egn.gnpy_uid_index["fiber (Brest → Morlaix)-F"]
        assert entry.kind == "fiber"
        assert entry.link_id is not None
        assert entry.span_index == 0
        # Cross-check that the link/span the index points to actually
        # references this fiber.
        link = egn.links[entry.link_id]
        assert link.spans[entry.span_index].fiber_uid == (
            "fiber (Brest → Morlaix)-F"
        )

    def test_edfa_index_resolves_to_same_span(
        self, egn: EgnTopologyData
    ) -> None:
        entry = egn.gnpy_uid_index["east edfa in Brest to Morlaix"]
        assert entry.kind == "edfa"
        assert entry.link_id is not None and entry.span_index == 0
        link = egn.links[entry.link_id]
        assert link.spans[entry.span_index].edfa_uid == (
            "east edfa in Brest to Morlaix"
        )

    def test_roadm_and_trx_indexed_as_nodes(
        self, egn: EgnTopologyData
    ) -> None:
        roadm = egn.gnpy_uid_index["roadm Brest"]
        assert roadm.kind == "roadm"
        assert roadm.link_id is None and roadm.span_index is None
        trx = egn.gnpy_uid_index["trx Brest_1"]
        assert trx.kind == "transceiver"

    def test_index_covers_every_relevant_uid(
        self, egn: EgnTopologyData
    ) -> None:
        gnpy_topo = load_gnpy_topology(FIXTURES / "edfa_example_network.json")
        relevant = {
            el.uid for el in gnpy_topo.elements
            if el.type in ("Fiber", "Edfa", "Roadm", "Transceiver")
        }
        missing = relevant - set(egn.gnpy_uid_index)
        assert not missing, f"UIDs not indexed: {sorted(missing)}"
