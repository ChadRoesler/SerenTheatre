"""What survives after the run directory is gone.

THE PROBLEM RESTATED, because every test here is a consequence of it: the
record worth keeping lives INSIDE the artifact you have to delete. Ten
kilobytes of eval report inside forty-five gigabytes of run. Deleting the run
is correct and routine; doing it destroys the evidence.

Three properties get guarded, and the third is the one that will matter in a
year:

  * a harvested run outlives its directory, and SAYS SO. A row whose run is
    gone is a historical record, not a run - and Theatre's own database gets
    no more benefit of the doubt than a manifest does.
  * harvest is idempotent, because it runs on a poll.
  * DOCUMENTS ARE THE TRUTH AND COLUMNS ARE AN INDEX. `rebuild` recomputes
    every column from the stored JSON, which is what makes a schema change a
    migration that cannot lose anything.
"""
from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pytest

from seren_theatre.archive import blobs as B
from seren_theatre.archive import harvest as H
from seren_theatre.archive import store as S
from seren_theatre.config import StageConfig, TheatreConfig


@pytest.fixture
def archive(tmp_path):
    with S.connect(str(tmp_path / "a.db")) as a:
        yield a


def _manifest(**over):
    base = {"schema_version": 1, "name": "dryrun_0.5B", "build_id": "cafe0001",
            "started": 1700000000.0, "finished": 1700003600.0, "ok": True,
            "state": "finished", "stages": []}
    base.update(over)
    return base


def _run(path="/lab/dryrun_0.5B", **over):
    out = {"name": "dryrun_0.5B", "path": path, "manifest": _manifest(),
           "state": "finished", "eval": None, "gate": None}
    out.update(over)
    return out


# ── the DSN, so the engine is a config line and not a rewrite ────────────────

@pytest.mark.parametrize("dsn, target", [
    ("~/x/a.db", str(Path.home() / "x" / "a.db")),
    ("sqlite:///~/x/a.db", str(Path.home() / "x" / "a.db")),
    ("sqlite:///mnt/nvme/a.db", "/mnt/nvme/a.db"),
    ("sqlite:////mnt/nvme/a.db", "/mnt/nvme/a.db"),
])
def test_a_bare_path_and_a_url_mean_the_same_thing(dsn, target):
    """Postel at the config layer. Somebody who wrote a path should not get a
    lecture about URL syntax, and `sqlite:///~/...` is a thing people type."""
    assert S.parse_dsn(dsn) == ("sqlite", target)


def test_an_unimplemented_engine_says_so_instead_of_pretending():
    """Shipping a driver that has never run against a real server would be a
    confident claim about untested code - the one kind this service exists not
    to make. The error has to name what is missing and why."""
    with pytest.raises(S.ArchiveError) as exc:
        S.connect("mysql://user@host/theatre")
    assert "only sqlite is implemented" in str(exc.value)
    assert "second writer" in str(exc.value)


def test_the_archive_may_not_live_inside_a_watched_directory(tmp_path):
    """It would be deleted by exactly the cleanup it exists to survive.

    And it would make the viewer a participant in what it is watching, which is
    the one promise stageguard exists to keep."""
    from seren_theatre.stageguard import WritesIntoStage
    stage = tmp_path / "lab"
    stage.mkdir()
    cfg = TheatreConfig()
    cfg.stages = [StageConfig(name="Lab", path=str(stage))]
    with pytest.raises(WritesIntoStage):
        S.connect(str(stage / "archive.db"), cfg)


def test_a_newer_schema_refuses_rather_than_guessing(tmp_path):
    path = tmp_path / "a.db"
    with S.connect(str(path)) as a:
        a._db.execute("UPDATE meta SET value=? WHERE key='schema_version'",
                      (str(S.SCHEMA_VERSION + 1),))
        a._db.commit()
    with pytest.raises(S.ArchiveError) as exc:
        S.connect(str(path))
    assert "Upgrade" in str(exc.value)


