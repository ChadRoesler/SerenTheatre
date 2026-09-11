"""`gone` was carrying two different facts. This is the third state.

"I looked and it is not there" and "I could not look" are not the same claim,
and a boolean column had to render them identically. So a share that hiccupped,
or a builder that was retired, made every archived run on it announce a
deletion - in red, over models sitting safely on a disk nobody had unmounted.

That is the same error this codebase already painted over eight consecutive
healthy fine-tune stages before it learned to report mtimes and refuse the
verdict: a conclusion a failed `stat()` is not entitled to draw.

WHAT MAKES `unknown` DECIDABLE AT ALL is the anchors - the stage roots Theatre
is currently able to look in. `Path.is_dir()` swallows OSError and answers
False, so a dead mount and a deleted directory are indistinguishable from the
path alone. The stage root is the thing that separates them, and a stage
declared remote gets one extra rule because an unmounted mount point is an
empty directory that stats perfectly.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from seren_theatre.archive import harvest as hv
from seren_theatre.archive.store import (RUN_GONE, RUN_PRESENT, RUN_STATES,
                                         RUN_UNKNOWN, Archive, ArchiveError)


def anchor(root, remote=False):
    return [hv.Anchor(Path(root), remote)]


class Stage:
    """The duck-typed shape anchors_for reads, without importing config."""

    def __init__(self, root, remote_prefix=""):
        self._root = Path(root)
        self.remote_prefix = remote_prefix

    def resolved(self):
        return self._root


# ── the discriminator ───────────────────────────────────────────────────────

class TestWhatEachAnswerMeans:

    def test_a_directory_that_is_there_is_present(self, tmp_path):
        run = tmp_path / "runs" / "a"
        run.mkdir(parents=True)
        assert hv._presence(str(run), anchor(tmp_path)) == RUN_PRESENT

    def test_a_deleted_directory_under_a_readable_stage_is_gone(self, tmp_path):
        """The real answer, and the one the feature is for."""
        (tmp_path / "runs").mkdir()
        (tmp_path / "runs" / "sibling").mkdir()
        assert hv._presence(str(tmp_path / "runs" / "a"),
                            anchor(tmp_path)) == RUN_GONE

    def test_a_path_no_configured_stage_covers_is_unknown(self, tmp_path):
        """THE LOUDEST CASE A BOOLEAN GOT WRONG.

        The builder was retired, or the stage renamed, so nothing here performed
        a look at all. Every row from that box read as a deletion.
        """
        assert hv._presence("/some/retired/box/runs/a",
                            anchor(tmp_path)) == RUN_UNKNOWN

    def test_no_anchors_at_all_means_unknown_not_gone(self, tmp_path):
        """Called without anchors, reconcile must still refuse to guess."""
        assert hv._presence(str(tmp_path / "gone"), []) == RUN_UNKNOWN

    def test_a_missing_stage_root_is_unknown(self, tmp_path):
        """The mount point itself is not there - nothing could be looked at."""
        root = tmp_path / "not-mounted"
        assert hv._presence(str(root / "runs" / "a"),
                            anchor(root)) == RUN_UNKNOWN

    def test_an_empty_root_on_a_REMOTE_stage_is_unknown(self, tmp_path):
        """TUESDAY. An unmounted mount point is an empty directory.

        It stats perfectly and lists cleanly, so every other signal says the
        stage is fine and every run on it is deleted. A mounted builder output
        root always holds something; an empty one almost certainly means the
        share is not up.
        """
        root = tmp_path / "mnt-spark"
        root.mkdir()
        assert hv._presence(str(root / "runs" / "a"),
                            anchor(root, remote=True)) == RUN_UNKNOWN

    def test_an_empty_root_on_a_LOCAL_stage_is_gone(self, tmp_path):
        """Because there no mount can be down, and empty means empty.

        Without this the remote rule would be a blanket excuse and `gone` would
        become unreachable for the one-box user - who is the default path.
        """
        root = tmp_path / "local"
        root.mkdir()
        assert hv._presence(str(root / "runs" / "a"),
                            anchor(root, remote=False)) == RUN_GONE

    def test_an_occupied_remote_root_still_reports_gone(self, tmp_path):
        """The remote rule must not swallow real deletions.

        A mounted share with other runs in it CAN answer the question, so a
        missing run there is genuinely missing.
        """
        root = tmp_path / "mnt-spark"
        (root / "runs" / "other").mkdir(parents=True)
        assert hv._presence(str(root / "runs" / "a"),
                            anchor(root, remote=True)) == RUN_GONE

    def test_no_path_recorded_is_unknown(self, tmp_path):
        assert hv._presence("", anchor(tmp_path)) == RUN_UNKNOWN

    def test_a_stat_that_raises_is_unknown(self, tmp_path, monkeypatch):
        """NOT `gone`. An EIO or ESTALE from a dead share is the mount failing.

        This is the branch `is_dir()` alone can never reach, because it catches
        OSError and answers False - which is the whole bug in one method call.
        """
        def boom(self):
            raise OSError(5, "Input/output error")
        monkeypatch.setattr(Path, "is_dir", boom)
        assert hv._presence(str(tmp_path / "a"),
                            anchor(tmp_path)) == RUN_UNKNOWN

    def test_every_answer_is_a_declared_state(self, tmp_path):
        """A state the store cannot name would fall through the viewer."""
        (tmp_path / "here").mkdir()
        for path in [str(tmp_path / "here"), str(tmp_path / "nope"), "",
                     "/elsewhere/x"]:
            assert hv._presence(path, anchor(tmp_path)) in RUN_STATES


class TestTheNearestStageWins:

    def test_the_longest_matching_root_answers(self, tmp_path):
        """Stages nest, and the inner one is the specific claim.

        /mnt/spark and /mnt/spark/msMoEMaker can both be configured; whether
        THAT tree is reachable is the more precise question.
        """
        outer = tmp_path
        inner = tmp_path / "msMoEMaker"
        inner.mkdir()
        anchors = [hv.Anchor(outer, False), hv.Anchor(inner, True)]
        # inner is remote and empty -> unknown, even though outer is occupied
        assert hv._presence(str(inner / "runs" / "a"),
                            anchors) == RUN_UNKNOWN
        assert hv._presence(str(inner / "runs" / "a"),
                            list(reversed(anchors))) == RUN_UNKNOWN, (
            "the answer depended on config ORDER, which makes it unreproducible")


class TestAnchorsFromStages:

    def test_a_declared_prefix_marks_the_stage_remote(self, tmp_path):
        got = hv.anchors_for([Stage(tmp_path, "/mnt/nvme/msMoEMaker")])
        assert got == [hv.Anchor(Path(tmp_path), True)]

    def test_no_prefix_is_a_local_stage(self, tmp_path):
        assert hv.anchors_for([Stage(tmp_path)]) == [
            hv.Anchor(Path(tmp_path), False)]

    def test_an_unreadable_stage_is_skipped_not_fatal(self, tmp_path):
        """Losing one anchor costs `unknown` on its rows - the safe direction.

        Raising would take the whole Previous Surgeries panel down over a typo
        in one stage, which is the failure the config loader already refuses.
        """
        class Broken:
            remote_prefix = ""

            def resolved(self):
                raise RuntimeError("not a stage")

        assert hv.anchors_for([Broken(), Stage(tmp_path)]) == [
            hv.Anchor(Path(tmp_path), False)]

    @pytest.mark.parametrize("value", [None, [], (), 0])
    def test_nothing_configured_yields_no_anchors(self, value):
        assert hv.anchors_for(value) == []


# ── the column, and the derived boolean ─────────────────────────────────────

class TestTheStoreHoldsThreeStates:

    def archive(self, tmp_path):
        arc = Archive(tmp_path / "a.db")
        arc.put_surgery({"run_key": "k", "stage": "s", "name": "n",
                         "run_path": str(tmp_path / "runs" / "a"),
                         "harvested": 1.0, "manifest": "{}"})
        return arc

    def test_each_state_round_trips(self, tmp_path):
        arc = self.archive(tmp_path)
        for state in RUN_STATES:
            arc.mark_presence("k", state)
            assert arc.surgery_row("k")["run_state"] == state
        arc.close()

    def test_the_boolean_is_derived_and_unknown_is_not_present(self, tmp_path):
        """A boolean asked "is it there" must not say yes about an unknown.

        And it must have exactly ONE writer, or the two columns can be set
        apart - two copies of one fact being the disease this whole design is
        treating.
        """
        arc = self.archive(tmp_path)
        expected = {RUN_PRESENT: 1, RUN_GONE: 0, RUN_UNKNOWN: 0}
        for state, flag in expected.items():
            arc.mark_presence("k", state)
            row = arc.surgery_row("k")
            assert row["run_present"] == flag, (
                f"{state!r} derived to run_present={row['run_present']}")
        arc.close()

    def test_a_state_the_code_cannot_name_is_refused(self, tmp_path):
        """A typo'd state would read as neither present nor gone, and the
        viewer would draw whichever branch fell through."""
        arc = self.archive(tmp_path)
        with pytest.raises(ArchiveError) as caught:
            arc.mark_presence("k", "probably")
        assert "probably" in str(caught.value)
        arc.close()

    def test_the_column_reaches_an_archive_that_already_existed(self, tmp_path):
        """CREATE TABLE IF NOT EXISTS is a no-op on an existing table.

        The column has to arrive by ALTER or it only ever exists in databases
        created after this change - green tests, and nobody's real history.
        """
        first = Archive(tmp_path / "old.db")
        first.close()
        import sqlite3
        db = sqlite3.connect(tmp_path / "old.db")
        db.execute("ALTER TABLE surgeries DROP COLUMN run_state")
        db.commit()
        db.close()
        again = Archive(tmp_path / "old.db")
        assert "run_state" in again._columns("surgeries")
        again.close()


# ── reconcile, at read time ─────────────────────────────────────────────────

class TestReconcile:

    def rows(self, tmp_path, path):
        return [{"run_key": "k", "run_path": str(path), "run_state": "",
                 "run_present": 1}]

    def test_it_reports_and_stores_the_state(self, tmp_path):
        arc = Archive(tmp_path / "a.db")
        arc.put_surgery({"run_key": "k", "stage": "s", "name": "n",
                         "run_path": str(tmp_path / "gone"),
                         "harvested": 1.0, "manifest": "{}"})
        out = hv.reconcile(arc, self.rows(tmp_path, tmp_path / "gone"),
                           anchor(tmp_path))
        assert out[0]["run_state"] == RUN_GONE
        assert out[0]["run_present"] is False
        assert arc.surgery_row("k")["run_state"] == RUN_GONE, (
            "the reading was returned and never stored, so it is recomputed "
            "on every read and the column never converges")
        arc.close()

    def test_a_read_survives_a_write_that_fails(self, tmp_path, monkeypatch):
        """The returned value is right regardless; a read must not fail."""
        arc = Archive(tmp_path / "a.db")
        monkeypatch.setattr(arc, "mark_presence",
                            lambda *a, **k: (_ for _ in ()).throw(OSError()))
        out = hv.reconcile(arc, self.rows(tmp_path, tmp_path / "gone"),
                           anchor(tmp_path))
        assert out[0]["run_state"] == RUN_GONE
        arc.close()

    def test_one_listing_per_root_not_two(self, tmp_path, monkeypatch):
        """Against a share that may be exactly the thing misbehaving.

        The first version asked "readable?" and then "empty?" separately - two
        round trips per row over a network mount.
        """
        root = tmp_path / "mnt"
        root.mkdir()
        calls = []
        real = Path.iterdir

        def counted(self):
            calls.append(str(self))
            return real(self)

        monkeypatch.setattr(Path, "iterdir", counted)
        hv._presence(str(root / "runs" / "a"), anchor(root, remote=True))
        assert calls.count(str(root)) == 1, (
            f"listed the stage root {calls.count(str(root))} times")


# ── merge, and the frame that travels with the fact ─────────────────────────

class TestMergeDoesNotAssertADeletionItCannotSee:
    """A row from another box is `unknown` here, not `gone`.

    Nothing on this archive's machine has looked for that directory - it may be
    sitting on a share this box has never had mounted. `gone` would be this
    archive asserting a deletion on no evidence, which is the entire reason the
    third state exists. The boolean was already handled; the STATE was not, and
    a mutation proving it went unnoticed is what added this test.
    """

    def two(self, tmp_path):
        spark = Archive(tmp_path / "spark.db")
        spark.put_surgery({"run_key": "r", "stage": "Spark", "name": "n",
                           "run_path": "/mnt/nvme/msMoEMaker/runs/r",
                           "harvested": 10.0, "manifest": "{}",
                           "run_state": RUN_PRESENT, "run_present": 1})
        nuc = Archive(tmp_path / "nuc.db")
        return spark, nuc

    def test_a_row_never_seen_here_lands_unknown(self, tmp_path):
        spark, nuc = self.two(tmp_path)
        nuc.merge(spark)
        row = nuc.surgery_row("r")
        assert row["run_state"] == RUN_UNKNOWN, (
            f"a foreign row landed as {row['run_state']!r}. The Spark said "
            f"present; this box has not looked at all, and calling that `gone` "
            f"announces a deletion nobody observed.")
        assert row["run_present"] == 0
        spark.close(); nuc.close()

    def test_a_local_observation_is_not_overwritten_by_the_merge(self, tmp_path):
        """This archive's own look is the only fact about this box's disk."""
        spark, nuc = self.two(tmp_path)
        nuc.put_surgery({"run_key": "r", "stage": "Spark", "name": "n",
                         "run_path": "/mnt/nvme/msMoEMaker/runs/r",
                         "harvested": 5.0, "manifest": "{}"})
        nuc.mark_presence("r", RUN_GONE)
        nuc.merge(spark)
        assert nuc.surgery_row("r")["run_state"] == RUN_GONE, (
            "the merge imported the other box's presence over a look this box "
            "actually performed")
        spark.close(); nuc.close()

    def test_the_frame_survives_the_merge(self, tmp_path):
        """The prefix is a property of the RUN, unlike presence."""
        spark, nuc = self.two(tmp_path)
        spark.put_surgery({"run_key": "q", "stage": "Spark", "name": "n",
                           "run_path": "/mnt/spark/msMoEMaker/runs/q",
                           "harvested": 10.0, "manifest": "{}",
                           "remote_prefix": "/mnt/nvme/msMoEMaker",
                           "builder_path": "/mnt/nvme/msMoEMaker/runs/q"})
        nuc.merge(spark)
        row = nuc.surgery_row("q")
        assert row["remote_prefix"] == "/mnt/nvme/msMoEMaker"
        assert row["builder_path"] == "/mnt/nvme/msMoEMaker/runs/q"
        spark.close(); nuc.close()


class TestTheFrameIsWiredFromConfigToRow:
    """config -> scan_stage -> run dict -> harvest -> archive row.

    Four hops, and a field that survives three of them is still invisible. The
    mutation that deleted the stamp in scan_stage passed every other test in
    this file, which is precisely the "data present, not surfaced" shape - so
    this walks the whole wire rather than testing either end.
    """

    def a_finished_run(self, root):
        run = root / "runs" / "msmoe_run_0.5B"
        run.mkdir(parents=True)
        (run / "msmoe-run.json").write_text(
            '{"schema_version": 1, "name": "n", "state": "finished", '
            '"started": 1.0, "finished": 2.0, "ok": true, "stages": []}',
            encoding="utf-8")
        return run

    def test_scan_stage_stamps_the_prefix_and_the_translated_path(self, tmp_path):
        from seren_theatre import sources as src
        self.a_finished_run(tmp_path)
        got = src.scan_stage("Spark", tmp_path, ["*.log"], [], 100,
                             remote_prefix="/mnt/nvme/msMoEMaker")
        run, = got["runs"]
        assert run["remote_prefix"] == "/mnt/nvme/msMoEMaker"
        assert run["builder_path"] == \
            "/mnt/nvme/msMoEMaker/runs/msmoe_run_0.5B", (
            "the builder-side path was not computed at scan time - and it "
            "cannot be recovered later, because the row keeps the prefix but "
            "not the stage root it was stripped against")

    def test_a_local_stage_stamps_the_keys_anyway(self, tmp_path):
        """Always present, so nobody has to tell "local" from "too old to say"."""
        from seren_theatre import sources as src
        self.a_finished_run(tmp_path)
        run, = src.scan_stage("Local", tmp_path, ["*.log"], [], 100)["runs"]
        assert run["remote_prefix"] == ""
        assert run["builder_path"] == run["path"]

    def test_harvest_records_both_onto_the_row(self, tmp_path):
        from seren_theatre import sources as src
        from seren_theatre.archive import harvest as harv
        stage_root = tmp_path / "stage"
        self.a_finished_run(stage_root)
        got = src.scan_stage("Spark", stage_root, ["*.log"], [], 100,
                             remote_prefix="/mnt/nvme/msMoEMaker")
        arc = Archive(tmp_path / "a.db")
        assert harv.harvest_stage(arc, got) == 1
        row, = arc.surgeries(limit=5)
        assert row["remote_prefix"] == "/mnt/nvme/msMoEMaker"
        assert row["builder_path"] == \
            "/mnt/nvme/msMoEMaker/runs/msmoe_run_0.5B", (
            "the frame reached the run and not the row, so a person reading "
            "this history after the mount moves has only the dead path")
        arc.close()

    def test_a_path_outside_the_stage_is_returned_untouched(self):
        """Guessing would produce a confident path to nowhere."""
        from seren_theatre import sources as src
        assert src.builder_path("/elsewhere/x", Path("/mnt/spark"),
                                "/mnt/nvme") == "/elsewhere/x"

    def test_no_prefix_is_the_identity(self):
        from seren_theatre import sources as src
        assert src.builder_path("/a/b", Path("/a"), "") == "/a/b"
