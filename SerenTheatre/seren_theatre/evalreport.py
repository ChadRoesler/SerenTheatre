"""The two result documents a finished run leaves behind. Reading half.

Same arrangement as manifest.py and evalrecord.py, for the same reason:
importing ms-moe-maker would make a viewer depend on a training pipeline, and
Theatre's `requires` is empty on purpose. Both ends implement the format
independently and tests/test_results_contract.py pins the constants against
the real writer.

READ-ONLY, ABSOLUTELY. There is no writer here and there must never be one.

────────────────────────────────────────────────────────────────────────────
WHY THESE ARE FILES AND NOT MANIFEST FIELDS

`eval` is a separate command from `build`, deliberately, so that a model is
never graded as part of being built. That means the eval does not own the
manifest - the builder does, and rewrites it on stage transitions. Two writers
on one small file is how a manifest gets truncated at 3am.

So each result drops its own document in the run directory and a reader finds
it by scanning, exactly the way the GGUF and the smoke-pass marker are found.
The upside is bigger than the avoided bug: an eval run from ANOTHER BOX, days
later, against the same rung, shows up in this viewer with nothing wired.
────────────────────────────────────────────────────────────────────────────

THE ONE THING THIS FILE MUST NEVER DO

Show a stale eval as though it described the model currently on disk.

A rung gets rebuilt. The eval report from before the rebuild sits there, still
valid JSON, still full of confident numbers about a model that no longer
exists. Rendering it beside the new build's playbill is not a small cosmetic
error - it is the C# 0/10 failure in a new costume: nothing looks wrong, and
the reader draws a conclusion about a thing that was never measured.

Hence `provenance`. The writer stamps the build_id of the model it graded; the
manifest carries the build_id of the model on disk. Three answers, and the
third is not a failure:

    matches     same build_id. This eval measured this build.
    stale       different build_id. Real numbers about an EARLIER build.
    unknown     one side did not say. Not evidence of either.

`unknown` must never render as `matches`. A viewer that cannot tell says so.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

# Pinned against ms_moe_maker by tests/test_results_contract.py.
EVAL_REPORT_NAME = "eval_report.json"
GATE_REPORT_NAME = "gate_experts.json"
EVAL_REPORT_SCHEMA = 1

# Provenance verdicts. Strings rather than a bool because there are three
# answers and a bool can only carry two - which is exactly how "we could not
# tell" gets silently promoted to "yes".
MATCHES = "matches"
STALE = "stale"
UNKNOWN = "unknown"


class UnreadableResult(Exception):
    """The document is there and cannot be trusted. Surfaced, never swallowed."""


def _num(value: Any) -> Optional[float]:
    """A float, or None. Never a zero standing in for a missing measurement."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def provenance(report_build_id: str, manifest_build_id: str) -> str:
    """Did this result measure the build that is on disk now?

    Empty on EITHER side is `unknown`, and that asymmetry is deliberate: an
    older writer that stamps nothing, and a manifest from before build_id
    existed, are both "cannot tell" - and cannot-tell is its own answer.
    """
    if not report_build_id or not manifest_build_id:
        return UNKNOWN
    return MATCHES if report_build_id == manifest_build_id else STALE


def _load(path: Path) -> Optional[Dict[str, Any]]:
    """Parse one small JSON document, or None if it is simply not there.

    Absent is NOT an error - most runs have never been evaluated, and a viewer
    that reports "missing eval report" on every one of them trains its reader
    to ignore it. Present-and-broken IS an error, and raises.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise UnreadableResult(f"{path.name}: {exc}") from exc
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise UnreadableResult(
            f"{path.name}: not valid JSON ({exc}). The file is there and "
            f"cannot be read, which is a different thing from no eval.") from exc
    if not isinstance(data, dict):
        raise UnreadableResult(f"{path.name}: expected an object at the top level")
    return data


# ── the eval report ──────────────────────────────────────────────────────────

def read_eval(run_dir: Path, manifest_build_id: str = "") -> Optional[Dict[str, Any]]:
    """Read a rung's eval report off disk and project it. None if never run."""
    path = Path(run_dir) / EVAL_REPORT_NAME
    data = _load(path)
    if data is None:
        return None
    return project_eval(data, manifest_build_id, path=str(path))


