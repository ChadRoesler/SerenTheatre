"""Readers. Turn a directory of training wreckage into something glanceable.

This module is the whole point of SerenTheatre and it has exactly one rule:
IT NEVER WRITES. Not a lockfile, not a cache, not a marker. The theatre must
be safe to point at a live 14B run at 34 s/it, and "safe" means it cannot
perturb what it is watching. Every function here opens read-only, seeks to the
tail, and gets out.

THE CARRIAGE-RETURN TRAP, since it is the first thing anyone hits:
tqdm writes progress with \\r and no newline, so a redirected training log is
one enormous line with NUL-ish bytes in it. `grep` calls the file binary and
silently refuses to print matches - "binary file matches" is not an error, it
is grep declining. Everything here reads bytes, strips \\r, and treats each
carriage-returned fragment as its own line. That single detail is the
difference between "the log is empty" and "the log is fine".
"""
from __future__ import annotations

import ast
import json
import os
import re
import time
from dataclasses import dataclass, field, asdict
from fnmatch import fnmatch, translate as _fn_translate

from . import evalreport as _results
from . import manifest as _manifest
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


# ── raw reading ─────────────────────────────────────────────────────────────

def tail_text(path: Path, limit: int) -> str:
    """Last `limit` bytes, CR-split into real lines. Never raises."""
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > limit:
                fh.seek(size - limit)
                fh.readline()          # drop the partial line we landed in
            raw = fh.read()
    except OSError:
        return ""
    # Decode leniently: a half-written UTF-8 sequence at the tail boundary is
    # normal, not a problem to report.
    text = raw.decode("utf-8", "replace")
    # \r is a line break here, not a control character. This is the trap above.
    return text.replace("\r\n", "\n").replace("\r", "\n")


# ── parsed shapes ───────────────────────────────────────────────────────────

@dataclass
class TrainStep:
    step: Optional[int] = None
    total: Optional[int] = None
    loss: Optional[float] = None
    grad_norm: Optional[float] = None
    lr: Optional[float] = None
    accuracy: Optional[float] = None
    epoch: Optional[float] = None
    tokens: Optional[float] = None
    rate: Optional[str] = None          # "34.79s/it" as written
    eta: Optional[str] = None


@dataclass
class RunLog:
    name: str
    path: str
    mtime: float
    size: int
    phase: str = "unknown"              # the STAGE of the pipeline it reached
    activity: Optional[str] = None      # the machinery it is grinding on now
    subject: Optional[str] = None       # which expert / stage
    cfg: Dict[str, str] = field(default_factory=dict)
    budgets: List[Dict[str, Any]] = field(default_factory=list)
    step: TrainStep = field(default_factory=TrainStep)
    milestones: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    stalled_for: Optional[float] = None


# ── patterns ────────────────────────────────────────────────────────────────

_CFG = re.compile(r"^\[cfg\]\s*(.+)$")

# THE 579 TRAP. Read this before touching the progress regex.
#
# A training log holds MANY tqdm bars and only one of them is the training. The
# others are `Loading weights:`, `Fetching N files:`, `Map:`, `Tokenizing train
# dataset:`, `Packing train dataset:`, `Writing model shards:` and friends.
#
# The 14B log's LAST bar is `Loading weights: 579/579` from the stitch phase,
# and the naive "take the last progress bar" reading therefore reports 579
# steps. That is not hypothetical - it is exactly the number a human read off
# this same file and asked about, because 579 appears identically under every
# expert and looks like a step count. The first version of this parser made the
# same mistake, which is the best possible argument for the comment.
#
# The tell is the LABEL. tqdm writes `desc: NN%|...` when it has a description
# and bare `NN%|...` when it does not, and the transformers Trainer's own bar
# is the unlabelled one. So: capture whatever precedes the percentage and
# refuse anything that is labelled. An unlabelled bar is training; a labelled
# bar is machinery, and machinery gets reported as a PHASE, never as a step.
_PROGRESS = re.compile(
    r"(?P<desc>[^|\n]*?)(?P<pct>\d+)%\|[^|]*\|\s*(?P<cur>\d+)/(?P<tot>\d+)\s*"
    r"\[(?P<elapsed>[^<]+)<(?P<eta>[^,]+),\s*(?P<rate>[^\]]+)\]")
# Trailing text right before "NN%|" that means "this bar is not the training".
_LABELLED = re.compile(r"[A-Za-z][\w .\-/]*:\s*$")
_METRICS = re.compile(r"^\{'loss':.*\}$")
_BUDGET = re.compile(
    r"token budget (\S+):\s*([\d.]+)M of ([\d.]+)M from (\d+)/(\d+) docs.*?~(\d+) steps")
_SHORT = re.compile(r"\*\*\* (\S+) is SHORT of the token budget")
_FINETUNE = re.compile(r"^Fine-tuning (\S+?)\.\.\.")
_SAVED = re.compile(r"Dense specialist saved to (.+)$")
_MILESTONES = (
    (re.compile(r"Stitching (\d+) experts"), "stitching experts"),
    (re.compile(r"MoE skeleton saved"), "skeleton saved"),
    (re.compile(r"router-only training: ([\d,]+) trainable"), "router training"),
    (re.compile(r"Fraunkenstein Agent MoE is ALIVE"), "MoE alive"),
    (re.compile(r"converted OK \(([\d.]+) GB\)"), "GGUF converted"),
    (re.compile(r"smoke test PASSED"), "smoke test passed"),
)
_WARNINGS = (
    (re.compile(r"\*\*\* DISAGREES WITH THE ENV"), "dense_layers env is being ignored"),
    (re.compile(r"is SHORT of the token budget"), "an expert is short of budget"),
    (re.compile(r"ALLOCATOR BALLOON"), "allocator ballooning"),
    (re.compile(r"did not finish in \d+s"), "smoke test hung"),
    (re.compile(r"SKIPPED - no "), "a language was not measurable"),
    (re.compile(r"Traceback \(most recent call last\)"), "traceback"),
    (re.compile(r"CUDA out of memory|NV_ERR_NO_MEMORY"), "CUDA OOM"),
)


def parse_run_log(path: Path, limit: int) -> RunLog:
    try:
        st = path.stat()
    except OSError:
        return RunLog(name=path.name, path=str(path), mtime=0.0, size=0)

    out = RunLog(name=path.name, path=str(path), mtime=st.st_mtime, size=st.st_size)
    text = tail_text(path, limit)
    if not text:
        return out

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        m = _CFG.match(line)
        if m:
            # "[cfg] run: size=0.5B ... target_steps=150" -> flat key/value.
            for k, v in re.findall(r"(\w+)=(\S+)", m.group(1)):
                out.cfg[k] = v
            continue

        m = _FINETUNE.match(line)
        if m:
            out.phase, out.subject = "training specialist", m.group(1)
            continue

        m = _BUDGET.search(line)
        if m:
            out.budgets.append({
                "expert": m.group(1), "tokens_m": float(m.group(2)),
                "budget_m": float(m.group(3)), "docs_used": int(m.group(4)),
                "docs_total": int(m.group(5)), "steps": int(m.group(6)),
                "short": False,
            })
            continue

        m = _SHORT.search(line)
        if m:
            for b in out.budgets:
                if b["expert"] == m.group(1):
                    b["short"] = True
            continue

        if _METRICS.match(line):
            # The trainer prints a python dict of STRINGS. literal_eval rather
            # than json.loads - single quotes are not JSON, and eval() on a
            # training log is how you get owned by your own tooling.
            try:
                d = ast.literal_eval(line)
            except (ValueError, SyntaxError):
                continue

            def num(key: str) -> Optional[float]:
                try:
                    return float(d[key])
                except (KeyError, TypeError, ValueError):
                    return None

            out.step.loss = num("loss") if "loss" in d else out.step.loss
            out.step.grad_norm = num("grad_norm")
            out.step.lr = num("learning_rate")
            out.step.accuracy = num("mean_token_accuracy")
            out.step.epoch = num("epoch")
            out.step.tokens = num("num_tokens")
            if "train_runtime" in d:
                out.phase = "specialist finished"
            continue

        m = _PROGRESS.search(line)
        if m:
            desc = m.group("desc").strip()
            if _LABELLED.search(m.group("desc")):
                # Machinery, not training. Kept in its OWN field: a 25-minute
                # weight load should look alive rather than hung, but it must
                # not overwrite "stitching experts" - the pipeline stage is the
                # thing you want at a glance, the spinner is the reassurance.
                out.activity = desc.rstrip(":").strip().lower() or out.activity
                continue
            # Unlabelled bar = the Trainer's own. Last one in the tail wins.
            out.step.step, out.step.total = int(m.group("cur")), int(m.group("tot"))
            out.step.eta = m.group("eta").strip()
            out.step.rate = m.group("rate").strip()
            continue

        m = _SAVED.search(line)
        if m:
            out.milestones.append(f"saved {Path(m.group(1)).name}")
            continue

        for pat, label in _MILESTONES:
            if pat.search(line):
                if label not in out.milestones:
                    out.milestones.append(label)
                out.phase = label
                break

        for pat, label in _WARNINGS:
            if pat.search(line) and label not in out.warnings:
                out.warnings.append(label)

    # "How long since anything happened" is the number you actually want at a
    # glance, because a 14B step is 35 seconds and a hang looks exactly like a
    # slow step until you know the cadence.
    import time
    out.stalled_for = max(0.0, time.time() - out.mtime)
    return out


