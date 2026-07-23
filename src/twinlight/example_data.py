"""Provision the GNPy example topologies used by the bundled scenarios.

The example scenarios under ``examples/`` reference GNPy reference topologies
(CORONET CONUS, the small ``edfa_example_network``) and the GNPy equipment
library. Those JSON files belong to the `oopt-gnpy`_ project and are **not**
redistributed with TwinLight; they are instead copied out of the ``gnpy``
distribution that is already installed as a runtime dependency, which
guarantees the topology schema matches the GNPy version actually in use.

Exposed as the ``twinlight-fetch-examples`` console script::

    twinlight-fetch-examples             # populate examples/gnpy-data/
    twinlight-fetch-examples --force     # overwrite existing copies
    twinlight-fetch-examples --list      # show what would be copied

.. _oopt-gnpy: https://github.com/Telecominfraproject/oopt-gnpy
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

# GNPy example-data files the bundled scenarios in examples/*.yaml depend on.
# Keys are the file names inside ``gnpy/example-data/``; values explain what
# each one is used for, and are printed by ``--list``.
REQUIRED_FILES: dict[str, str] = {
    "CORONET_CONUS_Topology.json": "CORONET CONUS backbone — 75 ROADMs, 198 fiber spans",
    "edfa_example_network.json": "Two-site example span with inline EDFAs",
    "eqpt_config.json": "Equipment library — EDFA models, transceivers, fiber types",
}

# Default destination, relative to the repository root: examples/gnpy-data/.
DEFAULT_DEST = Path("examples") / "gnpy-data"


def gnpy_example_data_dir() -> Path:
    """Return the ``example-data`` directory inside the installed gnpy package.

    Raises:
        FileNotFoundError: if gnpy is installed without its example data.
        ModuleNotFoundError: if gnpy is not installed at all.
    """
    import gnpy  # imported lazily so ``--help`` works without gnpy present

    package_dir = Path(gnpy.__file__).parent
    data_dir = package_dir / "example-data"
    if not data_dir.is_dir():
        raise FileNotFoundError(
            f"The installed gnpy package has no example-data directory ({data_dir}). "
            "Reinstall gnpy from PyPI, or download the files manually from "
            "https://github.com/Telecominfraproject/oopt-gnpy/tree/master/gnpy/example-data"
        )
    return data_dir


def provision(dest: Path = DEFAULT_DEST, *, force: bool = False) -> list[Path]:
    """Copy the required GNPy example files into ``dest``.

    Args:
        dest: destination directory; created if missing.
        force: overwrite files that are already present.

    Returns:
        The paths that were written (empty when everything already existed).
    """
    source_dir = gnpy_example_data_dir()
    dest.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for name in REQUIRED_FILES:
        target = dest / name
        if target.exists() and not force:
            continue
        shutil.copyfile(source_dir / name, target)
        written.append(target)
    return written


def main(argv: list[str] | None = None) -> int:
    """Console-script entry point for ``twinlight-fetch-examples``."""
    parser = argparse.ArgumentParser(
        prog="twinlight-fetch-examples",
        description=(
            "Copy the GNPy reference topologies and equipment library required "
            "by the bundled example scenarios out of the installed gnpy package. "
            "These files are owned by the oopt-gnpy project and are not "
            "redistributed with TwinLight."
        ),
    )
    parser.add_argument(
        "-d", "--dest", type=Path, default=DEFAULT_DEST,
        metavar="DIR",
        help=f"Destination directory (default: {DEFAULT_DEST}).",
    )
    parser.add_argument(
        "-f", "--force", action="store_true",
        help="Overwrite files that already exist in the destination.",
    )
    parser.add_argument(
        "-l", "--list", action="store_true", dest="list_only",
        help="List the required files and exit without copying.",
    )
    args = parser.parse_args(argv)

    if args.list_only:
        print("GNPy example files required by examples/*.yaml:")
        for name, description in REQUIRED_FILES.items():
            print(f"  {name:<32} {description}")
        return 0

    try:
        written = provision(args.dest, force=args.force)
    except ModuleNotFoundError:
        print(
            "gnpy is not installed. Install TwinLight first (see the README), "
            "or run 'uv pip install gnpy'.",
            file=sys.stderr,
        )
        return 1
    except (FileNotFoundError, OSError) as exc:
        print(f"Could not provision example data: {exc}", file=sys.stderr)
        return 1

    if written:
        print(f"Copied {len(written)} file(s) into {args.dest}:")
        for path in written:
            print(f"  {path}")
    else:
        print(f"{args.dest} is already populated; nothing to do (use --force to overwrite).")
    return 0


if __name__ == "__main__":  # pragma: no cover - console-script shim
    raise SystemExit(main())
