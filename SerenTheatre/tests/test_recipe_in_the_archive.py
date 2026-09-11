"""Keeping the recipe, so a record is a build order and not an epitaph.

WHAT THE ARCHIVE COULD NOT SAY. A harvested row carries the manifest, and
that manifest carries `resolved` (the whole config fingerprint), `build_id`
(a digest of it), and a sha256 of every DEFAULTS file the run inherited. So a
row could state, exactly, the fingerprint of what was built - and nothing at
all about the recipe text that built it.

That is the difference between a record that VERIFIES a rebuild and one that
makes a rebuild possible. `build_id` lets you check you got the same model
back. It does not hand you the thing to run.

ms-moe-maker now preserves its recipe beside the manifest, and harvest
content-addresses that text into blobs. Two ends of one name, so
test_manifest_contract.py pins RECIPE_STEM the way it pins MANIFEST_NAME.

FOUR STATES, DRAWN AS THREE. See store.RECIPE_* and the viewer probe: only
`unreadable` is anybody's problem, and only it gets a warning.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from seren_theatre import manifest as mf
from seren_theatre.archive import blobs as B
from seren_theatre.archive import harvest, store


def run_dict(path: Path, *, name="run", started=100.0) -> dict:
    """A scanned run, terminal, the shape harvest is handed by scan_stage."""
    return {"name": path.name, "path": str(path), "state": "finished",
            "manifest": {"name": name, "build_id": "bid-1",
                         "started": started, "finished": started + 10,
                         "ok": True, "state": "finished"}}


def built(tmp_path: Path, *, recipe: str | None = "name: mine\n",
          suffix: str = ".yaml") -> Path:
    run = tmp_path / "runs" / "0.5B"
    run.mkdir(parents=True, exist_ok=True)
    (run / mf.MANIFEST_NAME).write_text(json.dumps(
        {"schema_version": 1, "name": "mine", "stages": []}), encoding="utf-8")
    if recipe is not None:
        (run / (mf.RECIPE_STEM + suffix)).write_text(recipe, encoding="utf-8")
    return run


class TestTheFourStates:
    def test_a_preserved_recipe_is_captured_and_round_trips(self, tmp_path):
        run = built(tmp_path, recipe="name: mine\nbase: b\n")
        arc = store.Archive(tmp_path / "a.db")
        blobs = B.Blobs(tmp_path / "blobs")
        try:
            assert harvest.harvest_run(arc, "S", run_dict(run), blobs)
            row = arc.surgery_row(
                harvest.run_key("S", run, 100.0))
            assert row["recipe_state"] == store.RECIPE_CAPTURED
            assert row["recipe_blob"]
            kept = blobs.open(row["recipe_blob"]).read().decode("utf-8")
            assert kept == "name: mine\nbase: b\n"
        finally:
            arc.close()

    def test_a_run_without_one_is_absent_not_captured(self, tmp_path):
        """An older ms-moe-maker preserved nothing. That is a plain fact about
        the run, and it must not read as a failure or as a success."""
        run = built(tmp_path, recipe=None)
        arc = store.Archive(tmp_path / "a.db")
        try:
            harvest.harvest_run(arc, "S", run_dict(run),
                                 B.Blobs(tmp_path / "blobs"))
            row = arc.surgery_row(harvest.run_key("S", run, 100.0))
            assert row["recipe_state"] == store.RECIPE_ABSENT
            assert row["recipe_blob"] == ""
        finally:
            arc.close()

    def test_no_blob_store_records_that_nothing_was_attempted(self, tmp_path):
        """Distinct from `absent`. One means this run has no recipe; the other
        means nobody looked, because there was nowhere to put it."""
        run = built(tmp_path)
        arc = store.Archive(tmp_path / "a.db")
        try:
            harvest.harvest_run(arc, "S", run_dict(run), None)
            row = arc.surgery_row(harvest.run_key("S", run, 100.0))
            assert row["recipe_state"] == store.RECIPE_NOT_CAPTURED
        finally:
            arc.close()

    def test_a_recipe_that_cannot_be_stored_is_unreadable(self, tmp_path,
                                                          monkeypatch):
        """THE ONLY STATE THAT IS A PROBLEM. The text is on disk right now and
        is not in the archive - which is the opposite of a build that predates
        the feature, and collapsing the two would hide the live one."""
        run = built(tmp_path)
        blobs = B.Blobs(tmp_path / "blobs")
        monkeypatch.setattr(blobs, "put", lambda _p: (_ for _ in ()).throw(
            OSError("disk full")))
        arc = store.Archive(tmp_path / "a.db")
        try:
            harvest.harvest_run(arc, "S", run_dict(run), blobs)
            row = arc.surgery_row(harvest.run_key("S", run, 100.0))
            assert row["recipe_state"] == store.RECIPE_UNREADABLE
            assert row["recipe_blob"] == ""
        finally:
            arc.close()

    def test_the_row_is_archived_either_way(self, tmp_path, monkeypatch):
        """The manifest is the record. Refusing to keep it because the recipe
        could not be stored would trade the whole feature for part of it."""
        run = built(tmp_path)
        blobs = B.Blobs(tmp_path / "blobs")
        monkeypatch.setattr(blobs, "put", lambda _p: (_ for _ in ()).throw(
            OSError("nope")))
        arc = store.Archive(tmp_path / "a.db")
        try:
            assert harvest.harvest_run(arc, "S", run_dict(run), blobs)
            assert arc.count("surgeries") == 1
        finally:
            arc.close()


class TestTheWireFromConfigToBlob:
    """THE WIRING, WHICH NOTHING ABOVE TOUCHES.

    Every test in the class above calls `harvest_run` and hands it a store, so
    all of them pass while nobody upstream passes one. Mutation testing proved
    it: deleting the store from `harvest_stage`, and then deleting it from
    app.py's call, left the whole file green. A capture function nothing
    supplies is the same defect as a column nothing draws.
    """

    def test_harvest_stage_passes_the_store_down(self, tmp_path):
        run = built(tmp_path)
        arc = store.Archive(tmp_path / "a.db")
        try:
            scanned = {"name": "S", "runs": [run_dict(run)]}
            harvest.harvest_stage(arc, scanned, B.Blobs(tmp_path / "blobs"))
            row = arc.surgery_row(harvest.run_key("S", run, 100.0))
            assert row["recipe_state"] == store.RECIPE_CAPTURED, (
                "harvest_stage did not hand the store to harvest_run")
        finally:
            arc.close()

    def test_the_app_captures_it_from_config_alone(self, tmp_path):
        """END TO END through the thing that actually runs: a config, a stage
        on disk, one poll of /api/state. Nothing here names a Blobs
        store: the point is that app.py builds one from `archive.blobs`
        and passes it down.
        """
        from fastapi.testclient import TestClient

        from seren_theatre.app import create_app
        from seren_theatre.config import (ArchiveConfig, StageConfig,
                                          TheatreConfig)

        stage_dir = tmp_path / "workbench"
        run = stage_dir / "runs" / "0.5B"
        run.mkdir(parents=True)
        (run / mf.MANIFEST_NAME).write_text(json.dumps(
            {"schema_version": 1, "name": "mine", "state": "finished",
             "started": 100.0, "updated": 110.0, "finished": 110.0,
             "ok": True, "build_id": "bid-1", "stages": []}), encoding="utf-8")
        (run / (mf.RECIPE_STEM + ".yaml")).write_text(
            "name: mine\n", encoding="utf-8")

        cfg = TheatreConfig()
        cfg.archive = ArchiveConfig(enabled=True,
                                    dsn=str(tmp_path / "archive.db"),
                                    blobs=str(tmp_path / "blobs"))
        cfg.stages = [StageConfig(name="S", path=str(stage_dir))]
        app = create_app(cfg)
        with TestClient(app) as client:
            body = client.get("/api/state").json()
            assert body["archive"]["enabled"] is True, body["archive"]
            rows = app.state.archive.surgeries(limit=5)

        assert rows, "the finished run was never harvested"
        assert rows[0]["recipe_state"] == store.RECIPE_CAPTURED, (
            f"the app did not build and pass a blob store: "
            f"{rows[0]['recipe_state']!r}")
        assert rows[0]["recipe_blob"], "captured with no digest"
        # And the bytes are really there, under their own hash.
        kept = B.Blobs(tmp_path / "blobs").open(
            rows[0]["recipe_blob"]).read().decode("utf-8")
        assert kept == "name: mine\n"


class TestTheSuffixIsNotOurs:
    @pytest.mark.parametrize("suffix", [".yaml", ".json", ".yml"])
    def test_whatever_the_builder_kept_is_found(self, tmp_path, suffix):
        run = built(tmp_path, recipe="x", suffix=suffix)
        arc = store.Archive(tmp_path / "a.db")
        try:
            harvest.harvest_run(arc, "S", run_dict(run),
                                 B.Blobs(tmp_path / "blobs"))
            row = arc.surgery_row(harvest.run_key("S", run, 100.0))
            assert row["recipe_state"] == store.RECIPE_CAPTURED
        finally:
            arc.close()

    def test_find_recipe_reports_none_rather_than_a_path(self, tmp_path):
        (tmp_path / "bare").mkdir()
        assert mf.find_recipe(tmp_path / "bare") is None
        assert mf.find_recipe(tmp_path / "never-existed") is None

    def test_a_blank_recipe_is_found_not_called_missing(self, tmp_path):
        """A recipe that was written and came back empty is a DIFFERENT fact
        from a run that never had one - one is a bug somewhere upstream, the
        other is a build that predates the feature. Pinned on Theatre's copy of
        the finder as well as the builder's, because there are two of them and
        the whole reason there are two is that they can drift."""
        d = tmp_path / "blank"
        d.mkdir()
        (d / (mf.RECIPE_STEM + ".yaml")).write_text("", encoding="utf-8")
        found = mf.find_recipe(d)
        assert found is not None, "an empty recipe was reported as no recipe"
        assert found.read_text(encoding="utf-8") == ""


class TestTheColumnsReachAnExistingArchive:
    """THE MIGRATION, AND WHY IT NEEDED WRITING AT ALL.

    `CREATE TABLE IF NOT EXISTS` is a no-op on a table that exists, so a column
    added to SCHEMA appears on every fresh database - every test - and on no
    archive that was already on disk. The first write naming it would raise "no
    such column" on somebody else's box, months later, while the suite stayed
    green. `rebuild()`'s promise that adding a column "cannot lose anything"
    was true about FILLING one and silent about creating it.
    """

    def test_an_archive_predating_the_columns_gains_them(self, tmp_path):
        import sqlite3

        arc = store.Archive(tmp_path / "a.db")
        arc.put_surgery({"run_key": "k", "build_id": "b", "stage": "S",
                         "name": "n", "run_path": "/x", "started": 1.0,
                         "finished": 2.0, "state": "finished", "ok": 1,
                         "harvested": 1.0, "run_present": 1,
                         "manifest": "{}"})
        arc.close()

        # Rewind to before the columns existed, with a row already in place -
        # which is the only situation that matters and the one a fresh database
        # can never reproduce.
        db = sqlite3.connect(str(tmp_path / "a.db"))
        for _table, column, _decl in store.ADDED_COLUMNS:
            db.execute(f"ALTER TABLE surgeries DROP COLUMN {column}")
        db.commit()
        db.close()

        again = store.Archive(tmp_path / "a.db")
        try:
            present = again._columns("surgeries")
            for _table, column, _decl in store.ADDED_COLUMNS:
                assert column in present, f"{column} never arrived"
            assert again.count("surgeries") == 1, "the migration lost the row"
        finally:
            again.close()

    def test_every_added_column_has_a_default(self):
        """sqlite refuses to ADD a NOT NULL column to a table with rows in it
        unless there is a default, and the rows are the entire point."""
        for _table, column, decl in store.ADDED_COLUMNS:
            assert "DEFAULT" in decl.upper(), (
                f"{column} has no DEFAULT, so it cannot be added to an "
                f"archive that already has history in it")

    def test_the_columns_are_declared_in_exactly_one_place(self):
        """Listing them in ADDED_COLUMNS *and* in the CREATE statements is
        two copies of one fact - and the copy in CREATE is the one that would
        make the migration path dead code nobody exercises."""
        for _table, column, _decl in store.ADDED_COLUMNS:
            assert not any(column in s for s in store.SCHEMA), (
                f"{column} appears in SCHEMA as well, so fresh databases skip "
                f"the ALTER path and it stops being tested")
