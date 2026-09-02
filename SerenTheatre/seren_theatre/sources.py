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
from fnmatch import fnmatch

from . import manifest as _manifest
from pathlib import Path
from typing import Any, Dict, List, Optional


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
            # "[cfg] rung: size=0.5B ... target_steps=150" -> flat key/value.
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
# artifact names, which is a real coupling: rename `qwen_coder_*` during the
# ms-moe-maker decomposition and this reports "no specialists" for a perfectly
# healthy run, confidently. That is the worst way for a dashboard to be wrong,
# and it is exactly why the manifest exists.
#
# But scraping must NEVER be deleted, because "a stage is a directory" is the
# whole reason Theatre requires nothing. A folder somebody redirected a log
# into, with no pipeline cooperating at all, is a first-class thing to watch.
# So: believe the manifest when it is there, read the disk when it is not, and
# say which one you did.

_STAGES = (
    ("specialists", "qwen_coder_*", "config.json"),
    ("skeleton", "fraunkenstein_moe_untrained", "config.json"),
    ("final", "fraunkenstein_agent_final", "config.json"),
)

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
# NOT inside a rung. Stagehand does not know the rung layout and is not going to
# learn it, so this is read at stage level, which is also where it is most
# useful: a launch that died on arrival never creates a rung at all, so the rung
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
    for role, key in (("log", "log_file"), ("events", "events_file")):
        value = payload.get(key)
        if isinstance(value, str) and value:
            files.append(_file_activity(value, role))
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
        "last_activity": last,
        "since_activity": (max(0.0, time.time() - last)
                           if last is not None else None),
    }, None



def looks_like_rung(root: Path) -> bool:
    """Is this directory a RUN, or just something the glob happened to catch?

    The globs are patterns, and a pattern cannot tell a rung from its
    neighbours: `dryrun_*` matches `dryrun_0.5B` (a run) AND `dryrun_data`
    (the shared corpus root, which is not a run and never will be). Theatre
    rendered the corpus as an empty rung card reading "Nothing built here yet",
    which is a true sentence about a directory that was never going to have
    anything built in it - so it reads as a failure and is only clutter.

    Note the asymmetry that hid it: the non-dryrun globs are `*_agent_*`, which
    `fraunkenstein_data` escapes. So this only ever appeared in DRYRUN mode -
    the mode you use for every shakedown and never for a real rung. The worst
    possible distribution for noticing.

    So: ask what the directory IS, not what its name looks like. A rung has a
    manifest (an instrumented run, even one that has produced nothing yet), or
    it has rung-shaped artifacts (an uninstrumented one). Anything else is a
    directory that shares a prefix.
    """
    try:
        if (root / _manifest.MANIFEST_NAME).is_file():
            return True
    except OSError:
        return False
    try:
        for entry in root.iterdir():
            n = entry.name
            if n.endswith(".gguf"):
                return True
            for _, pattern, marker in _STAGES:
                if fnmatch(n, pattern) and (entry / marker).is_file():
                    return True
    except OSError:
        return False
    return False


def scan_rung(root: Path) -> Dict[str, Any]:
    """What EXISTS for one rung. Presence, not promises.

    The scraping half deliberately mirrors what the pipeline's own _done()
    checks, because the dashboard must agree with the thing it is describing.
    A rung that Theatre calls finished and the pipeline re-runs is worse than
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
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return out

    for entry in entries:
        n = entry.name
        if n.startswith("qwen_coder_") and (entry / "config.json").is_file():
            out["specialists"].append(n[len("qwen_coder_"):])
        elif n == "fraunkenstein_moe_untrained" and (entry / "config.json").is_file():
            out["skeleton"] = True
            try:
                cfg = json.loads((entry / "config.json").read_text())
                out["experts"] = cfg.get("expert_names")
                out["dense_layers"] = cfg.get("mlp_only_layers")
            except (OSError, ValueError):
                pass
        elif n == "fraunkenstein_agent_final" and (entry / "config.json").is_file():
            out["final"] = True
        elif n.endswith(".gguf"):
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


def activity_files(launch: Optional[Dict[str, Any]],
                   logs: List[RunLog]) -> List[Dict[str, Any]]:
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


def run_started(rung: Dict[str, Any]) -> float:
    """When this run began: manifest `started` first, directory mtime after."""
    found = rung.get("manifest") or {}
    for value in (found.get("started"), rung.get("mtime")):
        try:
            stamp = float(value)
        except (TypeError, ValueError):
            continue
        if stamp:
            return stamp
    return 0.0


def order_rungs(rungs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Newest run first. Name breaks ties, so the answer is the same twice.

    Determinism is not a nicety here: a viewer that shows a different run as
    "current" on two boxes reading the same directory is worse than one that
    shows the wrong one consistently, because nobody can reproduce it.
    """
    return sorted(rungs, key=lambda r: (run_started(r), str(r.get("name"))),
                  reverse=True)


