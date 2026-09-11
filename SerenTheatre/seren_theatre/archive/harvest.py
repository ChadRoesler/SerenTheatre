"""Lifting a run's record out of a run directory before the directory goes.

WHAT IS TAKEN, AND WHY IT IS SO LITTLE. The manifest, the eval report and the
gate report - three small JSON documents. Not the weights, not the GGUF, not
the corpus. Those are the forty-five gigabytes you are trying to delete, and
copying them would turn a feature about surviving cleanup into a second copy
of the thing being cleaned up.

WHEN. During the ordinary scan, when a run has reached a terminal state and is
not already in the archive. Not on a timer, not on a button, and specifically
NOT for a run that is still going: harvesting a half-finished manifest would
store a claim about a build that had not made it yet, and the row would then
be indistinguishable from a run that genuinely stopped there.

Idempotent, and that is what makes it safe to call from a poll. `run_key` is
stable for a given run, `put_*` upserts on it, and a scan that finds nothing
new does nothing at all.

────────────────────────────────────────────────────────────────────────────
A HARVESTED ROW IS NOT A RUN, AND THE VIEWER MUST NEVER CONFUSE THE TWO.

Theatre's whole discipline is `presence, not promises` - every reading prefers
what is on disk to what was claimed about it. An archived row is a claim with
no disk left to check it against. That is fine, and it is a different KIND of
fact, so `run_present` is refreshed on every harvest and travels with the row.
Merging archived rows into the live list would have the viewer assert that a
directory exists when it does not, which is the same confidently-wrong failure
this codebase keeps finding in new costumes.
────────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, Sequence

from .. import evalreport as _results
from .. import manifest as _manifest
from . import store as _store
from .store import Archive, dumps

# States a run does not come back from. A run still moving is left alone: its
# manifest is rewritten on every stage transition, so harvesting one would
# archive a sentence that was about to be replaced.
TERMINAL = ("finished", "failed")


def run_key(stage: str, run: Path, started: Optional[float]) -> str:
    """A stable id for one RUN, which is not the same thing as one build.

    build_id is the digest of the resolved CONFIG - two runs of the same recipe
    share it, and they are two runs. Two runs in different stages can share a
    directory name. So the key is the stage, the resolved path and the start
    time, hashed: stable across polls, distinct across re-runs, and it does not
    grow a slash problem on Windows.
    """
    raw = f"{stage}\x00{Path(run).as_posix()}\x00{started or 0:.0f}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def grading_key(key: str, report: Dict[str, Any]) -> str:
    """One grading. A re-run of eval against the same build is a NEW row.

    Keyed on when it was generated rather than on the build it graded, because
    the pair of results either side of installing a missing compiler is the
    single most useful thing this archive can hold - and keying on build_id
    would have the second one overwrite the first.
    """
    stamp = report.get("generated") or 0
    raw = f"{key}\x00{stamp:.3f}\x00{report.get('build_id') or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def capture_recipe(blobs, run_path: Path) -> tuple:
    """(digest, state) for this run's preserved recipe. Never raises.

    THE PRIMARY INPUT WAS THE ONE THING NOT KEPT. The manifest already carries a
    sha256 of every DEFAULTS file a run inherited, plus the whole resolved config
    and a `build_id` digest of it - so a row could state, exactly, the
    fingerprint of what was built and not the text that built it. That is the
    difference between a record that VERIFIES a rebuild and one that makes a
    rebuild possible.

    FOUR STATES, NOT A NULLABLE DIGEST. "No recipe on this row" has four causes
    and only one is a problem worth chasing:

      not-captured  no blob store was available - nothing was even attempted
      captured      the text is in blobs under the returned digest
      absent        looked, and this run has no recipe beside its manifest (an
                    older ms-moe-maker, or a build from before it preserved one)
      unreadable    it was there and could not be stored

    Collapsing those into "the digest is empty" is how somebody ends up unable
    to tell "nothing to worry about" from "your history is quietly incomplete",
    which is the failure this codebase keeps finding in new costumes.
    """
    if blobs is None:
        return "", _store.RECIPE_NOT_CAPTURED
    found = _manifest.find_recipe(run_path)
    if found is None:
        return "", _store.RECIPE_ABSENT
    try:
        return blobs.put(found), _store.RECIPE_CAPTURED
    except Exception:
        # Any failure to store is UNREADABLE rather than absent, because the
        # two mean opposite things to whoever reads the row: one is a build
        # that predates the feature, the other is a recipe that exists on disk
        # right now and is not in the archive.
        return "", _store.RECIPE_UNREADABLE


def harvest_run(archive: Archive, stage: str, run: Dict[str, Any],
                 blobs=None) -> bool:
    """Archive one scanned run if it is finished and not already stored.

    Takes the SCANNED DICT rather than a path, so it reads exactly what the
    viewer read - one parse, one set of readings, no second opinion about what
    is in that directory.

    Returns True when something was written.
    """
    found = run.get("manifest")
    if not isinstance(found, dict):
        return False                    # uninstrumented: nothing to record
    state = str(run.get("state") or found.get("state") or "")
    if state not in TERMINAL:
        return False

    path = Path(run.get("path") or "")
    key = run_key(stage, path, found.get("started"))
    present = path.is_dir()

    if archive.has_surgery(key):
        # Already recorded. The one thing still worth updating is whether the
        # directory survives - which is the whole point of the archive and the
        # only fact about an old run that legitimately changes.
        #
        # NEVER `unknown` HERE. This code path exists because a scan just
        # walked that stage and handed us the run, so the look DID happen -
        # `unknown` is for a reader that could not perform one. Recording it
        # here would throw away the strongest evidence in the system.
        archive.mark_presence(
            key, _store.RUN_PRESENT if present else _store.RUN_GONE)
        return False

    archive.put_surgery({
        "run_key": key,
        "build_id": str(found.get("build_id") or ""),
        "stage": stage,
        "name": str(found.get("name") or run.get("name") or ""),
        "run_path": str(path),
        "started": found.get("started"),
        "finished": found.get("finished"),
        "state": state,
        "ok": _tri(found.get("ok")),
        "harvested": time.time(),
        "run_present": 1 if present else 0,
        "run_state": _store.RUN_PRESENT if present else _store.RUN_GONE,
        # The frame this path was read under, recorded WITH the fact - see
        # ADDED_COLUMNS. Empty for a local stage, which is the common case and
        # means "the path is what it says it is".
        "remote_prefix": str(run.get("remote_prefix") or ""),
        "builder_path": str(run.get("builder_path") or ""),
        "manifest": dumps(found),
        **dict(zip(("recipe_blob", "recipe_state"),
                   capture_recipe(blobs, path))),
    })

    report = run.get("eval")
    if isinstance(report, dict):
        archive.put_grading({
            "grading_key": grading_key(key, report),
            "run_key": key,
            "build_id": str(report.get("build_id") or ""),
            "generated": report.get("generated"),
            # Frozen at harvest, against the manifest that was on disk then.
            # Recomputing it later from a run that has since been rebuilt
            # would answer a different question than the one being asked.
            "provenance": str(report.get("provenance")
                              or _results.UNKNOWN),
            "ok": _tri(report.get("ok")),
            # THE ORIGINAL DOCUMENT, NOT THE VIEWER'S PROJECTION.
            #
            # This stored the projection first, and it was wrong under this
            # module's own docstring: a projection is lossy by definition, and
            # that one drops `avg_length` and `capped_generations`. Keeping the
            # evidence rather than one reading of it is the entire argument for
            # documents-as-truth, and the archive is where breaking it costs
            # the most - once the run is gone this is the only copy.
            #
            # The projection is recomputed at READ time, so a viewer upgrade
            # improves rows archived years earlier. One frozen at harvest could
            # only ever get staler.
            "report": dumps(report.get("document") or report),
        })

    gate = run.get("gate")
    if isinstance(gate, dict):
        archive.put_gate({
            "run_key": key,
            "status": str(gate.get("status") or ""),
            "findings": len(gate.get("findings") or []),
            # KEPT AS ITS OWN COUNT. Folded into findings it would read as a
            # problem; dropped it would read as a pass. It is neither.
            "unmeasured": len(gate.get("unmeasured") or []),
            "report": dumps(gate.get("document") or gate),
        })
    return True


def harvest_stage(archive: Archive, stage: Dict[str, Any], blobs=None) -> int:
    """Every run in one scanned stage. Returns how many were newly stored.

    `blobs` is optional so a caller that has no store still archives the rows -
    the recipe is an addition to the record, and refusing to keep the manifest
    because the blob store is missing would trade the whole feature for part of
    it. A row harvested without a store says so; see capture_recipe.
    """
    name = str(stage.get("name") or "")
    return sum(1 for run in (stage.get("runs") or [])
               if harvest_run(archive, name, run, blobs))


def derive(table: str, document: Dict[str, Any]) -> Dict[str, Any]:
    """Recompute a row's indexed columns from its stored document.

    Handed to `Archive.rebuild`, which is what makes adding a column a
    migration that cannot lose anything. It lives here rather than in the store
    because the store must not have to know what a manifest means - it holds
    documents; this module is the one that reads them.
    """
    if table == "surgeries":
        return {"build_id": str(document.get("build_id") or ""),
                "name": str(document.get("name") or ""),
                "started": document.get("started"),
                "finished": document.get("finished"),
                "state": str(document.get("state") or ""),
                "ok": _tri(document.get("ok"))}
    if table == "gradings":
        # PROVENANCE IS NOT IN HERE, and its absence is the correct answer
        # rather than an oversight. "Did this eval grade the build that is on
        # disk" is a RELATIONAL fact - it needs this document AND the manifest
        # in the surgeries row - so it cannot be recomputed from the document
        # alone. It is decided once, at harvest, against the manifest that was
        # there at the time, which is also the only moment the question has a
        # single honest answer: recomputing it later against a run that has
        # since been rebuilt would quietly answer a different question.
        return {"build_id": str(document.get("build_id") or ""),
                "generated": document.get("generated"),
                "ok": _tri(document.get("ok"))}
    if table == "gates":
        return {"status": str(document.get("status") or ""),
                "findings": len(document.get("findings") or []),
                "unmeasured": len(document.get("unmeasured") or [])}
    return {}


def _tri(value: Any) -> Optional[int]:
    """True / False / unknown, kept as three values all the way to the column.

    `ok` is genuinely nullable - a run can be finished-and-good,
    finished-and-bad, or a manifest that never said. Coercing the third into
    False would turn "we do not know" into "it failed", which is the same
    mistake as folding `unmeasurable` into `fail` one layer up.
    """
    if value is None:
        return None
    return 1 if value else 0


class Anchor(NamedTuple):
    """A place Theatre is currently able to look, and whether it is a mount.

    `root` is the stage directory as THIS box sees it. `remote` is set when the
    stage declares a `remote_prefix`, which is the operator saying "this
    appears here through a mount" - and that changes what an empty directory
    means (see _presence).
    """
    root: Path
    remote: bool


def anchors_for(stages: Any) -> List[Anchor]:
    """Anchors from stage configs, tolerating anything that is not one.

    Takes duck-typed objects rather than importing config, so this module keeps
    knowing nothing about yaml. A stage that cannot be read is skipped: losing
    one anchor costs `unknown` on its rows, which is the safe direction.
    """
    out: List[Anchor] = []
    for stage in list(stages or []):
        try:
            root = stage.resolved()
        except Exception:               # noqa: BLE001 - not a stage config
            continue
        out.append(Anchor(Path(root),
                          bool(getattr(stage, "remote_prefix", ""))))
    return out


def _covering(path: Path, anchors: Sequence[Anchor]) -> Optional[Anchor]:
    """The anchor `path` sits under, longest root first.

    Longest first because stages nest: /mnt/spark and /mnt/spark/msMoEMaker can
    both be configured, and the inner one is the more specific answer about
    whether that particular tree is reachable.
    """
    best: Optional[Anchor] = None
    for anchor in anchors:
        try:
            path.relative_to(anchor.root)
        except ValueError:
            continue
        if best is None or len(str(anchor.root)) > len(str(best.root)):
            best = anchor
    return best


#: What one look at a stage root learned. ONE listing, three answers - the
#: first version asked twice (readable? then empty?) which is two round trips
#: per row against a share that may be exactly the thing misbehaving.
UNREADABLE = None
EMPTY = "empty"
OCCUPIED = "occupied"


def _look(root: Path) -> Optional[str]:
    """List a stage root once. None when the attempt itself failed.

    `is_dir()` swallows OSError and answers False, which is the whole bug: a
    dead NFS mount and a deleted directory produce the same False. So the
    listing is attempted and the exception is kept as its own answer, and
    whether anything was in there comes back from the same walk.
    """
    try:
        if not root.is_dir():
            return UNREADABLE
        for _entry in root.iterdir():
            return OCCUPIED
        return EMPTY
    except OSError:
        return UNREADABLE


def _presence(path: str, anchors: Sequence[Anchor]) -> str:
    """present / gone / unknown for one archived run path.

    THE RULE, AND WHY EACH BRANCH IS NOT THE OBVIOUS ONE:

      * No path recorded -> `unknown`. There is nothing to look at, and `gone`
        would be a claim about a directory we cannot even name.

      * The directory is there -> `present`. The only cheap certainty.

      * `stat` RAISED -> `unknown`. Not `gone`. An EIO or ESTALE from a dead
        share is the mount failing, not the operator deleting a model.

      * It is not there, and no configured stage covers it -> `unknown`.
        Theatre is not watching that tree any more - the box was retired, or
        the stage was renamed - so nothing here performed a look. This is the
        case a two-valued column got wrong most often and most loudly: every
        row from a decommissioned builder reading as a deletion.

      * It is not there, the covering stage is READABLE -> `gone`. A real
        answer, and the one the feature is for.

      * It is not there and the covering stage is unreadable or unlistable ->
        `unknown`.

      * It is not there, the covering stage is readable but EMPTY, and that
        stage is declared REMOTE -> `unknown`. The residual case, and it is
        Tuesday: an unmounted mount point is an empty directory that stats
        perfectly. A mounted builder output root always holds something; an
        empty one almost certainly means the share is not up. Erring to
        `unknown` here costs a grey badge, and erring the other way announces
        that every model on that box was deleted.

    A local stage that is genuinely empty still reports `gone`, because there
    no mount can be down and empty means empty.
    """
    if not path:
        return _store.RUN_UNKNOWN
    target = Path(path)
    try:
        if target.is_dir():
            return _store.RUN_PRESENT
    except OSError:
        return _store.RUN_UNKNOWN

    anchor = _covering(target, anchors)
    if anchor is None:
        return _store.RUN_UNKNOWN

    seen = _look(anchor.root)
    if seen is UNREADABLE:
        return _store.RUN_UNKNOWN
    if seen == EMPTY and anchor.remote:
        return _store.RUN_UNKNOWN
    return _store.RUN_GONE


def reconcile(archive: Archive, rows: List[Dict[str, Any]],
              anchors: Sequence[Anchor] = ()) -> List[Dict[str, Any]]:
    """Re-check, at READ time, whether each archived run is still on disk.

    THE COLUMN CANNOT MAINTAIN ITSELF, and the reason is the whole point of the
    feature: once a run is deleted it is never scanned again, so the harvest
    that would have updated it never runs. A stored presence is therefore a
    last-known value, and last-known is precisely the kind of claim this
    codebase refuses to render as current - a row saying the directory is there
    when it is not would have the viewer assert something a `stat` disagrees
    with, which is the confidently-wrong failure in yet another costume.

    So the reading is taken here, on the rows actually being returned - bounded
    by the caller's limit, so it is a few dozen stats and not a walk of the
    whole history. The stored column is updated as a side effect, which makes
    it converge without ever being trusted.

    `presence, not promises` applies to Theatre's own database too. It has no
    special standing just because Theatre wrote it.

    ANCHORS ARE WHAT MAKE `unknown` POSSIBLE. Without them this function can
    only ask "is it there", and the answer False conflates a deletion with an
    unreachable mount. Called with no anchors it still works and still refuses
    to guess: nothing covers any path, so a missing directory reads `unknown`
    rather than being reported as deleted on no evidence.
    """
    out = []
    for row in rows:
        item = dict(row)
        state = _presence(str(item.get("run_path") or ""), anchors)
        was = str(item.get("run_state") or "")
        item["run_state"] = state
        # DERIVED, one writer, and `unknown` is not present. A boolean asked
        # "is it there" must not answer yes about something nobody could look
        # at; a caller that needs to tell unknown from gone reads run_state.
        item["run_present"] = state == _store.RUN_PRESENT
        if state != was:
            try:
                archive.mark_presence(str(item.get("run_key") or ""), state)
            except Exception:           # noqa: BLE001 - a read must not fail
                pass                    # the returned value is right regardless
        out.append(item)
    return out
