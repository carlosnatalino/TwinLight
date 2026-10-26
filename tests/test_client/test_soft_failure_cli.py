"""Tests for the ``twinlight-soft-failure`` command-line client.

The FastAPI ``TestClient`` is an ``httpx.Client``, so the CLI runs against
an in-process twin with no server and no network.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from twinlight.app import create_app
from twinlight.config import GnpyConfig, TwinConfig
from twinlight.example_data import find_example_file
from twinlight_client.soft_failure import (
    ScenarioError,
    load_scenario,
    main,
    normalise_spec,
    parse_duration,
    parse_rate,
    watch,
)

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "soft_failure_scenario.yaml"


@pytest.fixture
def example_app(tmp_path: Path) -> TestClient:
    """The twin examples/twin_config.yaml starts: GNPy's two-site network."""
    topology = find_example_file("edfa_example_network.json")
    equipment = find_example_file("eqpt_config.json")
    if topology is None or equipment is None:
        pytest.skip("gnpy is not installed with its example data")
    return TestClient(create_app(TwinConfig(
        gnpy=GnpyConfig(topology=topology, equipment=equipment),
        simulation={"snapshot_dir": str(tmp_path / "snapshots")},
    )))


class TestParsing:
    @pytest.mark.parametrize(("text", "seconds"), [
        (90, 90.0), (1.5, 1.5), ("90", 90.0), ("90s", 90.0), ("10m", 600.0),
        ("1h30m", 5400.0), ("2h", 7200.0), ("0.5m", 30.0),
    ])
    def test_duration(self, text: str | float, seconds: float) -> None:
        assert parse_duration(text) == pytest.approx(seconds)

    @pytest.mark.parametrize("bad", ["", "ten minutes", "5d", True])
    def test_bad_duration(self, bad: str | bool) -> None:
        with pytest.raises(ScenarioError):
            parse_duration(bad)

    @pytest.mark.parametrize(("text", "per_s"), [
        (0.01, 0.01), ("0.01", 0.01), ("0.01/s", 0.01),
        ("0.6/min", 0.01), ("36/h", 0.01),
    ])
    def test_rate(self, text: str | float, per_s: float) -> None:
        assert parse_rate(text) == pytest.approx(per_s)

    @pytest.mark.parametrize("bad", ["fast", "1/day", False])
    def test_bad_rate(self, bad: str | bool) -> None:
        with pytest.raises(ScenarioError):
            parse_rate(bad)

    def test_normalise_rejects_both_spellings(self) -> None:
        with pytest.raises(ScenarioError, match="not both"):
            normalise_spec({"duration": "1m", "duration_s": 60})

    def test_example_scenario_loads_with_defaults(self) -> None:
        specs = load_scenario(EXAMPLE)
        assert len(specs) == 2
        assert all(s["interval_s"] == 2.0 for s in specs)
        assert specs[0]["duration_s"] == 600.0
        assert specs[1]["rate"] == pytest.approx(0.5 / 60)
        assert specs[1]["start_delay_s"] == 60.0

    def test_scenario_shape_errors(self, tmp_path: Path) -> None:
        f = tmp_path / "s.yaml"
        f.write_text("soft_failures: {}\n")
        with pytest.raises(ScenarioError, match="list"):
            load_scenario(f)


class TestCommands:
    def test_params(self, gnpy_app: TestClient, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["params", "/Fiber/*/loss_coef"], client=gnpy_app) == 0
        out = capsys.readouterr().out
        assert out.count("loss_coef") == 2
        assert "dB/km" in out

    def test_example_scenario_runs_against_example_topology(
        self, example_app: TestClient, capsys: pytest.CaptureFixture[str],
    ) -> None:
        # Loaded from the same GNPy example file examples/twin_config.yaml
        # uses, so a renamed element there breaks this test, not a demo.
        assert main(["run", str(EXAMPLE), "--dry-run"], client=example_app) == 0
        assert example_app.get("/config/soft-failures").json()["soft-failures"] == []
        assert main(["run", str(EXAMPLE)], client=example_app) == 0
        jobs = example_app.get("/config/soft-failures").json()["soft-failures"]
        assert [j["name"] for j in jobs] == [
            "dirty connector Site_A→Site_B", "pump ageing Edfa1",
        ]
        assert [t["uid"] for j in jobs for t in j["targets"]] == ["Span1", "Edfa1"]
        assert jobs[1]["state"] == "scheduled"
        assert "sf-2" in capsys.readouterr().out

    def test_start_control_and_delete(
        self, gnpy_app: TestClient, capsys: pytest.CaptureFixture[str],
    ) -> None:
        argv = ["start", "/Fiber/*/loss_coef", "--by", "0.05", "--duration", "10m",
                "--profile", "sigmoid", "--name", "cli"]
        assert main(argv, client=gnpy_app) == 0
        for cmd in ("pause", "resume", "stop", "status"):
            assert main([cmd, "sf-1"], client=gnpy_app) == 0
        assert main(["list"], client=gnpy_app) == 0
        assert main(["delete", "sf-1", "--revert"], client=gnpy_app) == 0
        assert "stopped" in capsys.readouterr().out

    def test_server_errors_exit_2_with_message(
        self, gnpy_app: TestClient, capsys: pytest.CaptureFixture[str],
    ) -> None:
        argv = ["start", "/Fiber/*/loss_coef", "--to", "9", "--duration", "1m"]
        assert main(argv, client=gnpy_app) == 2
        assert "out of range" in capsys.readouterr().err
        assert main(["stop", "sf-42"], client=gnpy_app) == 2

    def test_watch_follows_until_complete(
        self, gnpy_app: TestClient, capsys: pytest.CaptureFixture[str],
    ) -> None:
        mgr = gnpy_app.app.state.context.soft_failures  # type: ignore[attr-defined]
        now = [0.0]
        mgr.clock = lambda: now[0]
        main(["start", "/Fiber/*/loss_coef", "--by", "0.05", "--duration", "10"],
             client=gnpy_app)

        def fake_sleep(seconds: float) -> None:
            now[0] += 5.0
            mgr.tick()

        assert watch(gnpy_app, ["sf-1"], sleep=fake_sleep) == 0
        out = capsys.readouterr().out
        assert "50.0%" in out and "completed" in out
