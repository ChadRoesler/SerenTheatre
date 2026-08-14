"""Reading a run manifest, including every way one can be wrong.

The distinctions being tested are the whole value of the file. A viewer that
collapses "no manifest", "broken manifest" and "manifest from the future" into
one shrug will show you a plausible page in all three cases, and two of them
are situations you needed to know about.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from seren_theatre import manifest as mf


def write(run_dir: Path, payload) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / mf.MANIFEST_NAME
    path.write_text(
        payload if isinstance(payload, str) else json.dumps(payload),
        encoding="utf-8")
    return path


def minimal(**over):
    base = {
        "schema_version": 1,
        "recipe_id": "abc123",
        "name": "msmoe-coder-5x-dryrun",
        "size": "0.5B",
        "base": "Qwen/Qwen2.5-Coder-0.5B",
        "experts": ["python", "csharp"],
        "started": 1000.0,
        "updated": 1000.0,
        "finished": None,
        "ok": None,
        "refusals": [],
        "stages": [
            {"id": "preflight", "label": "Preflight", "status": "done",
             "started": 1000.0, "ended": 1001.0},
            {"id": "finetune.python", "label": "Fine-tune python",
             "status": "running", "started": 1001.0},
            {"id": "stitch", "label": "Stitch", "status": "pending"},
        ],
    }
    base.update(over)
    return base


# -- the three outcomes ------------------------------------------------------

def test_no_manifest_is_none_not_an_error(tmp_path):
    """An uninstrumented directory is a normal, supported thing to watch."""
    assert mf.read(tmp_path) is None


def test_corrupt_json_is_surfaced_never_swallowed(tmp_path):
    write(tmp_path, "{not json at all")
    with pytest.raises(mf.UnreadableManifest):
        mf.read(tmp_path)


def test_a_future_schema_refuses_rather_than_guesses(tmp_path):
    write(tmp_path, minimal(schema_version=mf.SCHEMA_VERSION + 1))
    with pytest.raises(mf.UnreadableManifest) as exc:
        mf.read(tmp_path)
    assert "newer than this viewer" in str(exc.value)


def test_a_list_at_top_level_is_not_a_manifest(tmp_path):
    write(tmp_path, [1, 2, 3])
    with pytest.raises(mf.UnreadableManifest):
        mf.read(tmp_path)


def test_a_good_manifest_reads(tmp_path):
    write(tmp_path, minimal())
    m = mf.read(tmp_path)
    assert m is not None
    assert m.name == "msmoe-coder-5x-dryrun"
    assert [s.id for s in m.stages] == ["preflight", "finetune.python", "stitch"]
    assert m.running.id == "finetune.python"
    assert m.done_count == 1


# -- leniency where it is safe ----------------------------------------------

def test_one_malformed_stage_does_not_sink_the_run(tmp_path):
    payload = minimal()
    payload["stages"].append({"label": "no id here"})
    payload["stages"].append("not even an object")
    write(tmp_path, payload)
    m = mf.read(tmp_path)
    assert len(m.stages) == 3, "the good stages should still have been read"


def test_unknown_keys_are_ignored_not_fatal(tmp_path):
    """Additive fields from a newer writer must not break an older reader -
    that is what lets ms-moe add a field without a lockstep upgrade."""
    write(tmp_path, minimal(gpu_hours=12.5, cluster="nano8gb"))
    assert mf.read(tmp_path) is not None


def test_an_unrecognised_status_is_reported_as_itself(tmp_path):
    """Never bucketed into 'pending'. The viewer paints it hollow instead."""
    payload = minimal()
    payload["stages"][2]["status"] = "quantising"
    write(tmp_path, payload)
    stage = mf.read(tmp_path).stages[2]
    assert stage.status == "quantising"
    assert stage.known_status is False


# -- staleness: the killed-process case --------------------------------------

def test_a_quiet_running_manifest_goes_stale(tmp_path):
    now = time.time()
    write(tmp_path, minimal(updated=now - (mf.STALE_AFTER_SECONDS + 60)))
    m = mf.read(tmp_path)
    assert m.stale() is True
    assert m.state == "stalled"


def test_a_recently_updated_running_manifest_is_not_stale(tmp_path):
    write(tmp_path, minimal(updated=time.time()))
    m = mf.read(tmp_path)
    assert m.stale() is False
    assert m.state == "running"


def test_a_finished_manifest_is_never_stale_however_old(tmp_path):
    """Finished long ago is not the same as died long ago, and the difference
    is the whole reason `finished` exists as a separate field."""
    write(tmp_path, minimal(updated=1.0, finished=2.0, ok=True,
                            stages=[{"id": "preflight", "label": "P",
                                     "status": "done"}]))
    m = mf.read(tmp_path)
    assert m.stale() is False
    assert m.state == "finished"


def test_a_failed_stage_makes_the_run_failed(tmp_path):
    payload = minimal()
    payload["stages"][1]["status"] = "failed"
    write(tmp_path, payload)
    assert mf.read(tmp_path).state == "failed"


# -- the flattening the API serves ------------------------------------------

def test_as_dict_carries_the_derived_fields_the_viewer_paints(tmp_path):
    write(tmp_path, minimal(updated=time.time()))
    d = mf.as_dict(mf.read(tmp_path))
    # Derived server-side ON PURPOSE: two implementations of "is this run
    # dead" would eventually disagree, and they would disagree on screen.
    for key in ("state", "stale", "done_count", "stage_count"):
        assert key in d
    assert all("known_status" in s for s in d["stages"])


# -- what counts as a rung ---------------------------------------------------

def test_a_corpus_root_is_not_a_rung(tmp_path):
    """`dryrun_*` matches `dryrun_data` - the shared corpus root - so Theatre
    rendered it as an empty rung reading "Nothing built here yet", a true
    sentence about a directory that will never have anything built in it.

    Note the asymmetry that hid it: the non-dryrun glob is `*_agent_*`, which
    `fraunkenstein_data` escapes. It only ever appeared in DRYRUN mode - the
    mode used for every shakedown and never for a real rung.
    """
    from seren_theatre.sources import looks_like_rung

    data = tmp_path / "dryrun_data"
    (data).mkdir()
    (data / "powershell_code.jsonl").write_text("{}", encoding="utf-8")
    assert looks_like_rung(data) is False


def test_a_run_with_only_a_manifest_IS_a_rung(tmp_path):
    """An instrumented run that has produced nothing yet is still a run - the
    runner writes the manifest at preflight, before any artifact exists."""
    from seren_theatre.sources import looks_like_rung

    run = tmp_path / "dryrun_0.5B"
    run.mkdir()
    (run / mf.MANIFEST_NAME).write_text('{"schema_version":1,"stages":[]}',
                                        encoding="utf-8")
    assert looks_like_rung(run) is True


def test_an_uninstrumented_run_with_artifacts_IS_a_rung(tmp_path):
    """No manifest, but rung-shaped contents. Scraping still has to work -
    "a stage is a directory" is why Theatre requires nothing."""
    from seren_theatre.sources import looks_like_rung

    run = tmp_path / "dryrun_0.5B"
    (run / "qwen_coder_python").mkdir(parents=True)
    (run / "qwen_coder_python" / "config.json").write_text("{}", encoding="utf-8")
    assert looks_like_rung(run) is True


def test_a_lone_gguf_is_enough(tmp_path):
    from seren_theatre.sources import looks_like_rung

    run = tmp_path / "dryrun_0.5B"
    run.mkdir()
    (run / "model.gguf").write_text("x", encoding="utf-8")
    assert looks_like_rung(run) is True


def test_an_empty_directory_that_merely_matches_is_not_a_rung(tmp_path):
    from seren_theatre.sources import looks_like_rung

    d = tmp_path / "dryrun_junk"
    d.mkdir()
    assert looks_like_rung(d) is False
