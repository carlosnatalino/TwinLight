"""ns-3-style paths into the transparent controller's data tree.

ns-3 reaches every attribute of a simulation through a configuration path:
``/NodeList/3/DeviceList/*/$ns3::WifiNetDevice/Phy/TxPowerStart`` walks from
a root list, through list indices (or wildcards, ranges and alternatives),
optionally casts to a type with ``$``, and ends at an attribute. The
transparent controller keeps that shape over the optical network:

    /ElementList/<uid>[/$<Kind>]/<leaf>
    /NodeList/<uid>[/$<Kind>]/<leaf>
    /LinkList/<from>/<to>/<leaf>
    /LinkList/<from>/<to>/ElementList/<uid>[/$<Kind>]/<leaf>
    /ServiceList/<uuid-or-name>/<leaf>          (and /qot/<leaf>)
    /ServiceList/<uuid-or-name>/ElementList/<uid>/...
    /ModulationList/<format>/<leaf>
    /Config/<section>/.../<field>

Every segment is a selector, so a path may match many nodes:

* a glob — ``*``, ``east*``, ``roadm ?orlaix``;
* a quoted literal — ``"fiber (A → B)-_(1/4)"``, required when a name holds
  ``/``, ``|``, quotes or glob characters;
* a regular expression — ``~"^fiber \\(Brest"`` (``re.search``);
* an ns-3 index or range — ``[0]``, ``[2-5]`` — selecting list entries by
  position in the list's order (sorted UIDs; creation order for services;
  propagation order for a link's elements);
* alternatives of any of these joined by ``|`` — ``roadm A|roadm B``,
  ``[0]|[3]``.

A ``$`` segment after an element entry is ns-3's type cast: ``$Fiber``,
``$Edfa|Roadm``. It keeps only elements of those kinds and, as in ns-3,
makes the attributes that follow it specific to them — naming one the kind
does not have is an error rather than an empty match.

Selector terms come from ``state/parameter_path.py``, so a UID selector
means the same thing here as in ``/config/parameters``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote

from twinlight.state.parameter_path import (
    ParameterPathError,
    Term,
    parse_term,
    quote,
    split_outside_quotes,
)


class PathSyntaxError(ValueError):
    """A path that cannot be parsed."""


_INDEX = re.compile(r"\[(\d+)(?:-(\d+))?\]")


@dataclass(frozen=True)
class IndexTerm:
    """ns-3's ``[i]`` / ``[i-j]``: list entries by position, inclusive."""

    lo: int
    hi: int

    def matches(self, value: str, position: int | None) -> bool:
        return position is not None and self.lo <= position <= self.hi

    def __str__(self) -> str:
        return f"[{self.lo}]" if self.lo == self.hi else f"[{self.lo}-{self.hi}]"


@dataclass(frozen=True)
class Selector:
    """One path segment: ``|``-joined terms, any of which may match."""

    terms: tuple[Term | IndexTerm, ...]

    def matches(self, value: str, position: int | None = None) -> bool:
        for term in self.terms:
            if isinstance(term, IndexTerm):
                if term.matches(value, position):
                    return True
            elif term.matches(value):
                return True
        return False

    @property
    def exact_names(self) -> list[str] | None:
        """The names this selector spells out, or None if any term is a
        pattern (glob metacharacters, regex or index).

        Spelled-out names are what make a typo detectable: ``loss_coeff``
        matching nothing is an error, ``loss*`` matching nothing is not.
        """
        names: list[str] = []
        for term in self.terms:
            if isinstance(term, IndexTerm) or term.kind == "regex":
                return None
            if term.kind == "glob" and any(c in term.pattern for c in "*?["):
                return None
            names.append(term.pattern)
        return names

    def __str__(self) -> str:
        return "|".join(
            str(t) if isinstance(t, IndexTerm) else _term_text(t) for t in self.terms
        )


def _term_text(term: Term) -> str:
    if term.kind == "literal":
        return quote(term.pattern)
    if term.kind == "regex":
        return "~" + quote(term.pattern)
    return term.pattern


