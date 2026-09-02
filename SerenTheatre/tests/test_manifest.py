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
    that is what lets ms-moe-maker add a field without a lockstep upgrade."""
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


# -- the playbill fields, which this reader used to throw away ---------------
#
# `resolved`, `defaults_files` and `build_id` are stamped by the writer and the
# dataclass here simply stopped at `refusals`, so all three were parsed past in
# silence. The writer's comment says additive fields are safe because "unknown
# keys already fall through to `extra`" - true of its own reader, and the thing
# this reader did not have. Both halves are pinned below.

RESOLVED = {"size": "0.5B", "target_steps": 602,
            "expert_names": ["python", "csharp"]}
DEFAULTS = {"/etc/msmoe/defaults.yaml": "9f2a1c0b7d31"}


def test_the_resolved_config_is_carried(tmp_path):
    write(tmp_path, minimal(build_id="abc123def456", resolved=RESOLVED,
                            defaults_files=DEFAULTS))
    m = mf.read(tmp_path)
    assert m.build_id == "abc123def456"
    assert m.resolved == RESOLVED
    assert m.defaults_files == DEFAULTS


def test_absent_playbill_fields_read_as_empty_not_as_missing(tmp_path):
    """An older writer stamps none of them, and that is not an error - it is a
    run with no playbill to show."""
    write(tmp_path, minimal())
    m = mf.read(tmp_path)
    assert m.build_id == ""
    assert m.resolved == {}
    assert m.defaults_files == {}


@pytest.mark.parametrize("bad", ["a string", 12, ["a", "list"], None, True])
def test_a_playbill_field_of_the_wrong_type_reads_as_empty(tmp_path, bad):
    """Lenient in the same direction as the rest of this module. A `resolved`
    that is a string is a writer bug, and an empty playbill is a far smaller
    failure than refusing to show the run at all."""
    write(tmp_path, minimal(resolved=bad, defaults_files=bad, build_id=bad))
    m = mf.read(tmp_path)
    assert m.resolved == {}
    assert m.defaults_files == {}


def test_defaults_files_are_coerced_to_strings_both_sides(tmp_path):
    write(tmp_path, minimal(defaults_files={"/etc/d.yaml": 12345}))
    assert mf.read(tmp_path).defaults_files == {"/etc/d.yaml": "12345"}


def test_an_unknown_key_lands_in_extra_rather_than_vanishing(tmp_path):
    """The structural half of the fix. Three fields have now been dropped one
    at a time; this is what stops the fourth."""
    write(tmp_path, minimal(gpu_hours=12.5, cluster="nano8gb"))
    m = mf.read(tmp_path)
    assert m.extra == {"gpu_hours": 12.5, "cluster": "nano8gb"}


def test_a_key_this_reader_does_know_never_lands_in_extra(tmp_path):
    """Otherwise `extra` would quietly duplicate the whole manifest, and a
    duplicate is how two readings of one field start to disagree."""
    write(tmp_path, minimal(build_id="abc", resolved=RESOLVED))
    assert mf.read(tmp_path).extra == {}


# -- the glossary that makes the playbill readable ---------------------------
#
# `knobs` explains `resolved` field by field, and it is stamped into the
# manifest rather than looked up, because a base seren-theatre install has no
# ms-moe-maker to ask and an archived run still has to explain itself. This
# reader's whole job is to carry it; WHICH entries are usable is one decision
# in the viewer (knobFor, pinned in tests/viewer_probe.js), not two here.

KNOBS = {
    "target_steps": {"summary": "How many optimiser steps each specialist runs.",
                     "derived_from": None},
    "collect_token_target": {"summary": "How many raw tokens to collect.",
                             "derived_from": "target_steps x tokens_per_step"},
}


def test_the_knob_glossary_is_carried(tmp_path):
    write(tmp_path, minimal(resolved=RESOLVED, knobs=KNOBS))
    assert mf.read(tmp_path).knobs == KNOBS


def test_an_absent_glossary_reads_as_empty_not_as_missing(tmp_path):
    """Every writer older than the glossary stamps none of it, and that is a
    run whose playbill carries no explanations - not an error."""
    write(tmp_path, minimal(resolved=RESOLVED))
    assert mf.read(tmp_path).knobs == {}


@pytest.mark.parametrize("bad", ["a string", 12, ["a", "list"], None, True])
def test_a_glossary_of_the_wrong_type_reads_as_empty(tmp_path, bad):
    """Lenient in the same direction as everything else here. A malformed
    glossary costs the reader some question marks; raising would cost them the
    run."""
    write(tmp_path, minimal(resolved=RESOLVED, knobs=bad))
    assert mf.read(tmp_path).knobs == {}


def test_an_entry_with_no_summary_is_carried_rather_than_dropped(tmp_path):
    """The contract says an entry with no summary renders no affordance - and
    RENDERS is the operative word. Dropping it here would make a writer's
    coverage gap invisible on /api/state as well as on screen, which is the
    silence this pair of packages keeps having to fix."""
    ragged = {"a": {"summary": "", "derived_from": "x + y"},
              "b": {"derived_from": "x + y"},
              "c": "not an object at all"}
    write(tmp_path, minimal(resolved=RESOLVED, knobs=ragged))
    assert mf.read(tmp_path).knobs == ragged


def test_the_glossary_never_lands_in_extra(tmp_path):
    """A key this reader knows must not be duplicated into the catch-all;
    a duplicate is how two readings of one field start to disagree."""
    write(tmp_path, minimal(resolved=RESOLVED, knobs=KNOBS))
    assert mf.read(tmp_path).extra == {}


def test_as_dict_serves_the_glossary(tmp_path):
    """Carried is not the same as served - a field parsed into the dataclass
    and left out of as_dict is invisible to the room and to anything scripting
    /api/state, which is the same silence one layer further on."""
    write(tmp_path, minimal(resolved=RESOLVED, knobs=KNOBS))
    assert mf.as_dict(mf.read(tmp_path))["knobs"] == KNOBS


def test_as_dict_serves_the_playbill_and_the_catch_all(tmp_path):
    write(tmp_path, minimal(build_id="abc123def456", resolved=RESOLVED,
                            quantiser="q4_k_m"))
    d = mf.as_dict(mf.read(tmp_path))
    assert d["build_id"] == "abc123def456"
    assert d["resolved"] == RESOLVED
    assert d["defaults_files"] == {}
    # Unrendered, deliberately present: visible on /api/state the day it starts
    # being written, rather than discovered a release later.
    assert d["extra"] == {"quantiser": "q4_k_m"}
