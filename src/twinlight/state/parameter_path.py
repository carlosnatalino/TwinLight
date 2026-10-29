"""Parameter paths: an ns-3-style addressing scheme for runtime knobs.

ns-3 addresses every attribute in a simulation with a configuration path
such as ``/NodeList/*/DeviceList/0/$ns3::WifiNetDevice/Phy/TxPowerStart``:
a slash-separated walk from a root container down to one attribute, where
any segment may be a wildcard or a set of alternatives. TwinLight borrows
that shape, but walks the *optical* hierarchy rather than an object tree:

    [/Service/<sel>] [/Link/<sel>/<sel>] /<Kind>/<sel>/<attribute>

* ``/<Kind>/<sel>/<attribute>`` — the core: element kind (``Fiber``,
  ``Edfa``, ``Roadm``, or ``*``), element UID selector, attribute name.
* ``/Service/<sel>`` — optional scope: only elements on the *allocated*
  path of a matching connectivity service (matched by UUID or by its
  ``service-name``).
* ``/Link/<sel>/<sel>`` — optional scope: only inline elements (fibers and
  amplifiers) between a matching pair of terminals (ROADM or transceiver),
  in that direction. GNPy links are unidirectional; use alternatives to
  address both directions.

Scopes compose as an intersection and may appear in either order before
the kind segment.

A selector is one or more terms joined by ``|`` (ns-3's alternative
syntax). Each term is one of:

* a glob — ``*``, ``east edfa in Brest*``, ``roadm ?orlaix``;
* a quoted literal — ``"fiber (A → B)-_(1/4)"``, needed when a UID contains
  ``/``, ``|``, or glob metacharacters (GNPy sub-span UIDs do);
* a regular expression — ``~"^fiber \\(Brest"`` (``re.search``: unanchored,
  like awk's ``~``; anchor with ``^`` and ``$``).

Inside quotes, ``\\"`` and ``\\\\`` escape a quote and a backslash; any
other backslash is literal, so regex escapes (``~"^fiber \\(Brest"``) work
as written.

The attribute segment is a single attribute name, or ``*`` to list every
attribute (accepted by the resolver for listing, rejected when writing,
which needs one number per parameter). When the kind is a wildcard, only
elements whose kind exposes the attribute match, so ``/*/*/loss_coef`` is
every fiber. Naming a kind explicitly with an attribute it does not expose
is an error rather than an empty match, because that is always a typo.
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from twinlight.physics.backend import element_kind_name

if TYPE_CHECKING:
    from twinlight.state.context import TapiContext


class ParameterPathError(ValueError):
    """Raised when a parameter path is malformed or names nothing valid."""


# -- Lexing ------------------------------------------------------------------

_GLOB_META = set("*?[")


def _split_outside_quotes(text: str, sep: str) -> list[str]:
    """Split ``text`` on ``sep`` except inside double-quoted spans.

    Quote characters and escapes are preserved in the returned pieces;
    ``_parse_term`` is what interprets them.
    """
    parts: list[str] = []
    buf: list[str] = []
    in_quotes = False
    i = 0
    while i < len(text):
        ch = text[i]
        if in_quotes and ch == "\\" and i + 1 < len(text):
            buf.append(text[i : i + 2])
            i += 2
            continue
        if ch == '"':
            in_quotes = not in_quotes
        if ch == sep and not in_quotes:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    if in_quotes:
        raise ParameterPathError(f"Unterminated quote in {text!r}")
    parts.append("".join(buf))
    return parts


def _unquote(raw: str) -> str:
    """``"a\\"b"`` → ``a"b``. ``raw`` must start and end with a quote.

    Only ``\\"`` and ``\\\\`` are escapes; any other backslash is kept, so a
    regex escape such as ``\\(`` needs no doubling.
    """
    body = raw[1:-1]
    out: list[str] = []
    i = 0
    while i < len(body):
        if body[i] == "\\" and i + 1 < len(body) and body[i + 1] in '"\\':
            out.append(body[i + 1])
            i += 2
        else:
            out.append(body[i])
            i += 1
    return "".join(out)


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


# -- Selectors ---------------------------------------------------------------


@dataclass(frozen=True)
class Term:
    """One alternative of a selector: ``glob``, ``literal`` or ``regex``."""

    kind: str
    pattern: str

    def matches(self, value: str) -> bool:
        if self.kind == "literal":
            return value == self.pattern
        if self.kind == "regex":
            return re.search(self.pattern, value) is not None
        return fnmatch.fnmatchcase(value, self.pattern)

    def __str__(self) -> str:
        if self.kind == "literal":
            return _quote(self.pattern)
        if self.kind == "regex":
            return "~" + _quote(self.pattern)
        return self.pattern


@dataclass(frozen=True)
class Selector:
    """A ``|``-separated set of terms; matches if any term matches."""

    terms: tuple[Term, ...]

    def matches(self, value: str) -> bool:
        return any(t.matches(value) for t in self.terms)

    @property
    def is_wildcard(self) -> bool:
        return any(t.kind == "glob" and t.pattern == "*" for t in self.terms)

    def __str__(self) -> str:
        return "|".join(str(t) for t in self.terms)


def _parse_term(raw: str) -> Term:
    raw = raw.strip()
    if not raw:
        raise ParameterPathError("Empty selector term")
    if raw.startswith("~"):
        quoted = raw[1:].strip()
        if not (len(quoted) >= 2 and quoted[0] == quoted[-1] == '"'):
            raise ParameterPathError(
                f"Regex term must be quoted, e.g. ~\"^fiber.*\": {raw!r}"
            )
        pattern = _unquote(quoted)
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ParameterPathError(f"Invalid regex {pattern!r}: {exc}") from exc
        return Term("regex", pattern)
    if raw.startswith('"'):
        if not (len(raw) >= 2 and raw.endswith('"')):
            raise ParameterPathError(f"Malformed quoted term: {raw!r}")
        return Term("literal", _unquote(raw))
    if '"' in raw:
        raise ParameterPathError(
            f"Quotes must wrap a whole term, not part of one: {raw!r}"
        )
    # An unquoted term with no glob metacharacters is an exact name; keep it
    # a glob anyway (fnmatch of a plain string is equality) so the canonical
    # form round-trips without gaining quotes.
    return Term("glob", raw)


def parse_selector(raw: str) -> Selector:
    return Selector(tuple(_parse_term(t) for t in _split_outside_quotes(raw, "|")))


# -- Paths -------------------------------------------------------------------

_SCOPES = {"Service": 1, "Link": 2}


@dataclass(frozen=True)
class ParameterPath:
    """A parsed parameter path. Build with :func:`parse`."""

    kinds: Selector
    uids: Selector
    attribute: str
    services: Selector | None = None
    link: tuple[Selector, Selector] | None = None

    @property
    def attribute_is_wildcard(self) -> bool:
        return self.attribute == "*"

    def __str__(self) -> str:
        parts: list[str] = []
        if self.services is not None:
            parts += ["Service", str(self.services)]
        if self.link is not None:
            parts += ["Link", str(self.link[0]), str(self.link[1])]
        parts += [str(self.kinds), str(self.uids), self.attribute]
        return "/" + "/".join(parts)

    def describe(self) -> dict[str, Any]:
        """Structured form, echoed by the API so clients can check parsing."""
        return {
            "canonical": str(self),
            "service": str(self.services) if self.services else None,
            "link": (
                {"from": str(self.link[0]), "to": str(self.link[1])}
                if self.link else None
            ),
            "kind": str(self.kinds),
            "uid": str(self.uids),
            "attribute": self.attribute,
        }


def parse(text: str) -> ParameterPath:
    """Parse a parameter path string. Raises :class:`ParameterPathError`."""
    if not isinstance(text, str) or not text.startswith("/"):
        raise ParameterPathError(
            f"A parameter path starts with '/', e.g. /Fiber/*/loss_coef: {text!r}"
        )
    segments = _split_outside_quotes(text[1:], "/")
    if any(not s.strip() for s in segments):
        raise ParameterPathError(f"Empty segment in parameter path {text!r}")

    services: Selector | None = None
    link: tuple[Selector, Selector] | None = None
    i = 0
    while i < len(segments) and segments[i] in _SCOPES:
        name = segments[i]
        arity = _SCOPES[name]
        args = segments[i + 1 : i + 1 + arity]
        if len(args) < arity:
            raise ParameterPathError(
                f"/{name} takes {arity} selector(s) in {text!r}"
            )
        if name == "Service":
            if services is not None:
                raise ParameterPathError(f"/Service given twice in {text!r}")
            services = parse_selector(args[0])
        else:
            if link is not None:
                raise ParameterPathError(f"/Link given twice in {text!r}")
            link = (parse_selector(args[0]), parse_selector(args[1]))
        i += 1 + arity

    rest = segments[i:]
    if len(rest) != 3:
        raise ParameterPathError(
            f"Expected /<Kind>/<uid>/<attribute> after any scopes, got "
            f"{len(rest)} segment(s) in {text!r}"
        )
    kinds = parse_selector(rest[0])
    uids = parse_selector(rest[1])
    attribute = rest[2].strip()
    if not re.fullmatch(r"\*|[A-Za-z_][A-Za-z0-9_]*", attribute):
        raise ParameterPathError(
            f"Attribute must be a single name or '*', got {attribute!r}"
        )
    return ParameterPath(
        kinds=kinds, uids=uids, attribute=attribute,
        services=services, link=link,
    )


# -- Resolution --------------------------------------------------------------


@dataclass(frozen=True)
class ParameterRef:
    """One concrete (element, attribute) a path resolved to."""

    uid: str
    kind: str
    attribute: str
    spec: dict[str, Any]

    @property
    def path(self) -> str:
        """The fully-qualified path naming just this parameter."""
        return f"/{self.kind}/{_quote(self.uid)}/{self.attribute}"


def _matching_services(ctx: TapiContext, sel: Selector) -> set[str]:
    """Service UUIDs whose UUID or ``service-name`` matches ``sel``."""
    out: set[str] = set()
    for svc in ctx.get_services():
        names = [
            n.value for n in (svc.name or [])
            if n.value_name == "service-name"
        ]
        if sel.matches(svc.uuid) or any(sel.matches(n) for n in names):
            out.add(svc.uuid)
    return out


def resolve(path: ParameterPath, ctx: TapiContext) -> list[ParameterRef]:
    """Expand ``path`` against the live twin, sorted by (uid, attribute).

    The order is deterministic so a job's target list, and therefore its
    status output, does not depend on dict iteration order.

    Raises:
        ParameterPathError: The path names a kind that exists in the
            backend's schema but does not expose the attribute.
    """
    backend = ctx._backend
    schema: dict[str, dict[str, dict[str, Any]]] = backend.supported_attributes()

    if not path.kinds.is_wildcard and not path.attribute_is_wildcard:
        named = [k for k in schema if path.kinds.matches(k)]
        for k in named:
            if path.attribute not in (schema.get(k) or {}):
                raise ParameterPathError(
                    f"{k} has no runtime-mutable attribute "
                    f"{path.attribute!r} under backend {backend.name!r}. "
                    f"Available: {sorted(schema.get(k) or {})!r}"
                )

    services = (
        _matching_services(ctx, path.services)
        if path.services is not None else None
    )

    refs: list[ParameterRef] = []
    for uid, el in backend.uid_map.items():
        kind = element_kind_name(el)
        attrs = schema.get(kind) or {}
        if not attrs or not path.kinds.matches(kind):
            continue
        if not path.uids.matches(uid):
            continue
        if services is not None and not (
            ctx.services_for_element(uid) & services
        ):
            continue
        if path.link is not None:
            a, z = ctx.element_terminals(uid)
            if a is None or z is None:
                continue
            if not (path.link[0].matches(a) and path.link[1].matches(z)):
                continue
        for attr, spec in attrs.items():
            if path.attribute_is_wildcard or attr == path.attribute:
                refs.append(ParameterRef(uid, kind, attr, dict(spec)))
    refs.sort(key=lambda r: (r.uid, r.attribute))
    return refs
