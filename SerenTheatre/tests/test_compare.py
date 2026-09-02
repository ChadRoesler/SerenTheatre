"""Two runs side by side, and what a reader is not entitled to conclude.

THE FEATURE IS MOSTLY CAVEAT, AND THAT IS THE DESIGN. Put two runs in two
columns and the eye finishes the job: `router_epochs 3 -> 8` next to
`1.02x -> 2.14x` produces a conclusion before anybody decides to draw one. The
conclusion is warranted only when those were the ONLY things that differed.

So the tests that matter here are not "does it compute a delta". They are:

  * does it REFUSE to attribute when several inputs moved
  * does it separate a decision from that decision's consequences
  * does it shout when identical inputs produced different numbers
  * does it keep "not measured" out of the delta columns

A diff that got the arithmetic right and the attribution wrong would be worse
than no diff at all - it would be a confidently-wrong claim generator with a
nice table, which is the exact failure this whole service exists against.
"""
from __future__ import annotations

import pytest

from seren_theatre.archive import compare as C

KNOBS = {
    "router_epochs": {"summary": "Passes over the router mix.",
                      "derived_from": None},
    "lora_r": {"summary": "Adapter rank.", "derived_from": None},
    "size": {"summary": "Base size.", "derived_from": None},
    "collect_token_target": {"summary": "Tokens collected.",
                             "derived_from": "expert_token_budget * headroom"},
}


def run(resolved, *, enrichment=None, reasoned=None, build="b1",
        knobs=KNOBS, evaluated=True, name="dryrun"):
    """An archived surgery row, shaped exactly as the store returns one."""
    row = {"run_key": f"{name}-{build}", "name": name, "build_id": build,
           "started": 1.0, "state": "finished", "ok": 1, "rung_present": 1,
           "manifest": {"build_id": build, "resolved": dict(resolved),
                        "knobs": knobs},
           "gradings": [], "gate": None}
    if evaluated:
        routing = {"experts": [], "mean_enrichment": enrichment}
        if enrichment is not None:
            routing["experts"] = [{"name": "python", "own_share": 0.7,
                                   "enrichment": enrichment,
                                   "enrichment_reliable": True}]
        quality = [{"name": "python", "exact_match": 0.9, "rouge1": 0.8,
                    "bleu": 0.7, "reasoned": reasoned, "thin": False}]
        row["gradings"] = [{"grading_key": f"g-{build}", "generated": 1.0,
                            "provenance": "matches",
                            "view": {"routing": routing, "quality": quality}}]
    return row


BASE = {"size": "0.5B", "router_epochs": 3, "lora_r": 16,
        "collect_token_target": 100}


# ── attribution: the whole point ────────────────────────────────────────────

def test_one_changed_input_is_the_case_you_may_point_at():
    a = run(BASE, enrichment=1.02, build="a")
    b = run({**BASE, "router_epochs": 8}, enrichment=2.14, build="b")
    got = C.compare(a, b)
    assert got["attribution"] == C.SINGLE
    assert [r["field"] for r in got["config"]["inputs"]] == ["router_epochs"]
    assert got["outcome"]["headline"][0]["delta"] == pytest.approx(1.12)


def test_several_changed_inputs_refuse_to_attribute():
    """THE ONE THAT KEEPS THIS HONEST.

    Two knobs moved and the number moved. Which one did it? Nothing here can
    say, and a feature that let the table imply otherwise would be worse than
    having no feature.
    """
    a = run(BASE, enrichment=1.02, build="a")
    b = run({**BASE, "router_epochs": 8, "lora_r": 32},
            enrichment=2.14, build="b")
    got = C.compare(a, b)
    assert got["attribution"] == C.MULTIPLE
    assert len(got["config"]["inputs"]) == 2


def test_a_consequence_does_not_count_as_a_decision():
    """Change one knob and a dozen derived values follow it.

    Counting those as inputs would report twelve decisions where somebody made
    one, and would downgrade every real single-variable comparison to
    `multiple` - which is to say it would break the feature entirely while
    still producing a plausible-looking table.
    """
    a = run(BASE, enrichment=1.02, build="a")
    b = run({**BASE, "router_epochs": 8, "collect_token_target": 250},
            enrichment=2.14, build="b")
    got = C.compare(a, b)
    assert got["attribution"] == C.SINGLE
    assert [r["field"] for r in got["config"]["inputs"]] == ["router_epochs"]
    assert [r["field"] for r in got["config"]["consequences"]] == [
        "collect_token_target"]


