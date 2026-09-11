"""Real bytes, both ends. The test the contract test cannot be.

WHY THIS FILE EXISTS, and it is the most useful paragraph in this suite:

`evalrecord.py` is a complete reader for the streaming eval sidecar. The
pipeline holds a complete writer for it. test_eval_contract pins them together
and has been green since the day it was written - and not one byte has ever
travelled between them, because the writer has no caller and this reader's
`find()` has no caller either. Two immaculate implementations of a protocol
with no traffic, under a passing test.

A contract test compares CONSTANTS. It proves the two ends would agree if they
ever spoke. It cannot notice that they never do.

So this one makes them speak: the real writer writes a real file into a real
directory, and the real scanner reads it back out through the same call the
web app makes. Nothing is stubbed and no fixture is hand-written, because a
fixture written by hand agrees with the reader by construction - it came from
the same head, on the same afternoon, holding the same assumption.

Skips, naming what it wants, when the writer is not importable here. Skipping
is honest; passing on a fake is not.
"""
from __future__ import annotations

import pytest

import _writerfinder as wf
from seren_theatre import evalreport as rr
from seren_theatre import sources

pytestmark = pytest.mark.skipif(
    not wf.is_importable(),
    reason=f"{wf.WRITER_DIST or 'the writer'} is not importable here, so the "
           f"two halves of the results format cannot be made to speak. Install "
           f"it (seren-theatre[stagehand]) to run this.")


@pytest.fixture
def run_dir(tmp_path):
    """A run the scanner will recognise, with a manifest carrying a build_id."""
    from ms_moe_maker.run import manifest as writer_mf
    d = tmp_path / "msmoe_run_0.5B"
    d.mkdir()
    m = writer_mf.Manifest(name="msmoe_run_0.5B", build_id="cafebabe0001")
    writer_mf.write(d, m)
    return d


def _report():
    from ms_moe_maker.eval.harness import EvalReport, EvalResult
    rep = EvalReport(ok=True, message="measured")
    rep.routing = {"experts": {"python": {"own_share": 0.71,
                                          "others_share": 0.12,
                                          "enrichment": 2.14,
                                          "enrichment_reliable": True,
                                          "top_competitor": "rust",
                                          "top_competitor_share": 0.12,
                                          "own_is_column_max": True}},
                   "named_experts": 2, "own_is_max_count": 2,
                   "mean_enrichment": 2.02, "mean_js_bits": 0.31,
                   "moe_layers": 12, "top_k": 2,
                   "mean_gate_confidence": 0.33, "uniform_confidence": 0.5}
    rep.caveats = ["three rows only"]
    rep.undiscriminating = ["markdown"]
    rep.unmeasured = ["csharp: no compiler on this box"]
    rep.stages["python"] = EvalResult(
        expert_name="python", domain="py", exact_match=0.91, rouge1=0.83,
        bleu=0.70, scored_samples=3, attempted_samples=20, reasoned=0.62,
        status="done")
    rep.stages["moe/python"] = EvalResult(
        expert_name="moe/python", domain="py", exact_match=0.44, rouge1=0.51,
        bleu=0.33, scored_samples=20, attempted_samples=20, reasoned=0.11,
        status="done")
    return rep


def test_the_writers_file_reaches_the_viewers_scan(run_dir):
    """The whole path: writer -> disk -> scan_run -> the dict /api/state sends.

    `scan_run` rather than `read_eval` on purpose. Testing the reader alone
    would have passed for the entire life of the eval sidecar, whose reader is
    also perfect and also never called by anything.
    """
    from ms_moe_maker.eval.harness import save_eval_report, EVAL_REPORT_NAME

    save_eval_report(_report(), run_dir / EVAL_REPORT_NAME,
                     build_id="cafebabe0001")

    scanned = sources.scan_run(run_dir)
    assert scanned["eval"] is not None, (
        "the writer put a report on disk and the scanner did not pick it up - "
        "which is exactly how a run that WAS evaluated renders as one that "
        "was not")
    assert scanned["eval_error"] is None

    ev = scanned["eval"]
    assert ev["provenance"] == rr.MATCHES, (
        "the build_id the writer stamped did not match the one the manifest "
        "carries, so every eval would render as being about some other build")

    # The headline measurement, which the writer used to drop in silence.
    assert ev["routing"]["experts"][0]["enrichment"] == 2.14
    assert ev["routing"]["mean_js_bits"] == 0.31
    assert ev["routing"]["input_blind"] is False

    # The quality table, with the moe row nested under its own expert.
    row = ev["quality"][0]
    assert row["name"] == "python" and row["scored"] == 3
    assert row["thin"] is True, "3 scored of 20 attempted is a thin sample"
    assert row["in_moe"]["exact_match"] == 0.44

    assert ev["caveats"] == ["three rows only"]
    assert ev["unmeasured"] == ["csharp: no compiler on this box"]
    assert ev["undiscriminating"] == ["markdown"]


def test_a_rebuilt_run_shows_its_old_eval_as_stale(run_dir):
    """The one thing this must never get wrong.

    Same file, a manifest that has moved on. Valid JSON, real numbers, a model
    that is gone. Rendering that as current is not a cosmetic slip - it is a
    confident claim about a thing that was never measured.
    """
    from ms_moe_maker.eval.harness import save_eval_report, EVAL_REPORT_NAME
    from ms_moe_maker.run import manifest as writer_mf

    save_eval_report(_report(), run_dir / EVAL_REPORT_NAME,
                     build_id="cafebabe0001")
    writer_mf.write(run_dir, writer_mf.Manifest(name=run_dir.name,
                                                build_id="d1fferent0002"))

    assert sources.scan_run(run_dir)["eval"]["provenance"] == rr.STALE


def test_the_gate_report_the_builder_writes_reaches_the_scan(run_dir):
    """The gate's own dict, through its own `to_dict`, not a hand-made shape."""
    import json
    from ms_moe_maker.train.experts import ExpertsReport, GATE_REPORT_NAME

    rep = ExpertsReport(
        status="ok",
        divergence={"python": 0.031, "rust": 0.028},
        cross_loss={"python": {"python": 1.21, "rust": 3.40},
                    "rust": {"python": 3.11, "rust": 1.33}},
        findings=[], unmeasured=["config audit: no config.json"])
    (run_dir / GATE_REPORT_NAME).write_text(
        json.dumps(rep.to_dict(), indent=2), encoding="utf-8")

    gate = sources.scan_run(run_dir)["gate"]
    assert gate is not None and gate["status"] == "ok"
    assert gate["cross_loss"]["python"]["rust"] == 3.40
    # UNMEASURED SURVIVES AS ITS OWN LIST. Folded into findings it would read
    # as a problem; dropped it would read as a pass. It is neither.
    assert gate["unmeasured"] == ["config audit: no config.json"]
    assert gate["findings"] == []


def test_an_unevaluated_run_stays_quiet_all_the_way_through(run_dir):
    scanned = sources.scan_run(run_dir)
    assert scanned["eval"] is None and scanned["gate"] is None
    assert scanned["eval_error"] is None and scanned["gate_error"] is None