# ── artifacts on disk ───────────────────────────────────────────────────────
#
# TWO WAYS TO READ A RUN, and the order matters.
#
# The manifest (msmoe-run.json) is AUTHORITATIVE when present. It is written by
# whatever is doing the building and it names its own stages, so it is exact
# and it survives the pipeline renaming things.
#
# Scraping is the FALLBACK. The globs below hardcode the pipeline's internal
# artifact names, which is a real coupling - and the coupling BIT, exactly as
# this comment predicted it would. The prediction used to end here, and that
# was the whole defect: somebody wrote the warning and never grepped.
#
# WHAT HAPPENED. ms-moe-maker renamed every one of these during the
# decomposition - `qwen_coder_*` became `specialist_*`, and the two
# fraunkenstein_* directories became `moe_untrained` and `moe_trained`. Theatre
# went on looking for the old names and so reported `specialists: []`,
# `skeleton: False`, `final: False` for every healthy ms-moe-maker run there
# has ever been. Nothing looked broken, because the manifest half kept working
# and rendered a confident FINISHED card over a disk reading of nothing.
#
# So BOTH vocabularies live here now, and the old ones are not deadweight: a
# viewer whose entire promise is "a stage is a directory" must not go blind on
# a run somebody built last year. tests/test_artifact_names.py pins this table
# against ms_moe_maker.run.stages whenever the builder is importable, which is
# the check whose absence let the rename land silently.
#
# But scraping must NEVER be deleted, because "a stage is a directory" is the
# whole reason Theatre requires nothing. A folder somebody redirected a log
# into, with no pipeline cooperating at all, is a first-class thing to watch.
# So: believe the manifest when it is there, read the disk when it is not, and
# say which one you did.

#: The three readings a run directory can offer, named rather than spelled at
#: each use site so that `scan_run`, `looks_like_run` and `evaluable` cannot
#: drift apart the way this table drifted from the builder.
SPECIALISTS_STAGE = "specialists"
SKELETON_STAGE = "skeleton"
FINAL_STAGE = "final"

_STAGES = (
    # ms-moe-maker, current. Quoted from ms_moe_maker.run.stages: ARTIFACTS
    # for the two MoE directories, FINETUNE_ARTIFACT for the specialists.
    (SPECIALISTS_STAGE, "specialist_*", "config.json"),
    (SKELETON_STAGE, "moe_untrained", "config.json"),
    (FINAL_STAGE, "moe_trained", "config.json"),
    # FraunkensteinsLab-era, kept on purpose. These directories still exist on
    # disks that predate the rename, and reading one is the difference between
    # a viewer that requires nothing and a viewer that requires a recent build.
    (SPECIALISTS_STAGE, "qwen_coder_*", "config.json"),
    (SKELETON_STAGE, "fraunkenstein_moe_untrained", "config.json"),
    (FINAL_STAGE, "fraunkenstein_agent_final", "config.json"),
)


def _rows_for(stage: str) -> Tuple[Tuple[str, str], ...]:
    """Every (pattern, marker) that means `stage`, newest vocabulary first."""
    return tuple((p, m) for name, p, m in _STAGES if name == stage)


def _expert_from(name: str, pattern: str) -> str:
    """The expert's name with the artifact prefix taken off.

    Only a plain trailing-`*` pattern has a prefix that can be removed by
    arithmetic; anything else is reported whole rather than guessed at, because
    a half-stripped expert name is worse than an ugly one.
    """
    if pattern.endswith("*") and "*" not in pattern[:-1]:
        return name[len(pattern) - 1:]
    return name


# ── the smoke test: the LOG and the PROOF are different files ───────────────
#
# THE LIE THIS REPLACED. `.smoketest.txt` is the LOG, and the writer opens it
# BEFORE running the checks that can fail. So it is sitting there for a GGUF
# whose llama-cli exited non-zero, and for one that failed the degenerate-run
# check and emits the same token forever. Reading the log as proof is how this
# viewer spent a while printing "smoke-tested" over a model that failed its
# smoke test - the single worst way for a dashboard to be wrong, because it is
# wrong in the reassuring direction.
#
# `.smokepass.txt` is the PROOF. The writer only writes it after every check
# passes: exit code asserted, output judged on stdout alone, degenerate run
# clean. That is the file that means the smoke test passed, and it is the only
# file this module will call a pass.
SMOKE_LOG_SUFFIX = ".smoketest.txt"
SMOKE_PROOF_SUFFIX = ".smokepass.txt"

# The three states, named so that the middle one cannot be mistaken for either
# of its neighbours. See _smoke_reading for why it is spelled this way.
SMOKE_PASSED = "passed"
SMOKE_UNPROVEN = "failed or unproven"
SMOKE_NOT_RUN = "not run"


def _smoke_reading(gguf: Path) -> Dict[str, Any]:
    """Which of the three smoke states this GGUF is in. Presence, not promises.

    THE MIDDLE STATE IS NOT A BOOLEAN, and that is the whole point of this
    function existing instead of a one-liner. A log with no proof beside it is
    one of two things and disk cannot tell them apart: the test RAN AND FAILED,
    or the run predates the proof file existing at all. Both of those are "not
    proven to have passed" and neither of them is "passed".

    So they share one honestly-named state. Calling it `failed` would invent a
    verdict about every older run on the box; calling it `passed` is the bug
    this replaced. "failed or unproven" is the true sentence, it is the state a
    person most needs to see, and it is deliberately the loud one in the viewer
    - a smoke test that ran and left no proof is exactly the thing you want to
    go read the log about.
    """
    log = Path(str(gguf) + SMOKE_LOG_SUFFIX)
    proof = Path(str(gguf) + SMOKE_PROOF_SUFFIX)
    has_log, has_proof = log.is_file(), proof.is_file()
    if has_proof:
        state = SMOKE_PASSED
    elif has_log:
        state = SMOKE_UNPROVEN
    else:
        state = SMOKE_NOT_RUN
    return {"state": state,
            "proof": proof.name if has_proof else None,
            "log": log.name if has_log else None}


