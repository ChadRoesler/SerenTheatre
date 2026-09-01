"""Stagehand, and the invariant it is not allowed to break.

The whole reason stagehand is a sibling command rather than a POST route is
that Theatre exposes no write surface, and that is what makes it safe to point
at a live run. So the most important tests in this file are the ones that check
stagehand did NOT quietly become part of the service:

  * no write route appeared
  * the viewer's import graph does not contain stagehand
  * the service can report that stagehand exists and cannot invoke it

The rest check that when it does run, it runs the LITERAL documented command -
because "every automated run also tests the hand-run path" is a guarantee that
holds only as long as the two are the same string.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from seren_theatre import stagehand
from seren_theatre.app import create_app
from seren_theatre.config import TheatreConfig


# -- the invariant -----------------------------------------------------------

def test_stagehand_adds_no_write_route():
    """The load-bearing one. If this fails, the theatre started doing the work."""
    client = TestClient(create_app(TheatreConfig()))
    mutating = {"POST", "PUT", "PATCH", "DELETE"}
    offenders = [r.path for r in client.app.routes
                 if getattr(r, "methods", None) and set(r.methods) & mutating]
    assert offenders == [], (
        f"a write route appeared: {offenders}. Starting a build is a write, "
        f"and it belongs in the sibling CLI - a stagehand is not on stage.")


def test_the_viewer_never_imports_stagehand():
    """Importing the app must not drag stagehand in.

    Checked in a subprocess because this test module imports stagehand
    directly, so asking sys.modules in-process would always pass - and a test
    that can only pass is not a test.
    """
    code = (
        "import sys; "
        "import seren_theatre.app; "
        "print('seren_theatre.stagehand' in sys.modules)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, cwd=str(Path(__file__).resolve().parent.parent))
    assert out.stdout.strip() == "False", (
        "seren_theatre.app pulled stagehand onto the viewer's import graph. "
        "It is imported lazily inside the / route on purpose - the room must "
        "stay installable and runnable with no build tooling present at all.")


def test_the_service_can_say_stagehand_exists_but_not_use_it():
    body = TestClient(create_app(TheatreConfig())).get("/").json()
    assert "stagehand" in body
    assert isinstance(body["stagehand"], bool)
    # Reporting is a GET; there is no companion route that acts on it.
    assert "build" not in body


# -- the literal command -----------------------------------------------------

def test_argv_is_the_documented_command(tmp_path, monkeypatch):
    """`ms-moe-maker build <recipe> --json`, exactly.

    This string is the guarantee. The moment stagehand runs something else, an
    automated build stops exercising the path the README documents, and the
    documented path is the one that rots because nothing runs it.
    """
    monkeypatch.setattr(stagehand.shutil, "which",
                        lambda name: f"/usr/local/bin/{name}")
    recipe = tmp_path / "recipe.yaml"
    argv = stagehand.build_argv(recipe)
    assert argv[0].endswith("ms-moe-maker")
    assert argv[1] == "build"
    assert argv[2] == str(recipe)
    assert argv[-1] == "--json"


def test_extra_flags_pass_through_after_the_recipe(tmp_path, monkeypatch):
    monkeypatch.setattr(stagehand.shutil, "which", lambda name: "/bin/ms-moe-maker")
    # REAL flags, both of them. This used to pass `--allow-refusals`, which
    # ms-moe-maker has never had, and an example is a claim - a test that pipes
    # an invented flag through teaches the next reader that the flag exists.
    argv = stagehand.build_argv(tmp_path / "r.yaml",
                                extra=["--dryrun", "--offline"])
    assert argv[-2:] == ["--dryrun", "--offline"]


def test_the_console_script_is_preferred_over_the_module(monkeypatch):
    monkeypatch.setattr(stagehand.shutil, "which", lambda name: "/bin/ms-moe-maker")
    assert stagehand.resolve_command() == ["/bin/ms-moe-maker"]


def test_the_module_fallback_is_used_only_when_the_script_is_missing(monkeypatch):
    monkeypatch.setattr(stagehand.shutil, "which", lambda name: None)
    monkeypatch.setitem(sys.modules, "ms_moe_maker", type(sys)("ms_moe_maker"))
    assert stagehand.resolve_command() == [sys.executable, "-m", "ms_moe_maker"]


# -- honest failure ----------------------------------------------------------

def test_a_missing_ms_moe_names_the_extra_rather_than_exploding(monkeypatch):
    """A missing optional dependency should be one sentence naming the extra,
    not a FileNotFoundError from subprocess three frames down."""
    monkeypatch.setattr(stagehand.shutil, "which", lambda name: None)
    monkeypatch.delitem(sys.modules, "ms_moe_maker", raising=False)

    import builtins
    real_import = builtins.__import__

    def blocked(name, *args, **kw):
        if name == "ms_moe_maker":
            raise ImportError("no ms_moe_maker")
        return real_import(name, *args, **kw)

    monkeypatch.setattr(builtins, "__import__", blocked)
    with pytest.raises(stagehand.StagehandUnavailable) as exc:
        stagehand.resolve_command()
    message = str(exc.value)
    assert "seren-theatre[stagehand]" in message
    assert "works perfectly without it" in message


def test_available_is_a_safe_question_that_never_raises(monkeypatch):
    monkeypatch.setattr(stagehand.shutil, "which", lambda name: None)
    import builtins
    real_import = builtins.__import__

    def blocked(name, *args, **kw):
        if name == "ms_moe_maker":
            raise ImportError("no ms_moe_maker")
        return real_import(name, *args, **kw)

    monkeypatch.setattr(builtins, "__import__", blocked)
    assert stagehand.available() is False


# -- the CLI -----------------------------------------------------------------

def test_check_reports_whether_the_documented_command_is_what_will_run(
        monkeypatch, capsys):
    monkeypatch.setattr(stagehand.shutil, "which", lambda name: "/bin/ms-moe-maker")
    assert stagehand.main(["--check"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["available"] is True
    assert payload["is_documented_command"] is True


def test_check_flags_the_module_fallback_as_not_the_documented_path(
        monkeypatch, capsys):
    """The fallback works, and it silently voids the 'every run tests the
    hand-run path' guarantee. So it is reported, not hidden."""
    monkeypatch.setattr(stagehand.shutil, "which", lambda name: None)
    monkeypatch.setitem(sys.modules, "ms_moe_maker", type(sys)("ms_moe_maker"))
    assert stagehand.main(["--check"]) == 0
    assert json.loads(capsys.readouterr().out)["is_documented_command"] is False


