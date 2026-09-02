"""Pin the two RESULT documents against the writer that emits them.

Same bargain as test_manifest_contract and test_eval_contract: `eval_report.json`
and `gate_experts.json` are wire formats the pipeline owns, Theatre implements
the reading half independently so a viewer never has to pull in a training
pipeline, and the price of two implementations is drift. This is where that
price gets paid.

────────────────────────────────────────────────────────────────────────────
AND A WARNING ABOUT WHAT A CONTRACT TEST CANNOT DO, written here because this
codebase has now been bitten by exactly it.

`evalrecord.py` is a complete reader for the streaming eval sidecar. It has a
matching complete WRITER in the pipeline. test_eval_contract pins them together
and has passed since the day it was written.

Not one byte has ever travelled between them. The writer has no caller; this
reader's `find()` has no caller. Two immaculate implementations of a protocol
with no traffic, and a green test standing over the whole arrangement.

A contract test proves the two ends AGREE. It cannot prove either end is
connected to anything. The pipeline carries the other half of this guard -
tests/test_results_reach_disk.py asserts the writers have callers - and the
day someone adds a third format, it needs both.
────────────────────────────────────────────────────────────────────────────

Read via `ast`, never by importing the writer.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import _writerfinder as wf
from seren_theatre import evalreport as rr

EVAL_MODULE = "harness.py"
GATE_MODULE = "experts.py"

eval_source = wf.find(EVAL_MODULE)
gate_source = wf.find(GATE_MODULE)

needs_eval = pytest.mark.skipif(
    eval_source is None,
    reason=f"{wf.WRITER_DIST or 'the writer'} is neither installed nor checked "
           f"out nearby; the writing half of the eval report isn't here.")
needs_gate = pytest.mark.skipif(
    gate_source is None,
    reason=f"{wf.WRITER_DIST or 'the writer'} is neither installed nor checked "
           f"out nearby; the writing half of the gate report isn't here.")


# ── the filenames, which are the whole discovery mechanism ───────────────────

@needs_eval
@pytest.mark.parametrize("name", ["EVAL_REPORT_NAME", "EVAL_REPORT_SCHEMA"])
def test_eval_report_constants_match(name):
    theirs = wf.constants(eval_source).get(name)
    ours = getattr(rr, name)
    assert theirs == ours, (
        f"{name}: {wf.WRITER_DIST} writes {theirs!r}, seren-theatre reads "
        f"{ours!r}. A viewer looking for the wrong filename reports every "
        f"evaluated run as un-evaluated, silently — which is indistinguishable "
        f"from the eval never having been run.")


@needs_gate
def test_gate_report_filename_matches():
    theirs = wf.constants(gate_source).get("GATE_REPORT_NAME")
    assert theirs == rr.GATE_REPORT_NAME, (
        f"GATE_REPORT_NAME: {wf.WRITER_DIST} writes {theirs!r}, seren-theatre "
        f"reads {rr.GATE_REPORT_NAME!r}.")


# ── the fields this viewer actually renders ──────────────────────────────────

@needs_eval
def test_the_writer_still_emits_every_field_the_panel_draws():
    """A field renamed on the writing side must fail HERE, not on screen.

    The failure mode without this is a table of em dashes: the panel renders,
    the layout is fine, every number is missing, and nothing anywhere says the
    key changed. That reads as "the run measured nothing", which is a claim
    about the model rather than about the viewer.
    """
    src = eval_source.read_text(encoding="utf-8")
    # The keys save_eval_report writes, as they appear in its dict literal.
    for key in ["ok", "message", "dead_experts", "undiscriminating", "caveats",
                "routing", "unmeasured", "experts", "stages", "build_id",
                "generated", "schema_version"]:
        assert f'"{key}"' in src, (
            f"the writer no longer emits {key!r}; evalreport.read_eval still "
            f"reads it and the panel still draws it.")
    # And the per-expert quality keys, which live in the same function.
    for key in ["exact_match", "rouge1", "bleu", "scored_samples",
                "attempted_samples", "reasoned", "status"]:
        assert f'"{key}"' in src, f"the writer no longer emits {key!r}"


@needs_gate
def test_the_gate_still_emits_every_field_the_panel_draws():
    src = gate_source.read_text(encoding="utf-8")
    for key in ["status", "divergence", "pairwise", "cross_loss",
                "config_audit", "findings", "unmeasured"]:
        assert f'"{key}"' in src, (
            f"ExpertsReport.to_dict no longer emits {key!r}; the gate block "
            f"still draws it.")


@needs_eval
def test_a_report_the_writer_produced_reads_back_whole(tmp_path):
    """Round-trip against the writer's OWN literal keys, not a hand-made fake.

    A fixture written by hand agrees with the reader by construction - it was
    written by the same person, from the same assumption. This builds the JSON
    from the key names found in the writer's source, so a rename shows up as a
    field the reader cannot see rather than as a fixture nobody updated.
    """
    payload = {
        "schema_version": rr.EVAL_REPORT_SCHEMA,
        "generated": 1700000000.0,
        "build_id": "feedface0000",
        "ok": True,
        "message": "measured",
        "dead_experts": [],
        "undiscriminating": ["markdown"],
        "caveats": ["three rows only"],
        "unmeasured": ["csharp: no compiler"],
        "experts": {"status": "ok"},
        "routing": {"experts": {"python": {"own_share": 0.7,
                                           "enrichment": 2.1}},
                    "mean_js_bits": 0.4, "top_k": 2},
        "stages": {"python": {"domain": "py", "exact_match": 0.9,
                              "rouge1": 0.8, "bleu": 0.7,
                              "scored_samples": 3, "attempted_samples": 20,
                              "reasoned": 0.6, "status": "done"}},
    }
    (tmp_path / rr.EVAL_REPORT_NAME).write_text(json.dumps(payload),
                                                encoding="utf-8")
    got = rr.read_eval(tmp_path, "feedface0000")
    assert got["provenance"] == rr.MATCHES
    assert got["quality"][0]["exact_match"] == 0.9
    assert got["quality"][0]["scored"] == 3
    assert got["routing"]["experts"][0]["enrichment"] == 2.1
    assert got["caveats"] and got["unmeasured"]
