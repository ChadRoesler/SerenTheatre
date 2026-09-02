"""Which ms-moe-maker answers, and the one way it is allowed to be decided.

THE BUG THIS CLOSES. Theatre forks the builder for `describe`, `validate` and
every build. Which binary that was came from `shutil.which` - a property of the
shell the SERVICE happened to be started from. On a box with a released
ms-moe-maker on PATH and a working tree in another venv, Backstage described
one install while the work happened in the other, and the only symptom was a
craft form that looked slightly out of date. Nothing anywhere reported which
binary had replied, so there was nothing to notice.

Two properties are asserted here, and the second is the one with teeth:

  * the resolution order, including that a CONFIGURED-BUT-MISSING install is an
    ERROR and never a quiet fall back to PATH - falling back is exactly the
    behaviour the setting exists to prevent;
  * there is only ONE DOOR. Every fork in the package goes through
    resolve_command, checked by AST, so a sixth call site added later cannot
    grow its own answer.
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

import pytest

from seren_theatre import stagehand
from seren_theatre.config import PipelineConfig, TheatreConfig

PKG = Path(stagehand.__file__).parent


def _venv(root: Path, *, script: bool = True, python: bool = True) -> Path:
    """A venv-shaped directory. Not a real venv - resolution only stats."""
    binned = root / ("Scripts" if os.name == "nt" else "bin")
    binned.mkdir(parents=True, exist_ok=True)
    if script:
        name = stagehand.MS_MOE_COMMAND + (".exe" if os.name == "nt" else "")
        (binned / name).write_text("#!/bin/sh\n", encoding="utf-8")
    if python:
        (binned / ("python.exe" if os.name == "nt" else "python")).write_text(
            "", encoding="utf-8")
    return root


# ── the order ────────────────────────────────────────────────────────────────

def test_an_explicit_command_beats_everything(tmp_path, monkeypatch):
    monkeypatch.setattr(stagehand.shutil, "which",
                        lambda n: "/usr/bin/ms-moe-maker")
    exe = tmp_path / "mine"
    exe.write_text("", encoding="utf-8")
    stagehand.configure(PipelineConfig(command=str(exe)))
    got = stagehand.resolve()
    assert got.argv == [str(exe)]
    assert got.source == "pipeline.command"


def test_a_venv_uses_its_own_console_script(tmp_path):
    v = _venv(tmp_path / "v")
    stagehand.configure(PipelineConfig(venv=str(v)))
    got = stagehand.resolve()
    assert got.argv[0].startswith(str(v))
    assert got.as_dict()["literal"] is True


def test_a_venv_without_a_script_uses_that_venvs_own_python(tmp_path):
    """AND NOT THE SYSTEM PYTHON. An editable install can leave a venv with the
    package and no console script - a real, recoverable state. Falling back to
    the system interpreter would run a DIFFERENT install while reporting that
    the config had been honoured, which is worse than failing."""
    v = _venv(tmp_path / "v", script=False)
    stagehand.configure(PipelineConfig(venv=str(v)))
    got = stagehand.resolve()
    assert got.argv[1:] == ["-m", "ms_moe_maker"]
    assert got.argv[0].startswith(str(v)), "it left the venv it was told to use"
    assert got.argv[0] != sys.executable
    assert got.as_dict()["literal"] is False, (
        "the `-m` form is not the documented command and must say so")


def test_nothing_configured_falls_back_to_path(monkeypatch):
    monkeypatch.setattr(stagehand.shutil, "which",
                        lambda n: "/usr/bin/ms-moe-maker")
    got = stagehand.resolve()
    assert got.argv == ["/usr/bin/ms-moe-maker"]
    assert got.source == "PATH"


# ── the rule with teeth ──────────────────────────────────────────────────────

@pytest.mark.parametrize("cfg_kwargs, wanted", [
    ({"venv": "/definitely/not/here"}, "pipeline.venv"),
    ({"command": "/definitely/not/here/ms-moe-maker"}, "pipeline.command"),
])
def test_a_configured_install_that_is_missing_is_an_error_not_a_fallback(
        cfg_kwargs, wanted, monkeypatch):
    """THE WHOLE POINT OF THE SETTING.

    PATH has a perfectly good ms-moe-maker on it here. Using it would be
    Theatre silently doing the exact thing the config was written to stop, and
    the person would see a working craft form describing the wrong box - a
    failure with no symptom at all.
    """
    monkeypatch.setattr(stagehand.shutil, "which",
                        lambda n: "/usr/bin/ms-moe-maker")
    stagehand.configure(PipelineConfig(**cfg_kwargs))
    got = stagehand.resolve()
    assert got.argv == [], "it fell back to PATH after being told where to look"
    assert wanted in got.error
    with pytest.raises(stagehand.StagehandUnavailable):
        stagehand.resolve_command()


def test_an_unresolvable_command_names_the_venv_setting(monkeypatch):
    """The error a person meets first has to contain the fix.

    "ms-moe-maker is not installed" is wrong and unhelpful on the box where it
    IS installed, in another venv - which is the common case, not the exotic
    one."""
    monkeypatch.setattr(stagehand.shutil, "which", lambda n: None)
    monkeypatch.setitem(sys.modules, "ms_moe_maker", None)
    monkeypatch.delitem(sys.modules, "ms_moe_maker")
    got = stagehand.resolve()
    if got.argv:
        pytest.skip("ms_moe_maker is importable here, so this path is not taken")
    assert "pipeline:" in got.error and "venv:" in got.error


def test_configure_none_resets(tmp_path, monkeypatch):
    monkeypatch.setattr(stagehand.shutil, "which", lambda n: "/usr/bin/x")
    stagehand.configure(PipelineConfig(venv=str(_venv(tmp_path / "v"))))
    assert stagehand.resolve().source.startswith("pipeline.venv")
    stagehand.configure(None)
    assert stagehand.resolve().source == "PATH"


# ── one door ─────────────────────────────────────────────────────────────────

def _functions_that_fork(path: Path):
    """Functions in a module that spawn a process, docstrings stripped.

    Docstrings go for the same reason test_eval_memory strips them: several of
    these functions EXPLAIN the forking they do, and a guard that matches its
    own explanation is not a guard.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            node.value.value = ""
    out = {}
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        forks = names = False
        for node in ast.walk(fn):
            if isinstance(node, ast.Attribute) and node.attr in (
                    "run", "Popen", "check_output", "call"):
                base = node.value
                if isinstance(base, ast.Name) and base.id == "subprocess":
                    forks = True
            if isinstance(node, ast.Name) and node.id in (
                    "resolve_command", "resolve", "build_argv"):
                names = True
            if isinstance(node, ast.Attribute) and node.attr in (
                    "resolve_command", "resolve", "build_argv"):
                names = True
        if forks:
            out[fn.name] = names
    return out


