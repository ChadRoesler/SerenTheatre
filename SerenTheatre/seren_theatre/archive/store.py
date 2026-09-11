"""Where a run's record lives after the run's directory is gone.

THE PROBLEM, STATED PROPERLY. It is not "files might get deleted." It is that
the thing worth keeping is INSIDE the thing you have to delete. An eval report
is about ten kilobytes; the run it sits in is forty-five gigabytes. Deleting
that run is not an accident or a mistake - it is the correct, routine
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
inside one - and it matters more here, because once the run is deleted this
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

# EVERY TABLE THAT HOLDS RECORDS, derived rather than retyped. `prompt_books`
# has no document column so it is absent from DOCUMENTS, and the membership
# test for it used to be spelled out at each call site as
# `table not in DOCUMENTS and table != "prompt_books"` - two copies of "which
# tables exist", which is the shape of defect this codebase keeps paying for.
# `meta` is not here: it holds the schema version, not records, and merging or
# counting it would mean something different.
TABLES = frozenset(DOCUMENTS) | {"prompt_books"}

# COLUMNS THAT ARRIVED AFTER THE TABLES DID. (table, column, declaration).
#
# Declared HERE ONLY - never also in the CREATE statements above - so there is
# exactly one place that says a column exists. See Archive._migrate for why
# putting a new column in SCHEMA alone is a silent no-op on any archive that is
# already on disk, and why routing fresh databases through the same ALTER path
# is what keeps this loop from being dead code nobody exercises.
#
# Every declaration needs a DEFAULT: sqlite will not add a NOT NULL column to a
# table with rows in it otherwise, and the rows are the whole point.
#: The recipe was NOT CAPTURED / captured / looked for and absent / unreadable.
#: Four states because "there is no recipe digest on this row" has four
#: different causes and only one of them is a problem worth chasing:
#:
#:   ""          this row predates recipe capture entirely
#:   "captured"  the text is in blobs under `recipe_blob`
#:   "absent"    the run directory holds no recipe - an older ms-moe-maker, or
#:               a build whose recipe was never written beside its output
#:   "unreadable" it was there and could not be stored; the reason is the row's
#:               problem to surface, not to swallow
#:
#: A single nullable digest column would collapse all four into "no", which is
#: how a person ends up unable to tell "nothing to worry about" from "your
#: history is quietly incomplete".
RECIPE_NOT_CAPTURED = ""
RECIPE_CAPTURED = "captured"
RECIPE_ABSENT = "absent"
RECIPE_UNREADABLE = "unreadable"

# ── IS THE RUN DIRECTORY STILL THERE: THREE ANSWERS, NOT TWO ────────────────
#
# `present` / `gone` were the whole vocabulary, and `gone` was carrying two
# completely different facts: "I looked and it is not there" and "I could not
# look". Collapsing those announces a model deletion because a share hiccupped.
#
# That is the same error this codebase already painted in red over eight
# consecutive healthy fine-tune stages before it learned to report mtimes and
# refuse the verdict - a conclusion a failed `stat()` is not entitled to draw.
# Under a mount the failure is not rare, it is Tuesday.
RUN_PRESENT = "present"
RUN_GONE = "gone"
RUN_UNKNOWN = "unknown"
RUN_STATES = (RUN_PRESENT, RUN_GONE, RUN_UNKNOWN)

ADDED_COLUMNS = (
    # WHAT BUILT THIS. The manifest already carries a sha256 of every DEFAULTS
    # file a run inherited, and carried nothing at all about the recipe that is
    # the primary input - so a row could tell you the fingerprint of the config
    # it resolved to and not the text that produced it. `build_id` makes a
    # rebuild verifiable; this makes it possible.
    ("surgeries", "recipe_blob", "TEXT NOT NULL DEFAULT ''"),
    ("surgeries", "recipe_state", "TEXT NOT NULL DEFAULT ''"),
    # PRESENCE, THREE-VALUED. A separate column rather than a widening of
    # `run_present`, because `INTEGER NOT NULL DEFAULT 1` cannot hold a third
    # state and re-typing a column in sqlite means rebuilding the table - on
    # somebody's only copy of their history, to add a value. `run_present`
    # stays as a DERIVED boolean with exactly one writer; see mark_presence.
    ("surgeries", "run_state", "TEXT NOT NULL DEFAULT ''"),
    # THE FRAME THE PATH WAS READ UNDER. A mount prefix is configuration, and
    # configuration is a statement about NOW; a row is a claim about the past.
    # Somebody re-mounts the Spark at /mnt/builders/spark, or retires the box,
    # and a history that only ever knew the CURRENT config would quietly
    # re-interpret every old row against a prefix that was never true for it.
    #
    # Same rule this archive already applies to `evalreport.provenance`, which
    # is decided once at harvest against the manifest that was there at the
    # time because recomputing it later "would quietly answer a different
    # question". This is that, for paths. Record the frame with the fact.
    ("surgeries", "remote_prefix", "TEXT NOT NULL DEFAULT ''"),
    # THE RUN DIRECTORY AS THE BUILDER SPELLED IT. Not derivable from the two
    # columns above: `remote_prefix` is the prefix that was swapped IN and the
    # stage root that was swapped OUT is not stored, so the mapping is only
    # invertible at the moment of harvest. Stored rather than recomputed for the
    # same reason the prefix is - it is the path that is still true after the
    # mount moves, which is precisely when somebody needs it.
    ("surgeries", "builder_path", "TEXT NOT NULL DEFAULT ''"),
)

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS meta (
           key TEXT PRIMARY KEY,
           value TEXT NOT NULL)""",

    # A BUILD THAT HAPPENED. `run_key` rather than build_id as the identity,
    # because build_id is the digest of the CONFIG - two runs of the same
    # recipe share it, and they are two runs. The run path plus its start
    # time is what makes one of them this one.
    """CREATE TABLE IF NOT EXISTS surgeries (
           run_key      TEXT PRIMARY KEY,
           build_id     TEXT NOT NULL DEFAULT '',
           stage        TEXT NOT NULL DEFAULT '',
           name         TEXT NOT NULL DEFAULT '',
           run_path    TEXT NOT NULL DEFAULT '',
           started      REAL,
           finished     REAL,
           state        TEXT NOT NULL DEFAULT '',
           ok           INTEGER,
           harvested    REAL NOT NULL,
           -- IS THE DIRECTORY STILL THERE. The one column that must never be
           -- guessed: a row whose run is gone is a HISTORICAL RECORD, and
           -- rendering it beside live runs would have the viewer assert that
           -- a directory exists when it does not. Refreshed on every harvest.
           run_present INTEGER NOT NULL DEFAULT 1,
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
           -- time. Deciding it later, from a run that has since been rebuilt,
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

    def _columns(self, table: str) -> set:
        with self._lock:
            return {row[1] for row in
                    self._db.execute(f"PRAGMA table_info({table})")}

    def _migrate(self) -> None:
        """Create what is missing, in an order that cannot make things worse.

        SCHEMA first, because `meta` has to exist before its version can be
        read. Then the version CHECK, before any ALTER - an archive written by a
        newer seren-theatre must be refused untouched rather than altered by an
        older one that does not know what its columns mean. Then the columns.
        """
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

        # ── COLUMNS ADDED AFTER THE FIRST SCHEMA ─────────────────────────
        #
        # `CREATE TABLE IF NOT EXISTS` IS A NO-OP ON A TABLE THAT EXISTS, which
        # makes adding a column to SCHEMA one of the quietest bugs available
        # here. On a fresh database the column appears and every test passes; on
        # a real archive that has been running for a week it never appears at
        # all, and the first write naming it raises "no such column" - on
        # somebody else's box, months later.
        #
        # It also left `rebuild()`'s promise without a floor. That docstring
        # says adding a column "becomes a migration that cannot lose anything",
        # which is true about FILLING one and silent about creating it: nothing
        # anywhere could add a column to a file that already existed.
        #
        # ADDED_COLUMNS IS THE ONLY PLACE THESE ARE DECLARED, deliberately.
        # Listing them here AND in the CREATE statements would be two copies of
        # one fact - the defect this codebase keeps paying for - so a fresh
        # database gets its base table from SCHEMA and every later column from
        # this same loop. Which means the migration path runs on every archive
        # ever opened and cannot rot in the corner where only an upgrade would
        # have exercised it.
        for table, column, decl in ADDED_COLUMNS:
            if column in self._columns(table):
                continue
            try:
                with self._lock, self._db:
                    self._db.execute(
                        f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
            except sqlite3.Error as exc:
                raise ArchiveError(
                    f"{self.path}: could not add {table}.{column}: {exc}"
                ) from exc

    def checkpoint(self) -> bool:
        """Fold the WAL back into the database file. Best effort, never raises.

        THE LIE THIS REMOVES, AND IT IS THE REASSURING KIND. In WAL mode the
        committed rows live in `<name>-wal` until something checkpoints, and
        sqlite only does that on its own when the log crosses
        `wal_autocheckpoint` (1000 pages, ~4 MB) or when the last connection
        closes. This archive holds its connection for the life of the process
        and nothing called `close()`, so neither ever happened: observed on a
        real box, `archive.db` was **4 KB** and `archive.db-wal` was 383 KB,
        with the main file untouched for eight days.

        Nothing was lost - sqlite reads the WAL and the database together, so
        every query answered correctly. What broke was BACKING IT UP.
        `cp archive.db somewhere` produces a file that opens, has the right
        name, and contains nothing, schema included, because the CREATE TABLEs
        went into the WAL too. And the file that actually held the history was
        called `.db-wal`, which looks exactly like the sort of temp artifact a
        person tidies away.

        TRUNCATE rather than PASSIVE: PASSIVE moves the pages and leaves the
        log file at its size, which keeps the shape that misleads. Returns
        whether it ran, because "I checkpointed" is a claim and a caller
        deciding whether a plain file copy is safe deserves the real answer.
        """
        try:
            with self._lock:
                self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            return True
        except sqlite3.Error:
            # A network filesystem, a reader holding the log open, a
            # read-only mount. Not fatal, and not something to pretend about.
            return False

    def close(self) -> None:
        """Checkpoint, then close. In that order, and the order is the point.

        Closing a WAL connection normally checkpoints on the way out, but only
        when it is the LAST connection - and "last" is not something this
        object can know. Doing it explicitly first means the file on disk is
        complete whether or not anything else still has the database open.
        """
        self.checkpoint()
        try:
            self._db.close()
        except sqlite3.Error:
            pass

    def backup(self, dest: Path) -> Path:
        """A consistent copy of a LIVE archive, without stopping anything.

        `sqlite3.Connection.backup` is the online backup API: it reads the
        database and its WAL through the engine, so the result is a coherent
        snapshot even while harvest is writing. That is the whole reason not to
        offer this as advice to use `cp` - see `checkpoint()` for what `cp`
        actually produces here.

        The destination is written and then VERIFIED by opening it and counting
        what came across, because a backup nobody has read is a belief. Returns
        the path written; raises ArchiveError with a sentence rather than a
        sqlite traceback.
        """
        dest = Path(dest)
        if dest.resolve() == self.path.resolve():
            raise ArchiveError(
                f"the backup destination is the archive itself ({dest}). "
                f"Name a different file.")
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ArchiveError(f"cannot create {dest.parent}: {exc}") from exc
        try:
            with self._lock:
                target = sqlite3.connect(str(dest))
                try:
                    self._db.backup(target)
                finally:
                    target.close()
        except (sqlite3.Error, OSError) as exc:
            raise ArchiveError(f"backup to {dest} failed: {exc}") from exc

        # PROVE IT. The failure this feature exists to prevent is a backup file
        # that looks right and holds nothing, so shipping one without reading
        # it back would be the same bug wearing the fix's clothes.
        try:
            check = Archive(dest)
        except ArchiveError as exc:
            raise ArchiveError(
                f"backup wrote {dest} and it could not be reopened: {exc}"
            ) from exc
        try:
            copied = check.count("surgeries")
            here = self.count("surgeries")
        finally:
            check.close()
        if copied != here:
            raise ArchiveError(
                f"backup to {dest} holds {copied} runs and the archive holds "
                f"{here}. Refusing to call that a backup.")
        return dest

    def merge(self, other: "Archive") -> Dict[str, int]:
        """Fold another archive into this one. Idempotent, by construction.

        WHY THIS IS SAFE AND NOT A CONFLICT-RESOLUTION PROBLEM. Every row is
        keyed by a content-derived id - `run_key` is sha256 of the stage, the
        resolved path and the start time - and every writer here is an UPSERT
        on that key. So the union of two archives is well defined: a key in
        both is the same run described twice, and a key in one is a run the
        other never saw. There is no ordering to get wrong and no last-writer
        to pick, which is why merging is a command rather than a project.

        THIS IS WHAT REPLACED A SERVICE. The reason to want a shared record
        store was surviving a box being swapped or reformatted; merge does that
        without another install, another port, or another thing to explain. Two
        boxes, two archives, one command, and running it twice changes nothing
        the second time.

        Blobs are NOT touched here - they are content-addressed files and a
        directory copy already merges them correctly, which is one of the four
        things blobs.py says that layout buys. Returns per-table counts.

        ── TWO RULES THAT ARE NOT "LAST WRITER WINS" ────────────────────────

        `run_present` IS NEVER TAKEN FROM THE SOURCE, and this was found by
        running a merge rather than by reading one. It is not a property of the
        run; it is THIS archive's answer to "can I open that directory".
        Merging the Spark's archive into the NUC's with a naive upsert made the
        NUC's row claim a directory was present - present on a box the NUC may
        not even have mounted. That is the one column whose comment says it
        "must never be guessed", guessed.

        So a row arriving from elsewhere lands with `run_present = 0`, which
        is not a compromise: on this archive's box the directory genuinely is
        not there until something looks and finds it. Harvest corrects it on
        the next scan if the path is reachable. A row that already exists keeps
        whatever THIS archive last observed, because that is the only
        observation about this archive's view of the disk.

        (The three-valued encoding this note used to ask for now exists as
        `run_state`, so a foreign row lands as `unknown` rather than as the
        nearest-true `0`. `run_present` is still written, derived, for
        consumers that read the boolean.)

        THE MORE RECENT HARVEST DESCRIBES THE RUN. Two rows under one key are
        one run looked at twice, and the later look has seen more of it - an
        eval report that arrived, a state that moved from running to finished.
        So
        `harvested` arbitrates the document rather than argument order, which
        also makes the result independent of which direction you merged.
        """
        moved: Dict[str, int] = {}

        rows = other._all("surgeries")
        kept = 0
        for row in rows:
            row = dict(row)
            mine = self.surgery_row(str(row.get("run_key") or ""))
            if mine is None:
                # NEVER SEEN HERE, AND THAT IS `unknown`, NOT `gone`. Nothing
                # on this box has looked for that directory - it may be sitting
                # on a share this archive has never had mounted. `gone` would
                # be this archive asserting a deletion it has no evidence for,
                # which is the whole reason the third state exists. Harvest
                # corrects it to present/gone on the first scan that can reach
                # the path.
                row["run_state"] = RUN_UNKNOWN
                row["run_present"] = 0
            else:
                # A row that already exists keeps whatever THIS archive last
                # observed, because that is the only observation about this
                # archive's view of the disk.
                row["run_state"] = mine.get("run_state") or RUN_UNKNOWN
                row["run_present"] = mine.get("run_present", 0)
                if float(mine.get("harvested") or 0) >= float(
                        row.get("harvested") or 0):
                    # Ours is the later look. Take nothing but keep the row.
                    kept += 1
                    continue
            self.put_surgery(row)
        moved["surgeries"] = len(rows)
        moved["surgeries_already_current"] = kept

        for table, writer in (("gradings", self.put_grading),
                              ("gates", self.put_gate),
                              ("prompt_books", self.put_prompt_book)):
            # Plain unions: these carry no observation about local disk, so
            # there is nothing here that only this archive could know.
            found = other._all(table)
            for row in found:
                writer(row)
            moved[table] = len(found)
        return moved

    def surgery_row(self, run_key: str) -> Optional[Dict[str, Any]]:
        """One raw surgeries row, or None. Columns as stored.

        Separate from `surgery()`, which assembles a rendering with gradings
        nested underneath it. Merge needs the flat truth, and reusing the
        rendering would have it reasoning about a shape built for a viewer.
        """
        with self._lock:
            found = self._db.execute(
                "SELECT * FROM surgeries WHERE run_key=?",
                (run_key,)).fetchone()
        return dict(found) if found is not None else None

    def _all(self, table: str) -> List[Dict[str, Any]]:
        """Every row of one table, as the put_* writers want them back.

        Deliberately unpaged: these are kilobyte documents and the largest
        realistic archive is thousands of rows. A cursor here would be
        machinery in front of a `SELECT *` that fits in memory twice over.
        """
        if table not in TABLES:
            raise ArchiveError(f"unknown table {table!r}")
        with self._lock:
            found = self._db.execute(f"SELECT * FROM {table}").fetchall()
        return [dict(row) for row in found]

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

    def mark_presence(self, run_key: str, state: str) -> None:
        """Record whether the run directory is still on disk, three-valued.

        Its own method because it is the one field that changes for reasons
        that have nothing to do with the run. A row whose run is gone is
        still a true record; it is just no longer a description of anything
        you can open.

        `run_present` IS WRITTEN HERE AND NOWHERE ELSE. It is the old boolean,
        kept because `archive/compare.py` and any consumer built against it
        still read it, and it is now DERIVED - one source of truth (`run_state`)
        and one derivation, rather than two columns a caller can set apart.
        Note which way the derivation falls: `unknown` reads as NOT present,
        because a boolean asked "is it there" must not answer yes about
        something nobody could look at. A three-state reader is required to
        tell `unknown` from `gone`; that is exactly why the column was added.
        """
        if state not in RUN_STATES:
            raise ArchiveError(
                f"run_state {state!r} is not one of {list(RUN_STATES)}. A "
                f"presence this code cannot name must not be stored: a "
                f"typo'd state would read as neither present nor gone and the "
                f"viewer would draw whichever branch fell through.")
        with self._lock, self._db:
            self._db.execute(
                "UPDATE surgeries SET run_state=?, run_present=? "
                "WHERE run_key=?",
                (state, 1 if state == RUN_PRESENT else 0, run_key))

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
        if table not in TABLES:
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
