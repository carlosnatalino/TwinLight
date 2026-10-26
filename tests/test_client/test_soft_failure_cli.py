"""Tests for the ``twinlight-soft-failure`` client, which owns the ramp logic.

The FastAPI ``TestClient`` is an ``httpx.Client``, so the script runs
against an in-process twin with no server and no network, and the runner's
injectable clock and sleep make a ten-minute scenario take microseconds.
"""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from twinlight.app import create_app
from twinlight.config import GnpyConfig, TwinConfig
from twinlight.example_data import find_example_file
from twinlight_client.soft_failure import (
    Profile,
    ScenarioError,
    SoftFailureRunner,
    SoftFailureSpec,
    _build_parser,
    build_spec,
    load_scenario,
    main,
    parse_duration,
    parse_rate,
    run_command,
    shape_fraction,
)

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "soft_failure_scenario.yaml"
FWD_FIBER = "fiber (Brest → Morlaix)-F"
REV_FIBER = "fiber (Morlaix → Brest)-F"
EAST_AMP = "east edfa in Brest to Morlaix"
FWD_LOSS = f'/Fiber/"{FWD_FIBER}"/loss_coef'


class FakeTime:
    """A clock and a sleep that advances it."""

    def __init__(self) -> None:
        self.now = 0.0

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


class Recorder:
    """Counts the HTTP calls a client makes, by (method, path)."""

    def __init__(self, client: httpx.Client) -> None:
        self.calls: list[tuple[str, str]] = []
        client.event_hooks = {"request": [self._hook], "response": []}

    def _hook(self, request: httpx.Request) -> None:
        self.calls.append((request.method, request.url.path))

    def count(self, method: str, path: str) -> int:
        return self.calls.count((method, path))


def _attr(client: TestClient, uid: str, attr: str) -> float:
    return float(client.get(f"/config/devices/{uid}").json()["attributes"][attr])


def _runner(client: TestClient, t: FakeTime, *specs: SoftFailureSpec) -> SoftFailureRunner:
    return SoftFailureRunner(
        client, list(specs), clock=t.clock, sleep=t.sleep, out=lambda _s: None,
    )


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


class TestSpec:
    def test_ramp_vs_step(self) -> None:
        assert build_spec({"path": "/x/y/z", "by": 1}).is_step
        assert not build_spec({"path": "/x/y/z", "by": 1, "duration": "1m"}).is_step

    def test_twin_sees_only_the_change(self) -> None:
        spec = build_spec({
            "path": "/x/y/z", "by": 1, "direction": "decrease", "duration": "1m",
            "profile": "sigmoid", "name": "n", "start_delay": "5s",
        })
        assert spec.change() == {
            "path": "/x/y/z", "by": 1.0, "direction": "decrease",
            "out_of_bounds": "reject",
        }

    @pytest.mark.parametrize(("entry", "msg"), [
        ({"path": "/x/y/z", "by": 1, "duration": "1m", "rate": 1}, "not both"),
        ({"path": "/x/y/z", "by": 1, "on_complete": "revert"}, "needs a ramp"),
        ({"path": "/x/y/z", "duration": "1m"}, "exactly one"),
        ({"path": "/x/y/z", "by": 1, "duration": "1m", "duration_s": 60}, "not both"),
        ({"path": "/x/y/z", "by": 1, "interval": "0.001s", "duration": 1}, "interval_s"),
    ])
    def test_invalid_entries(self, entry: dict, msg: str) -> None:
        with pytest.raises(ScenarioError, match=msg):
            build_spec(entry)

    def test_example_scenario_loads(self) -> None:
        specs = load_scenario(EXAMPLE)
        assert [s.is_step for s in specs] == [False, False, True]
        assert all(s.interval_s == 2.0 for s in specs)
        assert specs[0].duration_s == 600.0
        assert specs[1].rate == pytest.approx(0.5 / 60)
        assert specs[2].start_delay_s == 300.0

    def test_entry_speed_replaces_default_speed(self, tmp_path: Path) -> None:
        f = tmp_path / "s.yaml"
        f.write_text(
            "defaults: {duration: 10m}\n"
            "soft_failures:\n"
            "  - {path: /x/y/z, by: 1}\n"
            "  - {path: /x/y/z, by: 1, rate: 1/min}\n"
        )
        a, b = load_scenario(f)
        assert a.duration_s == 600.0
        assert b.duration_s is None and b.rate == pytest.approx(1 / 60)

    def test_scenario_shape_errors(self, tmp_path: Path) -> None:
        f = tmp_path / "s.yaml"
        f.write_text("soft_failures: {}\n")
        with pytest.raises(ScenarioError, match="list"):
            load_scenario(f)


