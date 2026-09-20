"""Tests for the shutdown-checkpoint file operations in ``twinlight.main``.

The startup *decision* (what to restore) is covered in test_cli.py. These cover
the two side effects that decision implies: archiving an existing checkpoint on
``--reset``, and writing one on graceful shutdown.

Both are deliberately forgiving at runtime — a twin that cannot write its
checkpoint should complain, not fail to stop — so the failure paths are tested
as carefully as the happy ones.
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from twinlight.config import TwinConfig
from twinlight.main import archive_checkpoint, write_checkpoint


def _config(tmp_path: Path, **simulation: Any) -> TwinConfig:
    return TwinConfig(
        gnpy={"topology": str(tmp_path / "net.json")},
        simulation={"snapshot_dir": str(tmp_path / "snapshots"), **simulation},
    )


class _FakeContext:
    """Stands in for TapiContext: records where snapshot() was asked to write."""

    def __init__(self, fail: bool = False) -> None:
        self.written: list[Path] = []
        self.fail = fail

    def snapshot(self, path: Path) -> Path:
        if self.fail:
            raise OSError("disk full")
        path.write_text('{"version": 1}')
        self.written.append(path)
        return path


def _app(context: Any) -> SimpleNamespace:
    return SimpleNamespace(state=SimpleNamespace(context=context))


# ---------------------------------------------------------------------------
# archive_checkpoint  (--reset)
# ---------------------------------------------------------------------------

class TestArchiveCheckpoint:
    def test_moves_existing_checkpoint_aside(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        checkpoint = config.checkpoint_path()
        checkpoint.parent.mkdir(parents=True)
        checkpoint.write_text('{"marker": true}')

        archive_checkpoint(config)

        assert not checkpoint.exists()
        backup = checkpoint.with_suffix(checkpoint.suffix + ".bak")
        assert backup.read_text() == '{"marker": true}'

    def test_is_a_no_op_when_there_is_no_checkpoint(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        archive_checkpoint(config)
        assert not config.checkpoint_path().exists()
        assert not config.checkpoint_path().with_suffix(".json.bak").exists()

    def test_second_reset_overwrites_the_backup(self, tmp_path: Path) -> None:
        """Only one generation is kept. Worth asserting so nobody assumes the
        backups accumulate and relies on an older one being there."""
        config = _config(tmp_path)
        checkpoint = config.checkpoint_path()
        checkpoint.parent.mkdir(parents=True)

        checkpoint.write_text("first")
        archive_checkpoint(config)
        checkpoint.write_text("second")
        archive_checkpoint(config)

        backup = checkpoint.with_suffix(checkpoint.suffix + ".bak")
        assert backup.read_text() == "second"


# ---------------------------------------------------------------------------
# write_checkpoint  (graceful shutdown)
# ---------------------------------------------------------------------------

class TestWriteCheckpoint:
    def test_writes_to_the_configured_location(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        context = _FakeContext()

        write_checkpoint(_app(context), config)

        assert context.written == [config.checkpoint_path()]
        assert config.checkpoint_path().is_file()

    def test_creates_the_snapshot_directory(self, tmp_path: Path) -> None:
        """The directory is a host mount in docker-compose and may not exist on
        a first run outside it."""
        config = _config(tmp_path)
        assert not config.checkpoint_path().parent.exists()

        write_checkpoint(_app(_FakeContext()), config)

        assert config.checkpoint_path().is_file()

    def test_skipped_when_auto_checkpoint_is_disabled(self, tmp_path: Path) -> None:
        config = _config(tmp_path, auto_checkpoint=False)
        context = _FakeContext()

        write_checkpoint(_app(context), config)

        assert context.written == []
        assert not config.checkpoint_path().exists()

    def test_tolerates_an_app_that_never_built_a_context(self, tmp_path: Path) -> None:
        """Shutdown can be reached after a failed startup."""
        config = _config(tmp_path)
        write_checkpoint(SimpleNamespace(state=SimpleNamespace()), config)
        assert not config.checkpoint_path().exists()

    def test_failure_is_logged_not_raised(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A checkpoint that cannot be written must not turn a clean stop into
        a crash — process supervisors would report a failed shutdown."""
        config = _config(tmp_path)

        with caplog.at_level(logging.ERROR, logger="twinlight.main"):
            write_checkpoint(_app(_FakeContext(fail=True)), config)

        assert "Could not write checkpoint" in caplog.text

    def test_honours_a_custom_checkpoint_filename(self, tmp_path: Path) -> None:
        config = _config(tmp_path, checkpoint_file="demo-state.json")
        context = _FakeContext()

        write_checkpoint(_app(context), config)

        assert context.written == [tmp_path / "snapshots" / "demo-state.json"]