# ── harvest ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("state, stored", [
    ("finished", True), ("failed", True),
    ("running", False), ("stalled", False), ("pending", False), ("", False),
])
def test_only_a_run_that_has_stopped_is_harvested(archive, state, stored):
    """A manifest is rewritten on every stage transition. Archiving one
    mid-run would store a sentence that was about to be replaced - and the row
    would then be indistinguishable from a run that genuinely stopped there."""
    run = _run(state=state, manifest=_manifest(state=state))
    assert H.harvest_run(archive, "Lab", run) is stored
    assert archive.count("surgeries") == (1 if stored else 0)


def test_harvesting_twice_stores_once(archive):
    """It runs on a poll. Anything else would be a race with itself."""
    run = _run()
    assert H.harvest_run(archive, "Lab", run) is True
    assert H.harvest_run(archive, "Lab", run) is False
    assert archive.count("surgeries") == 1


def test_an_uninstrumented_run_is_left_alone(archive):
    """No manifest means nothing to record. A directory with a GGUF in it is
    still watchable; it is just not a run that said anything about itself."""
    assert H.harvest_run(archive, "Lab", _run(manifest=None)) is False


def test_two_runs_of_one_recipe_are_two_rows(archive):
    """build_id is the digest of the CONFIG. Two runs of one recipe share it,
    and they are two runs - so identity is the stage, the path and the start."""
    a = _run(manifest=_manifest(started=1000.0))
    b = _run(manifest=_manifest(started=2000.0))
    H.harvest_run(archive, "Lab", a)
    H.harvest_run(archive, "Lab", b)
    assert archive.count("surgeries") == 2


def test_the_same_run_in_two_stages_is_two_rows(archive):
    """Two stages can hold a directory with the same name."""
    H.harvest_run(archive, "Lab", _run())
    H.harvest_run(archive, "Lyceum", _run())
    assert archive.count("surgeries") == 2


def test_re_running_eval_adds_a_grading_rather_than_replacing_one(archive):
    """THE PAIR IS THE POINT. The run that said C# 0/10 and the run that said
    9/10 after installing a compiler are, together, the clearest possible
    statement of what actually changed. Keying on build_id would have the
    second silently overwrite the first."""
    run = _run(eval={"build_id": "cafe0001", "generated": 100.0, "ok": False,
                       "provenance": "matches"})
    H.harvest_run(archive, "Lab", run)
    key = archive.surgeries()[0]["run_key"]
    archive.put_grading({
        "grading_key": H.grading_key(key, {"generated": 200.0,
                                           "build_id": "cafe0001"}),
        "run_key": key, "build_id": "cafe0001", "generated": 200.0,
        "provenance": "matches", "ok": 1, "report": S.dumps({"ok": True})})
    gradings = archive.surgeries()[0]["gradings"]
    assert len(gradings) == 2
    assert [g["generated"] for g in gradings] == [200.0, 100.0], "newest first"


def test_a_gates_unmeasured_count_is_kept_apart_from_its_findings(archive):
    """Folded into findings it reads as a problem; dropped it reads as a pass.
    It is neither, and that distinction has to survive into the database."""
    H.harvest_run(archive, "Lab", _run(gate={
        "status": "unmeasurable", "findings": ["two experts are alike"],
        "unmeasured": ["cross-domain loss: no held-out rows", "config audit"]}))
    gate = archive.surgeries()[0]["gate"]
    assert gate["findings"] == 1 and gate["unmeasured"] == 2
    assert gate["status"] == "unmeasurable"


def test_an_unknown_ok_stays_unknown(archive):
    """Three values, all the way to the column. Coercing "the manifest never
    said" into False turns a missing reading into a failed build - the same
    mistake as folding `unmeasurable` into `fail` one layer up."""
    H.harvest_run(archive, "Lab", _run(manifest=_manifest(ok=None)))
    assert archive.surgeries()[0]["ok"] is None


# ── the reason this exists ──────────────────────────────────────────────────

