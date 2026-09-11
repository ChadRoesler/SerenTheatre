"""Making the archive survive the box, not just the cleanup.

WHAT THE ARCHIVE ALREADY SURVIVED. Deleting a run directory. That is what
harvest is for, and `store.connect` refuses to put the archive inside a watched
stage precisely so the cleanup it exists to outlive cannot reach it.

WHAT IT DID NOT SURVIVE. The box. One file, in the home directory of whichever
machine runs Theatre, with no way to make a second copy and no way to fold two
together. And worse than "no backup command" - **the backup was a lie.**

Observed on a real box after a week of builds:

    -rw-r--r--  archive.db        4096 bytes   Sep  2
    -rw-r--r--  archive.db-wal    383192 bytes   Sep  5

WAL mode, a connection held for the life of the process, no autocheckpoint
configured, and nothing calling `Archive.close()`. Every query answered
correctly, because sqlite reads the log and the database together. But
`cp archive.db /somewhere/safe` produced a file that opened, carried the right
name and held NOTHING - schema included, because the CREATE TABLEs went
into the WAL too. And the file that actually held four runs of history
was named `.db-wal`, which looks like something a person tidies away.

That is the failure this file is about: not data loss, but a backup that looks
like a backup. TestTheBackupThatWasALie reproduces it on demand.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from seren_theatre.archive.store import (TABLES, Archive, ArchiveError,
                                        RUN_GONE, RUN_UNKNOWN)
from seren_theatre.config import ArchiveConfig, TheatreConfig


def surgery(key: str, *, name: str = "run", harvested: float = 100.0,
            present: int = 1) -> dict:
    return {"run_key": key, "build_id": "b1", "stage": "S", "name": name,
            "run_path": f"/runs/{key}", "started": 1.0, "finished": 2.0,
            "state": "finished", "ok": 1, "harvested": harvested,
            "run_present": present,
            "manifest": json.dumps({"name": name, "build_id": "b1"})}


def stocked(path: Path, keys=("a", "b"), **kw) -> Archive:
    a = Archive(path)
    for k in keys:
        a.put_surgery(surgery(k, name=f"{path.stem}-{k}", **kw))
    return a


class TestTheBackupThatWasALie:
    """The bug, reproduced, then removed two different ways."""

    def test_a_plain_copy_of_a_live_archive_is_empty(self, tmp_path):
        """THE WHOLE REASON THE OTHER TESTS EXIST.

        If this ever stops failing to carry rows, sqlite changed its WAL
        defaults and the rest of this file is guarding a bug that no longer
        exists - which is worth being told about rather than quietly passing.
        """
        live = stocked(tmp_path / "archive.db")
        assert live.count("surgeries") == 2

        shutil.copy2(tmp_path / "archive.db", tmp_path / "naive.db")
        naive = Archive(tmp_path / "naive.db")
        try:
            assert naive.count("surgeries") == 0, (
                "a plain copy carried rows, so the WAL had already been "
                "checkpointed and this test is no longer reproducing anything")
        finally:
            naive.close()
            live.close()

    def test_checkpoint_makes_the_main_file_real(self, tmp_path):
        live = stocked(tmp_path / "archive.db")
        assert live.checkpoint() is True
        shutil.copy2(tmp_path / "archive.db", tmp_path / "after.db")
        after = Archive(tmp_path / "after.db")
        try:
            assert after.count("surgeries") == 2
        finally:
            after.close()
            live.close()

    def test_the_wal_is_truncated_not_merely_flushed(self, tmp_path):
        """TRUNCATE, not PASSIVE. PASSIVE moves the pages and leaves the log at
        its size - which keeps the exact file shape that misled somebody into
        thinking `.db-wal` was scratch."""
        live = stocked(tmp_path / "archive.db")
        wal = tmp_path / "archive.db-wal"
        assert wal.exists() and wal.stat().st_size > 0
        live.checkpoint()
        assert wal.stat().st_size == 0, "the log was flushed but not truncated"
        live.close()

    def test_backup_does_not_need_a_checkpoint_first(self, tmp_path):
        """The online backup API reads through the engine, so it sees the WAL.
        This is why the documented answer is `archive backup` and not "remember
        to checkpoint, then cp"."""
        live = stocked(tmp_path / "archive.db")
        dest = live.backup(tmp_path / "bk.db")
        copy = Archive(dest)
        try:
            assert copy.count("surgeries") == 2
        finally:
            copy.close()
            live.close()

    def test_backup_reads_back_what_it_wrote(self, tmp_path):
        live = stocked(tmp_path / "archive.db")
        dest = live.backup(tmp_path / "bk.db")
        assert dest.is_file()
        with sqlite3.connect(str(dest)) as db:
            n = db.execute("SELECT COUNT(*) FROM surgeries").fetchone()[0]
        assert n == 2
        live.close()

    def test_a_backup_that_copied_nothing_is_REFUSED(self, tmp_path,
                                                     monkeypatch):
        """THE ASSERTION THAT MAKES THE VERIFY REAL.

        Checking that a working backup contains the rows passes whether or not
        anything verifies - the test above survived deleting the verify
        entirely. So this makes the copy silently fail and demands an error.

        The whole feature exists because a backup that looks right and holds
        nothing is worse than no backup, so "we wrote the file and did not read
        it" is the one outcome that must be impossible.
        """
        live = stocked(tmp_path / "archive.db")
        real_connect = sqlite3.connect
        calls = {"n": 0}

        def first_call_goes_nowhere(target, *a, **kw):
            # The FIRST connect inside backup() is the destination. Send
            # it to a throwaway so the real destination is never written;
            # every later connect (the verify's own Archive) behaves normally.
            calls["n"] += 1
            if calls["n"] == 1:
                return real_connect(str(tmp_path / "elsewhere.db"), *a, **kw)
            return real_connect(target, *a, **kw)

        monkeypatch.setattr("seren_theatre.archive.store.sqlite3.connect",
                            first_call_goes_nowhere)
        with pytest.raises(ArchiveError,
                           match="Refusing to call that a backup"):
            live.backup(tmp_path / "bk.db")
        monkeypatch.undo()
        live.close()

    def test_backup_refuses_to_overwrite_the_archive(self, tmp_path):
        live = stocked(tmp_path / "archive.db")
        with pytest.raises(ArchiveError, match="the archive itself"):
            live.backup(tmp_path / "archive.db")
        live.close()

    def test_closing_leaves_a_file_a_copy_can_trust(self, tmp_path):
        """`close()` checkpoints first, and the EXPLICIT checkpoint is only
        load-bearing while something else has the database open.

        The first version of this test opened one connection and closed it,
        which passes with or without the explicit call - sqlite checkpoints on
        its own when the LAST connection goes. So it proved nothing, and a
        mutation that deleted the checkpoint survived it. "Last" is exactly
        what this object cannot know, which is the reason to do it by hand.
        """
        live = stocked(tmp_path / "archive.db")
        # A SECOND READER, the way a viewer poll or a `sqlite3` shell would be.
        # With this open, closing `live` is not the last close and sqlite will
        # not fold the log back for us.
        other = sqlite3.connect(str(tmp_path / "archive.db"))
        try:
            # AND IT HAS TO ACTUALLY READ. `sqlite3.connect` is lazy - it does
            # not touch the file until a statement runs, so a merely-built
            # connection is not a reader and closing `live` still counts as
            # the last close. Measured: untouched, a plain copy carries
            # the rows either way; touched, it carries them ONLY with the
            # explicit checkpoint. Without this line the test proves nothing.
            other.execute("SELECT COUNT(*) FROM surgeries").fetchone()
            live.close()
            shutil.copy2(tmp_path / "archive.db", tmp_path / "after.db")
            after = Archive(tmp_path / "after.db")
            try:
                assert after.count("surgeries") == 2, (
                    "closing did not checkpoint while another connection was "
                    "open, so the file left on disk is still a stub")
            finally:
                after.close()
        finally:
            other.close()


