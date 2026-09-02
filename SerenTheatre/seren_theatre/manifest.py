"""Reading a run manifest - Theatre's half of a format it does not own.

WHY THIS IS A SECOND IMPLEMENTATION AND NOT AN IMPORT.

`ms-moe-maker` writes `msmoe-run.json` into a run directory. Theatre reads it. The
obvious move is for Theatre to `pip install ms-moe-maker` and import its reader, and
that is exactly the move that would wreck the design: Theatre would then
require a training pipeline - torch's whole world eventually - to display a
directory. Theatre's `requires` is empty on purpose, and a stage is a directory
precisely so that watching one costs nothing.

So this is a wire format, and both sides implement it independently, the way
both ends of any protocol do. The cost is that the two can drift. That cost is
paid down in tests/test_manifest_contract.py, which pins these constants
against the real ms-moe-maker source when a sibling checkout is present - the same
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

# Pinned against ms_moe_maker.manifest by tests/test_manifest_contract.py.
MANIFEST_NAME = "msmoe-run.json"
SCHEMA_VERSION = 1
STALE_AFTER_SECONDS = 15 * 60

PENDING = "pending"
RUNNING = "running"
DONE = "done"
SKIPPED = "skipped"
FAILED = "failed"
REFUSED = "refused"
# Terminal, and NOT a success: the stage finished without the result it exists
# to produce - no llama.cpp, so export "converted nothing". The writer added it
# while the contract test above was blind, which is the whole argument for that
# test; a viewer that did not know the word would have painted a real outcome
# as an unrecognised one.
#
# Deliberately NOT added to COMPLETE. That tuple feeds done_count, and whether
# a warned stage counts toward progress is a rendering decision belonging to
# whoever owns the viewer - naming the status is what the wire format requires.
WARNED = "warned"

STATUSES = (PENDING, RUNNING, DONE, SKIPPED, FAILED, REFUSED, WARNED)
COMPLETE = (DONE, SKIPPED)

# THE KEYS THIS READER CLAIMS TO UNDERSTAND. Everything else in a manifest is
# kept verbatim in `Manifest.extra` rather than discarded.
#
# WHY A CATCH-ALL AND NOT JUST ANOTHER FIELD. The writer's own note on
# build_id / resolved / defaults_files says they need no schema bump because
# "unknown keys already fall through to `extra`" - which is true of the
# writer's reader and has never been true of this one. Three fields have now
# been added on the writing side and silently dropped on this side, each found
# and fixed one at a time. A catch-all closes the CLASS of bug rather than its
# latest instance: the next field the writer adds arrives visible on
# /api/state under `extra`, unrendered but present, so somebody can see it is
# there before anyone has thought to teach the viewer about it.
KNOWN_KEYS = frozenset({
    "schema_version", "recipe_id", "name", "size", "base", "experts",
    "started", "updated", "finished", "ok", "stages", "refusals",
    "build_id", "resolved", "defaults_files", "knobs",
})

# THE TWO KEYS INSIDE A `knobs` ENTRY, named here beside the rest of the wire
# format because that is what they are. This reader carries the mapping
# verbatim and never looks inside an entry - the VIEWER does, and a name that
# lives in only one of two implementations is the shape every drift between
# these packages has had. Pinned against the writer in
# tests/test_manifest_contract.py and against scripts.js in tests/test_app.py.
#
#   summary       prose: what this field is
#   derived_from  the expression it was computed from, or absent/None for a
#                 field somebody typed. A DIFFERENT KIND OF FACT from the
#                 prose, and the more valuable one for a reader staring at a
#                 number nobody entered - so the viewer renders it apart
#                 rather than folded in.
KNOB_SUMMARY = "summary"
KNOB_DERIVED_FROM = "derived_from"


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
    # WHAT THIS RUN ACTUALLY BUILT, as opposed to what its recipe asked for.
    # All three are stamped by the writer and were all three being discarded
    # here, because this dataclass simply stopped at `refusals`.
    #
    #   build_id        digest of the resolved config
    #   resolved        the FINGERPRINT: every resolved value that decides what
    #                   the build produces, defaults already filled in. NOT the
    #                   recipe verbatim - the writer deliberately excludes
    #                   identity and paths, the force/redo flags, throughput
    #                   tuning that changes how fast the teacher runs but not
    #                   what it emits, and the smoke-test settings that inspect
    #                   an artifact rather than build one. Anything rendering
    #                   this has to say so; implying the excluded fields were
    #                   absent from the recipe would be a lie of omission.
    #   defaults_files  {path: sha256[:12]} for each defaults file that
    #                   contributed, so a reader can see which defaults this
    #                   run inherited without going to find the file.
    build_id: str = ""
    resolved: Dict[str, Any] = field(default_factory=dict)
    defaults_files: Dict[str, str] = field(default_factory=dict)
    # THE GLOSSARY FOR `resolved`, keyed to the same field names:
    # {field: {"summary": str, "derived_from": str | None}}.
    #
    # WHY IT TRAVELS IN THE DOCUMENT rather than being asked for. A base
    # seren-theatre install has no ms-moe-maker in it - Theatre's `requires` is
    # empty on purpose - so there is no live tool to ask what a field means,
    # and a run archived last year has to explain itself anyway. Stamping it
    # beside `resolved` also means the two packages cannot drift on it: the
    # words arrive with the values they describe.
    #
    # CARRIED VERBATIM, deliberately. This reader does not decide which
    # entries are usable - that is one rendering decision in one place in the
    # viewer (knobFor), and a reader that quietly dropped a malformed entry
    # would hide a writer's coverage gap instead of showing it.
    knobs: Dict[str, Any] = field(default_factory=dict)
    # Everything a newer writer emits that this reader has never heard of.
    # Carried, not dropped. See KNOWN_KEYS for why this exists at all.
    extra: Dict[str, Any] = field(default_factory=dict)

    def stage(self, stage_id: str) -> Optional[Stage]:
        """Look one up by id. Mirrors ms_moe_maker.manifest.Manifest.stage.

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
        # Lenient in the same direction as everything else in this module: a
        # field of the wrong type reads as empty rather than raising. A
        # manifest whose `resolved` is a string is a writer bug, and an empty
        # playbill is a far smaller failure than refusing to show the run.
        build_id=str(raw.get("build_id") or ""),
        resolved=_mapping(raw.get("resolved")),
        defaults_files={str(k): str(v)
                        for k, v in _mapping(raw.get("defaults_files")).items()},
        # Same leniency, same direction: a `knobs` that is a string is a writer
        # bug, and a playbill with no `?` on it is a far smaller failure than
        # refusing to show the run.
        knobs=_mapping(raw.get("knobs")),
        extra={k: v for k, v in raw.items() if k not in KNOWN_KEYS},
    )


def _num(value: Any) -> Optional[float]:
    """Lenient number coercion - a string timestamp is still a timestamp."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _mapping(value: Any) -> Dict[str, Any]:
    """A dict, or an empty one. Never a raise and never a half-read shape."""
    return dict(value) if isinstance(value, dict) else {}


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
        # The playbill's source. `resolved` is the FINGERPRINTED config and not
        # a recipe file: a recipe on disk is mutable and can be edited after
        # the run, while this is the record of what was actually built.
        "build_id": manifest.build_id,
        "resolved": manifest.resolved,
        "defaults_files": manifest.defaults_files,
        # The words for the values above. Served whether or not the viewer has
        # anything to do with them, for the same reason `extra` is.
        "knobs": manifest.knobs,
        # Unrendered, deliberately present. A field this viewer has never heard
        # of should be VISIBLE on /api/state the day it starts being written,
        # not discovered a release later by whoever notices the gap.
        "extra": manifest.extra,
        "stages": [
            {"id": s.id, "label": s.label, "status": s.status,
             "known_status": s.known_status, "started": s.started,
             "ended": s.ended, "elapsed": s.elapsed, "artifact": s.artifact,
             "note": s.note}
            for s in manifest.stages
        ],
    }
