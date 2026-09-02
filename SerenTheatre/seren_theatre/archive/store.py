"""Where a run's record lives after the run's directory is gone.

THE PROBLEM, STATED PROPERLY. It is not "files might get deleted." It is that
the thing worth keeping is INSIDE the thing you have to delete. An eval report
is about ten kilobytes; the rung it sits in is forty-five gigabytes. Deleting
that rung is not an accident or a mistake - it is the correct, routine
operation when you need the disk back. So the current design guarantees that
ordinary housekeeping destroys the evidence, and the more disciplined you are
about disk, the less history you have.

Harvest decouples the record from the artifact. A few hundred kilobytes per run
outlives the weights it describes.

────────────────────────────────────────────────────────────────────────────
DOCUMENTS ARE THE TRUTH; COLUMNS ARE AN INDEX OVER THEM.

Every row keeps the ORIGINAL JSON verbatim - the whole manifest, the whole eval
report, the whole gate report - and the columns beside it are values lifted out
of that JSON so they can be sorted and filtered.

That is not redundancy, it is the property that makes the schema safe to
change. `rebuild()` recomputes every column from the stored documents, so
adding a column later is a migration that cannot lose anything, and a column
that turns out to be wrong is a bug rather than a data loss. It is the same
bargain the JSON-plus-index design would have made across two stores, kept
inside one - and it matters more here, because once the rung is deleted this
IS the only copy.

Never write a column without the document it came from. A column is a claim;
the document is the evidence.
────────────────────────────────────────────────────────────────────────────

ON THE ENGINE. The DSN exists so the engine is a config line rather than a
rewrite. SQLite is the default and the only one implemented, deliberately:
Theatre's `requires` is empty on purpose, Margin and Probe are already SQLite,
and a viewer that needs a database daemon installed before it can look at a
directory has broken the bargain that makes it droppable on a Nano.

A `mysql://` DSN raises and says exactly what is missing rather than pretending
to work. Shipping a driver nobody has run against a real server would be a
confidently wrong claim about the one thing this service exists not to make.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

SCHEMA_VERSION = 1

# THE DOCUMENT COLUMN OF EACH TABLE, and the list is load-bearing: rebuild()
# walks it to recompute every derived column, so a table missing from here is a
# table that silently stops being rebuildable.
DOCUMENTS = {
    "surgeries": "manifest",
    "gradings": "report",
    "gates": "report",
}

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS meta (
           key TEXT PRIMARY KEY,
           value TEXT NOT NULL)""",

    # A BUILD THAT HAPPENED. `run_key` rather than build_id as the identity,
    # because build_id is the digest of the CONFIG - two runs of the same
    # recipe share it, and they are two runs. The rung path plus its start
    # time is what makes one of them this one.
    """CREATE TABLE IF NOT EXISTS surgeries (
           run_key      TEXT PRIMARY KEY,
           build_id     TEXT NOT NULL DEFAULT '',
           stage        TEXT NOT NULL DEFAULT '',
           name         TEXT NOT NULL DEFAULT '',
           rung_path    TEXT NOT NULL DEFAULT '',
           started      REAL,
           finished     REAL,
           state        TEXT NOT NULL DEFAULT '',
           ok           INTEGER,
           harvested    REAL NOT NULL,
           -- IS THE DIRECTORY STILL THERE. The one column that must never be
           -- guessed: a row whose rung is gone is a HISTORICAL RECORD, and
           -- rendering it beside live rungs would have the viewer assert that
           -- a directory exists when it does not. Refreshed on every harvest.
           rung_present INTEGER NOT NULL DEFAULT 1,
           manifest     TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS surgeries_build ON surgeries(build_id)",
    "CREATE INDEX IF NOT EXISTS surgeries_started ON surgeries(started DESC)",

    # A GRADING. N per surgery: eval is a separate verb, so re-running it after
    # installing the missing compiler leaves two results, and the PAIR is the
    # clearest possible statement of what changed.
    """CREATE TABLE IF NOT EXISTS gradings (
           grading_key  TEXT PRIMARY KEY,
           run_key      TEXT NOT NULL,
           build_id     TEXT NOT NULL DEFAULT '',
           generated    REAL,
           -- Computed AT HARVEST, against the manifest that was on disk at the
           -- time. Deciding it later, from a rung that has since been rebuilt,
           -- would answer a different question than the one being asked.
           provenance   TEXT NOT NULL DEFAULT 'unknown',
           ok           INTEGER,
           report       TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS gradings_run ON gradings(run_key)",

    # THE GATE, at most one per surgery. It runs inside the build, before the
    # stitch, so unlike an eval it cannot be left over from an earlier one.
    """CREATE TABLE IF NOT EXISTS gates (
           run_key      TEXT PRIMARY KEY,
           status       TEXT NOT NULL DEFAULT '',
           findings     INTEGER NOT NULL DEFAULT 0,
           unmeasured   INTEGER NOT NULL DEFAULT 0,
           report       TEXT NOT NULL)""",

    # A PROMPT BOOK: a recipe bundle somebody can stage again. The zip itself
    # is NOT in here - see blobs.py for why - only its hash.
    """CREATE TABLE IF NOT EXISTS prompt_books (
           book_id      TEXT PRIMARY KEY,
           name         TEXT NOT NULL DEFAULT '',
           created      REAL,
           imported     REAL NOT NULL,
           build_id     TEXT NOT NULL DEFAULT '',
           bytes        INTEGER NOT NULL DEFAULT 0,
           recipe       TEXT NOT NULL DEFAULT '',
           notes        TEXT NOT NULL DEFAULT '',
           meta         TEXT NOT NULL DEFAULT '{}')""",
]