class TestTheAppClosesIt:
    def test_shutdown_closes_the_archive(self, tmp_path):
        """The hook is why any of the above happens in practice. TestClient
        only runs lifespan as a context manager, hence the `with`."""
        from seren_theatre.app import create_app

        cfg = TheatreConfig()
        cfg.archive = ArchiveConfig(enabled=True,
                                    dsn=str(tmp_path / "archive.db"),
                                    blobs=str(tmp_path / "blobs"))
        cfg.stages = []
        app = create_app(cfg)
        with TestClient(app) as client:
            client.get("/api/state")           # opens the archive
            opened = app.state.archive
            assert opened is not None
        assert app.state.archive is None, "the lifespan did not close it"
        # And the closed file is complete: a copy of it carries the schema.
        reopened = Archive(tmp_path / "archive.db")
        try:
            assert reopened.count("surgeries") >= 0
        finally:
            reopened.close()


class TestMergeIsIdempotent:
    """What replaced the record service that got rejected."""

    def test_the_union_is_taken(self, tmp_path):
        nuc = stocked(tmp_path / "nuc.db", keys=("a", "b"))
        spark = stocked(tmp_path / "spark.db", keys=("b", "c"))
        nuc.merge(spark)
        assert nuc.count("surgeries") == 3
        nuc.close()
        spark.close()

    def test_running_it_twice_changes_nothing(self, tmp_path):
        nuc = stocked(tmp_path / "nuc.db", keys=("a",))
        spark = stocked(tmp_path / "spark.db", keys=("b", "c"))
        nuc.merge(spark)
        first = sorted(r["run_key"] for r in nuc.surgeries(limit=99))
        nuc.merge(spark)
        assert sorted(r["run_key"] for r in nuc.surgeries(limit=99)) == first
        assert nuc.count("surgeries") == 3
        nuc.close()
        spark.close()

    def test_every_table_comes_across(self, tmp_path):
        nuc = Archive(tmp_path / "nuc.db")
        spark = Archive(tmp_path / "spark.db")
        spark.put_surgery(surgery("a"))
        spark.put_grading({"grading_key": "g1", "run_key": "a",
                           "build_id": "b1", "generated": 1.0, "ok": 1,
                           "provenance": "matches",
                           "report": json.dumps({"ok": True})})
        spark.put_gate({"run_key": "a", "status": "pass", "findings": 0,
                        "unmeasured": 0, "report": json.dumps({})})
        moved = nuc.merge(spark)
        for table in TABLES:
            assert table in moved, f"{table} was not reported by merge"
        assert nuc.count("gradings") == 1
        assert nuc.count("gates") == 1
        nuc.close()
        spark.close()


