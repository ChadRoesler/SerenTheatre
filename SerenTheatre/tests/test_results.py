"""The results reader: readings, never verdicts, and never a confident guess.

Everything here is one of two failures wearing different clothes:

  * A NUMBER THAT IS NOT ABOUT WHAT THE READER THINKS. A rebuilt run keeps its
    old eval report - valid JSON, confident numbers, a model that no longer
    exists - and rendering it plainly is the C# 0/10 failure in a new coat.
  * AN ABSENCE RENDERED AS A ZERO. "Not measured" and "measured, scored zero"
    are different sentences, and only one of them is about the model.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from seren_theatre import evalreport as rr


def _write(tmp_path, name, payload):
    (tmp_path / name).write_text(json.dumps(payload), encoding="utf-8")


# ── absence is not an error ──────────────────────────────────────────────────

def test_a_run_that_was_never_evaluated_is_quiet(tmp_path):
    """Most runs have never been evaluated. A panel that says so on every one
    of them teaches its reader to stop looking at that corner of the screen."""
    assert rr.read_eval(tmp_path) is None
    assert rr.read_gate(tmp_path) is None


def test_a_broken_document_is_loud(tmp_path):
    """Present-and-unreadable is NOT the same as absent, and it gets said.

    Silently returning None here would report a run whose eval report is
    corrupt as a run that was never evaluated - the viewer's own failure,
    rendered as a fact about the pipeline."""
    (tmp_path / rr.EVAL_REPORT_NAME).write_text("{not json", encoding="utf-8")
    with pytest.raises(rr.UnreadableResult) as exc:
        rr.read_eval(tmp_path)
    assert "not valid JSON" in str(exc.value)


def test_a_newer_schema_refuses_rather_than_guesses(tmp_path):
    _write(tmp_path, rr.EVAL_REPORT_NAME,
           {"schema_version": rr.EVAL_REPORT_SCHEMA + 1, "ok": True})
    with pytest.raises(rr.UnreadableResult) as exc:
        rr.read_eval(tmp_path)
    assert "Upgrade seren-theatre" in str(exc.value)


# ── provenance: the stale-numbers guard ──────────────────────────────────────

@pytest.mark.parametrize("report_id, manifest_id, expected", [
    ("abc", "abc", rr.MATCHES),
    ("abc", "def", rr.STALE),
    ("",    "def", rr.UNKNOWN),
    ("abc", "",    rr.UNKNOWN),
    ("",    "",    rr.UNKNOWN),
])
def test_provenance_has_three_answers_and_unknown_is_one_of_them(
        report_id, manifest_id, expected):
    """UNKNOWN MUST NEVER ROUND UP TO MATCHES. A bool could only carry two of
    these three, which is precisely how "we could not tell" gets promoted."""
    assert rr.provenance(report_id, manifest_id) == expected


def test_a_rebuilt_run_reports_its_old_eval_as_stale(tmp_path):
    _write(tmp_path, rr.EVAL_REPORT_NAME,
           {"build_id": "old111", "ok": True, "stages": {}})
    got = rr.read_eval(tmp_path, "new222")
    assert got["provenance"] == rr.STALE, (
        "an eval left over from an earlier build must not be shown as though "
        "it described the model on disk")


# ── an absence is not a zero ─────────────────────────────────────────────────

def test_a_run_that_never_measured_reasoning_reports_none_not_zero(tmp_path):
    """-1 is the writer's 'never asked'. Rendered as 0.00 it reads as a model
    that never once produced a think block, which is a different claim."""
    _write(tmp_path, rr.EVAL_REPORT_NAME,
           {"stages": {"py": {"reasoned": -1, "scored_samples": 20,
                              "attempted_samples": 20}}})
    row = rr.read_eval(tmp_path)["quality"][0]
    assert row["reasoned"] is None

    _write(tmp_path, rr.EVAL_REPORT_NAME,
           {"stages": {"py": {"reasoned": 0.0, "scored_samples": 20,
                              "attempted_samples": 20}}})
    row = rr.read_eval(tmp_path)["quality"][0]
    assert row["reasoned"] == 0.0, "a measured zero is a result and must survive"


def test_a_missing_score_is_none_and_not_zero(tmp_path):
    _write(tmp_path, rr.EVAL_REPORT_NAME, {"stages": {"py": {"status": "done"}}})
    row = rr.read_eval(tmp_path)["quality"][0]
    assert row["exact_match"] is None and row["bleu"] is None


@pytest.mark.parametrize("scored, attempted, thin", [
    (3, 20, True),      # most rows could not be scored
    (4, 8, True),       # under five, whatever the ratio
    (20, 20, False),
    (12, 20, False),
    (0, 0, False),      # nothing attempted is not a thin sample
])
def test_thin_samples_are_flagged_server_side(tmp_path, scored, attempted, thin):
    """Decided here so the browser cannot pick a different threshold than the
    CLI does. Two thresholds for one word is how two surfaces disagree."""
    _write(tmp_path, rr.EVAL_REPORT_NAME,
           {"stages": {"py": {"scored_samples": scored,
                              "attempted_samples": attempted}}})
    assert rr.read_eval(tmp_path)["quality"][0]["thin"] is thin


def test_the_moe_row_sits_under_the_expert_it_measures(tmp_path):
    """`moe/python` is the SAME expert inside the stitched model. Alphabetical
    order would file it between two strangers, and the pairing is the whole
    comparison."""
    _write(tmp_path, rr.EVAL_REPORT_NAME, {"stages": {
        "python": {"exact_match": 0.9}, "moe/python": {"exact_match": 0.4},
        "rust": {"exact_match": 0.8}}})
    q = rr.read_eval(tmp_path)["quality"]
    assert [r["name"] for r in q] == ["python", "rust"]
    assert q[0]["in_moe"]["exact_match"] == 0.4
    assert q[1]["in_moe"] is None


def test_an_orphan_moe_row_does_not_vanish(tmp_path):
    """Rare. Vanishing is not an acceptable way to handle rare."""
    _write(tmp_path, rr.EVAL_REPORT_NAME,
           {"stages": {"moe/ghost": {"exact_match": 0.1}}})
    names = [r["name"] for r in rr.read_eval(tmp_path)["quality"]]
    assert names == ["moe/ghost"]


# ── routing: the numbers that invite the wrong quote ─────────────────────────

def test_unreliable_enrichment_is_withheld_rather_than_printed(tmp_path):
    """An abandoned expert's enrichment is one noise over another. Printing
    2.15x beside a 0.001 share invites a reader to quote the best-looking
    figure in the table."""
    _write(tmp_path, rr.EVAL_REPORT_NAME, {"routing": {"experts": {
        "ghost": {"own_share": 0.001, "enrichment": 2.15,
                  "enrichment_reliable": False}}}})
    row = rr.read_eval(tmp_path)["routing"]["experts"][0]
    assert row["enrichment"] is None
    assert row["enrichment_reliable"] is False
    assert row["own_share"] == 0.001, "the share is the readable number, keep it"


@pytest.mark.parametrize("k, conf, saturated", [
    (2, 0.49, True),     # ceiling is 0.50 at K=2
    (2, 0.30, False),
    (4, 0.249, True),    # ceiling is 0.25 at K=4
    (1, 0.99, True),
    (1, 0.50, False),
])
def test_saturation_is_measured_against_one_over_k(tmp_path, k, conf, saturated):
    """THE CEILING IS 1/K, NOT 1.0, and getting this wrong disables the check
    silently. `conf` is the mean of the K top softmax probabilities and those
    sum to at most 1, so a fixed `> 0.95` threshold is unreachable for any
    K >= 2 - which switches off the one diagnosis separating saturated-and-blind
    from balanced-and-blind. Same arithmetic as the CLI: both right or both
    wrong, never one each."""
    _write(tmp_path, rr.EVAL_REPORT_NAME, {"routing": {
        "experts": {"py": {"own_share": 0.5}},
        "top_k": k, "mean_gate_confidence": conf,
        "uniform_confidence": 1.0 / max(k, 1)}})
    assert rr.read_eval(tmp_path)["routing"]["saturated"] is saturated


@pytest.mark.parametrize("js, blind", [(0.0, True), (0.0005, True),
                                       (0.4, False)])
def test_input_blind_is_decided_once_server_side(tmp_path, js, blind):
    _write(tmp_path, rr.EVAL_REPORT_NAME, {"routing": {
        "experts": {"py": {"own_share": 0.5}}, "mean_js_bits": js}})
    assert rr.read_eval(tmp_path)["routing"]["input_blind"] is blind


def test_unmeasurable_routing_survives_as_unmeasurable(tmp_path):
    """An absence of a finding, never a finding."""
    _write(tmp_path, rr.EVAL_REPORT_NAME, {"routing": {
        "status": "unmeasurable", "reason": "top-k equals the expert count"}})
    rt = rr.read_eval(tmp_path)["routing"]
    assert rt["status"] == "unmeasurable"
    assert "top-k" in rt["reason"]
    assert rt["experts"] == []


# ── the gate ─────────────────────────────────────────────────────────────────

def test_the_gate_keeps_unmeasured_apart_from_findings(tmp_path):
    """A check that could not run must never render as a check that came back
    clean. Same discipline as `unmeasurable` staying out of the eval's
    denominator, and the same reason: the failure looks like good news."""
    _write(tmp_path, rr.GATE_REPORT_NAME, {
        "status": "unmeasurable",
        "findings": ["two experts are nearly identical"],
        "unmeasured": ["cross-domain loss: no held-out rows"],
        "cross_loss": {"py": {"py": 1.2, "md": 3.4}},
        "divergence": {"py": 0.03}})
    g = rr.read_gate(tmp_path)
    assert g["findings"] and g["unmeasured"]
    assert g["findings"] != g["unmeasured"]
    assert g["cross_loss"]["py"]["md"] == 3.4
    assert g["status"] == "unmeasurable"


def test_the_gate_needs_no_provenance(tmp_path):
    """It runs INSIDE the build, before the stitch, written by the same process
    that wrote the manifest beside it - so unlike an eval it cannot be left
    over from an earlier build."""
    _write(tmp_path, rr.GATE_REPORT_NAME, {"status": "ok"})
    assert "provenance" not in rr.read_gate(tmp_path)


def test_a_garbled_top_level_is_refused_not_coerced(tmp_path):
    (tmp_path / rr.GATE_REPORT_NAME).write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(rr.UnreadableResult):
        rr.read_gate(tmp_path)


# ── the scanner actually reaches for them ────────────────────────────────────
#
# A PERFECT READER NOBODY CALLS IS THIS CODEBASE'S RECURRING DISEASE, and the
# reader one file over is the live proof: evalrecord.py parses the streaming
# sidecar completely, has a contract test pinning it to a complete writer, and
# `find()` has never once been called by anything. Testing read_eval alone
# would have passed for that reader too, every day, for its whole life.
#
# So these go through scan_run - the call the web app actually makes.

from seren_theatre import sources                                  # noqa: E402


def _run(tmp_path):
    d = tmp_path / "msmoe_run_0.5B"
    d.mkdir()
    return d


def test_scan_run_surfaces_an_eval_report(tmp_path):
    d = _run(tmp_path)
    _write(d, rr.EVAL_REPORT_NAME,
           {"ok": True, "build_id": "b1", "stages": {"py": {"exact_match": 0.5}}})
    out = sources.scan_run(d)
    assert out["eval"] is not None, (
        "the reader works and the scanner never calls it, which renders an "
        "evaluated run as an un-evaluated one")
    assert out["eval"]["quality"][0]["exact_match"] == 0.5


def test_scan_run_surfaces_a_gate_report(tmp_path):
    d = _run(tmp_path)
    _write(d, rr.GATE_REPORT_NAME, {"status": "ok", "divergence": {"py": 0.03}})
    out = sources.scan_run(d)
    assert out["gate"] is not None
    assert out["gate"]["divergence"]["py"] == 0.03


def test_scan_run_reports_a_broken_document_instead_of_crashing(tmp_path):
    """One corrupt result file must not take the whole run's card down.

    The run still has a manifest, a log, artifacts and a state worth showing.
    Losing all of it to a bad JSON blob would be the viewer punishing the
    reader for the pipeline's mess."""
    d = _run(tmp_path)
    (d / rr.EVAL_REPORT_NAME).write_text("{oops", encoding="utf-8")
    out = sources.scan_run(d)
    assert out["eval"] is None
    assert "not valid JSON" in (out["eval_error"] or "")
    assert out["name"] == "msmoe_run_0.5B", "the rest of the reading survived"


