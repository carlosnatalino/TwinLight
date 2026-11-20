"""The pair search seed-demo.sh provisions from (scripts/find_pairs.py).

Its probe, /internal/path-info, includes the twin's time-varying transient
terms and admission does not, so a winner can be refused at create; the
search therefore keeps spares, and a seeded search must draw the same pairs
in the same order whatever the probes answer, so a run can be replayed.

The thresholds include the twin's admission margin, a runtime setting, which
the script reads from the twin it probes rather than assuming 1.5 dB.
"""

from __future__ import annotations

import hashlib
import io
import json
import random
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import find_pairs
import tapi_fields
from find_pairs import MARGIN_PATH, MarginUnavailableError, read_margin, search, thresholds

NAMES = sorted(f"City{i:02d}" for i in range(30))
ADJACENT = [(NAMES[i], NAMES[i + 1]) for i in range(len(NAMES) - 1)]
WANTED = {"DP-64QAM": 2, "DP-16QAM": 3, "DP-QPSK": 3}


def _gsnr(a: str, z: str) -> float:
    """A fixed GSNR per unordered pair, spread over 8-26 dB."""
    key = "|".join(sorted((a, z))).encode()
    return 8.0 + int(hashlib.md5(key).hexdigest(), 16) % 1800 / 100


class Probe:
    """Records every pair probed; ``noise`` shifts each answer, as the
    transient terms shift a real probe between runs."""

    def __init__(self, noise: float = 0.0, seed: int = 0) -> None:
        self.calls: list[tuple[str, str]] = []
        self.noise, self._rng = noise, random.Random(seed)

    def __call__(self, a: str, z: str) -> tuple[float, float]:
        self.calls.append((a, z))
        return _gsnr(a, z) + self._rng.uniform(-self.noise, self.noise), 100.0


def _run(seed: str, probe: Probe, spares: int = 1, margin_db: float = 1.5):
    return search(random.Random(seed), NAMES, ADJACENT, probe, WANTED, spares, 200, margin_db)


def test_a_seed_repeats_the_search():
    first, second = Probe(), Probe()
    assert _run("s1", first) == _run("s1", second)
    assert first.calls == second.calls


class Recording(random.Random):
    """The pairs the search draws, in order."""

    def __init__(self, seed: str) -> None:
        super().__init__(seed)
        self.draws: list[tuple[str, str]] = []

    def choice(self, seq):
        pick = super().choice(seq)
        self.draws.append(tuple(pick))
        return pick

    def sample(self, population, k, **kwargs):
        pick = super().sample(population, k, **kwargs)
        self.draws.append(tuple(pick))
        return pick


def test_a_seed_repeats_the_draws_whatever_the_probes_answer():
    """The probes' answers decide which drawn pairs are probed (an A-end can
    be taken) and where a winner is filed, never which pairs are drawn: a
    replay with the same seed walks the same sequence."""
    calm_rng, noisy_rng = Recording("s1"), Recording("s1")
    search(calm_rng, NAMES, ADJACENT, Probe(), WANTED, 1, 200, 1.5)
    search(noisy_rng, NAMES, ADJACENT, Probe(noise=0.6, seed=1), WANTED, 1, 200, 1.5)
    common = min(len(calm_rng.draws), len(noisy_rng.draws))
    assert common > 10
    assert calm_rng.draws[:common] == noisy_rng.draws[:common]


def test_another_seed_draws_other_pairs():
    first, second = Probe(), Probe()
    _run("s1", first)
    _run("s2", second)
    assert first.calls != second.calls


def test_each_format_gets_its_pairs_then_its_spares():
    found, tried = _run("s1", Probe(), spares=1)
    assert {fmt: len(pairs) for fmt, pairs in found.items()} == {
        fmt: n + 1 for fmt, n in WANTED.items()
    }
    assert tried > 0
    for fmt, threshold in thresholds(1.5):
        for _a, _z, gsnr, _km in found[fmt]:
            assert gsnr >= threshold


def test_no_spares_finds_exactly_what_is_wanted():
    found, _ = _run("s1", Probe(), spares=0)
    assert {fmt: len(pairs) for fmt, pairs in found.items()} == WANTED


def test_every_pair_spares_included_has_an_a_end_of_its_own():
    """ONOS keys a flow on its in-port: two lightpaths from one A-end collide,
    and a spare may be provisioned next to any winner."""
    found, _ = _run("s1", Probe(), spares=2)
    a_ends = [a for pairs in found.values() for a, _z, _g, _k in pairs]
    assert len(a_ends) == len(set(a_ends))


# --- The admission margin is the twin's ------------------------------------

TOKEN = "find-pairs-token-0123456789abcdef"


def _margin_body(value) -> dict:
    return {"twinlight-transparent:data": {"Config": {"rmsa": {"qot_margin_db": value}}}}


