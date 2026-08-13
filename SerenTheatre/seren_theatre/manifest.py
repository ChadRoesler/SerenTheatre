"""Reading a run manifest - Theatre's half of a format it does not own.

WHY THIS IS A SECOND IMPLEMENTATION AND NOT AN IMPORT.

`ms-moe` writes `msmoe-run.json` into a run directory. Theatre reads it. The
obvious move is for Theatre to `pip install ms-moe` and import its reader, and
that is exactly the move that would wreck the design: Theatre would then
require a training pipeline - torch's whole world eventually - to display a
directory. Theatre's `requires` is empty on purpose, and a stage is a directory
precisely so that watching one costs nothing.

So this is a wire format, and both sides implement it independently, the way
both ends of any protocol do. The cost is that the two can drift. That cost is
paid down in tests/test_manifest_contract.py, which pins these constants
against the real ms-moe source when a sibling checkout is present - the same
bargain as the installer/module `--describe` parity check.

READ-ONLY, ABSOLUTELY. There is no writer here and there must never be one.
Theatre is a room with seats; it cannot perturb the thing on the table. If a
manifest is wrong, the fix belongs in whatever wrote it.

THE THREE OUTCOMES, kept distinct because collapsing them is how a dashboard
starts lying:

  * no manifest      -> None. An uninstrumented directory. Completely normal,
                        fall back to scraping, lose nothing.
  * unreadable       -> UnreadableManifest. Something wrote a file we cannot
                        interpret. Surfaced, never swallowed - quietly scraping
                        past it would hide a real problem behind a display that
                        looks fine.
  * readable         -> Manifest, believed.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# Pinned against ms_moe.manifest by tests/test_manifest_contract.py.
MANIFEST_NAME = "msmoe-run.json"
SCHEMA_VERSION = 1
STALE_AFTER_SECONDS = 15 * 60

PENDING = "pending"
RUNNING = "running"
DONE = "done"
SKIPPED = "skipped"
FAILED = "failed"
REFUSED = "refused"

STATUSES = (PENDING, RUNNING, DONE, SKIPPED, FAILED, REFUSED)
COMPLETE = (DONE, SKIPPED)


class UnreadableManifest(Exception):
    """A manifest file exists and this reader cannot honestly interpret it."""


@dataclass
class Stage:
    id: str
    label: str
    status: str = PENDING
    started: Optional[float] = None
    ended: Optional[float] = None
    artifact: Optional[str] = None
    note: Optional[str] = None

    @property
    def elapsed(self) -> Optional[float]:
        if self.started is None:
            return None
        return (self.ended or time.time()) - self.started

    @property
    def known_status(self) -> bool:
        """False for a status from a newer writer.

        The viewer paints this differently rather than guessing. Rendering an
        unrecognised state as 'pending' would be inventing a reading.
        """
        return self.status in STATUSES


@dataclass
class Manifest:
    schema_version: int = SCHEMA_VERSION
    recipe_id: str = ""
    name: str = ""
    size: str = ""
    base: str = ""
    experts: List[str] = field(default_factory=list)
    started: float = 0.0
    updated: float = 0.0
    finished: Optional[float] = None
    ok: Optional[bool] = None
    stages: List[Stage] = field(default_factory=list)
    refusals: List[str] = field(default_factory=list)

    def stage(self, stage_id: str) -> Optional[Stage]:
        """Look one up by id. Mirrors ms_moe.manifest.Manifest.stage.

        Kept symmetric with the writer on purpose: the two ends of a wire
        format are easier to reason about when the same question is spelled the
        same way on both sides, and the asymmetry was noticed by writing a
        script against the reader and reaching for a method that only the
        writer had.
        """
        for s in self.stages:
            if s.id == stage_id:
                return s
        return None

    @property
    def running(self) -> Optional[Stage]:
        for s in self.stages:
            if s.status == RUNNING:
                return s
        return None

    @property
    def done_count(self) -> int:
        return sum(1 for s in self.stages if s.status in COMPLETE)

    @property
    def failed(self) -> List[Stage]:
        return [s for s in self.stages if s.status == FAILED]

    def stale(self, now: Optional[float] = None,
              after: float = STALE_AFTER_SECONDS) -> bool:
        """Claims to be running, but has gone quiet.

        A killed process - OOM, closed SSH session, box reboot - never writes a
        terminal status, so its last word stays 'running' forever. A viewer
        that believes that shows a live spinner for a run that died on Tuesday,
        which is a worse failure than showing nothing.
        """
        if self.finished is not None or self.running is None:
            return False
        return ((now or time.time()) - self.updated) > after

    @property
    def state(self) -> str:
        """One word for the whole run, for the card header."""
        if self.failed:
            return "failed"
        if self.finished is not None:
            return "finished" if self.ok else "failed"
        if self.stale():
            return "stalled"
        if self.running is not None:
            return "running"
        return "idle"


def read(run_dir: Path) -> Optional[Manifest]:
    """Load the manifest from a run directory, or None if there isn't one."""
    path = Path(run_dir) / MANIFEST_NAME
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UnreadableManifest(f"{path.name}: {exc}") from exc
    if not isinstance(raw, dict):
        raise UnreadableManifest(f"{path.name}: top level is not an object")

    version = raw.get("schema_version")
    if not isinstance(version, int):
        raise UnreadableManifest(f"{path.name}: no usable schema_version")
    if version > SCHEMA_VERSION:
        raise UnreadableManifest(
            f"{path.name}: schema_version {version} is newer than this viewer "
            f"understands ({SCHEMA_VERSION}). Upgrade seren-theatre rather "
            f"than showing you a guess.")

    stages: List[Stage] = []
    for entry in raw.get("stages") or []:
        if not isinstance(entry, dict) or "id" not in entry:
            continue        # lenient: one bad stage must not sink the run
        stages.append(Stage(
            id=str(entry["id"]),
            label=str(entry.get("label") or entry["id"]),
            status=str(entry.get("status") or PENDING),
            started=_num(entry.get("started")),
            ended=_num(entry.get("ended")),
            artifact=entry.get("artifact"),
            note=entry.get("note"),
        ))

    return Manifest(
        schema_version=version,
        recipe_id=str(raw.get("recipe_id") or ""),
        name=str(raw.get("name") or ""),
        size=str(raw.get("size") or ""),
        base=str(raw.get("base") or ""),
        experts=[str(e) for e in (raw.get("experts") or [])],
        started=_num(raw.get("started")) or 0.0,
        updated=_num(raw.get("updated")) or 0.0,
        finished=_num(raw.get("finished")),
        ok=raw.get("ok"),
        stages=stages,
        refusals=[str(r) for r in (raw.get("refusals") or [])],
    )


def _num(value: Any) -> Optional[float]:
    """Lenient number coercion - a string timestamp is still a timestamp."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def as_dict(manifest: Manifest) -> Dict[str, Any]:
    """Flatten for /api/state. Includes the DERIVED fields the viewer paints
    from (state, stale, counts) so the browser never recomputes policy the
    server already decided - two implementations of 'is this run dead' would
    eventually disagree, on screen."""
    return {
        "schema_version": manifest.schema_version,
        "recipe_id": manifest.recipe_id,
        "name": manifest.name,
        "size": manifest.size,
        "base": manifest.base,
        "experts": manifest.experts,
        "started": manifest.started,
        "updated": manifest.updated,
        "finished": manifest.finished,
        "ok": manifest.ok,
        "state": manifest.state,
        "stale": manifest.stale(),
        "done_count": manifest.done_count,
        "stage_count": len(manifest.stages),
        "refusals": manifest.refusals,
        "stages": [
            {"id": s.id, "label": s.label, "status": s.status,
             "known_status": s.known_status, "started": s.started,
             "ended": s.ended, "elapsed": s.elapsed, "artifact": s.artifact,
             "note": s.note}
            for s in manifest.stages
        ],
    }
