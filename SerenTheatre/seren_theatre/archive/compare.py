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


#: Below this, two numbers are the same number. Not a fudge: these metrics
#: arrive as JSON and are compared exactly everywhere else, but a mean recomputed
#: over a set whose members were summed in a different order can differ in the
#: last bits - and reporting THAT as "the pipeline is not repeatable" is crying
#: wolf about floating point. Anything a person would call a change is orders of
#: magnitude above this.
MOVED_EPSILON = 1e-9


def moved_dimensions(outcome: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every measured dimension that actually differs, with its delta.

    WHY THIS IS NOT JUST THE HEADLINE MEANS. `nondeterminism` used to ask only
    whether one of three averages moved, and an average is exactly the wrong
    place to look for it: two runs can route completely differently - this
    expert starved, that one taking its ground - and land on the same mean
    enrichment, because a mean is what you compute when you are willing to lose
    the distribution. The narrower check answered "no" on the runs with the most
    to say.

    So the whole surface is asked, and the two exclusions are the ones the
    outcome itself already flags:

      * an UNRELIABLE enrichment is noise on either side, and a delta between
        two noises is a number with no referent at all.
      * a THIN quality set is too small to have measured anything, so a moved
        score there is a statement about the sample and not about the build.

    Returns the dimensions, not a count - because which one moved is the whole
    difference between a grade and a design instrument.
    """
    out: List[Dict[str, Any]] = []

    def add(label: str, delta: Any) -> None:
        if isinstance(delta, (int, float)) and not isinstance(delta, bool) \
                and abs(delta) > MOVED_EPSILON:
            out.append({"label": label, "delta": delta})

    for row in outcome.get("headline") or []:
        add(str(row.get("label") or ""), row.get("delta"))

    for row in outcome.get("routing") or []:
        if not row.get("reliable"):
            continue
        add(f"{row.get('name')} enrichment", row.get("delta"))

    for row in outcome.get("quality") or []:
        if row.get("thin"):
            continue
        for metric in ("exact_match", "rouge1", "bleu", "reasoned"):
            cell = row.get(metric)
            if isinstance(cell, dict):
                add(f"{row.get('name')} {metric}", cell.get("delta"))

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
    moved = moved_dimensions(outcome)
    nondeterminism = bool(verdict == NONE and outcome["a_evaluated"]
                          and outcome["b_evaluated"] and moved)

    return {
        "a": _side(a), "b": _side(b),
        "config": diff,
        "attribution": verdict,
        "incomparable_because": comparability(a, b),
        "outcome": outcome,
        "nondeterminism": nondeterminism,
        # WHICH DIMENSIONS MOVED, additive beside the boolean rather than
        # replacing it. "The pipeline is not repeatable" is a grade; "it is not
        # repeatable IN csharp's enrichment, by 0.31, while every mean held" is
        # something a person can go and look into. Populated whether or not the
        # configs were identical, because the same list is what makes a
        # single-knob comparison readable.
        "moved": moved,
        "same_build": bool(_side(a)["build_id"])
                      and _side(a)["build_id"] == _side(b)["build_id"],
    }


def _side(row: Dict[str, Any]) -> Dict[str, Any]:
    manifest = row.get("manifest") or {}
    return {"run_key": row.get("run_key"), "name": row.get("name"),
            "build_id": str(row.get("build_id") or ""),
            "started": row.get("started"), "state": row.get("state"),
            "stage": row.get("stage"), "ok": row.get("ok"),
            # BOTH, and `run_state` is the one the viewer reads. Passing only
            # the boolean made this panel degrade `unknown` to "archived only" -
            # announcing a deletion because a share was down, which is the
            # precise false alarm the third state was added to stop. The
            # surgeries list got the three-state rendering and this surface did
            # not, because a derived boolean looks complete on its own.
            "run_present": row.get("run_present"),
            "run_state": row.get("run_state") or "",
            "resolved_count": len(manifest.get("resolved") or {})}


# ── the sweep: what the history already proves ──────────────────────────────
#
# `compare` answers a question you brought. This answers the one you did not
# know to ask.
#
# THE PROBLEM WITH A PAIRWISE TOOL. `attribution == SINGLE` is the only case
# where pointing at a knob is defensible - and finding those pairs by hand in
# forty runs means working through seven hundred and eighty combinations. So the
# instrument existed and nobody could aim it. Every run was in the archive and
# none of them were telling anybody anything.
#
# The archive holds every row and config_diff is already written, so the thing a
# history can do that a pair cannot is sweep itself: find the pairs where
# attribution is ALREADY warranted, rather than inventing attribution for the
# pairs somebody happened to tick.
#
# AND THE FLAT ONES COUNT. A knob that moved nothing across three pairs is a
# finding - arguably the more useful one, because it is the one you can stop
# turning. An instrument that only reports movement is a movement detector.
#
# NO NEW OPINIONS ARE FORMED HERE. Every verdict comes from config_diff,
# attribution, comparability and moved_dimensions. This decides which pairs to
# ask about; the answers are the same ones the side-by-side view gives, which is
# what stops this from becoming a second opinion with a nicer table.

#: The sweep is quadratic in a group, and the README is blunt about the budget:
#: "the dashboard must never be the reason the box is busy - that would be an
#: unusually stupid way to perturb a measurement." So it is capped, and the
#: result SAYS it was capped. A silently truncated sweep would report "nothing
#: found" about runs it never looked at, which is this codebase's signature
#: failure wearing a lab coat.
SWEEP_MAX_PAIRS = 20_000


def _identity(row: Dict[str, Any]) -> Tuple:
    """What makes two runs the same EXPERIMENT rather than two experiments.

    Grouping on this is not an optimisation that happens to help - comparability
    already refuses to treat a 0.5B dry run and a 7B as one experiment, so pairs
    across a boundary could never have produced a finding. It cuts the quadratic
    down as a side effect.
    """
    resolved = _resolved(row)
    return tuple(repr(resolved.get(f)) for f in IDENTITY_FIELDS)


def _finding(a: Dict[str, Any], b: Dict[str, Any], diff: Dict[str, Any],
             outcome: Dict[str, Any], kind: str) -> Dict[str, Any]:
    """One row of the report. Older run first, so a delta reads as a change."""
    inputs = diff.get("inputs") or []
    knob = inputs[0] if len(inputs) == 1 else None
    moved = moved_dimensions(outcome)
    return {
        "kind": kind,
        "a": a.get("run_key"), "b": b.get("run_key"),
        "a_name": a.get("name"), "b_name": b.get("name"),
        "a_started": a.get("started"), "b_started": b.get("started"),
        "field": (knob or {}).get("field"),
        "a_value": (knob or {}).get("a"), "b_value": (knob or {}).get("b"),
        "knob": (knob or {}).get("knob"),
        "moved": moved,
        # A knob that changed nothing is a RESULT, not an empty row. It is the
        # one you get to stop turning.
        "flat": not moved,
        "has_glossary": bool(diff.get("has_glossary")),
    }


def _biggest(finding: Dict[str, Any]) -> float:
    deltas = [abs(float(m["delta"])) for m in finding.get("moved") or []]
    return max(deltas) if deltas else 0.0


def sweep(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Every pair in this history that a reader is entitled to conclude from.

    Two kinds, and the order is deliberate:

      nondeterminism  identical inputs, different numbers. FIRST, because it
                      sets the floor below which no other finding here means
                      anything - a knob that moved enrichment by 0.04 is not a
                      result if rerunning the same config moves it by 0.09.
      single          exactly one input differed. What moved, or that nothing
                      did.

    Pairs with several inputs changed are counted and NOT listed. Nothing here
    could tell you which one moved the number, and a list of them would be a
    confidently-wrong claim generator with a nice table - so they are reported
    as a number, which is also a nudge toward changing one thing at a time.

    `by_field` is the part that turns this from a list into an instrument: per
    knob, how many pairs tested it and how many of them moved anything. That is
    the difference between "run B scored better" and "target_steps is
    load-bearing and lr is provably flat in this history".
    """
    findings: List[Dict[str, Any]] = []
    groups: Dict[Tuple, List[Dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(_identity(row), []).append(row)

    pairs = 0
    multiple = 0
    incomparable = 0
    unevaluated = 0
    truncated = False

    for group in groups.values():
        # Oldest first, so every pair reads a -> b in the direction time ran.
        ordered = sorted(group, key=lambda r: (float(r.get("started") or 0.0),
                                               str(r.get("run_key") or "")))
        for i, a in enumerate(ordered):
            for b in ordered[i + 1:]:
                if pairs >= SWEEP_MAX_PAIRS:
                    truncated = True
                    break
                pairs += 1
                if comparability(a, b):
                    # Cannot happen inside a group unless a manifest is missing
                    # an identity field on one side; counted rather than assumed
                    # away.
                    incomparable += 1
                    continue
                diff = config_diff(a, b)
                verdict = attribution(diff)
                if verdict == MULTIPLE:
                    multiple += 1
                    continue
                outcome = outcome_diff(a, b)
                if not (outcome["a_evaluated"] and outcome["b_evaluated"]):
                    # Built and never graded is a real state and a common one.
                    # There is nothing to compare, which is not a null finding.
                    unevaluated += 1
                    continue
                if verdict == NONE:
                    found = _finding(a, b, diff, outcome, "nondeterminism")
                    # Identical configs with identical numbers is the pipeline
                    # behaving. Not a finding, and listing it would bury the
                    # ones that are.
                    if found["moved"]:
                        findings.append(found)
                    continue
                findings.append(_finding(a, b, diff, outcome, SINGLE))
            if truncated:
                break
        if truncated:
            break

    by_field: Dict[str, Dict[str, Any]] = {}
    for found in findings:
        # ONE GUARD, ONE REASON. A nondeterminism finding has no field by
        # construction - verdict NONE means zero changed inputs, so `_finding`
        # leaves it None - and the second half of this condition used to say the
        # same thing a different way. Two conditions for one fact means a
        # mutation can remove either and nothing changes, which is how a guard
        # stops being tested.
        if found["kind"] != SINGLE:
            continue
        entry = by_field.setdefault(found["field"], {
            "field": found["field"], "pairs": 0, "moved": 0, "flat": 0,
            "knob": found["knob"], "largest": 0.0})
        entry["pairs"] += 1
        entry["moved" if found["moved"] else "flat"] += 1
        entry["largest"] = max(entry["largest"], _biggest(found))

    findings.sort(key=lambda f: (f["kind"] != "nondeterminism",
                                 -_biggest(f), str(f["field"] or "")))

    return {
        "findings": findings,
        # Sorted so the readable claim comes first: a knob that moved something,
        # by the most it moved. Flat knobs after, and they are the ones with a
        # `pairs` count and no movement.
        "by_field": sorted(by_field.values(),
                           key=lambda e: (-e["moved"], -e["largest"],
                                          e["field"])),
        "runs": len(rows),
        "groups": len(groups),
        "pairs": pairs,
        # What was looked at and rejected, said out loud. "No findings" has to be
        # distinguishable from "nothing was comparable", or the empty state is a
        # lie by omission.
        "skipped": {"several_inputs": multiple, "not_evaluated": unevaluated,
                    "incomparable": incomparable},
        "truncated": truncated,
        "max_pairs": SWEEP_MAX_PAIRS,
    }