def test_a_missing_recipe_is_a_clean_exit_not_a_traceback(tmp_path, capsys):
    assert stagehand.main([str(tmp_path / "nope.yaml")]) == 2
    assert "recipe not found" in capsys.readouterr().err


# -- it actually forks -------------------------------------------------------

def test_run_forks_and_relays_the_exit_code(tmp_path, monkeypatch):
    """End to end against a stand-in for the ms-moe-maker CLI."""
    fake = tmp_path / "fake-ms-moe-maker"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import sys, json\n"
        "assert sys.argv[1] == 'build', sys.argv\n"
        "assert sys.argv[3] == '--json', sys.argv\n"
        "print(json.dumps({'event': 'done', 'ok': True}))\n"
        "sys.exit(7)\n", encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setattr(stagehand.shutil, "which", lambda name: str(fake))
    recipe = tmp_path / "r.yaml"
    recipe.write_text("schema_version: 1\n", encoding="utf-8")
    assert stagehand.run(recipe, echo=False, cwd=tmp_path) == 7


# -- the detached launch -----------------------------------------------------
#
# THE BUG THESE EXIST FOR, so nobody re-introduces it while tidying:
#
# run_detached used to append `--log-file` and `--events-file` to the argv.
# ms-moe-maker has never had either flag, so argparse exited 2 in a few
# milliseconds. stdout and stderr were DEVNULL, so the message went nowhere.
# Nothing called wait() or poll(), so nothing noticed. run_detached returned a
# pid and Backstage answered 200. You launched a build, it died instantly, and
# the dashboard said it was running.
#
# Every test below checks the OTHER side of that boundary from
# test_argv_is_the_documented_command: not "did we build the string we meant
# to" but "did a process actually survive, and did the files appear".


def _fake_builder(path: Path, body: str) -> Path:
    path.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return path


def test_the_detached_argv_invents_no_flags(tmp_path, monkeypatch):
    """No flag reaches the builder that the builder does not have.

    The narrow version of tests/test_cli_contract.py, kept here because it is
    the exact shape of the original bug: extra argv appearing because a caller
    passed a path.
    """
    seen = {}

    class FakeProc:
        pid = 4242

        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired("x", timeout)

    def fake_popen(argv, **kw):
        seen["argv"] = argv
        return FakeProc()

    monkeypatch.setattr(stagehand.shutil, "which", lambda name: "/bin/ms-moe-maker")
    monkeypatch.setattr(stagehand.subprocess, "Popen", fake_popen)
    stagehand.run_detached(tmp_path / "r.yaml", cwd=tmp_path,
                           log_file=tmp_path / "a.log",
                           events_file=tmp_path / "a.jsonl",
                           extra=["--dryrun"])
    argv = seen["argv"]
    assert "--log-file" not in argv and "--events-file" not in argv, (
        f"the file paths are back in the argv: {argv}. They are streams, not "
        f"flags - the builder has neither option and exits 2 on them.")
    assert argv[-1] == "--dryrun"


