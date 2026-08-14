"""Config resolution, precedence, and the lenient-parse promise.

Every test here isolates HOME to a tmp_path first. ~/seren-theatre/ is a real
candidate path, so without that a developer with Theatre actually installed
would get different results than CI - and a test whose outcome depends on the
machine it runs on is worse than no test.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from seren_theatre.config import TheatreConfig, load_config


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """No stray ~/seren-theatre/seren-theatre.yaml leaking into a test."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    for var in ("SEREN_THEATRE_CONFIG", "SEREN_THEATRE_HOST",
                "SEREN_THEATRE_PORT", "SEREN_THEATRE_STAGE"):
        monkeypatch.delenv(var, raising=False)
    return home

DEFAULT_PORT=7427

def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# -- defaults ----------------------------------------------------------------

def test_no_config_anywhere_gives_working_defaults():
    cfg = load_config()
    assert cfg.port == DEFAULT_PORT
    assert cfg.host == "127.0.0.1"
    assert cfg.stages == []


def test_host_default_is_loopback_not_the_lan():
    # Not a style preference. Training logs carry absolute paths, hostnames and
    # the occasional corpus snippet, so binding 0.0.0.0 by default would be
    # publishing those by accident.
    assert TheatreConfig().host == "127.0.0.1"


# -- precedence --------------------------------------------------------------

def test_explicit_path_beats_env_beats_home(tmp_path, monkeypatch):
    home_cfg = Path.home() / "seren-theatre" / "seren-theatre.yaml"
    _write(home_cfg, "server:\n  port: 1111\n")
    env_cfg = _write(tmp_path / "env.yaml", "server:\n  port: 2222\n")
    explicit = _write(tmp_path / "explicit.yaml", "server:\n  port: 3333\n")

    assert load_config().port == 1111
    monkeypatch.setenv("SEREN_THEATRE_CONFIG", str(env_cfg))
    assert load_config().port == 2222
    assert load_config(str(explicit)).port == 3333


def test_env_vars_beat_the_file(tmp_path, monkeypatch):
    cfg_path = _write(tmp_path / "c.yaml", "server:\n  host: 10.0.0.5\n  port: 4444\n")
    monkeypatch.setenv("SEREN_THEATRE_HOST", "0.0.0.0")
    monkeypatch.setenv("SEREN_THEATRE_PORT", "5555")
    cfg = load_config(str(cfg_path))
    assert (cfg.host, cfg.port) == ("0.0.0.0", 5555)


def test_a_missing_explicit_path_falls_through_rather_than_dying(tmp_path):
    # Lenient: a --config pointing at nothing is not fatal.
    cfg = load_config(str(tmp_path / "does-not-exist.yaml"))
    assert cfg.port == DEFAULT_PORT


# -- lenient parse -----------------------------------------------------------

def test_malformed_yaml_falls_back_to_defaults(tmp_path):
    bad = _write(tmp_path / "bad.yaml", "server:\n  port: [unclosed\n")
    assert load_config(str(bad)).port == DEFAULT_PORT


def test_a_top_level_list_is_not_a_config(tmp_path):
    bad = _write(tmp_path / "list.yaml", "- server\n- port\n")
    assert load_config(str(bad)).port == DEFAULT_PORT


def test_an_empty_file_is_defaults_not_a_crash(tmp_path):
    empty = _write(tmp_path / "empty.yaml", "")
    assert load_config(str(empty)).port == DEFAULT_PORT


def test_a_non_integer_port_env_keeps_the_configured_one(tmp_path, monkeypatch):
    cfg_path = _write(tmp_path / "c.yaml", "server:\n  port: 6666\n")
    monkeypatch.setenv("SEREN_THEATRE_PORT", "not-a-number")
    assert load_config(str(cfg_path)).port == 6666


# -- stages ------------------------------------------------------------------

def test_stage_env_var_adds_a_stage_with_no_file_at_all(tmp_path, monkeypatch):
    lab = tmp_path / "fraunkensteinLab"
    lab.mkdir()
    monkeypatch.setenv("SEREN_THEATRE_STAGE", str(lab))
    cfg = load_config()
    assert len(cfg.stages) == 1
    assert cfg.stages[0].name == "fraunkensteinLab"
    assert cfg.stages[0].resolved() == lab.resolve()


def test_stage_env_var_appends_to_configured_stages(tmp_path, monkeypatch):
    cfg_path = _write(
        tmp_path / "c.yaml",
        "stages:\n  - name: FromFile\n    path: /tmp/from-file\n",
    )
    monkeypatch.setenv("SEREN_THEATRE_STAGE", str(tmp_path / "extra"))
    cfg = load_config(str(cfg_path))
    assert [s.name for s in cfg.stages] == ["FromFile", "extra"]


def test_stage_defaults_match_the_ladder_layout(tmp_path, monkeypatch):
    monkeypatch.setenv("SEREN_THEATRE_STAGE", str(tmp_path / "lab"))
    stage = load_config().stages[0]
    assert stage.logs == ["*.log"]
    assert "dryrun_*" in stage.rungs and "*_agent_*" in stage.rungs


def test_a_tilde_path_expands(tmp_path):
    from seren_theatre.config import StageConfig

    assert StageConfig(name="x", path="~/runs").resolved() == \
        (Path.home() / "runs").resolve()


# -- the tail-read promise ---------------------------------------------------

def test_tail_bytes_is_bounded_by_default():
    # The dashboard must never be the reason the box is busy. If someone
    # defaults this to "read the whole file", polling a 9-hour training log
    # every 5 seconds becomes the most expensive thing on the machine.
    cfg = TheatreConfig()
    assert 0 < cfg.tail_bytes <= 1_048_576
    assert cfg.refresh_seconds >= 1