def test_the_record_outlives_the_run_and_says_that_it_did(tmp_path):
    """THE WHOLE FEATURE, in one test.

    Harvest a finished run, delete the forty-five gigabytes, and the ten
    kilobytes that were worth keeping are still there - correctly reported as
    a historical record rather than as a directory somebody could open.
    """
    run_dir = tmp_path / "dryrun_0.5B"
    run_dir.mkdir()
    with S.connect(str(tmp_path / "a.db")) as archive:
        run = _run(path=str(run_dir), eval={
            "build_id": "cafe0001", "generated": 5.0, "provenance": "matches",
            "ok": True,
            "routing": {"experts": [{"name": "python", "enrichment": 2.14}]}})
        H.harvest_run(archive, "Lab", run)
        assert archive.surgeries()[0]["run_present"] == 1

        shutil.rmtree(run_dir)

        rows = H.reconcile(archive, archive.surgeries())
        assert rows[0]["run_present"] is False, (
            "a row whose run is gone must not claim the directory is there - "
            "Theatre's own database gets no more benefit of the doubt than a "
            "manifest does")
        assert (rows[0]["gradings"][0]["report"]["routing"]["experts"][0]
                ["enrichment"] == 2.14), "the measurement did not survive"
        # And it stuck, so the stored column converges without being trusted.
        assert archive.surgeries()[0]["run_present"] == 0


# ── documents are the truth; columns are an index ───────────────────────────

def test_the_original_document_is_stored_verbatim(archive):
    run = _run(manifest=_manifest(some_future_field="kept"))
    H.harvest_run(archive, "Lab", run)
    assert archive.surgeries()[0]["manifest"]["some_future_field"] == "kept", (
        "a field this version does not index still has to be IN the row, or "
        "the archive is a lossy projection of the thing it is preserving")


def test_rebuild_recomputes_every_column_from_the_documents(archive):
    """THE PROPERTY THAT MAKES THE SCHEMA SAFE TO CHANGE.

    Adding a column becomes a migration that cannot lose anything, and a column
    computed wrongly is a bug rather than a data loss - because the evidence it
    was computed from is still sitting in the row. Simulated here by corrupting
    the columns and rebuilding them back.
    """
    H.harvest_run(archive, "Lab", _run(
        gate={"status": "ok", "findings": [], "unmeasured": ["a"]},
        eval={"build_id": "cafe0001", "generated": 9.0, "ok": True,
              "provenance": "matches"}))
    with archive._db:
        archive._db.execute("UPDATE surgeries SET name='WRONG', state='WRONG'")
        archive._db.execute("UPDATE gates SET status='WRONG', unmeasured=99")
        archive._db.execute("UPDATE gradings SET build_id='WRONG'")

    touched = archive.rebuild(H.derive)
    assert touched == 3

    row = archive.surgeries()[0]
    assert row["name"] == "dryrun_0.5B" and row["state"] == "finished"
    assert row["gate"]["status"] == "ok" and row["gate"]["unmeasured"] == 1
    assert row["gradings"][0]["build_id"] == "cafe0001"


def test_provenance_is_decided_once_and_never_rebuilt(archive):
    """A RELATIONAL fact cannot be recomputed from one document.

    "Did this eval grade the build on disk" needs the report AND the manifest,
    so it is settled at harvest against the manifest that was there then - which
    is also the only moment it has a single honest answer. Re-deriving it later
    against a run that has since been rebuilt would quietly answer a different
    question and present it as the same one.
    """
    H.harvest_run(archive, "Lab", _run(
        eval={"build_id": "cafe0001", "generated": 9.0, "ok": True,
              "provenance": "matches"}))
    with archive._db:
        archive._db.execute("UPDATE gradings SET provenance='stale'")
    archive.rebuild(H.derive)
    assert archive.surgeries()[0]["gradings"][0]["provenance"] == "stale", (
        "rebuild recomputed provenance from the document alone, which cannot "
        "know what the manifest said")


def test_one_unparseable_document_does_not_take_the_history_down(archive):
    """The row still carries its columns, which is a real if lesser reading.
    Losing everything to one bad blob would be the archive punishing its reader
    for a write that went wrong months ago."""
    H.harvest_run(archive, "Lab", _run())
    with archive._db:
        archive._db.execute("UPDATE surgeries SET manifest='{not json'")
    row = archive.surgeries()[0]
    assert row["manifest"] is None
    assert row["name"] == "dryrun_0.5B", "the indexed columns survived"


# ── blobs ───────────────────────────────────────────────────────────────────