@dataclass(frozen=True)
class Segment:
    """A parsed path segment: a name/key selector, or a ``$`` cast."""

    selector: Selector
    cast: bool = False

    def __str__(self) -> str:
        if self.cast:
            return "|".join("$" + str(Selector((t,))) for t in self.selector.terms)
        return str(self.selector)


def _parse_selector(raw: str) -> Selector:
    terms: list[Term | IndexTerm] = []
    for part in split_outside_quotes(raw, "|"):
        stripped = part.strip()
        m = _INDEX.fullmatch(stripped)
        if m:
            lo = int(m.group(1))
            hi = int(m.group(2)) if m.group(2) is not None else lo
            if hi < lo:
                raise PathSyntaxError(f"Empty index range {stripped!r}")
            terms.append(IndexTerm(lo, hi))
            continue
        terms.append(parse_term(stripped))
    return Selector(tuple(terms))


def parse_segment(raw: str) -> Segment:
    """Parse one segment. ``$`` starts a cast; each alternative may repeat it."""
    try:
        if raw.strip().startswith("$"):
            parts = split_outside_quotes(raw, "|")
            names = [p.strip().removeprefix("$") for p in parts]
            if any(not n for n in names):
                raise PathSyntaxError(f"Empty type in cast {raw!r}")
            return Segment(_parse_selector("|".join(names)), cast=True)
        if not raw.strip():
            raise PathSyntaxError("Empty path segment")
        return Segment(_parse_selector(raw))
    except ParameterPathError as exc:
        raise PathSyntaxError(str(exc)) from exc


def parse_path(text: str) -> list[Segment]:
    """Parse a path string such as ``/ElementList/*/$Fiber/loss_coef``.

    ``""`` and ``"/"`` are the root. Slashes inside quotes do not split.
    """
    if not isinstance(text, str):
        raise PathSyntaxError(f"A path is a string, got {type(text).__name__}")
    body = text.strip()
    if body in ("", "/"):
        return []
    if not body.startswith("/"):
        raise PathSyntaxError(
            f"A path starts with '/', e.g. /ElementList/*/loss_coef: {text!r}"
        )
    try:
        raw_segments = split_outside_quotes(body[1:].rstrip("/"), "/")
    except ParameterPathError as exc:
        raise PathSyntaxError(str(exc)) from exc
    return [parse_segment(s) for s in raw_segments]


# An encoded slash must survive percent-decoding as data, never as a
# separator: it is how a URL carries a UID such as "fiber (A → B)-_(1/4)".
_SLASH_SENTINEL = "\x00"


def parse_url_path(raw: str) -> list[Segment]:
    """Parse the still-percent-encoded remainder of a request path.

    ``%2F`` (and ``%2f``) is a slash *inside* a segment; a literal ``/`` is a
    separator unless it is inside quotes. Everything else is decoded as
    UTF-8 first, so ``%22`` quotes and ``%7C`` alternatives work as typed.
    """
    if _SLASH_SENTINEL in raw:
        raise PathSyntaxError("NUL is not allowed in a path")
    protected = raw.replace("%2F", _SLASH_SENTINEL).replace("%2f", _SLASH_SENTINEL)
    decoded = unquote(protected, encoding="utf-8", errors="strict")
    body = decoded.strip("/")
    if not body:
        return []
    try:
        raw_segments = split_outside_quotes(body, "/")
    except ParameterPathError as exc:
        raise PathSyntaxError(str(exc)) from exc
    return [parse_segment(s.replace(_SLASH_SENTINEL, "/")) for s in raw_segments]


# Characters that would be read as syntax if a name were written bare.
_NEEDS_QUOTES = re.compile(r'[/|"*?\[\]\\~$]|^\s|\s$|^$')


def format_segment(name: str) -> str:
    """A name as a path segment that selects exactly that name."""
    return quote(name) if _NEEDS_QUOTES.search(name) else name


def format_path(names: tuple[str, ...] | list[str]) -> str:
    """Canonical path naming exactly one node: ``/ElementList/"x (1/2)"/…``."""
    return "/" + "/".join(format_segment(n) for n in names)


def join(base: list[Segment], relative: str) -> list[Segment]:
    """``base`` followed by ``relative`` (a YANG-Patch edit target)."""
    return base + parse_path(relative if relative.startswith("/") else "/" + relative)