def project_eval(data: Dict[str, Any], manifest_build_id: str = "",
                 *, path: str = "") -> Dict[str, Any]:
    """Turn a parsed eval document into what the viewer draws.

    SPLIT FROM READING ON PURPOSE, and the reason is the archive. Harvest keeps
    the ORIGINAL document rather than this projection, because a projection is
    lossy by definition - this one drops `avg_length` and `capped_generations`
    today, and the whole promise of keeping documents is that a field this
    version ignores is still there for a version that does not.

    So the stored document is re-projected at READ time, which means a viewer
    upgrade improves rows that were archived years earlier. A projection frozen
    into the database at harvest could only ever get staler.

    DERIVED FIELDS ARE DECIDED HERE, not in the browser. Two implementations of
    "did this pass" eventually disagree on screen, which is the specific way a
    dashboard starts lying - the same reason evalrecord.as_dict computes
    `score` server-side.
    """
    version = data.get("schema_version")
    if isinstance(version, int) and version > EVAL_REPORT_SCHEMA:
        raise UnreadableResult(
            f"{EVAL_REPORT_NAME}: schema_version {version} is newer than this "
            f"viewer understands ({EVAL_REPORT_SCHEMA}). Upgrade seren-theatre "
            f"rather than showing you a guess.")

    build_id = str(data.get("build_id") or "")
    generated = _num(data.get("generated")) or 0.0

    # The quality table. `moe/<name>` rows are the SAME expert measured inside
    # the stitched model, and they belong beside their own row rather than in
    # alphabetical exile between two other experts - that pairing is the whole
    # comparison. So they are nested, exactly as the CLI prints them.
    stages = data.get("stages")
    stages = stages if isinstance(stages, dict) else {}
    quality: List[Dict[str, Any]] = []
    for name in sorted(k for k in stages if not str(k).startswith("moe/")):
        row = _quality_row(name, stages.get(name) or {})
        moe = stages.get(f"moe/{name}")
        row["in_moe"] = _quality_row(f"moe/{name}", moe) if isinstance(moe, dict) else None
        quality.append(row)
    # A `moe/x` with no bare `x` would otherwise vanish. Rare, and vanishing is
    # not an acceptable way to handle rare.
    paired = {r["name"] for r in quality}
    for name in sorted(k for k in stages if str(k).startswith("moe/")):
        if str(name)[len("moe/"):] not in paired:
            row = _quality_row(name, stages.get(name) or {})
            row["in_moe"] = None
            quality.append(row)

    return {
        "path": path,
        "schema_version": version if isinstance(version, int) else None,
        "build_id": build_id,
        # WHEN, NOT HOW LONG AGO. An age computed here would change on every
        # poll and so would the structural signature that decides whether to
        # re-render at all - the page would go quietly back to rebuilding
        # itself every five seconds and nothing would say so. The viewer turns
        # this into "3h ago" through the same tick mechanism every other
        # elapsed string on the page uses.
        "generated": generated,
        "provenance": provenance(build_id, manifest_build_id),
        "ok": bool(data.get("ok")),
        "message": str(data.get("message") or ""),
        "dead_experts": _strlist(data.get("dead_experts")),
        "undiscriminating": _strlist(data.get("undiscriminating")),
        "caveats": _strlist(data.get("caveats")),
        "unmeasured": _strlist(data.get("unmeasured")),
        "quality": quality,
        "routing": _routing(data.get("routing")),
        # THE ORIGINAL, CARRIED ALONG. The archive stores this rather than the
        # projection above - see project_eval. Nothing renders it; it exists so
        # that what gets preserved is the evidence and not one reading of it.
        "document": data,
    }


def _strlist(value: Any) -> List[str]:
    return [str(v) for v in value] if isinstance(value, list) else []


def _quality_row(name: str, raw: Dict[str, Any]) -> Dict[str, Any]:
    """One row of the generation-quality table, with its denominator intact.

    `scored` and `attempted` travel together and are never averaged into a
    single number. A mean over 3 rows and a mean over 20 are not the same
    claim, and the CLI learned that the hard way: the count used to live only
    in free text that the routing verdict overwrote.
    """
    scored = raw.get("scored_samples")
    attempted = raw.get("attempted_samples")
    scored = int(scored) if isinstance(scored, int) else 0
    attempted = int(attempted) if isinstance(attempted, int) else 0
    reasoned = _num(raw.get("reasoned"))
    return {
        "name": str(name),
        "domain": str(raw.get("domain") or ""),
        "exact_match": _num(raw.get("exact_match")),
        "rouge1": _num(raw.get("rouge1")),
        "bleu": _num(raw.get("bleu")),
        "scored": scored,
        "attempted": attempted,
        # -1 is the writer's "never asked", and it must not render as 0.00 -
        # a run that did not measure reasoning did not score zero on it.
        "reasoned": reasoned if (reasoned is not None and reasoned >= 0) else None,
        "status": str(raw.get("status") or ""),
        "note": str(raw.get("note") or ""),
        # A SAMPLE TOO THIN TO COMPARE WITH A FULL ONE. Decided here so the
        # browser cannot pick a different threshold than the CLI does.
        "thin": bool(attempted) and (scored < 5 or scored * 2 < attempted),
    }