def test_identical_bytes_are_stored_once(tmp_path):
    """Dedup falls out of content addressing rather than being a feature.
    Two prompt books sharing a corpus share the file."""
    store = B.Blobs(tmp_path / "blobs")
    a, b = tmp_path / "a.zip", tmp_path / "b.zip"
    a.write_bytes(b"same"); b.write_bytes(b"same")
    assert store.put(a) == store.put(b)
    assert len(list(store.walk())) == 1


def test_the_filename_is_the_checksum_so_corruption_is_detectable(tmp_path):
    """Impossible to do this cheaply with a BLOB column: there is no
    independent name to check the bytes against, so corruption is undetectable
    until something downstream chokes on it."""
    store = B.Blobs(tmp_path / "blobs")
    src = tmp_path / "a.zip"
    src.write_bytes(b"payload")
    digest = store.put(src)
    assert store.verify(digest)
    store.path_for(digest).write_bytes(b"tampered")
    assert not store.verify(digest)


@pytest.mark.parametrize("bad", ["../../etc/passwd", "", "zz" * 32, "abc",
                                 "/etc/passwd", "a" * 63])
def test_a_hash_from_a_database_row_is_still_untrusted_input(tmp_path, bad):
    """`../..` in a column is exactly as effective as `../..` in a URL.
    Checking the SHAPE is the stronger guard - a string that cannot contain a
    separator cannot express a traversal."""
    with pytest.raises(B.BlobError):
        B.Blobs(tmp_path).path_for(bad)


def test_a_half_written_blob_never_appears_under_its_final_name(tmp_path):
    """Content addressing promises the name matches the bytes. A reader that
    finds a partial file under a full hash finds the one thing this design is
    supposed to make impossible - hence write-to-temp-then-rename."""
    store = B.Blobs(tmp_path / "blobs")
    src = tmp_path / "big.bin"
    src.write_bytes(b"x" * (B.CHUNK * 2))
    digest = store.put(src)
    assert store.verify(digest)
    assert not list(store.path_for(digest).parent.glob("*.part"))


def test_orphans_are_reported_and_never_swept(tmp_path):
    """A sweep that runs itself is a delete nobody asked for. Hands on the
    surface - and dedup means the caller, not this module, owns the refcount."""
    store = B.Blobs(tmp_path / "blobs")
    keep, drop = tmp_path / "k", tmp_path / "d"
    keep.write_bytes(b"keep"); drop.write_bytes(b"drop")
    kept, gone = store.put(keep), store.put(drop)
    assert store.orphans([kept]) == [gone]
    assert store.has(gone), "orphans() deleted something; it must only report"


# ── the invariants harvest-on-a-GET must not break ──────────────────────────

def test_the_scan_writes_but_adds_no_mutating_route(tmp_path):
    """"The viewer writes now" sounds like the end of the read-only promise.

    It is not, and the distinction is worth pinning rather than explaining in a
    comment. Two separate guarantees:

      * a base install exposes zero verbs that change anything - harvest has no
        HTTP verb, it happens inside the scan;
      * nothing is written inside a stage - the archive path went through
        stageguard when it was opened.

    Both still hold with harvesting on. If a later change adds a POST to make
    this convenient, this test is where it gets caught.
    """
    from fastapi.testclient import TestClient
    from seren_theatre.app import create_app
    from seren_theatre.stageguard import mutating_routes

    stage = tmp_path / "lab"
    stage.mkdir()
    cfg = TheatreConfig(archive={"dsn": str(tmp_path / "a.db")})
    cfg.stages = [StageConfig(name="Lab", path=str(stage))]
    # THE CLAIM IS ABOUT HARVEST, NOT ABOUT THE WHOLE APP, and the first
    # version of this test got that wrong: it asserted the app had NO mutating
    # routes at all, which is true only where Backstage is unmounted - so it
    # passed for the wrong reason in the environment it was written in and
    # failed the moment the writer was installed. Backstage is entitled to its
    # write surface; the question here is whether ARCHIVING adds to it.
    #
    # Asked by difference, which is the only form of the question that is
    # actually about harvesting.
    off = TheatreConfig(archive={"enabled": False})
    off.stages = list(cfg.stages)
    before = {p for p, _ in mutating_routes(create_app(off))}
    app = create_app(cfg)
    after = {p for p, _ in mutating_routes(app)}
    assert after == before, (
        f"enabling the archive added {sorted(after - before)} to the write "
        f"surface; harvesting is supposed to happen inside the scan and own "
        f"no verb of its own")
    with TestClient(app) as client:
        assert client.get("/api/state").json()["archive"]["enabled"] is True