def test_a_detached_run_writes_the_two_streams_into_the_two_files(
        tmp_path, monkeypatch):
    """stdout -> events, stderr -> log, which is what `--json` already means.

    File handles rather than pipes: the old comment was right that a pipe
    nobody reads blocks the child forever, and wrong that DEVNULL was the
    answer. A file cannot block and it keeps the evidence.
    """
    fake = _fake_builder(tmp_path / "fake-ms-moe-maker",
                         "import sys, time\n"
                         "print('{\"event\": \"start\"}', flush=True)\n"
                         "print('prose about the run', file=sys.stderr, flush=True)\n"
                         "time.sleep(30)\n")
    monkeypatch.setattr(stagehand.shutil, "which", lambda name: str(fake))
    marker = stagehand.run_detached(
        tmp_path / "r.yaml", cwd=tmp_path,
        log_file=tmp_path / "run.log", events_file=tmp_path / "run.jsonl")
    try:
        deadline = time.time() + 10
        while time.time() < deadline and not (
                (tmp_path / "run.jsonl").read_text()
                and (tmp_path / "run.log").read_text()):
            time.sleep(0.05)
        assert json.loads((tmp_path / "run.jsonl").read_text())["event"] == "start"
        assert "prose about the run" in (tmp_path / "run.log").read_text()
    finally:
        os.kill(marker["pid"], signal.SIGKILL)


def test_a_live_launch_writes_a_marker_that_says_started(tmp_path, monkeypatch):
    fake = _fake_builder(tmp_path / "fake-ms-moe-maker",
                         "import time\ntime.sleep(30)\n")
    monkeypatch.setattr(stagehand.shutil, "which", lambda name: str(fake))
    marker = stagehand.run_detached(
        tmp_path / "r.yaml", cwd=tmp_path,
        log_file=tmp_path / "run.log", events_file=tmp_path / "run.jsonl")
    try:
        on_disk = json.loads(
            (tmp_path / stagehand.DETACHED_MARKER).read_text(encoding="utf-8"))
        assert on_disk == marker
        # The whole key list, because another package parses this file and a
        # key that quietly stops being written is a reader rendering a blank.
        assert set(on_disk) == set(stagehand.MARKER_KEYS)
        assert on_disk["launch"] == stagehand.LAUNCH_STARTED
        assert on_disk["exit_code"] is None
        assert on_disk["schema_version"] == stagehand.MARKER_SCHEMA_VERSION
        assert on_disk["events_file"].endswith("run.jsonl")
    finally:
        os.kill(marker["pid"], signal.SIGKILL)


def test_a_build_that_dies_on_arrival_is_not_reported_as_started(
        tmp_path, monkeypatch):
    """THE BUG, end to end, against a child that behaves exactly as the real
    one did: complain on stderr and exit 2 immediately."""
    fake = _fake_builder(tmp_path / "fake-ms-moe-maker",
                         "import sys\n"
                         "print('error: unrecognized arguments: --log-file',\n"
                         "      file=sys.stderr)\n"
                         "sys.exit(2)\n")
    monkeypatch.setattr(stagehand.shutil, "which", lambda name: str(fake))
    with pytest.raises(stagehand.BuildDiedAtLaunch) as exc:
        stagehand.run_detached(tmp_path / "r.yaml", cwd=tmp_path,
                               log_file=tmp_path / "run.log",
                               events_file=tmp_path / "run.jsonl")
    assert exc.value.exit_code == 2
    assert "unrecognized arguments" in str(exc.value), (
        "the child said what was wrong and we threw it away again")

    # And the marker records the attempt, because "we tried and it died" is a
    # different fact from "nothing was ever launched".
    on_disk = json.loads(
        (tmp_path / stagehand.DETACHED_MARKER).read_text(encoding="utf-8"))
    assert on_disk["launch"] == stagehand.LAUNCH_FAILED
    assert on_disk["exit_code"] == 2
    assert "unrecognized arguments" in on_disk["log_tail"]


def test_a_run_shorter_than_the_settle_window_is_not_called_a_failure(
        tmp_path, monkeypatch):
    """`build --plan` exits 0 in about a third of a second.

    Found against the real builder, after the first version of the liveness
    check turned a working `--plan` into a 500. "Already gone" and "died" are
    not the same fact, and the exit code is the whole difference: argparse
    exits 2 on an unrecognised flag, and no successful build exits non-zero.
    """
    fake = _fake_builder(tmp_path / "fake-ms-moe-maker",
                         "import sys\n"
                         "print('{\"event\": \"plan\"}')\n"
                         "sys.exit(0)\n")
    monkeypatch.setattr(stagehand.shutil, "which", lambda name: str(fake))
    marker = stagehand.run_detached(tmp_path / "r.yaml", cwd=tmp_path,
                                    log_file=tmp_path / "run.log",
                                    events_file=tmp_path / "run.jsonl")
    assert marker["launch"] == stagehand.LAUNCH_FINISHED
    assert marker["exit_code"] == 0
    assert marker["error"] is None
    assert json.loads((tmp_path / "run.jsonl").read_text())["event"] == "plan"


def test_output_files_that_cannot_be_opened_fail_loudly_at_launch(
        tmp_path, monkeypatch):
    """Rather than starting a build nobody will ever be able to watch."""
    fake = _fake_builder(tmp_path / "fake-ms-moe-maker",
                         "import time\ntime.sleep(30)\n")
    monkeypatch.setattr(stagehand.shutil, "which", lambda name: str(fake))
    with pytest.raises(OSError):
        stagehand.run_detached(
            tmp_path / "r.yaml", cwd=tmp_path,
            log_file=tmp_path / "no" / "such" / "dir" / "run.log",
            events_file=tmp_path / "run.jsonl")
