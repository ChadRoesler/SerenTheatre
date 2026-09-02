"""Two runs, side by side. The thing that turns a history into an instrument.

A list of past runs is a filing cabinet. What makes the archive worth keeping
is the question you actually have:

    I changed one knob. Did it do anything?

────────────────────────────────────────────────────────────────────────────
THE HONESTY PROBLEM, WHICH IS THE WHOLE DESIGN.

Put two runs beside each other and the eye does the rest: `router.epochs 3 → 8`
in one column, `enrichment 1.02x → 2.14x` in the other, and a conclusion forms
before anyone decides to draw one. That conclusion is only warranted when those
were the ONLY things that differed.

So this refuses to render a comparison without saying how many inputs moved:

    none      the configs are identical. If the numbers differ anyway, that is
              a finding about NON-DETERMINISM, not about a knob - and it is
              worth more attention than any ordinary diff.
    single    exactly one input changed. The one case where pointing at it is
              reasonable - and still evidence rather than proof, because a
              seed, a corpus draw and a teacher's sampling all move underneath.
    multiple  several inputs changed. NOTHING HERE TELLS YOU WHICH ONE moved
              the number, and a diff that let you believe otherwise would be a
              confidently-wrong claim generator with a nice table.

INPUTS AND CONSEQUENCES ARE SEPARATED, and that is what makes it readable at
all. Change `router.epochs` and a dozen derived values follow it; a flat diff
of twelve rows looks like twelve decisions. The writer's own glossary travels
in the manifest beside `resolved`, and every entry says whether the value was
derived - so the split costs nothing and comes from the package that computes
them rather than from a guess made here.

That is the third job the knob glossary has done: tooltips, README agreement,
and now telling a change apart from its own consequences.
────────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# Attribution verdicts. Strings rather than a count because the COUNT is not
# the point - what a reader may conclude is - and three words carry that where
# `4` does not.
NONE = "none"
SINGLE = "single"
MULTIPLE = "multiple"

# Fields whose difference makes two runs different EXPERIMENTS rather than two
# points on one. Comparing a 0.5B dry run against a 7B is not a comparison; it
# is two facts printed near each other.
IDENTITY_FIELDS = ("size", "base", "expert_names")


def _resolved(row: Dict[str, Any]) -> Dict[str, Any]:
    manifest = row.get("manifest") or {}
    got = manifest.get("resolved")
    return got if isinstance(got, dict) else {}


def _knobs(row: Dict[str, Any]) -> Dict[str, Any]:
    manifest = row.get("manifest") or {}
    got = manifest.get("knobs")
    return got if isinstance(got, dict) else {}


def _is_derived(knobs: Dict[str, Any], field: str) -> bool:
    """Did the pipeline COMPUTE this, or did somebody choose it?

    Read off the glossary the writer stamped into the manifest, so the answer
    comes from the code that does the computing. An older manifest carries no
    glossary at all, and then everything reads as an input - which overstates
    how many decisions were made, and overstating is the safe direction: it
    weakens the attribution rather than inventing one.
    """
    entry = knobs.get(field)
    if isinstance(entry, dict):
        return bool(entry.get("derived_from"))
    return False


def _knob_of(knobs: Dict[str, Any], field: str) -> Optional[Dict[str, Any]]:
    entry = knobs.get(field)
    if not isinstance(entry, dict):
        return None
    summary = str(entry.get("summary") or "").strip()
    if not summary:
        return None
    return {"summary": summary,
            "derived_from": entry.get("derived_from") or None}


def config_diff(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    """What differs between two resolved configs, split by who decided it."""
    ra, rb = _resolved(a), _resolved(b)
    knobs = _knobs(b) or _knobs(a)

    inputs: List[Dict[str, Any]] = []
    consequences: List[Dict[str, Any]] = []
    only_a, only_b = [], []
    same = 0

    for field in sorted(set(ra) | set(rb)):
        if field not in ra:
            only_b.append(field)
            continue
        if field not in rb:
            only_a.append(field)
            continue
        if ra[field] == rb[field]:
            same += 1
            continue
        row = {"field": field, "a": ra[field], "b": rb[field],
               "knob": _knob_of(knobs, field)}
        (consequences if _is_derived(knobs, field) else inputs).append(row)

    return {"inputs": inputs, "consequences": consequences,
            "only_in_a": only_a, "only_in_b": only_b, "unchanged": same,
            # A manifest from before the glossary existed cannot tell an input
            # from a consequence, and saying so is better than a split the
            # reader would take at face value.
            "has_glossary": bool(knobs)}


def attribution(diff: Dict[str, Any]) -> str:
    """How much a reader is entitled to conclude. Counts INPUTS only."""
    n = len(diff.get("inputs") or [])
    if n == 0:
        return NONE
    return SINGLE if n == 1 else MULTIPLE


def comparability(a: Dict[str, Any], b: Dict[str, Any]) -> List[str]:
    """Reasons these two are not two points on one experiment.

    Reported rather than refused: somebody may genuinely want to look at a 0.5B
    dry run beside the 7B it was rehearsing for, and blocking that would be
    this tool deciding what its user is allowed to wonder about. What it may
    not do is let the comparison LOOK like a controlled one.
    """
    ra, rb = _resolved(a), _resolved(b)
    out = []
    for field in IDENTITY_FIELDS:
        if field in ra and field in rb and ra[field] != rb[field]:
            out.append(f"{field}: {ra[field]!r} vs {rb[field]!r}")
    return out


def _grading(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The newest grading's projected view, or None.

    NEWEST, not best, and not merged. A run evaluated twice has two honest
    answers and picking the flattering one would be the single worst thing this
    module could do; picking the latest is at least a rule.
    """
    for grading in row.get("gradings") or []:
        view = grading.get("view")
        if isinstance(view, dict):
            return view
    return None


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    return float(value) if isinstance(value, (int, float)) else None


