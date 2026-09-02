"""Lifting a run's record out of a rung directory before the directory goes.

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
A HARVESTED ROW IS NOT A RUNG, AND THE VIEWER MUST NEVER CONFUSE THE TWO.

Theatre's whole discipline is `presence, not promises` - every reading prefers
what is on disk to what was claimed about it. An archived row is a claim with
no disk left to check it against. That is fine, and it is a different KIND of
fact, so `rung_present` is refreshed on every harvest and travels with the row.
Merging archived rows into the live list would have the viewer assert that a
directory exists when it does not, which is the same confidently-wrong failure
this codebase keeps finding in new costumes.
────────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import evalreport as _results
from .. import manifest as _manifest
from .store import Archive, dumps

# States a run does not come back from. A run still moving is left alone: its
# manifest is rewritten on every stage transition, so harvesting one would
# archive a sentence that was about to be replaced.
TERMINAL = ("finished", "failed")


def run_key(stage: str, rung: Path, started: Optional[float]) -> str:
    """A stable id for one RUN, which is not the same thing as one build.

    build_id is the digest of the resolved CONFIG - two runs of the same recipe
    share it, and they are two runs. Two rungs in different stages can share a
    directory name. So the key is the stage, the resolved path and the start
    time, hashed: stable across polls, distinct across re-runs, and it does not
    grow a slash problem on Windows.
    """
    raw = f"{stage}\x00{Path(rung).as_posix()}\x00{started or 0:.0f}"
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


def harvest_rung(archive: Archive, stage: str, rung: Dict[str, Any]) -> bool:
    """Archive one scanned rung if it is finished and not already stored.

    Takes the SCANNED DICT rather than a path, so it reads exactly what the
    viewer read - one parse, one set of readings, no second opinion about what
    is in that directory.

    Returns True when something was written.
    """
    found = rung.get("manifest")
    if not isinstance(found, dict):
        return False                    # uninstrumented: nothing to record
    state = str(rung.get("state") or found.get("state") or "")
    if state not in TERMINAL:
        return False

    path = Path(rung.get("path") or "")
    key = run_key(stage, path, found.get("started"))
    present = path.is_dir()

    if archive.has_surgery(key):
        # Already recorded. The one thing still worth updating is whether the
        # directory survives - which is the whole point of the archive and the
        # only fact about an old run that legitimately changes.
        archive.mark_absent(key, present)
        return False

    archive.put_surgery({
        "run_key": key,
        "build_id": str(found.get("build_id") or ""),
        "stage": stage,
        "name": str(found.get("name") or rung.get("name") or ""),
        "rung_path": str(path),
        "started": found.get("started"),
        "finished": found.get("finished"),
        "state": state,
        "ok": _tri(found.get("ok")),
        "harvested": time.time(),
        "rung_present": 1 if present else 0,
        "manifest": dumps(found),
    })

    report = rung.get("eval")
    if isinstance(report, dict):
        archive.put_grading({
            "grading_key": grading_key(key, report),
            "run_key": key,
            "build_id": str(report.get("build_id") or ""),
            "generated": report.get("generated"),
            # Frozen at harvest, against the manifest that was on disk then.
            # Recomputing it later from a rung that has since been rebuilt
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
            # the most - once the rung is gone this is the only copy.
            #
            # The projection is recomputed at READ time, so a viewer upgrade
            # improves rows archived years earlier. One frozen at harvest could
            # only ever get staler.
            "report": dumps(report.get("document") or report),
        })

    gate = rung.get("gate")
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


def harvest_stage(archive: Archive, stage: Dict[str, Any]) -> int:
    """Every rung in one scanned stage. Returns how many were newly stored."""
    name = str(stage.get("name") or "")
    return sum(1 for rung in (stage.get("rungs") or [])
               if harvest_rung(archive, name, rung))


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
        # single honest answer: recomputing it later against a rung that has
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


def reconcile(archive: Archive, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Re-check, at READ time, whether each archived rung is still on disk.

    THE COLUMN CANNOT MAINTAIN ITSELF, and the reason is the whole point of the
    feature: once a rung is deleted it is never scanned again, so the harvest
    that would have updated it never runs. A stored `rung_present` is therefore
    a last-known value, and last-known is precisely the kind of claim this
    codebase refuses to render as current - a row saying the directory is there
    when it is not would have the viewer assert something a `stat` disagrees
    with, which is the confidently-wrong failure in yet another costume.

    So the reading is taken here, on the rows actually being returned - bounded
    by the caller's limit, so it is a few dozen stats and not a walk of the
    whole history. The stored column is updated as a side effect, which makes
    it converge without ever being trusted.

    `presence, not promises` applies to Theatre's own database too. It has no
    special standing just because Theatre wrote it.
    """
    out = []
    for row in rows:
        item = dict(row)
        path = str(item.get("rung_path") or "")
        present = bool(path) and Path(path).is_dir()
        was = bool(item.get("rung_present"))
        item["rung_present"] = present
        if present != was:
            try:
                archive.mark_absent(str(item.get("run_key") or ""), present)
            except Exception:           # noqa: BLE001 - a read must not fail
                pass                    # the returned value is right regardless
        out.append(item)
    return out
