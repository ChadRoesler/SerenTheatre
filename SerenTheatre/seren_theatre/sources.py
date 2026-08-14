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
    out: Dict[str, Any] = {"name": root.name, "path": str(root),
                           "specialists": [], "skeleton": False,
                           "final": False, "gguf": None, "smoketested": False,
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
            # CONVERTED IS NOT PROVEN. The pipeline learned this the hard way:
            # a GGUF that converted and then hung its smoke test would be
            # treated as finished forever. Presence of the log is the proof.
            out["smoketested"] = Path(str(entry) + ".smoketest.txt").is_file()
    out["specialists"].sort()
    return out


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
    return {"name": name, "path": str(root), "exists": root.is_dir(),
            "logs": [asdict(r) for r in logs], "rungs": rungs}
