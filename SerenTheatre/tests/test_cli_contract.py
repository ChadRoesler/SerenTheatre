"""Every flag stagehand can hand the builder must be a flag the builder has.

THE TEST THAT SHOULD HAVE CAUGHT IT, and the reason the one next door did not.

`test_argv_is_the_documented_command` monkeypatches `shutil.which` and asserts
the SHAPE of the argv. It executes nothing, so all it can prove is that
stagehand built the string it intended to build - and the bug was that the
intended string was wrong. `run_detached` appended `--log-file` and
`--events-file`, which ms-moe-maker has never had. argparse exited 2 in a few
milliseconds, the streams were DEVNULL so the message went nowhere, nothing
wait()ed, and the launch was reported as a success. Every assertion on our side
of the boundary passed the whole time, because every one of them was about us.

So this file checks the OTHER side: it asks the real builder what flags it
accepts and compares that against the flags our real argv-builders actually
emit. Same bargain as test_manifest_contract.py - two packages, one interface,
and two sources that can disagree are only useful if something compares them.

WHY THE FLAGS ARE COLLECTED BY RUNNING THE BUILDERS rather than listed here. A
list in this file is a third copy of the interface, and a third copy drifts
like the other two - it would go stale the day someone adds a flag, and it
would go stale QUIETLY, which is the failure this file exists to prevent. So
the argv comes out of `build_argv` and `run_detached` and Backstage's own run
route, with the spawn stubbed. Add a flag anywhere in stagehand and it arrives
here without an edit.

Discovery and the skip discipline follow _writerfinder: the writer's name is
derived from what SerenTheatre declares under [stagehand], never spelled out,
so a rename cannot switch these assertions off.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import _writerfinder as wf
from seren_theatre import stagehand

HELP_TIMEOUT = 30.0

# Two INDEPENDENT signals that the writer is here, for the same reason
# _writerfinder keeps two: a guard that shares its only signal with the thing
# it guards goes blind at the same moment.
ON_PATH = shutil.which(wf.WRITER_DIST) if wf.WRITER_DIST else None
IMPORTABLE = wf.is_importable()
PRESENT = bool(ON_PATH) or IMPORTABLE


def _writer_argv() -> list:
    """How to invoke the writer, preferring the console script.

    Same order as stagehand.resolve_command and for the same reason: the
    console script is the thing a person types and the thing stagehand execs,
    so it is the thing whose --help is authoritative.
    """
    if ON_PATH:
        return [ON_PATH]
    return [sys.executable, "-m", wf.WRITER_MODULE]


def _build_help():
    """`<writer> build --help`, or None if it could not be read."""
    if not PRESENT:
        return None
    try:
        proc = subprocess.run(_writer_argv() + [stagehand.BUILD_VERB, "--help"],
                              capture_output=True, text=True,
                              timeout=HELP_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout or None


HELP = _build_help()

needs_help = pytest.mark.skipif(
    HELP is None,
    reason=f"{wf.WRITER_DIST or 'the writer'} is not here, so there is no "
           f"help text to check our flags against.")


def _flags(argv) -> list:
    """The option tokens in an argv. A bare `-` is a filename convention."""
    return [a for a in argv if a.startswith("-") and a != "-"]


def _accepted(flag: str, help_text: str) -> bool:
    """Is `flag` a word in the help text?

    Word-bounded rather than `in`, because a substring match would let
    `--log` pass on the strength of `--logfile` and this test's whole job is to
    refuse a flag that is ALMOST right.
    """
    return re.search(rf"(?<![\w-]){re.escape(flag)}(?![\w-])",
                     help_text) is not None


def _assert_accepted(argv, help_text, where: str) -> None:
    unknown = [f for f in _flags(argv) if not _accepted(f, help_text)]
    assert not unknown, (
        f"{where} puts {unknown} in the argv and `{wf.WRITER_DIST} "
        f"{stagehand.BUILD_VERB} --help` does not list them. argparse exits 2 "
        f"on an unrecognised flag, so this is not a degraded build - it is a "
        f"build that dies in milliseconds. Full argv:\n    {argv}")


# ── the flags we actually emit ──────────────────────────────────────────────

@needs_help
def test_the_documented_argv_uses_only_flags_the_builder_has():
    argv = stagehand.build_argv(Path("recipe.yaml"))
    _assert_accepted(argv[2:], HELP, "build_argv")


@needs_help
def test_the_detached_argv_uses_only_flags_the_builder_has(tmp_path, monkeypatch):
    """The one that was broken. Popen is stubbed - this is about the argv, and
    a real build is not something a test suite gets to start."""
    seen = {}

    class FakeProc:
        pid = 4242

        def wait(self, timeout=None):
            # TimeoutExpired is the "still alive" answer run_detached wants.
            raise subprocess.TimeoutExpired("stub", timeout)

    def fake_popen(argv, **kw):
        seen["argv"] = argv
        return FakeProc()

    monkeypatch.setattr(stagehand.subprocess, "Popen", fake_popen)
    stagehand.run_detached(tmp_path / "r.yaml", cwd=tmp_path,
                           log_file=tmp_path / "a.log",
                           events_file=tmp_path / "a.jsonl")
    _assert_accepted(seen["argv"][2:], HELP, "run_detached")


@needs_help
@pytest.mark.parametrize("dryrun", [True, False])
def test_every_argv_backstage_can_produce_uses_flags_the_builder_has(
        tmp_path, monkeypatch, dryrun):
    """Through the route, because the route is where the extras are decided.

    `--allow-refusals` lived here and was invented in exactly the same way
    `--log-file` was: someone wrote down the flag they wished for. Ticking that
    box did not relax anything, it killed the build.
    """
    pytest.importorskip("ms_moe_maker")
    from fastapi.testclient import TestClient

    from seren_theatre import backstage as bs
    from seren_theatre.app import create_app
    from seren_theatre.config import StageConfig, TheatreConfig

    (tmp_path / "lab").mkdir()
    recipes = tmp_path / "recipes"
    recipes.mkdir()
    (recipes / "r.yaml").write_text("schema_version: 1\n", encoding="utf-8")

    cfg = TheatreConfig()
    cfg.recipes = str(recipes)
    cfg.stages = [StageConfig(name="Lab", path=str(tmp_path / "lab"))]

    seen = {}

    def fake_detached(recipe, *, cwd, log_file=None, events_file=None, extra=()):
        seen["argv"] = (list(stagehand.resolve_command())
                        + [stagehand.BUILD_VERB, str(recipe), "--json"]
                        + list(extra))
        return {"pid": 1, "argv": seen["argv"]}

    monkeypatch.setattr(bs.stagehand, "run_detached", fake_detached)
    client = TestClient(create_app(cfg))
    resp = client.post("/api/backstage/run",
                       json={"name": "r.yaml", "dryrun": dryrun})
    assert resp.status_code == 200, resp.text
    _assert_accepted(seen["argv"][2:], HELP, "POST /api/backstage/run")


# ── the guard on the guard ──────────────────────────────────────────────────

def test_the_skip_cannot_become_permanent_and_silent():
    """A skip that fires forever is not a test.

    The distinction that matters, and the one the manifest contract test
    already draws: the writer being genuinely absent is a reason to skip, and
    the writer being HERE while we could not read its help is a reason to fail.
    The second is this check going blind, and a blind check reports green - a
    flag could be invented tomorrow and nothing would say so.
    """
    if not PRESENT:
        pytest.skip(f"{wf.WRITER_DIST} is genuinely not here; nothing to ask")
    assert HELP, (
        f"{wf.WRITER_DIST} IS present (on PATH={bool(ON_PATH)}, "
        f"importable={IMPORTABLE}) but `{stagehand.BUILD_VERB} --help` could "
        f"not be read, so every flag check above just skipped. Either the "
        f"command moved or it stopped answering --help; both are things this "
        f"file has to be told about, not shrug at.")


def test_the_help_text_is_the_real_one_and_not_an_empty_string():
    """`_accepted` against an empty help would refuse everything, and against a
    help text that lists nothing would accept nothing - both are loud. This
    pins the opposite failure: a help text so generic that everything matches.
    """
    if HELP is None:
        pytest.skip("no help text; covered by the guard above")
    assert not _accepted("--not-a-real-flag-at-all", HELP), (
        "the help text matches a flag that cannot exist, so `_accepted` is "
        "answering yes to everything and these assertions prove nothing")
