"""The comparison that __main__.py promises and nothing was making.

Two things answer --describe for this service: the shell installer, which is
what SerenStarwright asks when it builds its grid, and the Python module, which
is what an operator asks a running box. Both docstrings say the point of having
two is that they can be checked against each other.

Nothing was checking them. That's the same shape as the bug they cite - the
`seren/port-map` fact records Workbench's code saying 7425 while its installer
said 7444, so an installed node answered where the docs did not - except it had
been reintroduced one layer up: single source of truth INSIDE the package,
hand-maintained copy in the installer, no comparison.

So: this compares them.

SKIPPING. The installer lives in the SerenStarwright repo, not this one, so a
standalone SerenTheatre checkout genuinely cannot run this and skipping is
honest. A skip that fires in the layout Chad actually works in would not be -
so test_installer_is_findable_in_a_sibling_checkout fails loudly if the sibling
repo is present but the script has gone missing or been renamed.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from seren_theatre._describe import DESCRIBE

INSTALLER_REL = "services/bash/seren-theatre-setup.sh"


def _find_installer() -> Path | None:
    """Walk up looking for the Starwright services tree.

    Same find_upward shape the installers use on themselves - never hardcode a
    relative hop, because the repo layout has been reorganised before and will
    be again.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        for candidate in (
            parent / "SerenStarwright" / "SerenStarwright" / INSTALLER_REL,
            parent / "SerenStarwright" / INSTALLER_REL,
            parent / INSTALLER_REL,
        ):
            if candidate.is_file():
                return candidate
    return None


def _starwright_repo_present() -> bool:
    here = Path(__file__).resolve()
    return any((p / "SerenStarwright").is_dir() for p in here.parents)


installer = _find_installer()

needs_installer = pytest.mark.skipif(
    installer is None,
    reason="SerenStarwright checkout not found alongside this repo; "
           "the installer half of the contract isn't here to compare against.",
)
needs_bash = pytest.mark.skipif(
    shutil.which("bash") is None, reason="bash not on PATH",
)


def test_installer_is_findable_in_a_sibling_checkout():
    """If Starwright IS here, the script must be too.

    Guards against the skip above turning into a silent always-skip after a
    rename. A test that can only pass by not running is not a test.
    """
    if not _starwright_repo_present():
        pytest.skip("no SerenStarwright checkout nearby; nothing to assert")
    assert installer is not None, (
        f"SerenStarwright is present but {INSTALLER_REL} was not found. "
        f"Renamed or moved? The parity check is now blind."
    )


@pytest.fixture(scope="module")
def installer_describe() -> dict:
    out = subprocess.run(["bash", str(installer), "--describe"],
                         capture_output=True, text=True)
    assert out.returncode == 0, f"installer --describe exited {out.returncode}: {out.stderr}"
    lines = [ln for ln in out.stdout.strip().splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected one line of JSON, got {len(lines)}"
    return json.loads(lines[0])


@needs_installer
@needs_bash
@pytest.mark.parametrize(
    "installer_key, module_key",
    [
        ("name", "name"),
        ("default_port", "port"),
        ("group", "group"),
        ("accent", "accent"),
        ("description", "description"),
        ("extras", "extras"),
        ("requires", "requires"),
    ],
)
def test_installer_and_module_agree(installer_describe, installer_key, module_key):
    theirs = installer_describe[installer_key]
    ours = DESCRIBE[module_key]
    if isinstance(ours, list):
        theirs, ours = sorted(theirs), sorted(ours)
    assert theirs == ours, (
        f"{installer_key}={theirs!r} in seren-theatre-setup.sh but "
        f"{module_key}={ours!r} in seren_theatre/_describe.py. "
        f"An installed node would answer where the grid does not."
    )


@needs_installer
@needs_bash
def test_installer_describe_has_zero_side_effects(tmp_path):
    env = {"HOME": str(tmp_path), "USERPROFILE": str(tmp_path),
           "PATH": "/usr/local/bin:/usr/bin:/bin"}
    subprocess.run(["bash", str(installer), "--describe"],
                   capture_output=True, text=True, env=env, check=True)
    assert list(tmp_path.iterdir()) == [], (
        f"--describe wrote {[p.name for p in tmp_path.iterdir()]} into HOME. "
        f"It has to be safe to run against an uninstalled service on someone "
        f"else's box."
    )


@needs_installer
@needs_bash
def test_installer_advertises_the_flags_it_actually_parses(installer_describe):
    """flags[] is derived from the case branches, so this checks the derivation
    still works rather than that a list was maintained."""
    for flag in ("port", "host", "stage", "service", "instance"):
        assert flag in installer_describe["flags"], (
            f"--{flag} is parsed but not advertised; seren_flags_from_self "
            f"stopped matching the case branches."
        )