def _delta(x: Optional[float], y: Optional[float]) -> Optional[float]:
    """b minus a, or None if either side never measured it.

    NEVER ZERO FOR A MISSING SIDE. A run that did not measure reasoning did not
    score the same as one that did, and a 0.00 in a delta column is the exact
    shape of a claim nobody made.
    """
    return None if (x is None or y is None) else y - x


def outcome_diff(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    """What the numbers did, side by side. Deltas only where both measured."""
    ga, gb = _grading(a), _grading(b)
    out: Dict[str, Any] = {"a_evaluated": ga is not None,
                           "b_evaluated": gb is not None,
                           "routing": [], "quality": [], "headline": []}
    if ga is None or gb is None:
        return out

    ra = {e["name"]: e for e in (ga.get("routing") or {}).get("experts") or []}
    rb = {e["name"]: e for e in (gb.get("routing") or {}).get("experts") or []}
    for name in sorted(set(ra) | set(rb)):
        ea, eb = ra.get(name, {}), rb.get(name, {})
        out["routing"].append({
            "name": name,
            "a": _num(ea.get("enrichment")), "b": _num(eb.get("enrichment")),
            "delta": _delta(_num(ea.get("enrichment")),
                            _num(eb.get("enrichment"))),
            "a_share": _num(ea.get("own_share")),
            "b_share": _num(eb.get("own_share")),
            # A starved expert's enrichment is noise on either side, and a
            # delta between two noises is a number with no referent at all.
            "reliable": bool(ea.get("enrichment_reliable", True))
                        and bool(eb.get("enrichment_reliable", True)),
        })

    qa = {q["name"]: q for q in ga.get("quality") or []}
    qb = {q["name"]: q for q in gb.get("quality") or []}
    for name in sorted(set(qa) | set(qb)):
        xa, xb = qa.get(name, {}), qb.get(name, {})
        row = {"name": name, "thin": bool(xa.get("thin")) or bool(xb.get("thin"))}
        for metric in ("exact_match", "rouge1", "bleu", "reasoned"):
            va, vb = _num(xa.get(metric)), _num(xb.get(metric))
            row[metric] = {"a": va, "b": vb, "delta": _delta(va, vb)}
        out["quality"].append(row)

    for label, key in (("mean enrichment", "mean_enrichment"),
                       ("mean JS divergence (bits)", "mean_js_bits"),
                       ("mean gate confidence", "mean_gate_confidence")):
        va = _num((ga.get("routing") or {}).get(key))
        vb = _num((gb.get("routing") or {}).get(key))
        if va is not None or vb is not None:
            out["headline"].append({"label": label, "a": va, "b": vb,
                                    "delta": _delta(va, vb)})
    return out


def compare(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    """Everything a reader needs, including what they may not conclude."""
    diff = config_diff(a, b)
    verdict = attribution(diff)
    outcome = outcome_diff(a, b)

    # THE ONE THAT DESERVES A SHOUT. Identical inputs, different numbers, means
    # something moved that nobody chose - a seed, a corpus draw, the teacher's
    # sampling. That is a fact about how repeatable this pipeline is, and it is
    # more valuable than any ordinary diff because it sets the floor below
    # which no other comparison here means anything.
    moved = any(r["delta"] not in (None, 0.0) for r in outcome["headline"])
    nondeterminism = (verdict == NONE and outcome["a_evaluated"]
                      and outcome["b_evaluated"] and moved)

    return {
        "a": _side(a), "b": _side(b),
        "config": diff,
        "attribution": verdict,
        "incomparable_because": comparability(a, b),
        "outcome": outcome,
        "nondeterminism": nondeterminism,
        "same_build": bool(_side(a)["build_id"])
                      and _side(a)["build_id"] == _side(b)["build_id"],
    }


def _side(row: Dict[str, Any]) -> Dict[str, Any]:
    manifest = row.get("manifest") or {}
    return {"run_key": row.get("run_key"), "name": row.get("name"),
            "build_id": str(row.get("build_id") or ""),
            "started": row.get("started"), "state": row.get("state"),
            "stage": row.get("stage"), "ok": row.get("ok"),
            "rung_present": row.get("rung_present"),
            "resolved_count": len(manifest.get("resolved") or {})}
