"""Read T-API v2.6.0 connectivity-service fields that live on the end-point,
find where the twin serves T-API (``twin_data_root``), and talk to a twin
that requires an API token (``twin_urlopen``).

``tapi-connectivity`` has no leaf for either modulation or assigned spectrum.
The photonic module augments the end-point's ``layer-protocol-constraint``
with both, so pulling them out takes more than a ``dict.get`` — and the demo
scripts all need the same two accessors.

They live here rather than inline in each script because this shape has
already moved once, and a `python3 -c` heredoc repeated across four scripts
is four places to forget. Scripts import it with::

    sys.path.insert(0, "<scripts dir>")
    from tapi_fields import modulation_of, spectrum_of

Only the standard library, matching the rest of the demo scripts.
"""

from __future__ import annotations

import os
import urllib.error
import urllib.request
from urllib.parse import urlsplit

OTSIA_CSEP_SPEC = "tapi-photonic-media:otsia-connectivity-service-end-point-spec"
MCG_CSEP_SPEC = "tapi-photonic-media:mcg-connectivity-service-end-point-spec"

# ONF spells 16QAM as MT_DP-QAM16, not MT_DP-16QAM.
MT_TO_MODULATION = {
    "MT_DP-QPSK": "DP-QPSK",
    "MT_DP-QAM16": "DP-16QAM",
    "MT_DP-QAM64": "DP-64QAM",
}


SERVICE_KEY = "tapi-connectivity:connectivity-service"


def created_service(payload: dict) -> dict:
    """The connectivity-service of a twin 201, as the adapter records it.

    The twin answers a create with the entry's GET body, an array of one
    (RFC 8040 §4.3, RFC 7951 §5.4). Anything else raises ``ValueError``
    rather than being read as a service without spectrum.
    """
    entries = payload.get(SERVICE_KEY) if isinstance(payload, dict) else None
    if not isinstance(entries, list) or len(entries) != 1:
        raise ValueError(f"expected {SERVICE_KEY} as an array of one, got {entries!r:.120}")
    return entries[0]


def _constraints(service: dict):
    """Every layer-protocol-constraint on every end-point of a service."""
    for end_point in service.get("end-point") or []:
        yield from end_point.get("layer-protocol-constraint") or []


def modulation_of(service: dict, default: str = "?") -> str:
    """The modulation format of a connectivity-service, e.g. ``DP-QPSK``."""
    for constraint in _constraints(service):
        spec = constraint.get(OTSIA_CSEP_SPEC) or {}
        for cfg in spec.get("otsi-config") or []:
            identity = (cfg.get("modulation") or {}).get(
                "standard-modulation-technique", ""
            )
            # RFC 7951 6.8 allows the module-qualified spelling too.
            name = MT_TO_MODULATION.get(identity.split(":")[-1])
            if name is not None:
                return name
    return default


def spectrum_of(service: dict) -> tuple[float, float] | None:
    """(centre THz, width GHz) of the assigned spectrum, or None.

    The augment carries band edges in uint64 Hz; these are the units the
    demo output prints.
    """
    for constraint in _constraints(service):
        spec = constraint.get(MCG_CSEP_SPEC) or {}
        for cfg in spec.get("mc-spectrum-config-pac") or []:
            band = cfg.get("spectrum") or {}
            lower, upper = (
                band.get("lower-frequency"),
                band.get("upper-frequency"),
            )
            if lower and upper:
                return (lower + upper) / 2 / 1e12, (upper - lower) / 1e9
    return None


def twin_data_root(base: str) -> str:
    """The twin's T-API data resource, ``{root}/data``, as a full URL.

    The root comes from ``/.well-known/host-meta`` (RFC 8040 §3.1): it is
    ``/restconf`` by default but configurable, so the scripts do not assume
    it. A twin with an empty root advertises ``http://host:port``, whose path
    is empty: T-API at ``/data``. An older twin without host-meta (404)
    serves T-API at ``/data`` too. A twin that refuses the token exits
    with the reason (see :func:`twin_urlopen`).
    """
    import xml.etree.ElementTree as ET

    base = base.rstrip("/")
    try:
        with twin_urlopen(f"{base}/.well-known/host-meta", timeout=30) as resp:
            xrd = resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        return f"{base}/data"
    root = ""
    for link in ET.fromstring(xrd).iter("{http://docs.oasis-open.org/ns/xri/xrd-1.0}Link"):
        if link.get("rel") == "restconf":
            href = link.get("href", "")
            root = (urlsplit(href).path if urlsplit(href).scheme else href).rstrip("/")
            break
    return f"{base}{root}/data"


# ---------------------------------------------------------------------------
# A twin that requires an API token
# ---------------------------------------------------------------------------

#: The one token a TwinLight client presents -- the CLIs and the ONOS adapter
#: read the same variable. lib.sh exports it, from the repository's .env when
#: the environment does not set it.
TOKEN_ENV_VAR = "TWINLIGHT_API_TOKEN"


def twin_request(url: str) -> urllib.request.Request:
    """A request for ``url`` on the twin, with the bearer token if one is set.

    Only for the twin: ONOS and the adapter must never be sent this token.
    """
    request = urllib.request.Request(url)
    token = os.environ.get(TOKEN_ENV_VAR, "").strip()
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    return request


def refusal(url: str, code: int) -> str:
    """Why a twin answered ``code`` (401/403), in words -- never the token."""
    parts = urlsplit(url)
    twin = f"{parts.scheme}://{parts.netloc}"
    if not os.environ.get(TOKEN_ENV_VAR, "").strip():
        return (
            f"the twin at {twin} requires an API token (HTTP {code}): set "
            f"{TOKEN_ENV_VAR}, in the environment or in the repository's .env"
        )
    return (
        f"the twin at {twin} rejected {TOKEN_ENV_VAR} (HTTP {code}): it is "
        "not one of the twin's TWINLIGHT_API_TOKENS"
    )


def twin_urlopen(url: str, timeout: float = 120):
    """``urllib.request.urlopen`` for the twin, presenting the token.

    A 401 or 403 exits with :func:`refusal`'s explanation instead of an
    HTTPError: the scripts catch HTTPError (and Exception) to mean "the twin
    is down" or "no data", which a refused token is not.
    """
    try:
        return urllib.request.urlopen(twin_request(url), timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise SystemExit(refusal(url, exc.code)) from None
        raise


def twin_auth_problem(base: str) -> str | None:
    """Why the twin at ``base`` refuses this shell's token, or None.

    Asks for the host-meta, which a twin with tokens protects (only /health
    is public). None also when the twin is unreachable: that is not an
    authorization problem, and the callers report it in their own words.
    """
    url = f"{base.rstrip('/')}/.well-known/host-meta"
    try:
        with urllib.request.urlopen(twin_request(url), timeout=30):
            return None
    except urllib.error.HTTPError as exc:
        return refusal(url, exc.code) if exc.code in (401, 403) else None
    except (urllib.error.URLError, OSError):
        return None