class TestMergeDoesNotImportSomebodyElsesDisk:
    """PRESENCE IS NOT A PROPERTY OF THE RUN.

    `run_present` answers "can THIS archive open that directory". Found by
    running a merge, not by reading one: a naive upsert let the Spark's
    "present" become the NUC's claim about a path the NUC may not have
    mounted - the one column whose comment says it must never be guessed.
    """

    def test_a_foreign_row_does_not_claim_presence(self, tmp_path):
        nuc = Archive(tmp_path / "nuc.db")
        spark = stocked(tmp_path / "spark.db", keys=("c",), present=1)
        nuc.merge(spark)
        row = nuc.surgery_row("c")
        assert row is not None
        assert row["run_present"] == 0, (
            "a row merged in from another box claims its directory is present "
            "here")
        nuc.close()
        spark.close()

    def test_a_local_observation_survives_the_merge(self, tmp_path):
        nuc = Archive(tmp_path / "nuc.db")
        nuc.put_surgery(surgery("b", name="local", harvested=100.0, present=1))
        nuc.mark_presence("b", RUN_GONE)  # this box looked: it is gone
        spark = Archive(tmp_path / "spark.db")
        spark.put_surgery(surgery("b", name="remote", harvested=200.0,
                                  present=1))
        nuc.merge(spark)
        row = nuc.surgery_row("b")
        assert row["run_present"] == 0, (
            "the merge overwrote what this box had actually observed")
        nuc.close()
        spark.close()

    def test_the_later_harvest_describes_the_run(self, tmp_path):
        """Two rows under one key are one run looked at twice. `harvested`
        arbitrates, so the answer does not depend on merge direction."""
        nuc = Archive(tmp_path / "nuc.db")
        nuc.put_surgery(surgery("b", name="older", harvested=100.0))
        spark = Archive(tmp_path / "spark.db")
        spark.put_surgery(surgery("b", name="newer", harvested=200.0))
        nuc.merge(spark)
        assert nuc.surgery_row("b")["name"] == "newer"
        nuc.close()
        spark.close()

    def test_an_older_source_does_not_overwrite_a_newer_row(self, tmp_path):
        nuc = Archive(tmp_path / "nuc.db")
        nuc.put_surgery(surgery("b", name="newer", harvested=200.0))
        spark = Archive(tmp_path / "spark.db")
        spark.put_surgery(surgery("b", name="older", harvested=100.0))
        moved = nuc.merge(spark)
        assert nuc.surgery_row("b")["name"] == "newer"
        assert moved["surgeries_already_current"] == 1, (
            "the skip is not reported, so a person cannot tell a no-op merge "
            "from a merge that found nothing")
        nuc.close()
        spark.close()


class TestTheTableListHasOneCopy:
    def test_count_and_all_agree_about_what_exists(self, tmp_path):
        """`prompt_books` has no document column, so membership used to be
        spelled `table not in DOCUMENTS and table != "prompt_books"` at each
        call site - two copies of "which tables exist"."""
        a = Archive(tmp_path / "archive.db")
        try:
            for table in TABLES:
                assert a.count(table) == 0
                assert a._all(table) == []
            for bogus in ("meta", "nope", ""):
                with pytest.raises(ArchiveError):
                    a.count(bogus)
                with pytest.raises(ArchiveError):
                    a._all(bogus)
        finally:
            a.close()