def _routing(raw: Any) -> Dict[str, Any]:
    """The enrichment table and its companions. The headline claim.

    ENRICHMENT IS SUPPRESSED WHEN IT IS NOISE, here rather than in the browser.
    An abandoned expert's enrichment is one small number divided by another,
    and printing `2.15x` next to a 0.001 share invites a reader to quote the
    best-looking figure in the table. The writer already decided this
    (`enrichment_reliable`); the viewer's job is to honour it, not re-litigate.
    """
    if not isinstance(raw, dict) or not raw:
        return {}
    experts = raw.get("experts")
    experts = experts if isinstance(experts, dict) else {}
    rows = []
    for name in sorted(experts):
        e = experts.get(name) or {}
        if not isinstance(e, dict):
            continue
        reliable = e.get("enrichment_reliable", True)
        rows.append({
            "name": str(name),
            "own_share": _num(e.get("own_share")),
            "others_share": _num(e.get("others_share")),
            "enrichment": _num(e.get("enrichment")) if reliable else None,
            "enrichment_reliable": bool(reliable),
            "top_competitor": str(e.get("top_competitor") or ""),
            "top_competitor_share": _num(e.get("top_competitor_share")),
            "own_is_column_max": bool(e.get("own_is_column_max")),
            "outranked": bool(e.get("outranked")),
        })

    k = raw.get("top_k")
    k = int(k) if isinstance(k, int) and k > 0 else None
    conf = _num(raw.get("mean_gate_confidence"))
    # SATURATION IS RELATIVE TO 1/K, NOT TO 1.0. `conf` is the mean of the K
    # top softmax probabilities and those sum to at most 1, so it can never
    # exceed 1/K - a fixed 0.95 threshold is unreachable for any K >= 2 and
    # silently switches off the one diagnosis that separates saturated-and-
    # blind from balanced-and-blind. The CLI carries the same arithmetic and
    # the same comment; both are wrong or both are right, never one each.
    ceiling = (1.0 / k) if k else None
    js = _num(raw.get("mean_js_bits"))
    return {
        "status": str(raw.get("status") or ""),
        "reason": str(raw.get("reason") or ""),
        "experts": rows,
        "excluded": _strlist(raw.get("excluded")),
        "named_experts": raw.get("named_experts") if isinstance(
            raw.get("named_experts"), int) else None,
        "own_is_max_count": raw.get("own_is_max_count") if isinstance(
            raw.get("own_is_max_count"), int) else None,
        "mean_enrichment": _num(raw.get("mean_enrichment")),
        "p_value": _num(raw.get("p_value")),
        "p_value_event": str(raw.get("p_value_event") or ""),
        "mean_js_bits": js,
        # A verdict the writer's own printer states in words. Stated once,
        # server-side, so the two surfaces cannot disagree about the threshold.
        "input_blind": (js is not None and js < 1e-3),
        "moe_layers": raw.get("moe_layers") if isinstance(
            raw.get("moe_layers"), int) else None,
        "top_k": k,
        "mean_gate_confidence": conf,
        "uniform_confidence": _num(raw.get("uniform_confidence")),
        "confidence_ceiling": ceiling,
        "saturated": bool(conf is not None and ceiling and conf > 0.95 * ceiling),
        "think_segments": raw.get("think_segments") if isinstance(
            raw.get("think_segments"), dict) else {},
        "think_segment_errors": raw.get("think_segment_errors") if isinstance(
            raw.get("think_segment_errors"), dict) else {},
    }


# ── the expert gate ──────────────────────────────────────────────────────────

def read_gate(run_dir: Path) -> Optional[Dict[str, Any]]:
    """The pre-stitch expert comparison. None if the gate never ran.

    NO PROVENANCE FIELD, and the absence is the point: the gate runs INSIDE the
    build, before the stitch, so its report cannot be left over from an earlier
    one the way an eval can. It is written by the same process that wrote the
    manifest beside it.
    """
    data = _load(Path(run_dir) / GATE_REPORT_NAME)
    if data is None:
        return None
    return project_gate(data, path=str(Path(run_dir) / GATE_REPORT_NAME))


def project_gate(data: Dict[str, Any], *, path: str = "") -> Dict[str, Any]:
    """The gate document, as the viewer draws it. Split for the same reason."""
    return {
        "path": path,
        "status": str(data.get("status") or ""),
        "findings": _strlist(data.get("findings")),
        # UNMEASURED IS NOT A FINDING AND NOT A PASS. Kept in its own list all
        # the way to the screen for the same reason `unmeasurable` is kept out
        # of the eval denominator: a check that could not run must never render
        # as a check that came back clean.
        "unmeasured": _strlist(data.get("unmeasured")),
        "divergence": data.get("divergence") if isinstance(
            data.get("divergence"), dict) else {},
        "pairwise": data.get("pairwise") if isinstance(
            data.get("pairwise"), dict) else {},
        "cross_loss": data.get("cross_loss") if isinstance(
            data.get("cross_loss"), dict) else {},
        "config_audit": data.get("config_audit") if isinstance(
            data.get("config_audit"), dict) else {},
        "document": data,
    }
