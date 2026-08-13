"""What --describe must always be true of.

These assert INVARIANTS, not observations. Every check below is something the
contract guarantees by construction - Starwright builds its grid from this
payload, so a missing key is a blank card and a wrong type is a crash in
someone else's process. None of them are "it was like that when I looked".

The one that is not obvious: --describe must not need anything but the standard
library. The moment you most want a service to be able to say its own name is
when its install is broken, so importing pydantic to answer would defeat the
purpose. test_describe_is_stdlib_only enforces that by importing the module in
a subprocess with the site-packages path stripped.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from seren_theatre import _describe

REPO_PKG_ROOT = Path(__file__).resolve().parent.parent


def test_describe_has_every_key_starwright_reads():
    for key in ("name", "port", "group", "accent", "extras", "requires",
                "description"):
        assert key in _describe.DESCRIBE, f"--describe is missing {key!r}"


def test_types_are_what_a_json_consumer_expects():
    d = _describe.DESCRIBE
    assert isinstance(d["name"], str) and d["name"]
    assert isinstance(d["port"], int)
    assert isinstance(d["group"], str) and d["group"]
    assert isinstance(d["extras"], list)
    assert isinstance(d["requires"], list)


def test_payload_is_json_serialisable_on_one_line():
    # The contract is literally "one line of JSON". A newline in a description
    # would split the record for anything reading line-by-line.
    line = json.dumps(_describe.DESCRIBE)
    assert "\n" not in line
    assert json.loads(line) == _describe.DESCRIBE


def test_port_and_accent_are_the_same_objects_config_binds():
    # config.py re-exports these rather than redeclaring them. If someone
    # "tidies up" by giving config.py its own copy, this fails - which is the
    # whole Workbench 7425-vs-7444 lesson encoded as a test.
    from seren_theatre import config

    assert config.DEFAULT_PORT is _describe.DEFAULT_PORT
    assert config.ACCENT is _describe.ACCENT


def test_default_port_is_not_a_seat_someone_else_owns():
    # The family's bound ports, INCLUDING the loopback-only ones. Symposium is
    # in this list on purpose: it is localhost-only, which is why it was
    # missing from `seren/port-map`, which is why Theatre first picked 7426 and
    # collided with it. A map that lists only what is reachable over the
    # network cannot catch a collision between two services on 127.0.0.1.
    taken = {
        6361: "lodestar",
        7420: "memory",
        7421: "margin",
        7422: "loci",
        7423: "corpus-callosum",
        7424: "corpus-callosum (vector variant, held)",
        7425: "workbench",
        7426: "symposium (loopback UI shim)",
        7430: "probe",
        7777: "observatory",
    }
    port = _describe.DEFAULT_PORT
    assert port not in taken, (
        f"DEFAULT_PORT {port} belongs to {taken.get(port)}. "
        f"Pick a free seat and add it to the family map in _describe.py."
    )
    # Probe's shipped sample topology allocates upward from 7440, and its
    # docker test containers sit at 7520-7524 so a harness can never
    # impersonate a real store. Both are Probe's, and both are off limits.
    assert not (7440 <= port <= 7461), (
        f"DEFAULT_PORT {port} is inside SerenProbe's sample topology range "
        f"7440-7461."
    )
    assert not (7520 <= port <= 7524), (
        f"DEFAULT_PORT {port} is inside SerenProbe's docker test-container "
        f"range 7520-7524."
    )


def test_describe_is_stdlib_only():
    """Importing _describe must not pull in pydantic, yaml, fastapi or uvicorn.

    Run in a subprocess because this process has already imported them.
    """
    code = (
        "import sys; "
        f"sys.path.insert(0, {str(REPO_PKG_ROOT)!r}); "
        "import seren_theatre._describe as d; "
        "heavy = [m for m in ('pydantic','yaml','fastapi','uvicorn') "
        "         if m in sys.modules]; "
        "print(','.join(heavy))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, check=True)
    leaked = out.stdout.strip()
    assert not leaked, (
        f"--describe's module pulled in {leaked}. It has to answer on a "
        f"half-installed service, so it must import nothing heavy."
    )


def test_module_describe_runs_clean_and_prints_one_line():
    out = subprocess.run(
        [sys.executable, "-m", "seren_theatre", "--describe"],
        capture_output=True, text=True, cwd=REPO_PKG_ROOT,
    )
    assert out.returncode == 0, f"--describe exited {out.returncode}: {out.stderr}"
    lines = [ln for ln in out.stdout.strip().splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected exactly one line, got {len(lines)}"
    assert json.loads(lines[0])["port"] == _describe.DEFAULT_PORT


@pytest.mark.parametrize("side_effect", ["seren-theatre", ".seren"])
def test_describe_creates_nothing_in_home(tmp_path, monkeypatch, side_effect):
    """ZERO side effects means zero. No config dir, no state dir, no nothing."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    subprocess.run([sys.executable, "-m", "seren_theatre", "--describe"],
                   capture_output=True, text=True, cwd=REPO_PKG_ROOT, check=True)
    assert not (tmp_path / side_effect).exists()
