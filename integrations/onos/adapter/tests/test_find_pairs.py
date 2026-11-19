"""The pair search seed-demo.sh provisions from (scripts/find_pairs.py).

Its probe, /internal/path-info, includes the twin's time-varying transient
terms and admission does not, so a winner can be refused at create; the
search therefore keeps spares, and a seeded search must draw the same pairs
in the same order whatever the probes answer, so a run can be replayed.
"""

from __future__ import annotations

import hashlib
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from find_pairs import THRESHOLDS, search

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


def _run(seed: str, probe: Probe, spares: int = 1):
    return search(random.Random(seed), NAMES, ADJACENT, probe, WANTED, spares, 200)


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
    search(calm_rng, NAMES, ADJACENT, Probe(), WANTED, 1, 200)
    search(noisy_rng, NAMES, ADJACENT, Probe(noise=0.6, seed=1), WANTED, 1, 200)
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
    for fmt, threshold in THRESHOLDS:
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
