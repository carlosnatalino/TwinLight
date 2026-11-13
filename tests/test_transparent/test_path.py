"""ns-3-style path grammar of the transparent controller."""

from __future__ import annotations

import pytest

from twinlight.transparent.path import (
    PathSyntaxError,
    format_path,
    parse_path,
    parse_url_path,
)


def _texts(path: str) -> list[str]:
    return [str(s) for s in parse_path(path)]


class TestParsePath:
    def test_root(self) -> None:
        assert parse_path("") == [] and parse_path("/") == []

    def test_segments_and_cast(self) -> None:
        segs = parse_path("/ElementList/*/$Fiber/loss_coef")
        assert [s.cast for s in segs] == [False, False, True, False]
        assert _texts("/ElementList/*/$Fiber/loss_coef") == [
            "ElementList", "*", "$Fiber", "loss_coef",
        ]

    def test_cast_alternatives_with_or_without_repeated_dollar(self) -> None:
        a = parse_path("/ElementList/*/$Fiber|$Edfa")[2]
        b = parse_path("/ElementList/*/$Fiber|Edfa")[2]
        assert a == b and a.cast
        assert a.selector.matches("Edfa") and not a.selector.matches("Roadm")

    def test_quoted_literal_keeps_slashes(self) -> None:
        segs = parse_path('/ElementList/"fiber (A → B)-F_(1/2)"/loss_coef')
        assert len(segs) == 3
        assert segs[1].selector.matches("fiber (A → B)-F_(1/2)")
        assert segs[1].selector.exact_names == ["fiber (A → B)-F_(1/2)"]

    def test_index_terms_select_by_position(self) -> None:
        sel = parse_path("/ElementList/[1-2]|[5]")[1].selector
        assert [sel.matches("x", i) for i in range(7)] == [
            False, True, True, False, False, True, False,
        ]
        assert sel.exact_names is None  # a pattern, not a name

    def test_regex_and_glob_are_patterns(self) -> None:
        assert parse_path('/ElementList/~"^fiber"')[1].selector.exact_names is None
        assert parse_path("/ElementList/east*")[1].selector.exact_names is None
        assert parse_path("/Config/rmsa")[1].selector.exact_names == ["rmsa"]

    @pytest.mark.parametrize("bad", [
        "ElementList/*", "/ElementList//x", '/ElementList/"open', "/ElementList/[3-1]",
        "/ElementList/*/$", '/ElementList/~"("',
    ])
    def test_errors(self, bad: str) -> None:
        with pytest.raises(PathSyntaxError):
            parse_path(bad)


class TestUrlPath:
    def test_encoded_slash_stays_inside_the_segment(self) -> None:
        segs = parse_url_path("/ElementList/fiber%20(A%20%E2%86%92%20B)-F_(1%2F2)/loss_coef")
        assert len(segs) == 3
        assert segs[1].selector.matches("fiber (A → B)-F_(1/2)")

    def test_encoded_quotes_and_pipes_decode(self) -> None:
        segs = parse_url_path("/ElementList/%22a%2Fb%22%7Cc/x")
        assert segs[1].selector.matches("a/b") and segs[1].selector.matches("c")

    def test_quoted_literal_slash_in_url(self) -> None:
        segs = parse_url_path('/ElementList/"x (1/2)"/loss_coef')
        assert len(segs) == 3 and segs[1].selector.matches("x (1/2)")

    def test_empty_is_root(self) -> None:
        assert parse_url_path("") == [] and parse_url_path("/") == []


class TestFormatPath:
    def test_plain_names_stay_bare(self) -> None:
        assert format_path(("ElementList", "roadm Brest", "uid")) == "/ElementList/roadm Brest/uid"

    @pytest.mark.parametrize("name", ["a/b", "a|b", "x*", "$x", "~x", "[0]", " x", 'q"q'])
    def test_syntax_characters_are_quoted_and_round_trip(self, name: str) -> None:
        text = format_path(("ElementList", name))
        segs = parse_path(text)
        assert len(segs) == 2 and segs[1].selector.exact_names == [name]
