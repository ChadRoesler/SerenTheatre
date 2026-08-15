"""Pin Theatre's eval-sidecar reader against the writer's.

Same bargain as test_manifest_contract.py: the sidecar is a wire format the
writer owns, Theatre implements it independently so a viewer never has to pull
in a training pipeline, and the price of two implementations is drift. This is
where that price gets paid.

Discovery is shared with the manifest contract test (see _writerfinder) and
derives the writer from what SerenTheatre declares it depends on, so a rename
cannot quietly switch these assertions off.

Read via `ast`, never by importing the writer.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import _writerfinder as wf
from seren_theatre import evalrecord as ev

SIDECAR_MODULE = "evalrecord.py"
source = wf.find(SIDECAR_MODULE)

needs_writer = pytest.mark.skipif(
    source is None,
    reason=f"{wf.WRITER_DIST or 'the writer'} is neither installed nor "
           f"checked out nearby; the writing half of the sidecar format isn't "
           f"here to compare against.")


@pytest.fixture(scope="module")
def writer() -> dict:
    return wf.constants(source)


# ── the format ───────────────────────────────────────────────────────────────

@needs_writer
@pytest.mark.parametrize("name", ["SCHEMA_VERSION", "SIDECAR_PREFIX",
                                  "SIDECAR_SUFFIX", "KIND_HEADER",
                                  "KIND_RECORD", "KIND_FOOTER"])
def test_format_constants_match(writer, name):
    theirs, ours = writer.get(name), getattr(ev, name)
    assert theirs == ours, (
        f"{name}: {wf.WRITER_DIST} writes {theirs!r}, seren-theatre reads "
        f"{ours!r}. A viewer looking for the wrong filename reports every "
        f"evaluated run as un-evaluated, silently.")


@needs_writer
@pytest.mark.parametrize("name", ["PASS", "FAIL", "UNMEASURABLE", "ERROR",
                                  "SKIPPED"])
def test_verdict_vocabulary_matches(writer, name):
    assert writer.get(name) == getattr(ev, name), (
        f"verdict {name} differs between writer and reader; the viewer would "
        f"paint a verdict it thinks it does not recognise.")


@needs_writer
def test_no_verdict_exists_that_the_viewer_cannot_name(writer):
    theirs = set(writer.get("VERDICTS") or ())
    ours = set(ev.VERDICTS)

    # THE EMPTY SET IS NOT AGREEMENT. Without this the comparison passes
    # whenever VERDICTS failed to parse - nothing minus anything is nothing.
    # That is precisely how the manifest's status check stayed green for its
    # whole life without ever comparing the two vocabularies.
    assert theirs, (
        f"could not read VERDICTS out of {source}, so the comparison below "
        f"would be an empty set against a full one, which always passes.")

    missing = theirs - ours
    assert not missing, (
        f"{wf.WRITER_DIST} can emit {sorted(missing)} and seren-theatre does "
        f"not know those verdicts.")


# ── the C# rule, enforced on both sides ──────────────────────────────────────
#
# An eval reported C# 0/10 because the harness shelled out to compilers that
# were not installed, and a missing compiler was recorded as ten wrong answers.
# The whole point of this format is that "could not be measured" and "measured
# and failed" never merge. These tests are that rule, written down where a
# well-meaning refactor has to argue with them.

@needs_writer
def test_the_writer_never_counts_unmeasurable_as_a_measurement(writer):
    measured = set(writer.get("MEASURED") or ())
    assert measured, f"could not read MEASURED out of {source}"
    assert writer["UNMEASURABLE"] not in measured, (
        "the writer put UNMEASURABLE into MEASURED, which puts it in the "
        "denominator of every score. That is the C# 0/10 result exactly: a "
        "missing compiler rendered as a model that cannot write the language.")


def test_the_reader_never_counts_unmeasurable_as_a_measurement():
    assert ev.UNMEASURABLE not in ev.MEASURED
    assert ev.ERROR not in ev.MEASURED
    assert ev.SKIPPED not in ev.MEASURED
    assert set(ev.MEASURED) == {ev.PASS, ev.FAIL}


@needs_writer
def test_both_sides_agree_on_what_counts(writer):
    assert set(writer.get("MEASURED") or ()) == set(ev.MEASURED), (
        "writer and reader disagree about which verdicts form the denominator "
        "of a score, so the same file would produce two different numbers.")


def test_an_all_unmeasurable_suite_scores_none_not_zero():
    """0.0 means the model failed everything. None means we checked nothing.

    Collapsing these is the bug this format exists to make impossible, so it
    is asserted on behaviour and not only on constants.
    """
    run = ev.EvalRun(items=[
        ev.EvalItem(item_id="cs-1", verdict=ev.UNMEASURABLE,
                    reason="no C# compiler on PATH"),
        ev.EvalItem(item_id="cs-2", verdict=ev.UNMEASURABLE,
                    reason="no C# compiler on PATH"),
    ])
    assert run.score is None, (
        "an unmeasurable suite scored a number; that number would read as a "
        "verdict on the model and it is a verdict on the toolchain")
    assert run.measured_count == 0
    assert len(run.unmeasured) == 2


def test_a_score_speaks_only_for_what_was_measured():
    run = ev.EvalRun(items=[
        ev.EvalItem(item_id="1", verdict=ev.PASS),
        ev.EvalItem(item_id="2", verdict=ev.FAIL),
        ev.EvalItem(item_id="3", verdict=ev.UNMEASURABLE),
    ])
    # 1 of 2 measured, NOT 1 of 3 seen.
    assert run.score == 0.5
    assert (run.measured_count, len(run.items)) == (2, 3)
    flat = ev.as_dict(run)
    assert (flat["measured"], flat["seen"], flat["unmeasured"]) == (2, 3, 1)


# ── streaming behaviour ──────────────────────────────────────────────────────

def _write(path: Path, rows) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows),
                    encoding="utf-8")
    return path


def test_a_half_written_final_line_is_normal_not_corruption(tmp_path):
    """The writer flushes per record, so a reader routinely arrives mid-line.

    That is the cost of the file being watchable live, and it must not surface
    as damage - a viewer that cried corruption during every healthy eval would
    teach people to ignore it.
    """
    p = tmp_path / "eval-x.jsonl"
    _write(p, [{"kind": "header", "schema_version": 1, "eval_id": "x"},
               {"kind": "record", "seq": 1, "item_id": "a", "verdict": "pass"}])
    with p.open("a", encoding="utf-8") as fh:
        fh.write('{"kind": "record", "seq": 2, "item_i')

    run = ev.read(p)
    assert len(run.items) == 1
    assert run.damaged_lines == 0, "a partial tail was reported as damage"


def test_a_bad_line_in_the_MIDDLE_is_reported(tmp_path):
    """Position is the only available signal and it is a sufficient one: a
    broken line with more lines after it was never a write in progress."""
    p = tmp_path / "eval-y.jsonl"
    p.write_text(
        json.dumps({"kind": "header", "schema_version": 1}) + "\n"
        + "{not json at all\n"
        + json.dumps({"kind": "record", "seq": 1, "item_id": "a",
                      "verdict": "pass"}) + "\n",
        encoding="utf-8")
    run = ev.read(p)
    assert run.damaged_lines == 1
    assert len(run.items) == 1


def test_an_unknown_kind_is_ignored_rather_than_called_broken(tmp_path):
    """Forward compatibility: the writer must be able to add a line type
    without every older viewer declaring the file damaged."""
    p = tmp_path / "eval-z.jsonl"
    _write(p, [{"kind": "header", "schema_version": 1},
               {"kind": "annotation", "note": "from a newer writer"},
               {"kind": "record", "seq": 1, "item_id": "a", "verdict": "pass"}])
    run = ev.read(p)
    assert run.damaged_lines == 0
    assert len(run.items) == 1


def test_a_newer_schema_refuses_rather_than_guessing(tmp_path):
    p = tmp_path / "eval-future.jsonl"
    _write(p, [{"kind": "header", "schema_version": ev.SCHEMA_VERSION + 1}])
    with pytest.raises(ev.UnreadableSidecar):
        ev.read(p)


def test_an_unrecognised_verdict_is_painted_as_itself(tmp_path):
    """Bucketing an unknown verdict into an existing one is inventing a
    reading - the single thing this viewer must never do."""
    p = tmp_path / "eval-w.jsonl"
    _write(p, [{"kind": "header", "schema_version": 1},
               {"kind": "record", "seq": 1, "item_id": "a",
                "verdict": "flaky"}])
    item = ev.read(p).items[0]
    assert item.verdict == "flaky"
    assert item.known_verdict is False
    assert item.measured is False


def test_a_missing_footer_eventually_reads_as_stalled():
    """A killed eval never writes a footer, so its last word stays 'running'
    forever. Same reasoning as the run manifest: a live spinner for something
    that died on Tuesday is worse than showing nothing."""
    run = ev.EvalRun(started=0.0,
                     items=[ev.EvalItem(item_id="a", verdict=ev.PASS, ts=0.0)])
    assert run.stale(now=ev.STALE_AFTER_SECONDS + 1) is True
    assert run.state == "stalled"


# ── the guards on the guard ──────────────────────────────────────────────────

def test_the_writer_is_still_derivable_from_what_theatre_declares():
    assert wf.WRITER_DIST, (
        f"no distribution is declared under the [{wf.WRITER_EXTRA}] extra, so "
        f"this file cannot work out whose format it is checking.")


def test_the_skip_cannot_become_permanent_and_silent():
    roots, importable = wf.checkout_roots(), wf.is_importable()
    if not roots and not importable:
        pytest.skip(f"{wf.WRITER_DIST} is genuinely not here")
    assert source is not None, (
        f"{wf.WRITER_DIST} IS present (checkouts={[str(r) for r in roots]}, "
        f"importable={importable}) but {wf.WRITER_MODULE}/{SIDECAR_MODULE} was "
        f"not found. The contract check is now blind.")
