"""The eval sidecar - reading half. Theatre's share of a format it does not own.

Same arrangement as manifest.py, for the same reason: importing ms-moe-maker
would make a viewer depend on a training pipeline, and Theatre's `requires` is
empty on purpose. So both ends implement the format independently and
tests/test_eval_contract.py pins the constants against the real writer.

READ-ONLY, ABSOLUTELY. There is no writer here and there must never be one.

────────────────────────────────────────────────────────────────────────────
WHAT THIS FILE EXISTS TO PREVENT

An eval reported C# 0/10. The honest reading of that number is "this model
cannot write C#". The model was fine - the harness was shelling out to
`csc`/`mcs`, neither installed, and a missing compiler was being recorded as
ten wrong answers.

Nothing about the number was visibly wrong. That is the failure this whole
service is against: not being broken, being CONFIDENTLY WRONG. So the reader
keeps `unmeasurable` apart from `fail` all the way to the screen, and refuses
to produce a single number that hides the difference. `score` is the fraction
of MEASURED items that passed, and it is None - not 0.0 - when nothing could
be measured, because "we checked nothing" and "it got everything wrong" are
different sentences and only one of them is about the model.
────────────────────────────────────────────────────────────────────────────

STREAMING, AND THE PARTIAL LAST LINE. The writer appends and flushes per
record, so this file is routinely read WHILE it is being written. The final
line can therefore be half a JSON object. That is normal, not corruption, and
it is skipped in silence - unlike a bad line in the middle, which is real
damage and gets surfaced. Treating the two the same would either spam the
viewer during every healthy eval or hide genuine breakage; they are
distinguished by position, which is the only signal available and a sufficient
one.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

# Pinned against ms_moe_maker.evalrecord by tests/test_eval_contract.py.
SCHEMA_VERSION = 1

SIDECAR_PREFIX = "eval-"
SIDECAR_SUFFIX = ".jsonl"
SIDECAR_GLOB = f"{SIDECAR_PREFIX}*{SIDECAR_SUFFIX}"

KIND_HEADER = "header"
KIND_RECORD = "record"
KIND_FOOTER = "footer"
KINDS = (KIND_HEADER, KIND_RECORD, KIND_FOOTER)

PASS = "pass"
FAIL = "fail"
UNMEASURABLE = "unmeasurable"
ERROR = "error"
SKIPPED = "skipped"

VERDICTS = (PASS, FAIL, UNMEASURABLE, ERROR, SKIPPED)

# Only these form the denominator of a score. See the C# note above; the
# contract test asserts UNMEASURABLE is not in here on either side.
MEASURED = (PASS, FAIL)
UNMEASURED = (UNMEASURABLE, ERROR, SKIPPED)

# An eval that has not written a line in this long, with no footer, is not
# running any more. Matches the manifest's stall reasoning: a killed process
# never writes a terminal record, so its last word stays "in progress"
# forever and a viewer that believes it shows a spinner for something that
# died on Tuesday.
STALE_AFTER_SECONDS = 15 * 60


class UnreadableSidecar(Exception):
    """The file exists and this reader cannot honestly interpret it."""


@dataclass
class EvalItem:
    seq: int = 0
    item_id: str = ""
    suite: str = ""
    language: str = ""
    prompt: str = ""
    generation: str = ""
    validator: str = ""
    verdict: str = ""
    reason: str = ""
    expected: Any = None
    elapsed: Optional[float] = None
    ts: Optional[float] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    @property
    def known_verdict(self) -> bool:
        """False for a verdict from a newer writer.

        Painted as itself with a hollow marker rather than bucketed into
        anything. Guessing which existing bucket an unrecognised verdict
        belongs in is inventing a reading.
        """
        return self.verdict in VERDICTS

    @property
    def measured(self) -> bool:
        return self.verdict in MEASURED


@dataclass
class EvalRun:
    path: str = ""
    schema_version: int = SCHEMA_VERSION
    eval_id: str = ""
    suite: str = ""
    rung: str = ""
    model: str = ""
    total: Optional[int] = None
    started: Optional[float] = None
    ended: Optional[float] = None
    ok: Optional[bool] = None
    items: List[EvalItem] = field(default_factory=list)
    damaged_lines: int = 0

    # -- counting, kept honest --
    @property
    def counts(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for item in self.items:
            out[item.verdict] = out.get(item.verdict, 0) + 1
        return out

    @property
    def measured_count(self) -> int:
        return sum(1 for i in self.items if i.measured)

    @property
    def score(self) -> Optional[float]:
        """Fraction of MEASURED items that passed. None when nothing was.

        None, not 0.0. This is the C# rule in one line: a suite where every
        item was unmeasurable must not render as a model that failed
        everything.
        """
        if not self.measured_count:
            return None
        return sum(1 for i in self.items
                   if i.verdict == PASS) / self.measured_count

    @property
    def unmeasured(self) -> List[EvalItem]:
        """The items a score cannot speak for. Surfaced separately, always -
        this list being non-empty is a claim about the HARNESS, not the model,
        and it is usually the actionable one."""
        return [i for i in self.items if i.verdict in UNMEASURED]

    def stale(self, now: Optional[float] = None,
              after: float = STALE_AFTER_SECONDS) -> bool:
        if self.ended is not None:
            return False
        # ABSENCE, not falsiness. `ts or 0.0` and `if not last` both treat a
        # legitimate epoch of 0.0 as "no timestamp", so a run whose clock reads
        # zero can never go stale - the same class of bug as an empty set
        # reading as agreement. Collect what is actually present and check
        # emptiness explicitly.
        stamps = [i.ts for i in self.items if i.ts is not None]
        if self.started is not None:
            stamps.append(self.started)
        if not stamps:
            return False
        return ((now if now is not None else time.time()) - max(stamps)) > after

    @property
    def state(self) -> str:
        if self.ended is not None:
            return "finished" if self.ok is not False else "failed"
        if self.stale():
            return "stalled"
        return "running"


def _num(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def find(run_dir: Path) -> List[Path]:
    """Every eval sidecar in a run directory, newest last.

    A rung can hold several: eval is a standalone verb as well as a build
    phase, so re-running it after fixing a harness bug leaves both files side
    by side. That history is worth keeping - comparing the run that said 0/10
    against the run that said 9/10 after installing a compiler is the clearest
    possible statement of what actually changed.
    """
    try:
        found = [p for p in Path(run_dir).glob(SIDECAR_GLOB) if p.is_file()]
    except OSError:
        return []
    return sorted(found, key=lambda p: p.stat().st_mtime)


def read(path: Path) -> EvalRun:
    """Parse one sidecar. Tolerant of a partial final line, loud about the rest."""
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise UnreadableSidecar(f"{path.name}: {exc}") from exc

    out = EvalRun(path=str(path))
    lines = raw.splitlines()
    for index, line in enumerate(lines):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            # Last line mid-write is expected while an eval is running. Any
            # other position is real damage and gets counted so the viewer can
            # say the display is incomplete rather than quietly showing less.
            if index == len(lines) - 1:
                continue
            out.damaged_lines += 1
            continue
        if not isinstance(obj, dict):
            out.damaged_lines += 1
            continue

        kind = obj.get("kind")
        if kind == KIND_HEADER:
            version = obj.get("schema_version")
            if isinstance(version, int) and version > SCHEMA_VERSION:
                raise UnreadableSidecar(
                    f"{path.name}: schema_version {version} is newer than this "
                    f"viewer understands ({SCHEMA_VERSION}). Upgrade "
                    f"seren-theatre rather than showing you a guess.")
            out.schema_version = version if isinstance(version, int) \
                else out.schema_version
            out.eval_id = str(obj.get("eval_id") or "")
            out.suite = str(obj.get("suite") or "")
            out.rung = str(obj.get("rung") or "")
            out.model = str(obj.get("model") or "")
            total = obj.get("total")
            out.total = int(total) if isinstance(total, int) else None
            out.started = _num(obj.get("started"))
        elif kind == KIND_FOOTER:
            out.ended = _num(obj.get("ended"))
            out.ok = obj.get("ok")
        elif kind == KIND_RECORD:
            extra = obj.get("extra")
            out.items.append(EvalItem(
                seq=int(obj.get("seq") or 0),
                item_id=str(obj.get("item_id") or ""),
                suite=str(obj.get("suite") or out.suite),
                language=str(obj.get("language") or ""),
                prompt=str(obj.get("prompt") or ""),
                generation=str(obj.get("generation") or ""),
                validator=str(obj.get("validator") or ""),
                verdict=str(obj.get("verdict") or ""),
                reason=str(obj.get("reason") or ""),
                expected=obj.get("expected"),
                elapsed=_num(obj.get("elapsed")),
                ts=_num(obj.get("ts")),
                extra=extra if isinstance(extra, dict) else {},
            ))
        # An unknown kind is IGNORED, not damaged. That is the forward-compat
        # promise that lets the writer add a line type without every older
        # viewer declaring the file broken.
    return out


def as_dict(run: EvalRun, *, tail: Optional[int] = None) -> Dict[str, Any]:
    """Flatten for /api/state, including the DERIVED fields.

    The server decides `score`, `state` and the measured/unmeasured split, and
    the browser never recomputes them - two implementations of "did this pass"
    would eventually disagree, on screen, which is the specific way a dashboard
    starts lying.

    `tail` bounds how many items cross the wire. A 300-item suite carrying full
    generations is megabytes, and the room must never be the reason the box is
    busy; the viewer asks for the rest when a person opens one.
    """
    items = run.items if tail is None else run.items[-tail:]
    return {
        "path": run.path,
        "schema_version": run.schema_version,
        "eval_id": run.eval_id,
        "suite": run.suite,
        "rung": run.rung,
        "model": run.model,
        "total": run.total,
        "started": run.started,
        "ended": run.ended,
        "ok": run.ok,
        "state": run.state,
        "stale": run.stale(),
        "counts": run.counts,
        # BOTH numbers, always, and never just the ratio. `measured` is the
        # denominator the score is entitled to speak for; `seen` is how many
        # items exist. When those differ, something was not checked, and the
        # viewer is obliged to say so rather than quietly dividing by the
        # bigger number.
        "measured": run.measured_count,
        "seen": len(run.items),
        "score": run.score,
        "unmeasured": len(run.unmeasured),
        "damaged_lines": run.damaged_lines,
        "items": [
            {"seq": i.seq, "item_id": i.item_id, "suite": i.suite,
             "language": i.language, "prompt": i.prompt,
             "generation": i.generation, "validator": i.validator,
             "verdict": i.verdict, "known_verdict": i.known_verdict,
             "measured": i.measured, "reason": i.reason,
             "expected": i.expected, "elapsed": i.elapsed, "ts": i.ts,
             "extra": i.extra}
            for i in items
        ],
    }