def test_scan_run_hands_the_manifests_build_id_to_the_provenance_check(tmp_path):
    """The staleness check is only as good as what it is given.

    If the scanner passes nothing, every eval reads `unknown` forever - which
    is not wrong, exactly, and is useless, which is worse: it looks like the
    feature works."""
    import json
    d = _run(tmp_path)
    (d / "msmoe-run.json").write_text(json.dumps({
        "schema_version": 1, "name": d.name, "build_id": "match-me",
        "stages": [], "started": 1.0, "updated": 2.0}), encoding="utf-8")
    _write(d, rr.EVAL_REPORT_NAME, {"ok": True, "build_id": "match-me"})
    assert sources.scan_run(d)["eval"]["provenance"] == rr.MATCHES

    _write(d, rr.EVAL_REPORT_NAME, {"ok": True, "build_id": "some-other"})
    assert sources.scan_run(d)["eval"]["provenance"] == rr.STALE


def test_the_default_finds_a_run_whatever_its_directory_is_called(tmp_path):
    """THE TEST THIS REPLACES COULD NOT HAVE CAUGHT THE BUG IT WAS FOR.

    It asserted that the shipped globs matched the output directories somebody
    had thought of - `msmoe_run_{size}` and friends - which pins a whitelist
    against a list of names, and a whitelist is only ever wrong about the name
    nobody listed. It passed for months and the failure it existed to prevent
    happened twice anyway: once against a default that predated
    `msmoe_run_*`, and once against `gauntlet-nano-runs/0.5B`, written by
    someone who had read the config an hour earlier.

    So the invariant is no longer "the names we thought of are matched". It is
    "the name does not matter", and it is checked by putting real directories
    on disk with names nobody would have listed and asking with NO config at
    all. `_run` builds them the way the pipeline does; discovery is expected
    to find every one.
    """
    from seren_theatre.config import StageConfig

    stage = tmp_path / "workbench"
    wanted = {
        "msmoe_run_0.5B",              # the flat default
        "msmoe_dryrun_0.5B",
        "gauntlet-runs/0.5B",          # a `<name>/{size}` output root
        "gauntlet-nano-runs/0.5B",     # the one a real whitelist missed
        "a-name-nobody-would-whitelist/xyz",
    }

    def build(at: Path) -> None:
        """A run the way the pipeline leaves one: a manifest from its first
        second, and the artifacts that arrive later."""
        at.mkdir(parents=True)
        (at / "msmoe-run.json").write_text(
            '{"schema_version": 1, "name": "r", "stages": [], '
            '"started": 1.0, "updated": 2.0}', encoding="utf-8")
        art = at / "moe_trained"
        art.mkdir()
        (art / "config.json").write_text("{}", encoding="utf-8")

    # noqa-free spacing: a nested def wants a blank line before it.
    for rel in wanted:
        build(stage / rel)

    # A neighbour that is NOT a run, to prove discovery is still narrow.
    (stage / "shard_cache").mkdir(parents=True, exist_ok=True)
    (stage / "shard_cache" / "shard-0.jsonl").write_text("x", encoding="utf-8")

    globs = StageConfig(name="x", path=str(stage)).runs
    assert globs == [], "the default is now discovery, not a whitelist"

    found = sources.resolve_runs(stage, globs)
    assert {p.relative_to(stage).as_posix() for p in found} == wanted


def test_a_corpus_directory_still_does_not_render_as_a_run(tmp_path):
    """The broad glob is safe because the predicate is narrow.

    `msmoe_*` also catches `msmoe_data`, the shared corpus root, which is not a
    run and never will be. looks_like_run asks what the directory IS rather
    than what its name looks like - written after `dryrun_data` rendered as an
    empty run card reading 'Nothing built here yet', a true sentence about a
    directory that was never going to have anything built in it.
    """
    corpus = tmp_path / "msmoe_data"
    corpus.mkdir()
    (corpus / "python.jsonl").write_text('{"text":"x"}\n', encoding="utf-8")
    assert not sources.looks_like_run(corpus)