def test_a_broken_archive_never_costs_you_the_dashboard(tmp_path):
    """Watching a run has never required being able to archive one.

    A full disk is exactly the moment you most want to still be able to look at
    what is happening, so the failure is REPORTED in the payload and the
    readings come back regardless.
    """
    from fastapi.testclient import TestClient
    from seren_theatre.app import create_app

    stage = tmp_path / "lab"
    stage.mkdir()
    # A directory where the database file should be: open() cannot succeed.
    broken = tmp_path / "a.db"
    broken.mkdir()
    cfg = TheatreConfig(archive={"dsn": str(broken)})
    cfg.stages = [StageConfig(name="Lab", path=str(stage))]
    with TestClient(create_app(cfg)) as client:
        payload = client.get("/api/state").json()
    assert payload["archive"]["enabled"] is False
    assert payload["archive"]["error"], "a dead archive said nothing"
    assert "stages" in payload, "the dashboard died with the archive"


def test_archiving_can_be_turned_off_and_says_so(tmp_path):
    from fastapi.testclient import TestClient
    from seren_theatre.app import create_app

    stage = tmp_path / "lab"
    stage.mkdir()
    cfg = TheatreConfig(archive={"enabled": False})
    cfg.stages = [StageConfig(name="Lab", path=str(stage))]
    with TestClient(create_app(cfg)) as client:
        block = client.get("/api/state").json()["archive"]
    assert block["enabled"] is False and "disabled" in block["error"]


def test_the_archive_keeps_the_document_and_not_the_viewers_reading_of_it(
        tmp_path):
    """THE LOSSY-ARCHIVE BUG, pinned.

    Harvest first stored what the viewer draws, which drops `avg_length` and
    `capped_generations` - so the archive was a lossy copy under a docstring
    promising verbatim documents, and the loss was permanent the moment the
    run was deleted.

    A field this version ignores has to still be in the row, because the whole
    argument for keeping documents is that the NEXT version might not ignore
    it.
    """
    from seren_theatre import evalreport as ev

    raw = {"schema_version": 1, "build_id": "cafe0001", "generated": 5.0,
           "ok": True, "message": "measured", "routing": {},
           "stages": {"python": {"exact_match": 0.9, "avg_length": 64.0,
                                 "capped_generations": 2,
                                 "scored_samples": 18,
                                 "attempted_samples": 20}}}
    projected = ev.project_eval(raw, "cafe0001")
    assert "avg_length" not in projected["quality"][0], (
        "if the projection ever stops dropping this the test is moot, but the "
        "principle is not: some field is always dropped")

    with S.connect(str(tmp_path / "a.db")) as archive:
        H.harvest_run(archive, "Lab", _run(eval=projected))
        stored = archive.surgeries()[0]["gradings"][0]["report"]

    assert stored["stages"]["python"]["avg_length"] == 64.0, (
        "the archive kept the viewer's reading instead of the evidence")
    assert stored["stages"]["python"]["capped_generations"] == 2