class TestShapes:
    @pytest.mark.parametrize("profile", list(Profile))
    def test_endpoints_and_monotonic(self, profile: Profile) -> None:
        ys = [shape_fraction(profile, i / 100, 5.0, 4) for i in range(101)]
        assert ys[0] == pytest.approx(0.0)
        assert ys[-1] == pytest.approx(1.0)
        assert all(b >= a - 1e-12 for a, b in pairwise(ys))

    def test_shapes_differ_where_expected(self) -> None:
        mid = {p: shape_fraction(p, 0.5, 5.0, 4) for p in Profile}
        assert mid[Profile.LINEAR] == pytest.approx(0.5)
        assert mid[Profile.SIGMOID] == pytest.approx(0.5)
        assert mid[Profile.ACCELERATING] < 0.5 < mid[Profile.SATURATING]
        assert shape_fraction(Profile.STEP, 0.49, 5.0, 4) == 0.25


class TestRunner:
    def test_linear_ramp_reaches_end(self, gnpy_app: TestClient) -> None:
        t = FakeTime()
        rec = Recorder(gnpy_app)
        runner = _runner(gnpy_app, t, build_spec(
            {"path": FWD_LOSS, "by": 0.1, "duration": 10, "interval": 1},
        ))
        runner.plan()
        # Planning is one dry run of the changes endpoint, and writes nothing.
        assert rec.calls == [("POST", "/config/parameters/changes")]
        runner.run()
        assert t.now == pytest.approx(10.0)
        assert _attr(gnpy_app, FWD_FIBER, "loss_coef") == pytest.approx(0.3)
        # t=0 is the start value (no write), then one write per second.
        assert rec.count("POST", "/config/parameters") == 10

    def test_due_ramps_share_one_call_per_tick(self, gnpy_app: TestClient) -> None:
        t = FakeTime()
        rec = Recorder(gnpy_app)
        _runner(gnpy_app, t,
                build_spec({"path": "/Fiber/*/loss_coef", "by": 0.1, "duration": 4,
                            "interval": 2}),
                build_spec({"path": f'/Edfa/"{EAST_AMP}"/tilt_target', "to": 1.0,
                            "duration": 4, "interval": 2}),
                ).run()
        assert rec.count("POST", "/config/parameters") == 2  # t=2 and t=4
        assert _attr(gnpy_app, REV_FIBER, "loss_coef") == pytest.approx(0.3)
        assert _attr(gnpy_app, EAST_AMP, "tilt_target") == pytest.approx(1.0)

    def test_reverse_and_revert_end_at_start(self, gnpy_app: TestClient) -> None:
        t = FakeTime()
        _runner(gnpy_app, t,
                build_spec({"path": "/Fiber/*/loss_coef", "by": 0.1, "duration": 10,
                            "on_complete": "reverse"}),
                build_spec({"path": f'/Edfa/"{EAST_AMP}"/tilt_target', "to": 1.0,
                            "duration": 5, "on_complete": "revert"}),
                ).run()
        assert t.now == pytest.approx(20.0)
        assert _attr(gnpy_app, FWD_FIBER, "loss_coef") == pytest.approx(0.2)
        assert _attr(gnpy_app, EAST_AMP, "tilt_target") == 0.0

    def test_rate_sets_duration(self, gnpy_app: TestClient) -> None:
        t = FakeTime()
        runner = _runner(gnpy_app, t, build_spec(
            {"path": FWD_LOSS, "to": 0.3, "rate": "0.6/min"},
        ))
        [entry] = runner.plan()
        assert entry.targets[0].duration == pytest.approx(10.0)

    def test_step_profile_writes_only_on_steps(self, gnpy_app: TestClient) -> None:
        rec = Recorder(gnpy_app)
        _runner(gnpy_app, FakeTime(), build_spec({
            "path": FWD_LOSS, "by": 0.1, "duration": 10, "interval": 0.5,
            "profile": "step", "steps": 2,
        })).run()
        assert rec.count("POST", "/config/parameters") == 2  # +0.05, then +0.1

    def test_step_entry_applies_live_relative_change_at_its_time(
        self, gnpy_app: TestClient,
    ) -> None:
        t = FakeTime()
        rec = Recorder(gnpy_app)
        _runner(gnpy_app, t,
                build_spec({"path": FWD_LOSS, "by": 0.1, "duration": 10}),
                build_spec({"path": f'/Edfa/"{EAST_AMP}"/tilt_target', "by": 0.5,
                            "start_delay": 4}),
                ).run()
        # Planned once (dry run) and applied once, at t=4.
        assert rec.count("POST", "/config/parameters/changes") == 2
        assert _attr(gnpy_app, EAST_AMP, "tilt_target") == pytest.approx(0.5)

    def test_bad_entry_refuses_whole_scenario_before_any_write(
        self, gnpy_app: TestClient,
    ) -> None:
        rec = Recorder(gnpy_app)
        runner = _runner(gnpy_app, FakeTime(),
                         build_spec({"path": FWD_LOSS, "by": 0.1, "duration": 10}),
                         build_spec({"path": FWD_LOSS.replace("loss_coef", "att_in"),
                                     "to": 99, "duration": 10}))
        with pytest.raises(ScenarioError, match="outside"):
            runner.run()
        assert rec.count("POST", "/config/parameters") == 0

    def test_revert_after_interrupt(self, gnpy_app: TestClient) -> None:
        t = FakeTime()
        runner = _runner(gnpy_app, t, build_spec(
            {"path": "/Fiber/*/loss_coef", "by": 0.1, "duration": 10},
        ))

        def interrupt_at_5(seconds: float) -> None:
            t.now += seconds
            if t.now >= 5:
                raise KeyboardInterrupt

        runner.sleep = interrupt_at_5
        with pytest.raises(KeyboardInterrupt):
            runner.run()
        # Interrupted while sleeping towards t=5: the t=4 value is the last.
        assert _attr(gnpy_app, FWD_FIBER, "loss_coef") == pytest.approx(0.24)
        runner.revert()
        assert _attr(gnpy_app, FWD_FIBER, "loss_coef") == pytest.approx(0.2)
        assert _attr(gnpy_app, REV_FIBER, "loss_coef") == pytest.approx(0.2)


