"""The scripts' reader of the twin's created-service body (scripts/tapi_fields.py).

lightpath.sh, correlate.sh and validate.sh read the 201 the adapter recorded
for each lightpath ONOS created. The twin answers a create with the entry's
GET body, an array of one (RFC 8040 §4.3, RFC 7951 §5.4); anything else must
fail loudly rather than read as a service without spectrum.
"""

from __future__ import annotations

import io
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import tapi_fields
from tapi_fields import created_service, spectrum_of, twin_data_root

_ENTRY = {
    "uuid": "svc-1",
    "end-point": [{"layer-protocol-constraint": [{
        "tapi-photonic-media:mcg-connectivity-service-end-point-spec": {
            "mc-spectrum-config-pac": [{"spectrum": {
                "lower-frequency": 191_278_125_000_000,
                "upper-frequency": 191_334_375_000_000,
            }}],
        },
    }]}],
}


def test_reads_the_entry_of_an_array_of_one() -> None:
    service = created_service({"tapi-connectivity:connectivity-service": [_ENTRY]})
    assert service["uuid"] == "svc-1"
    assert spectrum_of(service) == pytest.approx((191.30625, 56.25))


@pytest.mark.parametrize("payload", [
    {"tapi-connectivity:connectivity-service": _ENTRY},  # the object earlier releases sent
    {"tapi-connectivity:connectivity-service": []},
    {"tapi-connectivity:connectivity-service": [_ENTRY, _ENTRY]},
    {},
])
def test_anything_else_is_an_error(payload: dict) -> None:
    with pytest.raises(ValueError, match="array of one"):
        created_service(payload)


def _host_meta(href: str):
    return io.BytesIO((
        "<?xml version='1.0' encoding='UTF-8'?>\n"
        "<XRD xmlns='http://docs.oasis-open.org/ns/xri/xrd-1.0'>\n"
        f"    <Link rel='restconf' href='{href}'/>\n</XRD>\n"
    ).encode())


@pytest.mark.parametrize(("href", "data"), [
    ("/restconf", "http://twin:8080/restconf/data"),
    ("/api", "http://twin:8080/api/data"),
    # An empty root: the twin's own URL, whose path is empty (RFC 3986 §3.3).
    ("http://twin:8080", "http://twin:8080/data"),
])
def test_twin_data_root_reads_host_meta(monkeypatch, href: str, data: str) -> None:
    urls = []

    def urlopen(url, timeout=120):
        urls.append(url)
        return _host_meta(href)

    monkeypatch.setattr(tapi_fields, "twin_urlopen", urlopen)
    assert twin_data_root("http://twin:8080/") == data
    assert urls == ["http://twin:8080/.well-known/host-meta"]


def test_twin_data_root_of_an_older_twin_without_host_meta(monkeypatch) -> None:
    def urlopen(url, timeout=120):
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    monkeypatch.setattr(tapi_fields, "twin_urlopen", urlopen)
    assert twin_data_root("http://twin:8080") == "http://twin:8080/data"