def test_without_a_glossary_everything_reads_as_an_input():
    """An older manifest cannot tell a decision from its consequence.

    Overstating the number of decisions is the SAFE direction - it weakens the
    attribution rather than inventing one - and the panel says the glossary is
    missing rather than presenting a split it could not make.
    """
    a = run(BASE, enrichment=1.0, build="a", knobs={})
    b = run({**BASE, "router_epochs": 8, "collect_token_target": 250},
            enrichment=2.0, build="b", knobs={})
    got = C.compare(a, b)
    assert got["config"]["has_glossary"] is False
    assert got["attribution"] == C.MULTIPLE
    assert got["config"]["consequences"] == []


# ── the loud one ────────────────────────────────────────────────────────────

def test_identical_inputs_with_different_numbers_is_flagged():
    """NOT A FAILURE - A MEASUREMENT, and the most valuable one here.

    Nothing in the fingerprint differs and the results moved anyway, so
    something changed that nobody chose: a seed, a corpus draw, the teacher's
    sampling. That number is the floor under every other comparison on the
    page, because no delta smaller than it means anything.
    """
    a = run(BASE, enrichment=1.02, build="same")
    b = run(BASE, enrichment=1.47, build="same")
    got = C.compare(a, b)
    assert got["attribution"] == C.NONE
    assert got["same_build"] is True
    assert got["nondeterminism"] is True


def test_identical_inputs_and_identical_numbers_is_not_flagged():
    a = run(BASE, enrichment=1.02, build="same")
    b = run(BASE, enrichment=1.02, build="same")
    got = C.compare(a, b)
    assert got["attribution"] == C.NONE
    assert got["nondeterminism"] is False, (
        "a reproducible pair was reported as non-determinism, which would "
        "make the warning noise and therefore ignored")


def test_nondeterminism_is_not_claimed_when_a_run_was_never_evaluated():
    """No numbers is not the same as numbers that did not move."""
    a = run(BASE, enrichment=1.02, build="same")
    b = run(BASE, build="same", evaluated=False)
    got = C.compare(a, b)
    assert got["nondeterminism"] is False
    assert got["outcome"]["b_evaluated"] is False


# ── comparability ───────────────────────────────────────────────────────────

def test_two_different_experiments_say_so_and_are_still_shown():
    """Reported, not refused. Somebody may genuinely want to look at the 0.5B
    dry run beside the 7B it was rehearsing for - blocking that would be the
    tool deciding what its user is allowed to wonder about. What it may not do
    is let the comparison look controlled."""
    a = run(BASE, enrichment=1.02, build="a")
    b = run({**BASE, "size": "7B"}, enrichment=2.14, build="b")
    got = C.compare(a, b)
    assert got["incomparable_because"], "a 0.5B vs 7B comparison read as controlled"
    assert "size" in got["incomparable_because"][0]
    assert got["outcome"]["headline"], "and it is still shown"


# ── an absence is not a zero ────────────────────────────────────────────────

def test_a_metric_only_one_side_measured_has_no_delta():
    """A run that never measured reasoning did not score the same as one that
    did. A 0.00 in a delta column is the exact shape of a claim nobody made."""
    a = run(BASE, enrichment=1.0, reasoned=0.62, build="a")
    b = run(BASE, enrichment=1.0, reasoned=None, build="b")
    got = C.compare(a, b)
    row = got["outcome"]["quality"][0]
    assert row["reasoned"]["a"] == 0.62
    assert row["reasoned"]["b"] is None
    assert row["reasoned"]["delta"] is None


def test_a_delta_between_two_starved_experts_is_withheld():
    """An abandoned expert's enrichment is noise. A delta between two noises is
    a number with no referent, and it would be the best-looking figure in the
    table."""
    a = run(BASE, build="a")
    b = run(BASE, build="b")
    for row, value in ((a, 2.15), (b, 0.98)):
        row["gradings"][0]["view"]["routing"]["experts"] = [
            {"name": "ghost", "own_share": 0.001, "enrichment": value,
             "enrichment_reliable": False}]
    got = C.compare(a, b)
    assert got["outcome"]["routing"][0]["reliable"] is False


def test_a_field_only_one_run_has_is_reported_as_such():
    """A version difference, which is exactly the thing worth being told."""
    a = run({**BASE, "gone_field": 1}, build="a")
    b = run({**BASE, "new_field": 2}, build="b")
    got = C.compare(a, b)
    assert got["config"]["only_in_a"] == ["gone_field"]
    assert got["config"]["only_in_b"] == ["new_field"]


def test_the_newest_grading_is_the_one_compared():
    """A run evaluated twice has two honest answers. Picking the flattering one
    would be the worst thing this module could do; picking the latest is at
    least a rule, and the store already orders them."""
    a = run(BASE, enrichment=1.0, build="a")
    b = run(BASE, enrichment=9.9, build="b")
    b["gradings"].append({"grading_key": "older", "generated": 0.5,
                          "view": {"routing": {"mean_enrichment": 0.1},
                                   "quality": []}})
    got = C.compare(a, b)
    assert got["outcome"]["headline"][0]["b"] == 9.9


