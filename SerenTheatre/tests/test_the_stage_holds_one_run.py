"""What belongs on the stage: the run there is something to DO about.

THE BRIEF, in Chad's words: you eyeball the stage and go "cool something's
cooking", or "oh no something failed" and investigate. Everything else is
history, and history has a tab.

So exactly one state comes OFF the stage - `finished`. Every other reading
stays: `running` and `idle` are cooking, `failed` is the whole reason a person
looks, and `stalled` is the one you most want to catch. An unknown or missing
state stays too, because a run this code cannot classify is precisely the one
to put in front of somebody.

DECIDED SERVER-SIDE, and not as a preference. `activity_state` already says
why: two implementations of "is this run over" would eventually disagree, and
they would disagree on screen. The browser is handed a verdict.

AND THE PAYLOAD STILL CARRIES EVERYTHING. `harvest_stage` iterates `runs`, so
a viewer-facing decision that shortened that list would stop finished runs
being archived - the retention bug this suite already has a file about. The
stage picks one to draw; nothing is dropped.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from seren_theatre import manifest as mf
from seren_theatre import sources


def run_dir(stage: Path, name: str, *, state: str = "running",
            started: float = 100.0) -> Path:
    """A run whose manifest puts it in one state.

    States are derived by `manifest.Manifest.state` rather than stored, so the
    fields have to be arranged to produce the word rather than asserting it.
    """
    d = stage / name
    d.mkdir(parents=True, exist_ok=True)
    doc = {"schema_version": 1, "name": name, "started": started,
           "updated": started + 5, "stages": []}
    if state == "finished":
        doc.update(finished=started + 10, ok=True)
    elif state == "failed":
        doc.update(finished=started + 10, ok=False)
    elif state == "running":
        doc["stages"] = [{"id": "preflight", "label": "Preflight",
                          "status": "running", "started": started}]
    # `idle` is the default: started, nothing running, not finished.
    (d / mf.MANIFEST_NAME).write_text(json.dumps(doc), encoding="utf-8")
    return d


def scan(stage: Path) -> dict:
    return sources.scan_stage("S", stage, ["*.log"], [], 1024)


class TestOnlyACleanFinishLeavesTheStage:
    @pytest.mark.parametrize("state", ["running", "failed", "idle"])
    def test_a_run_with_something_to_do_stays(self, tmp_path, state):
        stage = tmp_path / "workbench"
        run_dir(stage, "only", state=state)
        got = scan(stage)
        assert got["on_stage"] is not None, (
            f"a {state!r} run was taken off the stage, so the one thing the "
            f"stage is for cannot be seen at a glance")
        assert got["on_stage"]["name"] == "only"

    def test_a_clean_finish_comes_off(self, tmp_path):
        stage = tmp_path / "workbench"
        run_dir(stage, "only", state="finished")
        got = scan(stage)
        assert got["on_stage"] is None, (
            "a run that ended cleanly last Tuesday is still on the stage, so "
            "'something is cooking' and 'this is over' look the same")

    def test_an_unclassifiable_run_stays_on_the_stage(self, tmp_path):
        """FAILS TOWARD VISIBILITY. An uninstrumented directory has no state at
        all; hiding it would be the same silent miss as the empty state that
        used to say 'No runs here yet' over four archived builds."""
        stage = tmp_path / "workbench"
        d = stage / "mystery"
        d.mkdir(parents=True)
        (d / "moe_trained").mkdir()
        (d / "moe_trained" / "config.json").write_text("{}", encoding="utf-8")
        got = scan(stage)
        assert got["runs"], "the uninstrumented run was not even discovered"
        assert got["on_stage"] is not None

    def test_the_newest_is_the_one_considered(self, tmp_path):
        """One run on stage, and it is the current one - not whichever finished
        run happens to sort first."""
        stage = tmp_path / "workbench"
        run_dir(stage, "older", state="finished", started=100.0)
        run_dir(stage, "newer", state="running", started=900.0)
        got = scan(stage)
        assert got["on_stage"]["name"] == "newer"

    def test_a_finished_newest_takes_the_stage_empty(self, tmp_path):
        """AND IT DOES NOT FALL BACK to an older unfinished run. The stage
        is about now; reaching back for something to display would invent a
        reading, and an older run that never finished is not what is
        happening."""
        stage = tmp_path / "workbench"
        run_dir(stage, "older", state="failed", started=100.0)
        run_dir(stage, "newer", state="finished", started=900.0)
        got = scan(stage)
        assert got["on_stage"] is None, (
            "the stage reached past the current run to find something to draw")


class TestNothingIsDroppedFromThePayload:
    """The retention rule, as a test, because it has been broken once."""

    def test_every_run_is_still_in_runs(self, tmp_path):
        stage = tmp_path / "workbench"
        for i, state in enumerate(("finished", "finished", "running")):
            run_dir(stage, f"r{i}", state=state, started=100.0 + i * 100)
        got = scan(stage)
        assert len(got["runs"]) == 3, (
            "the stage decision shortened `runs`, which harvest reads - so "
            "finished runs would stop being archived")

    def test_harvest_still_sees_the_finished_ones(self, tmp_path):
        from seren_theatre.archive import harvest, store

        stage = tmp_path / "workbench"
        run_dir(stage, "done-a", state="finished", started=100.0)
        run_dir(stage, "done-b", state="finished", started=200.0)
        run_dir(stage, "live", state="running", started=300.0)
        got = scan(stage)
        assert got["on_stage"]["name"] == "live"

        arc = store.Archive(tmp_path / "a.db")
        try:
            harvest.harvest_stage(arc, got)
            assert arc.count("surgeries") == 2, (
                "the two finished runs are off the stage AND out of the "
                "archive - which is how a record disappears silently")
        finally:
            arc.close()

    def test_the_counts_describe_what_is_there(self, tmp_path):
        stage = tmp_path / "workbench"
        run_dir(stage, "a", state="finished", started=100.0)
        run_dir(stage, "b", state="finished", started=200.0)
        run_dir(stage, "c", state="running", started=300.0)
        got = scan(stage)
        assert got["finished_here"] == 2, (
            "the number a person needs to tell 'nothing cooking' from "
            "'nothing here' is wrong")
        assert got["earlier"] == 2
        assert got["current"].endswith("c"), (
            "`current` changed meaning - it is the newest run, whatever its "
            "state, and something else already depends on that")


class TestAnEmptyStageIsNotOneSentence:
    def test_a_stage_that_never_built_anything(self, tmp_path):
        stage = tmp_path / "workbench"
        stage.mkdir()
        got = scan(stage)
        assert got["runs"] == []
        assert got["on_stage"] is None
        assert got["finished_here"] == 0, (
            "nothing was ever built here and the payload implies otherwise")

    def test_a_stage_whose_work_is_all_done(self, tmp_path):
        """Distinguishable from the above by `finished_here` alone, which is
        what lets the viewer say which kind of empty it is."""
        stage = tmp_path / "workbench"
        run_dir(stage, "a", state="finished")
        got = scan(stage)
        assert got["on_stage"] is None
        assert got["finished_here"] == 1


class TestTheStateReadingIsTheOneTheScanSettled:
    def test_a_downgraded_stall_counts_as_running(self, tmp_path):
        """`scan_stage` may downgrade `stalled` to `running` after looking
        at file activity, and that verdict is the better one. The stage rule
        must read the settled word, not the raw manifest field, or the two
        halves of one payload disagree about the same run."""
        stage = tmp_path / "workbench"
        d = run_dir(stage, "slow", state="idle")
        assert sources._run_state({"state": "running",
                                   "manifest": {"state": "stalled"}}) == \
            "running"
        assert sources._wants_the_stage({"state": "finished"}) is False
        assert sources._wants_the_stage({"manifest": {"state": "finished"}}) \
            is False
        assert sources._wants_the_stage({}) is True, (
            "a run with no state at all was hidden; unknown must fail toward "
            "being seen")
        assert d.is_dir()