class TestExampleScenario:
    def test_runs_against_the_example_network(
        self, example_app: TestClient, capsys: pytest.CaptureFixture[str],
    ) -> None:
        """examples/soft_failure_scenario.yaml, end to end, in fake time.

        Loaded from the same GNPy example file examples/twin_config.yaml uses,
        so a renamed element there breaks this test, not a demo.
        """
        t = FakeTime()
        rec = Recorder(example_app)
        # The designed gain, not the topology file's 17 dB: the reversed
        # ramp must end where the amplifier actually started.
        start_gain = _attr(example_app, "Edfa1", "gain_target")
        args = _build_parser().parse_args(["run", str(EXAMPLE)])
        assert run_command(args, example_app, clock=t.clock, sleep=t.sleep) == 0
        # pump ageing: 3 dB at 0.5 dB/min = 6 min, plus 6 min back, after 1 min.
        assert t.now == pytest.approx(13 * 60)
        assert _attr(example_app, "Span1", "loss_coef") == pytest.approx(0.25)
        assert _attr(example_app, "Edfa1", "gain_target") == pytest.approx(start_gain)
        assert _attr(example_app, "Edfa1", "out_voa") == pytest.approx(1.0)
        assert rec.count("POST", "/config/parameters/changes") == 2  # plan + step
        assert rec.count("POST", "/config/parameters") > 0
        out = capsys.readouterr().out
        assert "step VOA mis-set during maintenance: Edfa1.out_voa 0 → 1 dB" in out
        assert "Done." in out

    def test_dry_run_changes_nothing(
        self, example_app: TestClient, capsys: pytest.CaptureFixture[str],
    ) -> None:
        assert main(["run", str(EXAMPLE), "--dry-run"], client=example_app) == 0
        assert _attr(example_app, "Span1", "loss_coef") == pytest.approx(0.2)
        out = capsys.readouterr().out
        assert "Span1.loss_coef: 0.2 → 0.25 dB/km" in out
        assert "pump ageing Edfa1: accelerating over 720s, then reverse, at +60s" in out


class TestCommands:
    def test_params(self, gnpy_app: TestClient, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["params", "/Fiber/*/loss_coef"], client=gnpy_app) == 0
        out = capsys.readouterr().out
        assert out.count("loss_coef") == 2 and "dB/km" in out

    def test_set_several_in_one_call(self, gnpy_app: TestClient) -> None:
        rec = Recorder(gnpy_app)
        argv = ["set", "/Fiber/*/loss_coef=0.25", f'/Edfa/"{EAST_AMP}"/tilt_target=0.5']
        assert main(argv, client=gnpy_app) == 0
        assert rec.calls == [("POST", "/config/parameters")]
        assert _attr(gnpy_app, FWD_FIBER, "loss_coef") == pytest.approx(0.25)
        assert _attr(gnpy_app, EAST_AMP, "tilt_target") == 0.5

    def test_change_now(self, gnpy_app: TestClient) -> None:
        argv = ["change", "/Fiber/*/loss_coef", "--by-percent", "50"]
        assert main(argv, client=gnpy_app) == 0
        assert _attr(gnpy_app, REV_FIBER, "loss_coef") == pytest.approx(0.3)

    def test_ramp_interrupted_with_revert(self, gnpy_app: TestClient) -> None:
        t = FakeTime()

        def interrupt(seconds: float) -> None:
            t.now += seconds
            if t.now >= 3:
                raise KeyboardInterrupt

        args = _build_parser().parse_args([
            "ramp", FWD_LOSS, "--by", "0.1", "--duration", "10s",
            "--revert-on-interrupt",
        ])
        assert run_command(args, gnpy_app, clock=t.clock, sleep=interrupt) == 130
        assert _attr(gnpy_app, FWD_FIBER, "loss_coef") == pytest.approx(0.2)

    def test_errors_exit_2_with_message(
        self, gnpy_app: TestClient, capsys: pytest.CaptureFixture[str],
    ) -> None:
        assert main(["set", "/Fiber/*/loss_coef=9"], client=gnpy_app) == 2
        assert "outside" in capsys.readouterr().err
        assert main(["set", "no-equals-sign"], client=gnpy_app) == 2
        argv = ["ramp", "/Fiber/*/loss_coef", "--to", "9", "--duration", "1m"]
        assert main(argv, client=gnpy_app) == 2