def scan_stage(name: str, root: Path, log_globs: List[str],
               rung_globs: List[str], limit: int) -> Dict[str, Any]:
    logs: List[RunLog] = []
    rungs: List[Dict[str, Any]] = []
    if root.is_dir():
        seen: set[Path] = set()
        for pattern in log_globs:
            for p in root.glob(pattern):
                if p.is_file() and p not in seen:
                    seen.add(p)
                    logs.append(parse_run_log(p, limit))
        for pattern in rung_globs:
            for p in sorted(root.glob(pattern)):
                # is_dir AND looks_like_rung. The glob proposes; the directory
                # decides. See looks_like_rung.
                if p.is_dir() and looks_like_rung(p):
                    rungs.append(scan_rung(p))
    logs.sort(key=lambda r: r.mtime, reverse=True)
    # WHAT WAS LAUNCHED FROM HERE, if anything. At STAGE level because that is
    # where stagehand drops the marker - beside the log and events files, in the
    # cwd it was handed - and because a launch that died on arrival produces no
    # rung directory at all, so a rung-level seat would be empty in exactly the
    # case that matters most. Same lenient read as the manifest: a missing,
    # unreadable or ancient marker degrades to a reported error and nothing else
    # on this dict changes. It is a record of a START and never a second opinion
    # about progress; the manifest keeps that job.
    launch, launch_error = read_launch(root)

    # ONE RUN PER STAGE ON SCREEN - the current one, or the most recent - and
    # EVERY run still in the payload. A gauntlet leaves a rung directory per
    # size and the room rendered all of them, every stage of every one, which
    # is the opposite of the brief: you glance at it, you learn where the run
    # is, you look away.
    #
    # Ordered HERE rather than in the browser so /api/state and the page agree
    # about which run is current. `earlier` is a COUNT and not a deletion: a
    # viewer that silently drops data is the exact failure this file keeps
    # fixing, and the count is also the seam a Previous Shows section lands on.
    rungs = order_rungs(rungs)

    # The log and events files sit at STAGE level - beside the marker, in the
    # cwd the build was launched in - so this evidence belongs to the run that
    # is happening NOW and is attached to that one alone. Hanging a live log's
    # mtime on a rung that finished last Tuesday would be inventing a reading.
    if rungs:
        current = rungs[0]
        found = current.get("manifest")
        if found is not None:
            quiet = quiet_reading(found.get("updated"),
                                  activity_files(launch, logs))
            current["quiet"] = quiet
            current["state"] = activity_state(found.get("state"), quiet)
            # Which readings decided the word above. Same honesty as `source`
            # one field over: a person should always be able to tell how
            # confident the display is entitled to be.
            current["state_source"] = (
                "manifest" if current["state"] == found.get("state")
                else "manifest + file activity")

    return {"name": name, "path": str(root), "exists": root.is_dir(),
            "logs": [asdict(r) for r in logs], "rungs": rungs,
            # Which of `rungs` is the one on stage, and how many are behind it.
            "current": rungs[0]["path"] if rungs else None,
            "earlier": max(0, len(rungs) - 1),
            "launch": launch, "launch_error": launch_error}
