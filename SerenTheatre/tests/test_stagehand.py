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
import subprocess
import sys
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
    """`ms-moe build <recipe> --json`, exactly.

    This string is the guarantee. The moment stagehand runs something else, an
    automated build stops exercising the path the README documents, and the
    documented path is the one that rots because nothing runs it.
    """
    monkeypatch.setattr(stagehand.shutil, "which",
                        lambda name: f"/usr/local/bin/{name}")
    recipe = tmp_path / "recipe.yaml"
    argv = stagehand.build_argv(recipe)
    assert argv[0].endswith("ms-moe")
    assert argv[1] == "build"
    assert argv[2] == str(recipe)
    assert argv[-1] == "--json"


def test_extra_flags_pass_through_after_the_recipe(tmp_path, monkeypatch):
    monkeypatch.setattr(stagehand.shutil, "which", lambda name: "/bin/ms-moe")
    argv = stagehand.build_argv(tmp_path / "r.yaml",
                                extra=["--dryrun", "--allow-refusals"])
    assert argv[-2:] == ["--dryrun", "--allow-refusals"]


def test_the_console_script_is_preferred_over_the_module(monkeypatch):
    monkeypatch.setattr(stagehand.shutil, "which", lambda name: "/bin/ms-moe")
    assert stagehand.resolve_command() == ["/bin/ms-moe"]


def test_the_module_fallback_is_used_only_when_the_script_is_missing(monkeypatch):
    monkeypatch.setattr(stagehand.shutil, "which", lambda name: None)
    monkeypatch.setitem(sys.modules, "ms_moe", type(sys)("ms_moe"))
    assert stagehand.resolve_command() == [sys.executable, "-m", "ms_moe"]


# -- honest failure ----------------------------------------------------------

def test_a_missing_ms_moe_names_the_extra_rather_than_exploding(monkeypatch):
    """A missing optional dependency should be one sentence naming the extra,
    not a FileNotFoundError from subprocess three frames down."""
    monkeypatch.setattr(stagehand.shutil, "which", lambda name: None)
    monkeypatch.delitem(sys.modules, "ms_moe", raising=False)

    import builtins
    real_import = builtins.__import__

    def blocked(name, *args, **kw):
        if name == "ms_moe":
            raise ImportError("no ms_moe")
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
        if name == "ms_moe":
            raise ImportError("no ms_moe")
        return real_import(name, *args, **kw)

    monkeypatch.setattr(builtins, "__import__", blocked)
    assert stagehand.available() is False


# -- the CLI -----------------------------------------------------------------

def test_check_reports_whether_the_documented_command_is_what_will_run(
        monkeypatch, capsys):
    monkeypatch.setattr(stagehand.shutil, "which", lambda name: "/bin/ms-moe")
    assert stagehand.main(["--check"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["available"] is True
    assert payload["is_documented_command"] is True


def test_check_flags_the_module_fallback_as_not_the_documented_path(
        monkeypatch, capsys):
    """The fallback works, and it silently voids the 'every run tests the
    hand-run path' guarantee. So it is reported, not hidden."""
    monkeypatch.setattr(stagehand.shutil, "which", lambda name: None)
    monkeypatch.setitem(sys.modules, "ms_moe", type(sys)("ms_moe"))
    assert stagehand.main(["--check"]) == 0
    assert json.loads(capsys.readouterr().out)["is_documented_command"] is False


def test_a_missing_recipe_is_a_clean_exit_not_a_traceback(tmp_path, capsys):
    assert stagehand.main([str(tmp_path / "nope.yaml")]) == 2
    assert "recipe not found" in capsys.readouterr().err


# -- it actually forks -------------------------------------------------------

def test_run_forks_and_relays_the_exit_code(tmp_path, monkeypatch):
    """End to end against a stand-in for the ms-moe CLI."""
    fake = tmp_path / "fake-ms-moe"
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
