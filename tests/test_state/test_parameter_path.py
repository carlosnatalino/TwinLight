"""Tests for the ns-3-style parameter path language (state/parameter_path.py)."""

from __future__ import annotations

import pytest

from twinlight.state.context import TapiContext
from twinlight.state.parameter_path import (
    ParameterPathError,
    Term,
    parse,
    parse_selector,
    resolve,
)

# Fixture topology (tests/fixtures/edfa_example_network.json) after GNPy's
# designed_network(): two ROADMs, one fiber each way, one inline amp each
# way, plus the booster amps GNPy inserts after each ROADM.
FWD_FIBER = "fiber (Brest → Morlaix)-F"
REV_FIBER = "fiber (Morlaix → Brest)-F"
EAST_AMP = "east edfa in Brest to Morlaix"
WEST_AMP = "west edfa in Morlaix to Brest"


class TestParse:
    def test_core_three_segments(self) -> None:
        p = parse("/Fiber/*/loss_coef")
        assert str(p.kinds) == "Fiber"
        assert p.uids.is_wildcard
        assert p.attribute == "loss_coef"
        assert p.services is None and p.link is None

    def test_scopes_in_either_order(self) -> None:
        a = parse("/Service/svc-1/Link/A/B/Edfa/*/nf0")
        b = parse("/Link/A/B/Service/svc-1/Edfa/*/nf0")
        assert a == b
        assert str(a.services) == "svc-1"
        assert a.link is not None and str(a.link[0]) == "A"

    def test_quoted_literal_may_contain_slash_and_pipe(self) -> None:
        p = parse('/Fiber/"fiber (A → B)-_(1/4)"|"x|y"/loss_coef')
        assert p.uids.terms == (
            Term("literal", "fiber (A → B)-_(1/4)"),
            Term("literal", "x|y"),
        )

    def test_escapes_inside_quotes(self) -> None:
        sel = parse_selector(r'"a\"b\\c"')
        assert sel.terms == (Term("literal", 'a"b\\c'),)

    def test_regex_term(self) -> None:
        sel = parse_selector(r'~"^east .*Brest"')
        assert sel.matches("east edfa in Brest")
        assert not sel.matches("west edfa in Brest to Morlaix")

    def test_regex_escapes_need_no_doubling(self) -> None:
        sel = parse_selector(r'~"^fiber \(Brest"')
        assert sel.matches(FWD_FIBER)
        assert not sel.matches(REV_FIBER)

    def test_glob_and_alternatives(self) -> None:
        sel = parse_selector("east*|west edfa in Morlaix to Brest")
        assert sel.matches(EAST_AMP)
        assert sel.matches(WEST_AMP)
        assert not sel.matches("Edfa_booster")

    def test_glob_metacharacters_in_literal_are_not_special(self) -> None:
        sel = parse_selector('"amp[1]"')
        assert sel.matches("amp[1]")
        assert not sel.matches("amp1")

    def test_canonical_form_round_trips(self) -> None:
        text = r'/Service/x*/Link/A|B/~"C.*"/Fiber/"a/b"/loss_coef'
        assert str(parse(str(parse(text)))) == str(parse(text))

    @pytest.mark.parametrize("bad", [
        "Fiber/*/loss_coef",          # no leading slash
        "/Fiber/*",                   # missing attribute
        "/Fiber/*/loss_coef/extra",   # too many segments
        "/Fiber//loss_coef",          # empty segment
        '/Fiber/"unterminated/loss_coef',
        '/Fiber/ab"cd"/loss_coef',    # quote inside an unquoted term
        '/Fiber/~"("/loss_coef',      # invalid regex
        "/Fiber/~abc/loss_coef",      # unquoted regex
        "/Fiber/*/loss coef",         # attribute is not a name
        "/Link/A/Fiber/*/loss_coef",  # /Link needs two selectors
        "/Service/a/Service/b/Fiber/*/loss_coef",
    ])
    def test_malformed_paths_rejected(self, bad: str) -> None:
        with pytest.raises(ParameterPathError):
            parse(bad)


class TestResolve:
    def test_wildcard_kind_filters_by_attribute(
        self, gnpy_context: TapiContext,
    ) -> None:
        refs = resolve(parse("/*/*/loss_coef"), gnpy_context)
        assert [r.uid for r in refs] == sorted([FWD_FIBER, REV_FIBER])
        assert all(r.kind == "Fiber" for r in refs)
        assert refs[0].spec["unit"] == "dB/km"

    def test_named_kind_without_attribute_is_an_error(
        self, gnpy_context: TapiContext,
    ) -> None:
        with pytest.raises(ParameterPathError, match="no runtime-mutable"):
            resolve(parse("/Edfa/*/loss_coef"), gnpy_context)

    def test_attribute_wildcard_lists_every_attribute(
        self, gnpy_context: TapiContext,
    ) -> None:
        refs = resolve(parse(f'/Edfa/"{EAST_AMP}"/*'), gnpy_context)
        assert {r.attribute for r in refs} == {
            "gain_target", "tilt_target", "out_voa", "nf0",
        }

    def test_link_scope_is_directional(self, gnpy_context: TapiContext) -> None:
        refs = resolve(
            parse("/Link/roadm Brest/roadm Morlaix/*/*/loss_coef"), gnpy_context,
        )
        assert [r.uid for r in refs] == [FWD_FIBER]
        amps = resolve(
            parse("/Link/roadm Morlaix/roadm Brest/Edfa/*/gain_target"),
            gnpy_context,
        )
        # The inline amp and the booster GNPy inserted after the ROADM —
        # the latter only exists in the designed network, which is why the
        # terminal index walks the backend's graph.
        assert {r.uid for r in amps} == {
            WEST_AMP, "Edfa_booster_roadm Morlaix_to_fiber (Morlaix → Brest)-F",
        }

    async def test_service_scope_uses_allocated_path(
        self, gnpy_context: TapiContext,
    ) -> None:
        from tests.test_state.test_config_overrides import _add, _two_sips

        assert resolve(parse("/Service/*/Fiber/*/loss_coef"), gnpy_context) == []
        sip_a, sip_z = _two_sips(gnpy_context)
        svc = await _add(gnpy_context, sip_a, sip_z)
        by_uuid = resolve(
            parse(f"/Service/{svc.uuid}/Fiber/*/loss_coef"), gnpy_context,
        )
        by_name = resolve(parse("/Service/m2-*/Fiber/*/loss_coef"), gnpy_context)
        assert by_uuid == by_name
        assert len(by_uuid) == 1
        assert svc.uuid in gnpy_context.services_for_element(by_uuid[0].uid)

    def test_ref_path_addresses_exactly_one_parameter(
        self, gnpy_context: TapiContext,
    ) -> None:
        for ref in resolve(parse("/*/*/*"), gnpy_context):
            again = resolve(parse(ref.path), gnpy_context)
            assert again == [ref]