class ArchiveError(Exception):
    """The archive could not be opened or written. Always surfaced."""


def parse_dsn(dsn: str) -> Tuple[str, str]:
    """(driver, target). A bare path is sqlite, because that is the kind thing.

    Postel at the config layer: `~/seren-theatre/archive.db` and
    `sqlite:///~/seren-theatre/archive.db` mean the same thing, and somebody
    who wrote the first should not get a lecture about URL syntax.
    """
    raw = (dsn or "").strip()
    if not raw:
        return "sqlite", ""
    if "://" not in raw:
        return "sqlite", os.path.expanduser(raw)
    driver, _, target = raw.partition("://")
    driver = driver.lower()
    if driver == "sqlite":
        # After partitioning on "://" the target of `sqlite:///abs/path` is
        # already `/abs/path` - the third slash IS the root. The only case
        # needing a hand is `sqlite:///~/...`, which people write and which
        # would otherwise expanduser to the literal `/~`. Strip the root slash
        # only when a `~` is hiding behind it.
        if target.startswith("/~"):
            target = target[1:]
        # `sqlite:///abs` and `sqlite:////abs` both mean the same absolute
        # path - three slashes is the URL convention, four is what SQLAlchemy
        # users type because its relative form takes three. Leading runs of
        # slashes are implementation-defined in POSIX, so they are collapsed
        # here rather than handed to Path to interpret.
        while target.startswith("//"):
            target = target[1:]
        return "sqlite", os.path.expanduser(target)
    return driver, target