# ── the stagehand run marker ────────────────────────────────────────────────
#
# `.stagehand-run.json` is dropped by stagehand.run_detached into the CWD a
# build was launched in - the stage directory, beside the log and events files,
# NOT inside a run. Stagehand does not know the run layout and is not going to
# learn it, so this is read at stage level, which is also where it is most
# useful: a launch that died on arrival never creates a run at all, so the run
# cards are exactly the place it would be invisible.
#
# NAMED HERE, NOT IMPORTED FROM stagehand. sources.py is on the viewer's import
# graph and stagehand deliberately is not - tests/test_stagehand.py's
# test_the_viewer_never_imports_stagehand pins that, because the room has to
# stay installable on a box with no build tooling at all. So this takes the same
# bargain manifest.py takes with msmoe-run.json: the reader spells the name out
# itself, and a test asserts the two constants still agree.
DETACHED_MARKER = ".stagehand-run.json"

# The schema this reader was written against. Reported rather than enforced: a
# newer marker is still read for the keys it does share, and the viewer says
# which version it met. Refusing to read one would turn a writer upgrade into a
# blank panel.
MARKER_SCHEMA_VERSION = 1

# WHAT A LAUNCH OUTCOME LOOKS LIKE. The writer owns this vocabulary and this
# reader does not get a vote, so: recognised words map to a verdict, an
# unrecognised one maps to None and is reported AS ITSELF. Same rule the viewer
# already follows for an unknown manifest status, and it is here for the same
# reason - bucketing something you do not recognise into a state you do is
# inventing a reading.
#
# THREE WORDS, NOT TWO, AND THE MIDDLE ONE IS THE INTERESTING ONE.
#
#   started   still alive when stagehand looked. The normal shape of a build.
#   finished  ALREADY EXITED 0, inside stagehand's liveness window. This is a
#             success. `ms-moe-maker build r.yaml --json --plan` resolves the
#             config, prints its stages and is done in about a third of a
#             second - legitimately shorter than the window it is checked in.
#             "Already gone" is not "died"; the EXIT CODE is the discriminator,
#             and no successful build exits non-zero.
#   failed    already exited non-zero. Nothing is running, and `exit_code`,
#             `error` and `log_tail` are populated.
#
# `finished` maps to launched=True because it DID launch - it just also already
# ended. The word itself is kept in `launch_state` so the room can say "ran and
# finished immediately" instead of painting a live-looking panel over a pid that
# was gone before the page loaded.
_LAUNCH_WORDS = {
    "started": True,
    "finished": True,
    "failed": False,
    # Spellings from a writer that is not this one. Tolerated, not expected -
    # train strict, infer lenient.
    "running": True, "ok": True, "success": True, "alive": True,
    "error": False, "died": False, "dead": False,
}