class FakeTwin:
    """What find_pairs.py asks of the twin, through tapi_fields' urlopen.

    Two SIPs (Boston, Albany) one link apart, whose probe answers ``gsnr``;
    the margin leaf answers ``margin`` (a body), or ``margin_status``.
    """

    def __init__(self, margin=2.5, gsnr=16.5, margin_status: int = 200) -> None:
        self.margin, self.gsnr, self.margin_status = margin, gsnr, margin_status
        self.requests: list[urllib.request.Request] = []

    def urlopen(self, request, timeout=None):
        self.requests.append(request)
        url = request.full_url
        path = url.split("://", 1)[1].split("/", 1)[1]
        path = "/" + path.split("?", 1)[0]
        if path == MARGIN_PATH:
            if self.margin_status != 200:
                raise urllib.error.HTTPError(url, self.margin_status, "error", {}, io.BytesIO(b"{}"))
            body = self.margin if isinstance(self.margin, dict) else _margin_body(self.margin)
        elif path == "/.well-known/host-meta":
            raise urllib.error.HTTPError(url, 404, "no host-meta", {}, io.BytesIO(b""))
        elif path.endswith("/service-interface-point"):
            body = {"tapi-common:service-interface-point": [
                {"uuid": "sip-boston", "name": [{"value-name": "node-name", "value": "trx Boston"}]},
                {"uuid": "sip-albany", "name": [{"value-name": "node-name", "value": "trx Albany"}]},
            ]}
        elif path == "/internal/links":
            body = {"links": [{"label": "roadm Albany -> roadm Boston"}]}
        elif path == "/internal/path-info":
            body = {"measurements": {"gsnr-db": self.gsnr}, "total-fiber-km": 300.0}
        else:
            raise urllib.error.HTTPError(url, 404, "not found", {}, io.BytesIO(b""))
        return io.BytesIO(json.dumps(body).encode())


@pytest.fixture
def fake_twin(monkeypatch):
    twin = FakeTwin()
    monkeypatch.setattr(tapi_fields.urllib.request, "urlopen", twin.urlopen)
    monkeypatch.delenv("TWINLIGHT_API_TOKEN", raising=False)
    for name in ("Q64_WANTED", "Q16_WANTED", "QPSK_WANTED", "SPARES", "ATTEMPTS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TWIN_URL", "http://twin")
    monkeypatch.setenv("SEED_RANDOM_SEED", "s1")
    return twin


def test_the_margin_is_read_from_the_twin(fake_twin):
    assert read_margin("http://twin") == 2.5
    assert fake_twin.requests[0].full_url == f"http://twin{MARGIN_PATH}"


def test_the_search_files_pairs_at_the_twins_margin(fake_twin, capsys):
    """A pair probing 16.5 dB carries DP-16QAM at the default 1.5 dB margin
    (16.0 dB), not at the twin's 2.5 dB (17.0 dB): it is filed as DP-QPSK."""
    assert find_pairs.main() == 0
    out, err = capsys.readouterr()
    assert out.splitlines() and all(":DP-QPSK:" in line for line in out.splitlines())
    assert "admission margin 2.5 dB, read from the twin" in err
    fake_twin.margin = 1.5
    assert find_pairs.main() == 0
    out, _ = capsys.readouterr()
    assert all(":DP-16QAM:" in line for line in out.splitlines())


def test_the_margin_request_carries_the_token(fake_twin, monkeypatch):
    monkeypatch.setenv("TWINLIGHT_API_TOKEN", TOKEN)
    read_margin("http://twin")
    assert fake_twin.requests[0].get_header("Authorization") == f"Bearer {TOKEN}"


def test_a_refused_token_stops_with_the_reason(fake_twin, monkeypatch):
    fake_twin.margin_status = 401
    monkeypatch.setenv("TWINLIGHT_API_TOKEN", TOKEN)
    with pytest.raises(SystemExit) as stop:
        read_margin("http://twin")
    assert "rejected TWINLIGHT_API_TOKEN (HTTP 401)" in str(stop.value)
    assert TOKEN not in str(stop.value)


@pytest.mark.parametrize("value", ["2.5", 2])
def test_a_number_or_a_decimal64_string_is_read(fake_twin, value):
    fake_twin.margin = value
    assert read_margin("http://twin") == float(value)


def test_a_twin_without_the_transparent_controller_stops_the_search(fake_twin, capsys):
    """No silent fallback to 1.5 dB: that is the mismatch this replaced."""
    fake_twin.margin_status = 404
    with pytest.raises(MarginUnavailableError, match="HTTP 404; a twin without the transparent"):
        read_margin("http://twin")
    assert find_pairs.main() == 2
    out, err = capsys.readouterr()
    assert out == ""
    assert "cannot read the twin's admission margin" in err
    assert not any("/internal/path-info" in r.full_url for r in fake_twin.requests)


@pytest.mark.parametrize(
    "body",
    [
        {"twinlight-transparent:data": {"Config": {"rmsa": {}}}},
        _margin_body(None),
        _margin_body(True),
        _margin_body(-1.0),
        _margin_body("wide"),
        {"unexpected": 1},
    ],
)
def test_an_answer_without_a_usable_margin_stops_the_search(fake_twin, body):
    fake_twin.margin = body
    with pytest.raises(MarginUnavailableError, match="no usable qot_margin_db"):
        read_margin("http://twin")


def test_thresholds_add_the_margin_to_each_format():
    assert thresholds(2.0) == [("DP-64QAM", 22.5), ("DP-16QAM", 16.5), ("DP-QPSK", 10.5)]