def connect(dsn: str, cfg=None) -> "Archive":
    """Open (and create) the archive named by a DSN.

    THE STAGE GUARD APPLIES HERE TOO, and it is not ceremony: an archive
    written inside a watched directory would make the viewer a participant in
    the thing it is watching - and worse, would be deleted by exactly the
    cleanup this feature exists to survive.
    """
    driver, target = parse_dsn(dsn)
    if driver != "sqlite":
        raise ArchiveError(
            f"the archive DSN names {driver!r}, and only sqlite is implemented.\n"
            f"The schema is engine-neutral and the driver is a small file, but "
            f"shipping one that has never run against a real {driver} server "
            f"would be a confident claim about untested code - which is the "
            f"one thing this service exists not to make.\n"
            f"Use a sqlite path for now; a shared store is worth doing "
            f"properly when there is a second writer to test against.")
    if not target:
        raise ArchiveError("the archive DSN names no path")
    path = Path(target)
    if cfg is not None:
        from ..stageguard import assert_outside_stages
        path = assert_outside_stages(path, cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    return Archive(path)


class Archive:
    """A tiny DAO. No ORM, and that is a decision rather than an omission.

    Theatre's whole dependency list is four packages, and the queries here are
    a handful of upserts and two selects. An ORM would be a larger dependency
    than the feature, and it would put a layer between the schema above and
    the SQL that runs - which is the layer a person debugging their own
    archive at 2am has to peel back off.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        # ── THE THREAD BUG, AND WHY IT WOULD HAVE SHIPPED ──────────────────
        #
        # sqlite3.connect defaults to check_same_thread=True, and FastAPI runs
        # every sync endpoint in a THREADPOOL. The connection is created lazily
        # on whatever pool thread serves the first request, so a later request
        # landing on a different thread raises ProgrammingError.
        #
        # That is a load-dependent failure, which is the worst kind to leave
        # in: with a handful of requests the pool reuses one thread and
        # everything passes. It only breaks with a browser polling every five
        # seconds while somebody clicks around - which is to say, only in use.
        #
        # check_same_thread=False lifts the check; the lock is what makes that
        # safe rather than merely quiet. A cursor is not safe to share across
        # concurrent statements even when the check is off, and "it did not
        # crash in testing" is exactly the evidence that got us here.
        try:
            self._db = sqlite3.connect(str(self.path), check_same_thread=False)
        except sqlite3.Error as exc:
            raise ArchiveError(f"{self.path}: {exc}") from exc
        self._lock = threading.RLock()
        self._db.row_factory = sqlite3.Row
        # WAL so a long read cannot block the harvest, and vice versa. Theatre
        # polls; a poll that blocks on a writer is a frozen dashboard.
        # Under the lock like everything else, even though nothing else can
        # reach this object yet. An invariant with one exception is an
        # invariant somebody forgets - and the AST guard that enforces it
        # cannot tell "safe because it is a constructor" from "forgot".
        try:
            with self._lock:
                self._db.execute("PRAGMA journal_mode=WAL")
        except sqlite3.Error:
            pass                        # a network filesystem may refuse. Fine.
        self._migrate()

    # ── lifecycle ────────────────────────────────────────────────────────

    def _migrate(self) -> None:
        with self._lock, self._db:
            for statement in SCHEMA:
                self._db.execute(statement)
            found = self._db.execute(
                "SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if found is None:
                self._db.execute(
                    "INSERT INTO meta(key, value) VALUES('schema_version', ?)",
                    (str(SCHEMA_VERSION),))
            elif int(found["value"]) > SCHEMA_VERSION:
                raise ArchiveError(
                    f"{self.path} was written by schema_version "
                    f"{found['value']}; this seren-theatre understands "
                    f"{SCHEMA_VERSION}. Upgrade rather than being shown a "
                    f"guess about your own history.")

    def close(self) -> None:
        try:
            self._db.close()
        except sqlite3.Error:
            pass

    def __enter__(self) -> "Archive":
        return self

    def __exit__(self, *exc) -> bool:
        self.close()
        return False

    # ── writing ──────────────────────────────────────────────────────────

    def put_surgery(self, row: Dict[str, Any]) -> None:
        """Upsert one run. Idempotent on run_key, by design.

        Harvest runs on every scan and most scans find nothing new. An insert
        that had to be guarded by a prior SELECT would be a race with itself
        the moment two viewers watch one directory.
        """
        self._upsert("surgeries", "run_key", row)

    def put_grading(self, row: Dict[str, Any]) -> None:
        self._upsert("gradings", "grading_key", row)

    def put_gate(self, row: Dict[str, Any]) -> None:
        self._upsert("gates", "run_key", row)

    def put_prompt_book(self, row: Dict[str, Any]) -> None:
        self._upsert("prompt_books", "book_id", row)

    def _upsert(self, table: str, key: str, row: Dict[str, Any]) -> None:
        cols = list(row)
        marks = ", ".join("?" for _ in cols)
        sets = ", ".join(f"{c}=excluded.{c}" for c in cols if c != key)
        sql = (f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({marks}) "
               f"ON CONFLICT({key}) DO UPDATE SET {sets}")
        try:
            with self._lock, self._db:
                self._db.execute(sql, [row[c] for c in cols])
        except sqlite3.Error as exc:
            raise ArchiveError(f"writing {table}: {exc}") from exc

    def mark_absent(self, run_key: str, present: bool) -> None:
        """Record whether the rung directory is still on disk.

        Its own method because it is the one field that changes for reasons
        that have nothing to do with the run. A row whose rung is gone is
        still a true record; it is just no longer a description of anything
        you can open.
        """
        with self._lock, self._db:
            self._db.execute(
                "UPDATE surgeries SET rung_present=? WHERE run_key=?",
                (1 if present else 0, run_key))

    # ── reading ──────────────────────────────────────────────────────────

    def has_surgery(self, run_key: str) -> bool:
        with self._lock:
            got = self._db.execute(
                "SELECT 1 FROM surgeries WHERE run_key=?", (run_key,)).fetchone()
        return got is not None

    def surgeries(self, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        """Newest first, with the gradings nested under each.

        NESTED, NOT JOINED FLAT. A grading is meaningless without the build it
        graded - that is the whole provenance argument - so the shape that
        reaches a reader keeps them attached rather than inviting a list of
        scores with no builds beside them.
        """
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM surgeries ORDER BY COALESCE(started, harvested) "
                "DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
            out = []
            for row in rows:
                item = dict(row)
                item["manifest"] = _loads(item.get("manifest"))
                item["gradings"] = [
                    {**dict(g), "report": _loads(g["report"])}
                    for g in self._db.execute(
                        "SELECT * FROM gradings WHERE run_key=? "
                        "ORDER BY COALESCE(generated, 0) DESC",
                        (row["run_key"],)).fetchall()]
                gate = self._db.execute(
                    "SELECT * FROM gates WHERE run_key=?",
                    (row["run_key"],)).fetchone()
                item["gate"] = ({**dict(gate), "report": _loads(gate["report"])}
                                if gate else None)
                out.append(item)
        return out

    def surgery(self, run_key: str) -> Optional[Dict[str, Any]]:
        """One run by key, shaped exactly like a row from `surgeries()`.

        Built on the same query rather than a second one, because two ways to
        assemble a surgery is two ways for them to disagree - and the one that
        would drift is the rarely-used one, which is this.
        """
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM surgeries WHERE run_key=?", (run_key,)).fetchone()
            if row is None:
                return None
            item = dict(row)
            item["manifest"] = _loads(item.get("manifest"))
            item["gradings"] = [
                {**dict(g), "report": _loads(g["report"])}
                for g in self._db.execute(
                    "SELECT * FROM gradings WHERE run_key=? "
                    "ORDER BY COALESCE(generated, 0) DESC",
                    (run_key,)).fetchall()]
            gate = self._db.execute(
                "SELECT * FROM gates WHERE run_key=?", (run_key,)).fetchone()
            item["gate"] = ({**dict(gate), "report": _loads(gate["report"])}
                            if gate else None)
        return item

    def count(self, table: str = "surgeries") -> int:
        if table not in DOCUMENTS and table != "prompt_books":
            raise ArchiveError(f"no such table: {table}")
        with self._lock:
            return int(self._db.execute(
                f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])

    def prompt_books(self, limit: int = 200) -> List[Dict[str, Any]]:
        """The shelf. WITHOUT the recipe text, deliberately.

        A list of forty books, each carrying a fully stamped recipe, is a
        megabyte of YAML nobody is reading - and the list is what renders on
        every visit while a recipe is what renders when somebody opens one.
        `prompt_book()` fetches the body.
        """
        with self._lock:
            rows = self._db.execute(
                "SELECT book_id, name, created, imported, build_id, bytes, "
                "       length(recipe) AS recipe_bytes, notes, meta "
                "FROM prompt_books ORDER BY imported DESC LIMIT ?",
                (limit,)).fetchall()
        return [{**dict(r), "meta": _loads(r["meta"])} for r in rows]

    def prompt_book(self, book_id: str) -> Optional[Dict[str, Any]]:
        """One book, with its recipe. None if there is no such row."""
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM prompt_books WHERE book_id=?",
                (book_id,)).fetchone()
        if row is None:
            return None
        return {**dict(row), "meta": _loads(row["meta"])}

    def prompt_book_ids(self) -> set:
        """Every book_id still on the shelf.

        THE REFCOUNT, and it lives here rather than in the blob store because
        the blob store cannot know it. book_id IS the content hash, so two rows
        naming one blob are two rows with the same id - which cannot happen -
        but the shape is kept because the day a book carries a SEPARATE corpus
        blob, this is the query that stops a delete taking somebody else's
        bytes with it.
        """
        with self._lock:
            return {r["book_id"] for r in
                    self._db.execute("SELECT book_id FROM prompt_books")}

    def delete_prompt_book(self, book_id: str) -> bool:
        """Remove one row. Returns whether there was one to remove."""
        with self._lock, self._db:
            cur = self._db.execute(
                "DELETE FROM prompt_books WHERE book_id=?", (book_id,))
        return cur.rowcount > 0

    # ── the property that makes the schema safe to change ────────────────

    def rebuild(self, derive) -> int:
        """Recompute every derived column from the stored documents.

        THIS IS WHY THE DOCUMENTS ARE KEPT. Adding a column becomes a migration
        that cannot lose anything, and a column that was computed wrongly is a
        bug rather than a data loss - because the evidence it was computed from
        is still sitting in the row.

        `derive` is `(table, document) -> dict of columns`, passed in rather
        than imported so this module never has to know what a manifest means.
        Returns the number of rows touched.
        """
        touched = 0
        for table, column in DOCUMENTS.items():
            key = "grading_key" if table == "gradings" else "run_key"
            with self._lock:
                rows = self._db.execute(
                    f"SELECT {key}, {column} FROM {table}").fetchall()
            for row in rows:
                doc = _loads(row[column])
                if doc is None:
                    continue
                cols = derive(table, doc)
                if not cols:
                    continue
                sets = ", ".join(f"{c}=?" for c in cols)
                with self._lock, self._db:
                    self._db.execute(
                        f"UPDATE {table} SET {sets} WHERE {key}=?",
                        list(cols.values()) + [row[key]])
                touched += 1
        return touched


def _loads(raw: Any) -> Any:
    """JSON or None. A stored document that will not parse is not a crash.

    The row still carries its columns, which is a real if lesser reading, and
    losing the whole history to one bad blob would be the archive punishing its
    reader for a write that went wrong months ago.
    """
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def dumps(value: Any) -> str:
    """Documents go in sorted and indented, so a diff of two rows is readable."""
    return json.dumps(value, sort_keys=True, indent=2, default=str)