def _launch_verdict(payload: Dict[str, Any]) -> tuple:
    """(launched, raw_state, detail). launched is True / False / None.

    None means THE MARKER DOES NOT SAY - an older writer, or a word this reader
    has not met. It is not a synonym for fine. An absent verdict rendered as
    success is the identical bug to a smoke log rendered as a pass, and this
    module just finished fixing that one twenty lines up.
    """
    raw = payload.get("launch")
    if isinstance(raw, bool):           # a writer that spelled it as a flag
        return raw, ("started" if raw else "failed"), None
    if isinstance(raw, str) and raw.strip():
        word = raw.strip()
        verdict = _LAUNCH_WORDS.get(word.lower())
        # .get, so an unrecognised word lands on None and is handed back intact
        # for the room to render as itself.
        return verdict, word, None
    for key in ("launched", "ok", "alive"):
        if isinstance(payload.get(key), bool):
            value = payload[key]
            return value, ("started" if value else "failed"), None
    # No verdict field at all. A one-line reason is still a verdict of sorts -
    # nobody writes an error string about a launch that went fine.
    for key in ("error", "launch_error", "detail", "reason"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return False, "failed", value.strip()
    code = payload.get("exit_code")
    # ONLY REACHED BY A MARKER WITH NO VERDICT WORD AT ALL - an older writer, or
    # one that is not stagehand. An exit code here means the same thing it means
    # everywhere else in this file: the child WAS ALREADY GONE when somebody
    # looked. Nonzero is unambiguously a dead launch. Zero is not: it could be a
    # `--plan` that did its whole job in a third of a second, or a marker some
    # future writer updated hours later when the build finished. So the loud
    # case stays loud and the ambiguous one stays None - which means "this
    # marker does not say", and never means "fine".
    if isinstance(code, int) and not isinstance(code, bool) and code != 0:
        return False, "failed", f"the child exited with code {code}"
    return None, None, None


def _file_activity(path: str, role: str) -> Dict[str, Any]:
    """stat() one of the files the launch named. A read, and only a read."""
    try:
        st = Path(path).stat()
    except OSError:
        # Absent is a real reading, and a common one: a build that died on
        # arrival never opened either file. Reported, not hidden.
        return {"role": role, "path": path, "exists": False,
                "mtime": None, "size": None, "since": None}
    return {"role": role, "path": path, "exists": True,
            "mtime": st.st_mtime, "size": st.st_size,
            "since": max(0.0, time.time() - st.st_mtime)}


def read_launch(directory: Path) -> tuple:
    """The `.stagehand-run.json` a detached launch left in `directory`.

    Returns (reading, error) and NEVER raises - the same three outcomes, and
    the same lenient discipline, as manifest.read:

      * absent     -> (None, None). Nobody launched from here. Normal.
      * unreadable -> (None, "why"). Reported, never swallowed: a file we
                      cannot interpret is a real problem, and scraping quietly
                      past it hides that behind a display that looks fine.
      * readable   -> (dict, None).

    WHAT THIS IS AND, MORE IMPORTANTLY, WHAT IT IS NOT. The marker says what was
    LAUNCHED: a command line, a recipe, a pid, a moment, and whether the child
    survived being started. THE MANIFEST REMAINS AUTHORITATIVE for what the run
    is doing. Nothing here feeds `source`, a stage status or a progress number,
    because stagehand's own docstring is blunt about why it opens no privileged
    channel back: a second way to know what is happening is a second opinion,
    and two opinions is how a dashboard starts disagreeing with itself.

    LAST ACTIVITY IS A MTIME, NOT A HEARTBEAT. `since_activity` is how long ago
    a file last grew, and that is ALL it is. A perfectly healthy run is silent
    for whole minutes - a 25-minute weight load writes nothing - so this reports
    the reading and refuses to draw the conclusion. "No output for four hours"
    is a fact somebody can act on. "Dead" is not something a stat() can tell
    you, and guessing it is how a dashboard earns a reputation for crying wolf.
    """
    marker = directory / DETACHED_MARKER
    try:
        raw = marker.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, None
    except OSError as exc:
        return None, f"{DETACHED_MARKER} could not be read: {exc}"
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        return None, f"{DETACHED_MARKER} is not readable JSON: {exc}"
    if not isinstance(payload, dict):
        return None, (f"{DETACHED_MARKER} holds a {type(payload).__name__}, "
                      f"not an object; this reader cannot honestly interpret "
                      f"it")

    launched, launch_state, detail = _launch_verdict(payload)
    if detail is None:
        for key in ("error", "launch_error", "detail"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                detail = value.strip()
                break

    files: List[Dict[str, Any]] = []
    events_path = None
    for role, key in (("log", "log_file"), ("events", "events_file")):
        value = payload.get(key)
        if isinstance(value, str) and value:
            files.append(_file_activity(value, role))
            if key == "events_file":
                events_path = value
    stamps = [f["mtime"] for f in files if f["mtime"] is not None]
    last = max(stamps) if stamps else None

    argv = payload.get("argv")
    command_line = payload.get("command_line")
    if not isinstance(command_line, str) or not command_line:
        command_line = (" ".join(str(a) for a in argv)
                        if isinstance(argv, list) and argv else None)

    exit_code = payload.get("exit_code")
    tail = payload.get("log_tail")
    return {
        "marker": DETACHED_MARKER,
        "path": str(marker),
        # Reported, not enforced. A newer marker is still read for the keys it
        # shares; the viewer says which version it met so a reader knows how
        # much of the panel to trust.
        "schema_version": payload.get("schema_version"),
        "understands_schema": MARKER_SCHEMA_VERSION,
        "launched": launched,
        # The writer's own word, kept verbatim so an unrecognised one can be
        # rendered as itself rather than bucketed into a state we do know.
        "launch_state": launch_state,
        "launch_detail": detail,
        # NOT FAILURE-ONLY. An exit code is recorded whenever the child was
        # already gone when stagehand looked, which includes the `finished`
        # case where it is 0 and is good news. The room reads it alongside
        # `launch_state` rather than treating its presence as a verdict.
        "exit_code": exit_code if isinstance(exit_code, int) else None,
        # The child's dying words, when the writer captured them. Failure-only,
        # and the single most useful thing on a failed launch - it is why this
        # panel is worth having rather than just a badge.
        "log_tail": tail if isinstance(tail, str) else None,
        "pid": payload.get("pid"),
        "recipe": payload.get("recipe"),
        "cwd": payload.get("cwd"),
        "started": payload.get("started"),
        "command_line": command_line,
        "files": files,
        # WHAT THE BUILD SAID ABOUT ITSELF, from the file the marker already
        # named. The marker is STAGEHAND's account - what it launched and
        # whether the child survived being started. This is the BUILDER's, and
        # the split is the point: the marker cannot know the run directory
        # because stagehand does not know the run layout and is not going to
        # learn it, while the builder chose the directory and says so.
        #
        # STRICTLY BOUNDED TO "WHERE AND UNDER WHAT". It answers which directory
        # this build is writing into and what environment was imposed on it, and
        # it feeds no status, no stage state and no progress number - the
        # manifest keeps that job, permanently. A second opinion about what a
        # run is DOING is how a dashboard starts disagreeing with itself, and
        # stagehand's own docstring said so before any of this existed.
        "run": read_started(events_path),
        "last_activity": last,
        "since_activity": (max(0.0, time.time() - last)
                           if last is not None else None),
    }, None


#: How deep under a stage path a run may sit before Theatre stops looking.
#:
#: THREE, AND THE NUMBER IS CHOSEN BY WHICH WAY IT FAILS. `roots.output` is a
#: template - `msmoe_run_{size}` lands a run at depth 1, `gauntlet-runs/{size}`
#: at depth 2 - so 2 covers every layout ms-moe-maker produces today and 3
#: leaves room for one the author of this line has not seen.
#:
#: Being too SHALLOW loses a run silently: it is not drawn, and because harvest
#: only ever sees runs the scan found, it is never archived either. Being too
#: DEEP costs about twenty milliseconds per refresh on an 87,000-entry
#: workbench. Those are not comparable prices, so this errs deep.
RUN_DEPTH_DEFAULT = 3


# COMPILED ONCE PER TABLE, and that is a measurement rather than a habit.
# `fnmatch.fnmatch` normcases its arguments and re-consults its cache on every
# call, and this predicate runs it against every entry of every directory under
# a stage: on a realistic workbench it was 295,200 calls and 70% of the cost of
# one discovery pass.
#
# Keyed on the table object so a test that installs a different _STAGES gets
# matchers for the table it installed. A set compiled at import would make the
# fast path quietly ignore the very thing a mutation test is changing.
_MATCHERS: Dict[Any, Tuple[Tuple[str, Any, str], ...]] = {}


def _matchers() -> Tuple[Tuple[str, Any, str], ...]:
    hit = _MATCHERS.get(_STAGES)
    if hit is None:
        hit = tuple((stage, re.compile(_fn_translate(pattern)).match, marker)
                    for stage, pattern, marker in _STAGES)
        _MATCHERS[_STAGES] = hit
    return hit


def _run_reading(root: Path) -> Tuple[bool, List[Path]]:
    """Is this a run, and what are its subdirectories - from ONE read.

    TWO ANSWERS FROM ONE SCANDIR, because discovery needs both and asking
    separately meant reading the big directories twice: a forty-thousand-entry
    shard cache walked end to end to learn it holds no subdirectories, then
    walked again to learn it is not a run. Sharing the read halved a
    discovery pass.

    A MANIFEST ENDS THE QUESTION AND THE DESCENT. It is one stat, it is true
    for every instrumented run from its first second, and a run's children are
    artifacts - never runs - so there is nothing below worth enumerating.

    The subdirectory list is what makes pruning free: a caller that gets
    `True` should not descend, and one that gets `False` already holds the
    children it would otherwise have to go back and ask for.
    """
    try:
        if (root / _manifest.MANIFEST_NAME).is_file():
            return True, []
    except OSError:
        return False, []

    is_run = False
    subdirs: List[Path] = []
    try:
        with os.scandir(root) as entries:
            for entry in entries:
                name = entry.name
                # DIRENT TYPE BEFORE PATTERN. A stage artifact is a directory,
                # so testing the cheap thing first skips every file in a cache
                # before it can reach a regex - which is where the 295,200
                # calls went.
                if not entry.is_dir(follow_symlinks=False):
                    if not is_run and name.endswith(".gguf"):
                        is_run = True
                    continue
                subdirs.append(Path(entry.path))
                if is_run:
                    continue
                for _stage, match, marker in _matchers():
                    if match(name) and os.path.isfile(
                            os.path.join(entry.path, marker)):
                        is_run = True
                        break
    except OSError:
        return False, []
    return is_run, subdirs


def looks_like_run(root: Path) -> bool:
    """Is this directory a RUN, or just something the glob happened to catch?

    The globs are patterns, and a pattern cannot tell a run from its
    neighbours: `dryrun_*` matches `dryrun_0.5B` (a run) AND `dryrun_data`
    (the shared corpus root, which is not a run and never will be). Theatre
    rendered the corpus as an empty run card reading "Nothing built here yet",
    which is a true sentence about a directory that was never going to have
    anything built in it - so it reads as a failure and is only clutter.

    Note the asymmetry that hid it: the non-dryrun globs are `*_agent_*`, which
    `fraunkenstein_data` escapes. So this only ever appeared in DRYRUN mode -
    the mode you use for every shakedown and never for a real run. The worst
    possible distribution for noticing.

    So: ask what the directory IS, not what its name looks like. A run has a
    manifest (an instrumented run, even one that has produced nothing yet), or
    it has run-shaped artifacts (an uninstrumented one). Anything else is a
    directory that shares a prefix.

    This is one half of `_run_reading` and delegates to it rather than
    repeating it, for the reason the _STAGES table learned the hard way: two
    copies of one rule are two rules, and they drift.
    """
    return _run_reading(root)[0]


def discover_runs(root: Path,
                   max_depth: int = RUN_DEPTH_DEFAULT) -> List[Path]:
    """Every run under `root`, found by asking directories what they are.

    WHY THIS EXISTS AT ALL. Discovery used to be a list of globs in the config,
    and a glob is a promise the person has to keep: name your output root
    something the pattern matches, or your run is not merely undrawn but
    UNARCHIVED, because harvest only sees runs the scan found. That promise
    was broken twice by people who had the config open - once by a default that
    did not match the pipeline's own output directory, and once by a recipe
    whose `roots.output` fell outside a whitelist written the same evening.
    Both times the symptom was an empty stage, which reads as "nothing built
    here" rather than "the viewer is looking somewhere else".

    So Theatre stops being told and starts looking. `looks_like_run` was
    always the real gate; the globs only ever bounded the walk. This bounds it
    two better ways: stop at a run, because its children are artifacts, and
    stop at `max_depth`. The expensive directories on a workbench - shard and
    HF caches, corpora, a llama.cpp build tree - are either inside a run, and
    pruned, or shallow and cheap to reject.

    Breadth-first so `max_depth` means depth and not recursion order, and
    sorted at the end so two boxes reading one directory agree about what they
    found.
    """
    found: List[Path] = []
    seen: set[Path] = set()
    try:
        with os.scandir(root) as entries:
            frontier = [Path(e.path) for e in entries
                        if e.is_dir(follow_symlinks=False)]
    except OSError:
        return []

    depth = 1
    while frontier and depth <= max_depth:
        deeper: List[Path] = []
        for candidate in frontier:
            if candidate in seen:
                continue
            seen.add(candidate)
            is_run, subdirs = _run_reading(candidate)
            if is_run:
                found.append(candidate)
                continue
            deeper.extend(subdirs)
        frontier = deeper
        depth += 1
    return sorted(found)


def resolve_runs(root: Path, run_globs: Sequence[str],
                  max_depth: int = RUN_DEPTH_DEFAULT) -> List[Path]:
    """Which directories under `root` are runs. One answer, for every caller.

    ONE FUNCTION ON PURPOSE. `scan_stage` drew the cards and
    `backstage._evaluable_runs` decided what the eval button was allowed to
    measure, and each walked the tree its own way - so a run one of them found
    was not necessarily a run the other did, and nothing would have said so.
    That is the same two-copies-of-one-rule defect that let the artifact rename
    half-land, and it is cheaper to refuse it here than to find it later.

    An explicit `runs` list still wins, because a person who has told Theatre
    exactly where to look has said something worth obeying - a tree too large
    to walk, or a stage that should show one run out of forty. Empty means
    look, which is the default: mandate is not ethos, and a knob that must be
    kept in sync with another file will be wrong on somebody else's machine.
    """
    if run_globs:
        # DEDUPED. Overlapping globs are the normal case, not a
        # misconfiguration: `msmoe_*` plus `msmoe_run_*` matches one directory
        # twice, and it was then scanned twice, rendered twice, counted twice
        # in `earlier`, and handed to harvest twice.
        out: List[Path] = []
        seen: set[Path] = set()
        for pattern in run_globs:
            for p in sorted(root.glob(pattern)):
                # The glob proposes; the directory decides. See looks_like_run.
                if p in seen:
                    continue
                if p.is_dir() and looks_like_run(p):
                    seen.add(p)
                    out.append(p)
        return out
    return discover_runs(root, max_depth=max_depth)


def evaluable(root: Path) -> Dict[str, Any]:
    """Is there something in this run for `ms-moe-maker eval` to measure?

    ASK THE DISK, NOT THE HISTORY. The obvious gate for an eval button is "did
    a build finish successfully", and it is the wrong one three ways: a manifest
    rotates, so a model sitting right there becomes un-evaluable after a
    restart; a build that exited non-zero because llama.cpp was missing has a
    perfectly evaluable MoE and would be refused; and a "successful" build whose
    output directory was moved would offer a button that only 409s. The
    question eval itself asks is whether the trained MoE is on disk - its own
    CLI refuses with "run build first" on exactly that - so this asks the same
    thing, from the same table the run scan reads.

    Returns {ok, reason, path}. The reason is for the person, and it is present
    when ok is False, because a hidden button and a button that explains itself
    are different kindnesses and only one of them teaches you anything.
    """
    rows = _rows_for(FINAL_STAGE)
    if not rows:
        # The table changed shape under us. Say so rather than guessing.
        return {"ok": False, "path": None,
                "reason": f"no {FINAL_STAGE!r} row in the stage table, so "
                          f"Theatre cannot tell what a finished MoE looks like"}

    # EVERY row, not the first one. This used to take `next(...)` off the
    # table, which was fine while there was exactly one vocabulary and became a
    # silent refusal the moment there were two: the button asked for
    # fraunkenstein_agent_final, ms-moe-maker had written moe_trained, and the
    # answer was a confident "no trained MoE here yet" standing next to one.
    for pattern, marker in rows:
        try:
            found = sorted(root.glob(pattern))
        except OSError as exc:
            return {"ok": False, "path": str(root),
                    "reason": f"cannot read {root.name}: {exc}"}
        for candidate in found:
            try:
                if (candidate / marker).is_file():
                    return {"ok": True, "path": str(candidate), "reason": ""}
            except OSError as exc:
                return {"ok": False, "path": str(candidate),
                        "reason": f"cannot read {candidate.name}: {exc}"}

    # NAME EVERY VOCABULARY IT LOOKED FOR. A reader staring at a directory that
    # obviously holds a model needs to know which name Theatre wanted, and a
    # message that mentions only the current one is how somebody with an older
    # run on disk concludes the viewer is broken.
    wanted = " or ".join(f"{pattern}/" for pattern, _m in rows)
    return {"ok": False, "path": str(root / rows[0][0]),
            "reason": f"no trained MoE here yet - eval measures "
                      f"{wanted}, and none of those has a "
                      f"{rows[0][1]}. Build first."}


def scan_run(root: Path) -> Dict[str, Any]:
    """What EXISTS for one run. Presence, not promises.

    The scraping half deliberately mirrors what the pipeline's own _done()
    checks, because the dashboard must agree with the thing it is describing.
    A run that Theatre calls finished and the pipeline re-runs is worse than
    no dashboard.

    `source` in the returned dict says which reading this was - "manifest",
    "disk", or "disk (manifest unreadable)". The viewer shows it. A reader
    should always be able to tell how confident the display is entitled to be.
    """
    try:
        mtime = root.stat().st_mtime
    except OSError:
        mtime = None
    out: Dict[str, Any] = {"name": root.name, "path": str(root),
                           # The ordering fallback for an UNINSTRUMENTED run,
                           # which has no manifest `started` to sort on.
                           # Scraping is a first-class reading here and not a
                           # consolation prize, so it has to be orderable too.
                           "mtime": mtime,
                           "specialists": [], "skeleton": False,
                           "final": False, "gguf": None, "smoketested": False,
                           "smoke": None,
                           "source": "disk", "manifest": None,
                           "manifest_error": None}

    # The manifest first - but it AUGMENTS the disk scan rather than replacing
    # it. Both readings are cheap, and having them side by side is what lets
    # the viewer show "the manifest says stitched, and the skeleton is on
    # disk" - agreement being its own kind of evidence.
    try:
        found = _manifest.read(root)
    except _manifest.UnreadableManifest as exc:
        out["manifest_error"] = str(exc)
        out["source"] = "disk (manifest unreadable)"
    else:
        if found is not None:
            out["manifest"] = _manifest.as_dict(found)
            out["source"] = "manifest"

    # ── what a FINISHED run left behind ──────────────────────────────────
    #
    # Read here, beside the manifest, because these are the same KIND of
    # thing: small result documents dropped in the run directory rather than
    # artifacts to be pattern-matched. `eval` is a separate command from
    # `build` on purpose - a model must not grade itself as part of being
    # built - so its report is its own file and gets found by scanning,
    # exactly the way the GGUF and the smoke-pass marker are.
    #
    # THE MANIFEST'S build_id IS PASSED IN, and that is the load-bearing part.
    # A run gets rebuilt and the old eval report stays there, still valid
    # JSON, still full of confident numbers about a model that no longer
    # exists. Rendering that beside the new build is the C# 0/10 failure in a
    # new costume: nothing looks wrong and the reader draws a conclusion about
    # a thing that was never measured. evalreport.provenance answers it in
    # three states, and "unknown" is one of them.
    #
    # A BROKEN DOCUMENT IS SURFACED, NOT SWALLOWED. Backstage carried an
    # `errors` list that nothing ever rendered, and the fix for that was the
    # fourth time this codebase found data present and not shown. These two
    # error slots exist only if the viewer draws them; that is the deal.
    out["eval"] = None
    out["eval_error"] = None
    out["gate"] = None
    out["gate_error"] = None
    manifest_build = str((out.get("manifest") or {}).get("build_id") or "")
    try:
        out["eval"] = _results.read_eval(root, manifest_build)
    except _results.UnreadableResult as exc:
        out["eval_error"] = str(exc)
    try:
        out["gate"] = _results.read_gate(root)
    except _results.UnreadableResult as exc:
        out["gate_error"] = str(exc)

    try:
        entries = sorted(root.iterdir())
    except OSError:
        return out

    # READ FROM THE TABLE, NOT FROM MEMORY. This loop used to spell the three
    # artifact names out a SECOND time - `startswith("qwen_coder_")` and two
    # `==` comparisons - which is how a rename half-landed: _STAGES is what
    # looks_like_run and evaluable consult, this was what the card was drawn
    # from, and nothing made the two agree. Same table now, so a name added
    # above is a name understood here, and there is no second place to forget.
    for entry in entries:
        n = entry.name
        matched = False
        for stage, pattern, marker in _STAGES:
            if not fnmatch(n, pattern):
                continue
            try:
                if not (entry / marker).is_file():
                    continue
            except OSError:
                continue
            matched = True
            if stage == SPECIALISTS_STAGE:
                out["specialists"].append(_expert_from(n, pattern))
            elif stage == SKELETON_STAGE:
                out["skeleton"] = True
                try:
                    cfg = json.loads((entry / marker).read_text())
                    out["experts"] = cfg.get("expert_names")
                    out["dense_layers"] = cfg.get("mlp_only_layers")
                except (OSError, ValueError):
                    pass
            elif stage == FINAL_STAGE:
                out["final"] = True
            break
        if matched:
            continue
        if n.endswith(".gguf"):
            out["gguf"] = {"name": n, "gb": round(entry.stat().st_size / 1e9, 2)}
            # CONVERTED IS NOT PROVEN, and neither is TESTED. This line used to
            # read `.smoketest.txt` - the LOG, which the writer opens before the
            # checks that can fail - so a GGUF that flunked its degenerate-run
            # check was reported here as smoke-tested. The PROOF is
            # `.smokepass.txt`, and the three genuinely different states are
            # kept apart in `smoke`; see _smoke_reading.
            out["smoke"] = _smoke_reading(entry)
            # Kept, and now it means what its name always claimed: the smoke
            # test is PROVEN to have passed. Consumers of /api/state that only
            # want a boolean get the honest one.
            out["smoketested"] = out["smoke"]["state"] == SMOKE_PASSED
    out["specialists"].sort()
    return out


# ── has anything happened lately: readings, never a verdict ─────────────────
#
# THE STALLED LIE THIS REPLACES. `Manifest.stale()` compares `updated` against
# STALE_AFTER_SECONDS - fifteen minutes. But the manifest is rewritten on STAGE
# TRANSITIONS, and a fine-tune stage runs about 58 minutes (602 steps at
# 5.79 s/it). So every fine-tune stage of every healthy build crossed the
# threshold, and the room painted it red saying the process "was probably
# killed" - eight consecutive hour-long false alarms on an 8-expert gauntlet,
# while the thing trained perfectly.
#
# NO SINGLE THRESHOLD CAN FIX THAT, and raising the number is the wrong SHAPE
# of answer rather than the wrong value: it measures a stage-transition cadence
# against stages that range from seconds (preflight) to over an hour
# (fine-tune). Any value large enough to stop lying about a fine-tune is far
# too large to notice a preflight that died.
#
# The evidence was on disk the whole time and unread. The log and the events
# file are written CONTINUOUSLY - every tqdm redraw, every metrics dict - so
# "the manifest is quiet AND the log is quiet" is a completely different
# reading from "the manifest is quiet and the log wrote three seconds ago", and
# only the first of those is interesting.
#
# So this reports MTIMES and refuses to draw the conclusion, for exactly the
# reason read_launch's docstring already gives: "manifest quiet 46m · log wrote
# 3s ago" is a fact somebody can act on, and "the process was probably killed"
# is not something a stat() can tell you. It was said in red, on every healthy
# hour-long stage, twice watched.


ARTIFACTS_ROLE = "artifacts"


def run_activity(run: Optional[Path]) -> Optional[Dict[str, Any]]:
    """The run directory's OWN churn, as one reading. Never raises.

    WHY THIS EXISTS, and it is the same bug as the one above wearing different
    clothes: the evidence was on disk and nobody looked. A build run by hand in
    a terminal sends stdout to the TERMINAL. There is no `*.log` in the stage
    and stagehand dropped no marker, so `activity_files` finds nothing at all -
    and a live router training at 3157/4000 at 3.47 s/it read, in red, "Nothing
    here has been written for 2h 42m - not the manifest, not the log."

    But the run directory is CHURNING the whole time. Fine-tune writes
    `tmp_<expert>/checkpoint-N/`, the router writes into `moe_trained/`, and
    stitch, abliterate and export all drop artifacts. Every one of those is an
    immediate child of the run directory, and CREATING A SUBDIRECTORY BUMPS
    ITS PARENT'S MTIME - so the directory itself plus one level of children
    catches all of it for the price of one readdir.

    ONE scandir, THEN stat the entries. NO RECURSION, and that is not a
    performance nicety - that tree holds ~45 GB of shards across thousands of
    files, and the README is blunt about it: "the dashboard must never be the
    reason the box is busy - that would be an unusually stupid way to perturb a
    measurement." A recursive walk here would be exactly that.

    THE OVERLAP WITH THE MANIFEST IS DELIBERATE AND INERT. The manifest lives
    in this directory, so creating it bumped the directory mtime too - this can
    never be fully independent of the reading it seconds. It does not matter:
    `activity_state` only ever consults this when the manifest has ALREADY been
    quiet longer than the window, and a manifest that was written 46 minutes
    ago left a 46-minute-old directory mtime behind it. The overlapping half is
    always outside the window by construction.

    KNOWN GAP, LEFT VISIBLY UNSOLVED. Corpus collection writes into a SIBLING
    of the run - `gauntlet-data/{size}` - not into the run itself, and
    `data_root` sits in the writer's `_FINGERPRINT_EXCLUDE`, so `resolved`
    cannot tell Theatre where that directory went. A long shard scan therefore
    still reads as stalled here. That is a gap and not a bug, and it is left
    open on purpose: guessing at sibling paths by name would be inventing a
    reading, which is the one thing this module never does.

    lstat, not stat: a symlinked child must not send this off following a link
    to an NFS mount to answer a question about local churn.
    """
    if run is None:
        return None
    path = Path(run)
    reading: Dict[str, Any] = {"role": ARTIFACTS_ROLE, "path": str(path),
                               "exists": False, "mtime": None, "size": None,
                               "since": None, "entries": 0}
    try:
        newest: Optional[float] = path.stat().st_mtime
    except OSError:
        # Absent or unreadable is a real reading, and reported AS one - the
        # same discipline _file_activity already takes with a named file.
        return reading
    entries = 0
    try:
        with os.scandir(path) as scan:
            for entry in scan:
                entries += 1
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    continue        # one unreadable child is not the answer
                if st.st_mtime > newest:
                    newest = st.st_mtime
    except OSError:
        pass                        # the directory's own mtime still stands
    reading.update({"exists": True, "mtime": newest, "entries": entries,
                    "since": max(0.0, time.time() - newest)})
    return reading


def activity_files(launch: Optional[Dict[str, Any]],
                   logs: List[RunLog],
                   run: Optional[Path] = None) -> List[Dict[str, Any]]:
    """The files whose mtimes bear on whether anything is still happening.

    Reuses read_launch's per-file machinery rather than growing a second one -
    `_file_activity` already answers exists/mtime/size/since and already
    reports absence AS a reading. The marker's files come first because the
    writer NAMED them; the newest glob-matched log is added after because a
    build run by hand drops no marker at all, and the hand-run path is the
    documented one.

    NEWEST LOG ONLY. An older log in the same directory belongs to a previous
    run, and reading a finished run's log as evidence about this one is the
    cross-attribution mistake this module refuses to make everywhere else.

    THE RUN DIRECTORY IS THE THIRD SOURCE, and it is the one that covers the
    hand-run case the other two miss entirely - see run_activity. It is kept
    as its OWN reading under its own role rather than folded into "the log",
    because "artifacts written 14s ago" and "the log wrote 14s ago" are
    different sentences and only one of them is true when there is no log.

    `run` is optional so a caller with no run in hand - and every existing
    one - gets exactly the two readings it always got.
    """
    out: List[Dict[str, Any]] = []
    seen: set = set()
    for entry in (launch or {}).get("files") or []:
        path = entry.get("path")
        if path in seen:
            continue
        seen.add(path)
        out.append(entry)
    for entry in logs[:1]:
        if entry.path in seen:
            continue
        seen.add(entry.path)
        out.append(_file_activity(entry.path, "log"))
    artifacts = run_activity(run)
    if artifacts is not None and artifacts["path"] not in seen:
        seen.add(artifacts["path"])
        out.append(artifacts)
    return out


def quiet_reading(updated: Optional[float], files: List[Dict[str, Any]],
                  now: Optional[float] = None,
                  after: float = _manifest.STALE_AFTER_SECONDS
                  ) -> Dict[str, Any]:
    """How long each source of evidence has been silent. Pure, and a READING.

    Deliberately carries no key named for a live/dead conclusion - the same
    line test_nothing_in_the_launch_reading_claims_the_run_is_alive_or_dead
    holds on the launch dict, held here for the same reason. `recent_write` is
    the one derived boolean and it is a fact about FILES: something was written
    inside the window. What that implies about a process is the reader's to
    decide, and the room prints the numbers so they can.
    """
    now = now if now is not None else time.time()
    manifest_quiet = max(0.0, now - updated) if updated else None
    reads: List[Dict[str, Any]] = []
    for entry in files or []:
        mtime = entry.get("mtime")
        reads.append({
            "role": entry.get("role"),
            "path": entry.get("path"),
            "exists": bool(entry.get("exists")),
            "quiet_for": max(0.0, now - mtime) if mtime else None,
        })
    quiets = [r["quiet_for"] for r in reads if r["quiet_for"] is not None]
    recent = [q for q in quiets if q <= after]
    if manifest_quiet is not None:
        quiets.append(manifest_quiet)
    return {
        "after_seconds": after,
        "manifest_quiet_for": manifest_quiet,
        "files": reads,
        "newest_activity_for": min(quiets) if quiets else None,
        # FILES ONLY. A fresh manifest already means "not stale"; this is the
        # evidence the manifest by itself cannot supply.
        "recent_write": bool(recent),
    }


def activity_state(manifest_state: str,
                   quiet: Optional[Dict[str, Any]]) -> str:
    """The run's state once the log has had a vote. `stalled` DOWNGRADES ONLY.

    Note which way this fails. Absent evidence leaves `stalled` exactly where
    the manifest put it - the word is withdrawn only when a file was positively
    seen to have been written inside the window. An empty set is not agreement,
    and "there is no log to read" must never quietly render as "still going".

    Decided here rather than in the browser for the reason manifest.as_dict
    already gives: two implementations of "is this run dead" would eventually
    disagree, and they would disagree on screen.
    """
    if manifest_state != "stalled" or not quiet:
        return manifest_state
    return "running" if quiet.get("recent_write") else "stalled"


#: The one run state that means "nothing to do here" - see scan_stage. Spelled
#: once so the stage rule and the count cannot drift from each other.
FINISHED = "finished"


def _run_state(run: Dict[str, Any]) -> str:
    """This run's one-word state, preferring the reading the scan settled on.

    `scan_stage` may have downgraded `stalled` to `running` after looking at
    file activity, and that verdict is the better one - so it wins over the raw
    manifest word when it is there. An uninstrumented run has no state at all,
    and "" is a real answer: it is not finished, so it stays on the stage.
    """
    if run.get("state"):
        return str(run["state"])
    found = run.get("manifest") or {}
    return str(found.get("state") or "")


def _wants_the_stage(run: Dict[str, Any]) -> bool:
    """Is there something to do about this run?

    Everything except a clean finish. Note which way this fails: an unknown or
    missing state stays ON the stage, because a run this code cannot classify
    is exactly the one a person should be looking at. Hiding it silently would
    be the same failure as the empty state that used to say "No runs here yet"
    over a stage with four archived builds in it.
    """
    return _run_state(run) != FINISHED


def run_started(run: Dict[str, Any]) -> float:
    """When this run began: manifest `started` first, directory mtime after."""
    found = run.get("manifest") or {}
    for value in (found.get("started"), run.get("mtime")):
        try:
            stamp = float(value)
        except (TypeError, ValueError):
            continue
        if stamp:
            return stamp
    return 0.0


def order_runs(runs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Newest run first. Name breaks ties, so the answer is the same twice.

    Determinism is not a nicety here: a viewer that shows a different run as
    "current" on two boxes reading the same directory is worse than one that
    shows the wrong one consistently, because nobody can reproduce it.
    """
    return sorted(runs, key=lambda r: (run_started(r), str(r.get("name"))),
                  reverse=True)


def builder_path(local: Any, root: Path, remote_prefix: str) -> str:
    """`local` as the BUILDER spells it. Unchanged when the stage is local.

    ONE FUNCTION, because the alternative is two: the same mapping is needed
    when a run is scanned and when an archived row is rendered, and a second
    implementation of a path rewrite is a second set of edge cases.

    For the person who reads a card and then goes to ssh at the box. The mount
    path is a fact about Theatre's filesystem; the builder-side path is the one
    that is true over there, and it is also the one that survives the mount
    moving.

    A path that does not sit under this stage comes back UNTOUCHED rather than
    force-fitted. Guessing would produce a confident path to nowhere, which is
    worse than the honest original - and it happens legitimately, for a row
    harvested before the stage root was changed.
    """
    text = str(local or "")
    if not remote_prefix or not text:
        return text
    try:
        rest = Path(text).relative_to(root)
    except ValueError:
        return text
    return str(Path(remote_prefix) / rest)


def local_path(builder: Any, root: Path, remote_prefix: str) -> str:
    """A path the BUILDER named, as this box can open it. The inverse.

    THE DIRECTION THAT WAS MISSING. `builder_path` exists so a person reading an
    archived row knows what to type once they ssh over. This one is for the
    opposite moment: the builder just announced an absolute path in its own
    filesystem, and Theatre has to find that directory under a mount.

    Not folded into one function with a flag. A flag would make every call site
    read as "translate, somehow", and the two directions fail differently - the
    forward one is cosmetic if it is wrong, this one silently fails to match a
    run and the launch link just never appears.

    A path that is not under the declared prefix comes back UNTOUCHED, which is
    also the whole behaviour for a local stage. Guessing is worse than the
    original: a forced path would be confidently wrong and would match nothing.
    """
    text = str(builder or "")
    if not remote_prefix or not text:
        return text
    try:
        rest = Path(text).relative_to(Path(remote_prefix))
    except ValueError:
        return text
    return str(root / rest)


# ── what the BUILD said about itself ────────────────────────────────────────
#
# The events JSONL's `started` object, which ms-moe-maker publishes the keys of
# under `started_fields`. It is the ONLY place a build states its own absolute
# run directory: the manifest deliberately carries none, because `Stage.artifact`
# is relative precisely so a run directory survives being moved, copied to
# another box, or read through a mount with a different prefix.
#
# So this is the single bridge between "the process stagehand launched" and "the
# directory on disk", and until now Theatre walked right past it -
# stagehand._last_error_event opens this exact file, keeps `event == "error"`,
# and drops the other nine fields of the launch's own account of itself.
#
# NAMED HERE RATHER THAN IMPORTED, on the same bargain manifest.py takes with
# msmoe-run.json and read_launch takes with the marker: sources.py is on the
# viewer's import graph and stagehand deliberately is not. A contract test
# asserts the names still agree with the writer's published card.
STARTED_EVENT = "started"

#: Bounded like the marker's own event read, and for the same reason: a build
#: that ran for nine hours has a large events file and a dashboard must never be
#: the reason the box is busy. The `started` object is the FIRST line of that
#: file, so a small head read finds it - unlike the last error, which needs the
#: tail. Generous enough that a fat `resolved` block cannot push it out of reach.
STARTED_MAX_BYTES = 256 * 1024


def read_started(path: Any) -> Optional[Dict[str, Any]]:
    """The build's own `started` object, or None. Never raises.

    None means THE BUILD SAID NOTHING STRUCTURED - an older ms-moe-maker, a
    build run by hand without `--json`, a file that has not been written yet.
    That is a real answer and the caller renders it as one; it must never become
    an empty dict a reader mistakes for "started, with nothing in it".

    READ FROM THE HEAD, first match wins. `started` is emitted before anything
    else, so the front of the file is where it is - and taking the FIRST rather
    than the last is deliberate: stagehand stamps a fresh events file per
    launch, so a second `started` in one file would mean somebody appended two
    runs, and the earlier one is the one this file's marker describes.

    Lines that do not parse are skipped rather than repaired, because the tail
    of a bounded read is a fragment and a half-read event is not an event.
    """
    if not path:
        return None
    try:
        with open(str(path), "rb") as fh:
            blob = fh.read(STARTED_MAX_BYTES).decode("utf-8", "replace")
    except OSError:
        return None
    for line in blob.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("event") == STARTED_EVENT:
            return row
    return None


def scan_stage(name: str, root: Path, log_globs: List[str],
               run_globs: Sequence[str], limit: int,
               run_depth: int = RUN_DEPTH_DEFAULT,
               remote_prefix: str = "") -> Dict[str, Any]:
    logs: List[RunLog] = []
    runs: List[Dict[str, Any]] = []
    if root.is_dir():
        seen: set[Path] = set()
        for pattern in log_globs:
            for p in root.glob(pattern):
                if p.is_file() and p not in seen:
                    seen.add(p)
                    logs.append(parse_run_log(p, limit))
        # WHICH DIRECTORIES ARE RUNS is resolve_runs' question, not this
        # one's - the same answer the eval endpoint gets, deduped there. The
        # duplicate-scan guard that used to live here moved with the loop; see
        # resolve_runs for why overlapping globs are the normal case.
        for p in resolve_runs(root, run_globs, max_depth=run_depth):
            found = scan_run(p)
            # THE FRAME TRAVELS WITH THE RUN, because harvest is downstream of
            # this and has no access to the config. Stamped even when empty so
            # the key always exists: a consumer that has to tell "local" from
            # "this scanner is too old to say" is back to guessing.
            found["remote_prefix"] = remote_prefix
            # AND THE TRANSLATED PATH, computed HERE because this is the only
            # place both halves exist at once. Deriving it later from the row
            # is impossible: the row keeps the prefix but not the stage root it
            # was stripped against, and today's config is a statement about now
            # rather than about the run.
            found["builder_path"] = builder_path(found.get("path"), root,
                                                 remote_prefix)
            runs.append(found)
    logs.sort(key=lambda r: r.mtime, reverse=True)
    # WHAT WAS LAUNCHED FROM HERE, if anything. At STAGE level because that is
    # where stagehand drops the marker - beside the log and events files, in the
    # cwd it was handed - and because a launch that died on arrival produces no
    # run directory at all, so a run-level seat would be empty in exactly the
    # case that matters most. Same lenient read as the manifest: a missing,
    # unreadable or ancient marker degrades to a reported error and nothing else
    # on this dict changes. It is a record of a START and never a second opinion
    # about progress; the manifest keeps that job.
    launch, launch_error = read_launch(root)

    # ONE RUN PER STAGE ON SCREEN - the current one, or the most recent - and
    # EVERY run still in the payload. A gauntlet leaves a run directory per
    # size and the room rendered all of them, every stage of every one, which
    # is the opposite of the brief: you glance at it, you learn where the run
    # is, you look away.
    #
    # Ordered HERE rather than in the browser so /api/state and the page agree
    # about which run is current. `earlier` is a COUNT and not a deletion: a
    # viewer that silently drops data is the exact failure this file keeps
    # fixing, and the count is also the seam a Previous Shows section lands on.
    runs = order_runs(runs)

    # The log and events files sit at STAGE level - beside the marker, in the
    # cwd the build was launched in - so this evidence belongs to the run that
    # is happening NOW and is attached to that one alone. Hanging a live log's
    # mtime on a run that finished last Tuesday would be inventing a reading.
    if runs:
        current = runs[0]
        found = current.get("manifest")
        if found is not None:
            quiet = quiet_reading(found.get("updated"),
                                  activity_files(launch, logs,
                                                 Path(current["path"])))
            current["quiet"] = quiet
            current["state"] = activity_state(found.get("state"), quiet)
            # Which readings decided the word above. Same honesty as `source`
            # one field over: a person should always be able to tell how
            # confident the display is entitled to be.
            current["state_source"] = (
                "manifest" if current["state"] == found.get("state")
                else "manifest + file activity")

    # ── WHAT BELONGS ON THE STAGE, decided here and not in the browser ──
    #
    # A stage holds the run there is something to DO about. You glance at it
    # and learn one of three things: something is cooking, something stopped
    # badly, or nothing is happening - and the third of those is not the same
    # sentence as "nothing was ever built here" - which is what it used to say.
    #
    # `finished` is the ONLY state that comes off the stage. Everything else is
    # unresolved business and stays: `running` and `idle` are cooking,
    # `stalled` is the one you most want to see, and `failed` is the whole
    # reason a person looks. A clean finish is not news - it is history, and
    # history has a tab.
    #
    # DECIDED SERVER-SIDE for the reason activity_state already gives one
    # function up: two implementations of "is this run over" would eventually
    # disagree, and they would disagree on screen. The browser gets a verdict,
    # not the ingredients for a second one.
    # ── WHICH RUN IS THE ONE STAGEHAND LAUNCHED ────────────────────────────
    #
    # Until the build's `started` event was read, nothing connected the two.
    # The marker sits at STAGE level - stagehand drops it in the cwd it was
    # handed, beside the log - and runs are discovered independently by walking
    # the directory. So "the thing I started" and "the thing on disk" were two
    # unrelated facts on one card, and the stage picked its run by ORDER: newest
    # wins. That is a heuristic, and it is wrong in the case a person most cares
    # about - a hand-run build finishing while a launched one is still going
    # makes the hand-run one newest and puts it on the stage.
    #
    # With a run directory the link is an IDENTITY instead. Translated through
    # the stage's mount prefix, because the builder named a path in its own
    # filesystem and this box may see it somewhere else entirely.
    launched_at = ""
    if isinstance(launch, dict):
        said = launch.get("run") or {}
        launched_at = local_path(said.get("run_dir"), root, remote_prefix)
        if launched_at:
            launch["run_path"] = launched_at
    launched = None
    for run in runs:
        run["launched"] = bool(launched_at) and \
            str(run.get("path") or "") == launched_at
        if run["launched"]:
            launched = run

    # THE LAUNCHED RUN WINS THE STAGE, unless it is over. `finished` is still
    # the only state that comes off the stage, so a launch whose run completed
    # hands the stage back rather than pinning a done card there forever - and
    # with nothing launched, or a launch Theatre cannot locate, this is exactly
    # the previous newest-wins rule. Strictly better-informed, never different
    # when there is no better information.
    if launched is not None and _wants_the_stage(launched):
        on_stage = launched
    else:
        on_stage = runs[0] if runs and _wants_the_stage(runs[0]) else None
    return {"name": name, "path": str(root), "exists": root.is_dir(),
            "logs": [asdict(r) for r in logs], "runs": runs,
            # Which of `runs` is newest, and how many are behind it. Kept as
            # an honest inventory: harvest reads every one of these, so a
            # viewer-facing decision must never shorten the list.
            "current": runs[0]["path"] if runs else None,
            "earlier": max(0, len(runs) - 1),
            # The one the stage draws, or None. A separate key rather than a
            # redefinition of `current`, because quietly changing what an
            # existing field means is how a reader ends up confidently wrong.
            "on_stage": on_stage,
            # How many runs THIS SCAN found that are simply done. The number a
            # person needs to tell "nothing cooking" from "nothing here".
            "finished_here": sum(1 for r in runs
                                 if _run_state(r) == FINISHED),
            "launch": launch, "launch_error": launch_error}