def test_the_knob_summary_travels_with_the_changed_field():
    """The glossary the writer stamped into the manifest explains the row, so
    a person meeting `collect_token_target` for the first time is not left to
    guess. Third job that glossary has done."""
    a = run(BASE, build="a")
    b = run({**BASE, "router_epochs": 8}, build="b")
    got = C.compare(a, b)
    assert got["config"]["inputs"][0]["knob"]["summary"].startswith("Passes")


# ── through the route, on real archived rows ────────────────────────────────

def test_the_diff_route_reads_what_the_cards_read(tmp_path):
    """SAME PROJECTION AS THE LISTING, deliberately.

    A second path to "what does this eval say" would be a second opinion about
    a number, rendered on the same page as the first, in a place nobody is
    checking. The diff must read the archived documents through exactly the
    projection the surgery cards use.
    """
    import json

    from fastapi.testclient import TestClient

    from seren_theatre.app import create_app
    from seren_theatre.config import StageConfig, TheatreConfig

    stage = tmp_path / "lab"
    for name, epochs, enrich, build in (("dryrun_a", 3, 1.02, "aaa1"),
                                        ("dryrun_bb", 8, 2.14, "bbb2")):
        d = stage / name
        d.mkdir(parents=True)
        (d / "msmoe-run.json").write_text(json.dumps({
            "schema_version": 1, "name": name, "build_id": build,
            "started": 1700000000.0 + epochs, "updated": 1.0,
            "finished": 1.0, "ok": True, "state": "finished", "stages": [],
            "resolved": {"size": "0.5B", "router_epochs": epochs,
                         "collect_token_target": 100 * epochs, "lora_r": 16},
            "knobs": KNOBS}), encoding="utf-8")
        (d / "eval_report.json").write_text(json.dumps({
            "schema_version": 1, "build_id": build, "generated": 1.0,
            "ok": True,
            "routing": {"experts": {"python": {"own_share": 0.7,
                                               "enrichment": enrich,
                                               "enrichment_reliable": True}},
                        "mean_enrichment": enrich, "top_k": 2,
                        "mean_gate_confidence": 0.49,
                        "uniform_confidence": 0.5},
            "stages": {"python": {"exact_match": 0.9, "scored_samples": 18,
                                  "attempted_samples": 20, "reasoned": 0.62,
                                  "status": "done"}}}), encoding="utf-8")

    cfg = TheatreConfig(archive={"dsn": str(tmp_path / "a.db")})
    cfg.stages = [StageConfig(name="Lab", path=str(stage))]
    with TestClient(create_app(cfg)) as client:
        client.get("/api/state")
        keys = [s["run_key"]
                for s in client.get("/api/archive").json()["surgeries"]]
        got = client.get(
            f"/api/archive/diff?a={keys[1]}&b={keys[0]}").json()

    assert got["attribution"] == C.SINGLE
    assert [r["field"] for r in got["config"]["inputs"]] == ["router_epochs"]
    assert [r["field"] for r in got["config"]["consequences"]] == [
        "collect_token_target"]
    assert got["outcome"]["headline"][0]["delta"] == pytest.approx(1.12)
    # The derived verdicts the projection computes are present here too, which
    # is the evidence that one projection served both surfaces.
    assert got["outcome"]["quality"][0]["exact_match"]["delta"] == 0.0


def test_the_diff_route_says_which_run_it_could_not_find(tmp_path):
    from fastapi.testclient import TestClient

    from seren_theatre.app import create_app
    from seren_theatre.config import StageConfig, TheatreConfig

    stage = tmp_path / "lab"
    stage.mkdir()
    cfg = TheatreConfig(archive={"dsn": str(tmp_path / "a.db")})
    cfg.stages = [StageConfig(name="Lab", path=str(stage))]
    with TestClient(create_app(cfg)) as client:
        r = client.get("/api/archive/diff?a=nope&b=alsonope")
    assert r.status_code == 404 and "nope" in r.json()["detail"]


def test_comparing_stays_a_read_route(tmp_path):
    """It exists on a plain viewer. Somebody watching a box they do not build
    on is exactly the person who wants to ask what changed."""
    from fastapi.testclient import TestClient

    from seren_theatre.app import create_app
    from seren_theatre.config import StageConfig, TheatreConfig
    from seren_theatre.stageguard import mutating_routes

    stage = tmp_path / "lab"
    stage.mkdir()
    cfg = TheatreConfig(archive={"dsn": str(tmp_path / "a.db")})
    cfg.stages = [StageConfig(name="Lab", path=str(stage))]
    app = create_app(cfg)
    assert "/api/archive/diff" in {getattr(r, "path", None) for r in app.routes}
    assert "/api/archive/diff" not in {p for p, _ in mutating_routes(app)}
