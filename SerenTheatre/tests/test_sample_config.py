"""The shipped sample has to still be true.

A sample config is documentation that looks like code, which is the worst kind
to let rot: it is copied verbatim by people who have no reason to doubt it, and
a stale `# Default: 5` is a lie that reads exactly like a fact. Starwright will
eventually lay this file down on machines nobody in this repo has seen.

Three things get pinned, and the third is the one with teeth:

  * every key `load_config` reads appears in the file. A setting nobody
    documents is a setting nobody knows about, which is the same as not having
    shipped it.
  * every SEREN_THEATRE_* override appears too. Env vars BEAT the file, so an
    undocumented one is a way for a box to behave unlike its own config with
    nothing on disk to explain why.
  * LOADING THE SAMPLE PRODUCES THE SHIPPED DEFAULTS. Not "it parses" - that
    the values it hands you are the values the code would have chosen anyway.
    Change a default in config.py and this fails until the sample says so.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from seren_theatre import config as cfgmod
from seren_theatre.config import StageConfig, TheatreConfig, load_config

SAMPLE = Path(cfgmod.__file__).resolve().parent.parent / "seren-theatre.yaml.sample"
pytestmark = pytest.mark.skipif(
    not SAMPLE.is_file(),
    reason="installed copy, not a source checkout - the sample ships in the "
           "repo rather than in the wheel")


@pytest.fixture(scope="module")
def text() -> str:
    return SAMPLE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def loaded():
    return load_config(str(SAMPLE))


def test_the_sample_is_valid_yaml(loaded):
    assert loaded.stages, (
        "the sample must carry a stages entry - it is the one setting with no "
        "sensible default, because only the operator knows where they build")


def _keys_the_loader_reads(source: str):
    """Every config key config.py pulls out of a mapping, nested ones included.

    THIS USED TO BE `re.findall(r'data.get("([a-z_]+)"')` AND SAW NINE KEYS.
    Nine, out of twenty-two. `data` is the local name inside `load_config`
    alone; every nested section - ArchiveConfig, PipelineConfig, UpdatesConfig,
    StageConfig - reads its own keys off a local called `d`, and the guard
    could not see one of them. So `blobs`, `dsn`, `command`, `venv`, `runs`,
    `logs`, `index_url` and the rest could go undocumented and this test, whose
    entire name is that they cannot, stayed green.

    A test that reports less than it knows, which is the failure this codebase
    keeps finding in its own instruments. The variable name was never the
    thing that mattered; the `.get("literal")` is. So it is parsed rather than
    grepped, and a new sub-config gets covered by existing here rather than by
    somebody remembering to widen a regex.
    """
    keys = set()
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get" and node.args):
            continue
        first = node.args[0]
        if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
            continue
        # Environment overrides are the NEXT test's job, and they are read off
        # a mapping too. Excluded by shape rather than by receiver name, so a
        # rename of the local cannot switch the exclusion on and off.
        if first.value.upper() == first.value:
            continue
        keys.add(first.value)
    return sorted(keys)


def test_every_setting_the_loader_reads_is_documented(text):
    source = Path(cfgmod.__file__).read_text(encoding="utf-8")
    keys = _keys_the_loader_reads(source)
    assert len(keys) > 15, (
        f"only {len(keys)} config keys were found in config.py, which means "
        f"this guard has stopped seeing most of them again - it went blind "
        f"once already and nothing said so")
    missing = [k for k in keys if k not in text]
    assert not missing, (
        f"config.py reads {missing} and the sample never mentions them. A "
        f"setting nobody documents is one nobody knows about.")


def test_every_environment_override_is_documented(text):
    source = Path(cfgmod.__file__).read_text(encoding="utf-8")
    envs = sorted(set(re.findall(r"SEREN_THEATRE_[A-Z_]+", source)))
    missing = [e for e in envs if e not in text]
    assert not missing, (
        f"{missing} override this file and are undocumented. An env var that "
        f"beats the config with nothing on disk to explain it is how a box "
        f"behaves unlike its own configuration.")


def test_loading_the_sample_gives_the_shipped_defaults(loaded):
    """THE ONE WITH TEETH. Every `# Default: x` in that file is a claim."""
    default = TheatreConfig()
    assert loaded.server.host == "127.0.0.1", (
        "the sample must ship loopback: training logs carry paths, hostnames "
        "and corpus snippets, and with [stagehand] this service starts builds")
    assert loaded.server.port == 7427
    assert loaded.server.resolve_bearer() == "", "the sample must ship open-but-loopback"
    assert loaded.tail_bytes == default.tail_bytes
    assert loaded.refresh_seconds == default.refresh_seconds
    assert loaded.recipes_dir() == default.recipes_dir()
    assert loaded.archive.enabled is default.archive.enabled
    assert loaded.archive.resolved_dsn() == default.archive.resolved_dsn()
    assert loaded.archive.blobs_dir() == default.archive.blobs_dir()
    assert (loaded.pipeline.venv, loaded.pipeline.command) == ("", []), (
        "pipeline must ship UNSET - a sample that names somebody else's venv "
        "raises on every box but the one it was written on")


def test_the_samples_run_globs_are_the_shipped_defaults(loaded):
    """Because the sample spells them out, and a spelled-out list OVERRIDES
    the default rather than extending it. If the two drift, everyone who
    copied the sample silently stops matching directories the default would
    have found."""
    assert loaded.stages[0].runs == StageConfig(name="x", path="/x").runs


def test_the_sample_documents_the_archive_containment_rule(text):
    """Theatre REFUSES at startup if the archive resolves inside a watched
    directory. A rule enforced by an exception has to be written down where
    somebody reads before they hit it."""
    assert "OUTSIDE" in text and "archive" in text


def test_the_sample_warns_about_widening_the_bind(text):
    """With [stagehand] this service starts builds and accepts uploaded
    recipes, and a recipe can name an eval.script the harness executes. That
    is fine and it is not fine SILENTLY."""
    for phrase in ("eval.script", "bearer_token"):
        assert phrase in text, (
            f"the sample never mentions {phrase!r}; somebody widening the bind "
            f"deserves to meet both facts in the file they are editing")