def test_every_fork_goes_through_the_one_resolver():
    """A sixth call site must not grow its own answer.

    This module's own docstring says a second answer to one question is how a
    dashboard starts disagreeing with itself. `describe` reporting install A
    while the build runs install B is that failure exactly - and it presents as
    a craft form that is merely a little stale, which nobody investigates.
    """
    offenders = []
    for path in sorted(PKG.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for fn, resolved in _functions_that_fork(path).items():
            if not resolved:
                offenders.append(f"{path.name}:{fn}")
    assert not offenders, (
        f"these spawn a process without going through resolve_command: "
        f"{offenders}. Every fork has to reach the same install, or Backstage "
        f"will describe one ms-moe-maker while the build runs another.")


def test_the_config_carries_it_end_to_end(tmp_path):
    """A yaml block that never reaches the resolver is a setting in name only."""
    v = _venv(tmp_path / "v")
    cfg = TheatreConfig(pipeline={"venv": str(v)})
    assert isinstance(cfg.pipeline, PipelineConfig), "the dict was not coerced"
    stagehand.configure(cfg.pipeline)
    assert stagehand.resolve().argv[0].startswith(str(v))


def test_the_api_root_says_which_install_answered(tmp_path):
    """Unanswerable from outside the process before this.

    Two installs, one stale, and no way to tell which one replied. `source` is
    reported beside the command so a config that is being IGNORED cannot read
    as one that was honoured.
    """
    from fastapi.testclient import TestClient
    from seren_theatre.app import create_app

    v = _venv(tmp_path / "v")
    cfg = TheatreConfig(pipeline={"venv": str(v)})
    client = TestClient(create_app(cfg))
    block = client.get("/").json()["pipeline"]
    assert block["command"].startswith(str(v))
    assert block["source"].startswith("pipeline.venv")
    assert block["error"] == ""


def test_a_bare_command_name_may_still_be_found_on_path(monkeypatch):
    """`command: ms-moe-maker` means "the one on PATH" and searching is right.

    The pair to the test above, and the reason that one had to be careful: the
    rule is not "never search", it is "never search when you were handed a
    PATH". A name with no separator in it is not a path, it is a name.
    """
    monkeypatch.setattr(stagehand.shutil, "which",
                        lambda n: "/usr/bin/" + n)
    stagehand.configure(PipelineConfig(command="ms-moe-maker-nightly"))
    got = stagehand.resolve()
    assert got.argv == ["/usr/bin/ms-moe-maker-nightly"]
    assert "PATH" in got.source