def test_an_archived_report_is_re_projected_on_read(tmp_path):
    """Which is what makes a viewer upgrade improve old rows.

    A projection frozen into the database at harvest can only get staler; one
    recomputed from the stored document gains every derived field a later
    version learns to compute - `saturated`, `input_blind`, `thin` and whatever
    comes next - for runs that were archived before those existed.
    """
    from fastapi.testclient import TestClient
    from seren_theatre import evalreport as ev
    from seren_theatre.app import create_app

    stage = tmp_path / "lab"
    run = stage / "dryrun_0.5B"
    run.mkdir(parents=True)
    (run / "msmoe-run.json").write_text(json.dumps(
        _manifest(name="dryrun_0.5B")), encoding="utf-8")
    (run / "eval_report.json").write_text(json.dumps({
        "schema_version": 1, "build_id": "cafe0001", "generated": 5.0,
        "ok": True,
        "routing": {"experts": {"python": {"own_share": 0.7,
                                           "enrichment": 2.1}},
                    "top_k": 2, "mean_gate_confidence": 0.49,
                    "uniform_confidence": 0.5, "mean_js_bits": 0.0},
        "stages": {"python": {"exact_match": 0.9, "scored_samples": 3,
                              "attempted_samples": 20}}}), encoding="utf-8")

    cfg = TheatreConfig(archive={"dsn": str(tmp_path / "a.db")})
    cfg.stages = [StageConfig(name="Lab", path=str(stage))]
    with TestClient(create_app(cfg)) as client:
        client.get("/api/state")
        shutil.rmtree(run)
        row = client.get("/api/archive").json()["surgeries"][0]

    view = row["gradings"][0]["view"]
    assert view["quality"][0]["thin"] is True
    assert view["routing"]["saturated"] is True, (
        "0.49 against a top-2 ceiling of 0.50 is saturated; the derived verdict "
        "was not recomputed from the stored document")
    assert view["routing"]["input_blind"] is True
    assert row["run_present"] is False


# ── the threadpool ──────────────────────────────────────────────────────────

def test_the_archive_survives_being_used_from_many_threads(tmp_path):
    """THE BUG THAT WOULD HAVE SHIPPED, and the reason it nearly did.

    `sqlite3.connect` defaults to check_same_thread=True, and FastAPI runs
    every sync endpoint in a THREADPOOL. The connection is opened lazily on
    whichever pool thread serves the first request, so a later request landing
    on a different thread raises ProgrammingError.

    It passed every test and every hand-run smoke, because with a handful of
    requests the pool reuses one thread. It only breaks with a browser polling
    every five seconds while somebody clicks around - which is to say, only in
    use, and intermittently, which is the worst way for anything to break.

    So this hammers it from real threads rather than trusting that a sequence
    of requests happened to be enough.
    """
    import threading

    with S.connect(str(tmp_path / "a.db")) as archive:
        H.harvest_run(archive, "Lab", _run())
        errors = []

        def hammer(n):
            try:
                for i in range(20):
                    archive.count("surgeries")
                    archive.surgeries()
                    archive.put_prompt_book({
                        "book_id": f"{n:02d}{i:062d}", "name": f"b{n}",
                        "imported": 1.0, "bytes": 1, "meta": "{}"})
                    archive.prompt_book_ids()
            except Exception as exc:                    # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=hammer, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"the archive is not usable from a threadpool: {errors[0]!r}"
        assert archive.count("prompt_books") == 160


def test_every_statement_runs_under_the_lock():
    """check_same_thread=False lifts the CHECK; the lock is what makes it safe.

    Turning the check off and stopping there would swap a loud, immediate
    ProgrammingError for a quiet one - interleaved statements on a shared
    cursor, which surfaces as wrong rows rather than as an exception. Read by
    AST because "I remembered on all thirteen" is not a property anyone can
    keep by hand across future edits.
    """
    import ast

    src = Path(S.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)

    def guarded(node, stack):
        """Is this call lexically inside a `with self._lock`?"""
        for parent in stack:
            if not isinstance(parent, ast.With):
                continue
            for item in parent.items:
                expr = item.context_expr
                if (isinstance(expr, ast.Attribute) and expr.attr == "_lock"):
                    return True
        return False

    unguarded = []

    def walk(node, stack):
        for child in ast.iter_child_nodes(node):
            if (isinstance(child, ast.Call)
                    and isinstance(child.func, ast.Attribute)
                    and child.func.attr in ("execute", "executemany")
                    and isinstance(child.func.value, ast.Attribute)
                    and child.func.value.attr == "_db"):
                if not guarded(child, stack):
                    unguarded.append(getattr(child, "lineno", "?"))
            walk(child, [child] + stack)

    walk(tree, [])
    assert not unguarded, (
        f"statements at lines {unguarded} run without holding self._lock. "
        f"With check_same_thread=False that is not an error you will see - it "
        f"is interleaved statements on one cursor, which shows up as wrong "
        f"rows instead of as an exception.")
