"""What the history already proves, and the floor under every claim in it.

TWO THINGS THIS FILE IS ABOUT.

The SWEEP. `compare` answers a question you brought; `attribution == SINGLE` is
the only case where pointing at a knob is defensible, and finding those pairs by
hand in forty runs means working through seven hundred and eighty combinations.
The instrument existed and nobody could aim it. So the sweep finds the pairs
where attribution is ALREADY warranted, rather than inventing it for whichever
pair somebody happened to tick.

The WIDENING. `nondeterminism` asked only whether one of three AVERAGES moved,
and an average is the worst place to look: two runs can route completely
differently - this expert starved, that one taking its ground - and land on the
same mean, because a mean is what you compute when you are willing to lose the
distribution. The narrow check answered "no" on the runs with the most to say.

Neither forms a new opinion. Every verdict still comes from config_diff,
attribution, comparability and the outcome. What is new is which pairs get asked
and how much of the answer gets looked at.
"""
from __future__ import annotations

import pytest

from seren_theatre.archive import compare as cmp


def row(key, *, started=1.0, size="0.5B", base="Qwen/Q", experts=("py", "cs"),
        resolved=None, routing=None, quality=None, headline=None,
        evaluated=True, knobs=None, name=None):
    """An archived row shaped as the listing hands it over, with a view."""
    res = {"size": size, "base": base, "expert_names": list(experts)}
    res.update(resolved or {})
    manifest = {"resolved": res, "build_id": f"build-{key}",
                "knobs": knobs or {}}
    out = {"run_key": key, "name": name or key, "started": started,
           "state": "finished", "ok": 1, "manifest": manifest, "gradings": []}
    if evaluated:
        view = {"routing": {"experts": routing if routing is not None else [],
                            "mean_enrichment": None, "mean_js_bits": None,
                            "mean_gate_confidence": None},
                "quality": quality or []}
        for label, key_name in (("mean_enrichment", "mean_enrichment"),
                                ("mean_js_bits", "mean_js_bits"),
                                ("mean_gate_confidence",
                                 "mean_gate_confidence")):
            if headline and label in headline:
                view["routing"][key_name] = headline[label]
        out["gradings"] = [{"grading_key": f"g-{key}", "view": view}]
    return out


def expert(name, enrichment, reliable=True, share=0.5):
    return {"name": name, "enrichment": enrichment, "own_share": share,
            "enrichment_reliable": reliable}


# ── the widening ────────────────────────────────────────────────────────────

class TestMovedDimensions:

    def outcome(self, a, b):
        return cmp.outcome_diff(a, b)

    def test_a_moved_mean_is_found(self):
        got = cmp.moved_dimensions(self.outcome(
            row("a", headline={"mean_enrichment": 1.0}),
            row("b", headline={"mean_enrichment": 2.0})))
        assert [m["label"] for m in got] == ["mean enrichment"]
        assert got[0]["delta"] == pytest.approx(1.0)

    def test_per_expert_routing_is_found_when_every_mean_held(self):
        """THE CASE THE OLD CHECK MISSED, and it is not a corner.

        Two experts swap ground - one starves, the other takes it - and the mean
        enrichment is identical. The distribution moved completely and the
        average says nothing happened.
        """
        got = cmp.moved_dimensions(self.outcome(
            row("a", routing=[expert("py", 2.0), expert("cs", 1.0)],
                headline={"mean_enrichment": 1.5}),
            row("b", routing=[expert("py", 1.0), expert("cs", 2.0)],
                headline={"mean_enrichment": 1.5})))
        labels = sorted(m["label"] for m in got)
        assert labels == ["cs enrichment", "py enrichment"], (
            "per-expert routing moved and the mean did not, so widening past "
            "the means is the whole point of this function")

    def test_quality_metrics_are_found(self):
        got = cmp.moved_dimensions(self.outcome(
            row("a", quality=[{"name": "code", "exact_match": 0.4,
                               "rouge1": 0.5, "bleu": 0.1, "reasoned": 0.2}]),
            row("b", quality=[{"name": "code", "exact_match": 0.6,
                               "rouge1": 0.5, "bleu": 0.1, "reasoned": 0.2}])))
        assert [m["label"] for m in got] == ["code exact_match"]

    def test_an_unreliable_enrichment_is_not_movement(self):
        """A starved expert's enrichment is noise on either side, and a delta
        between two noises is a number with no referent at all."""
        got = cmp.moved_dimensions(self.outcome(
            row("a", routing=[expert("py", 1.0, reliable=False)]),
            row("b", routing=[expert("py", 9.0, reliable=False)])))
        assert got == []

    def test_a_thin_quality_set_is_not_movement(self):
        """Too small to have measured anything: a moved score there is a
        statement about the sample, not about the build."""
        got = cmp.moved_dimensions(self.outcome(
            row("a", quality=[{"name": "code", "thin": True,
                               "exact_match": 0.1}]),
            row("b", quality=[{"name": "code", "thin": True,
                               "exact_match": 0.9}])))
        assert got == []

    def test_float_noise_is_not_movement(self):
        """A mean summed in a different order can differ in the last bits, and
        calling that non-repeatability is crying wolf about floating point."""
        got = cmp.moved_dimensions(self.outcome(
            row("a", headline={"mean_enrichment": 1.0}),
            row("b", headline={"mean_enrichment": 1.0 + 1e-15})))
        assert got == []

    def test_a_one_sided_measurement_is_not_movement(self):
        """A run that did not measure reasoning did not score the same as one
        that did - `_delta` returns None and None is not a change."""
        got = cmp.moved_dimensions(self.outcome(
            row("a", headline={"mean_enrichment": 1.0}),
            row("b")))
        assert got == []


class TestNondeterminismUsesTheWholeSurface:

    def test_identical_configs_with_moved_routing_is_flagged(self):
        """The claim that sets the floor. Previously invisible."""
        got = cmp.compare(
            row("a", routing=[expert("py", 2.0), expert("cs", 1.0)],
                headline={"mean_enrichment": 1.5}),
            row("b", routing=[expert("py", 1.0), expert("cs", 2.0)],
                headline={"mean_enrichment": 1.5}))
        assert got["attribution"] == cmp.NONE
        assert got["nondeterminism"] is True, (
            "same config, the routing distribution completely rearranged, and "
            "this reported the pipeline as repeatable")
        assert len(got["moved"]) == 2

    def test_identical_configs_and_identical_numbers_is_not_flagged(self):
        got = cmp.compare(
            row("a", routing=[expert("py", 2.0)], headline={"mean_js_bits": 1}),
            row("b", routing=[expert("py", 2.0)], headline={"mean_js_bits": 1}))
        assert got["nondeterminism"] is False
        assert got["moved"] == []

    def test_it_is_a_bool_not_a_list(self):
        """The viewer reads it as a flag; widening must not change its type."""
        got = cmp.compare(row("a"), row("b"))
        assert got["nondeterminism"] is False

    def test_a_changed_config_is_never_nondeterminism(self):
        """Different inputs producing different numbers is the pipeline
        WORKING. Only an unchanged config makes movement a repeatability
        claim."""
        got = cmp.compare(
            row("a", resolved={"lr": 1e-4}, headline={"mean_enrichment": 1.0}),
            row("b", resolved={"lr": 2e-4}, headline={"mean_enrichment": 2.0}))
        assert got["attribution"] == cmp.SINGLE
        assert got["nondeterminism"] is False
        assert len(got["moved"]) == 1


# ── the sweep ───────────────────────────────────────────────────────────────

class TestTheSweepFindsWhatIsWarranted:

    def test_a_single_knob_pair_is_found(self):
        got = cmp.sweep([
            row("a", started=1, resolved={"steps": 100},
                headline={"mean_enrichment": 1.0}),
            row("b", started=2, resolved={"steps": 200},
                headline={"mean_enrichment": 1.6}),
        ])
        found, = got["findings"]
        assert found["kind"] == cmp.SINGLE
        assert found["field"] == "steps"
        assert (found["a_value"], found["b_value"]) == (100, 200)
        assert found["moved"][0]["delta"] == pytest.approx(0.6)
        assert found["flat"] is False

    def test_a_flat_knob_is_also_a_finding(self):
        """Arguably the more useful one: it is the one you stop turning.

        An instrument that only reports movement is a movement detector.
        """
        got = cmp.sweep([
            row("a", started=1, resolved={"seed": 1},
                headline={"mean_enrichment": 1.0}),
            row("b", started=2, resolved={"seed": 2},
                headline={"mean_enrichment": 1.0}),
        ])
        found, = got["findings"]
        assert found["flat"] is True and found["moved"] == []
        assert found["field"] == "seed"

    def test_pairs_with_several_inputs_are_counted_not_listed(self):
        """Nothing here could say WHICH one moved the number, and a list of
        them would be a confidently-wrong claim generator with a nice table."""
        got = cmp.sweep([
            row("a", started=1, resolved={"steps": 100, "lr": 1e-4},
                headline={"mean_enrichment": 1.0}),
            row("b", started=2, resolved={"steps": 200, "lr": 2e-4},
                headline={"mean_enrichment": 2.0}),
        ])
        assert got["findings"] == []
        assert got["skipped"]["several_inputs"] == 1

    def test_nondeterminism_sorts_first(self):
        """It sets the floor below which no other finding here means anything.

        A knob that moved enrichment by 0.04 is not a result if rerunning the
        same config moves it by 0.09.
        """
        got = cmp.sweep([
            row("a", started=1, resolved={"steps": 100},
                headline={"mean_enrichment": 1.0}),
            row("b", started=2, resolved={"steps": 200},
                headline={"mean_enrichment": 9.0}),
            row("c", started=3, resolved={"steps": 100},
                headline={"mean_enrichment": 1.2}),
        ])
        kinds = [f["kind"] for f in got["findings"]]
        assert kinds[0] == "nondeterminism", (
            f"findings led with {kinds[0]} - the repeatability floor has to be "
            f"read before any delta above it is believed")

    def test_identical_configs_that_agree_are_not_listed(self):
        """The pipeline behaving is not a finding, and listing it would bury
        the ones that are."""
        got = cmp.sweep([
            row("a", started=1, headline={"mean_enrichment": 1.0}),
            row("b", started=2, headline={"mean_enrichment": 1.0}),
        ])
        assert got["findings"] == []
        assert got["pairs"] == 1

    def test_different_experiments_are_never_paired(self):
        """comparability already refuses to treat a 0.5B and a 7B as one
        experiment, so such a pair could never produce a finding."""
        got = cmp.sweep([
            row("a", started=1, size="0.5B", resolved={"steps": 100}),
            row("b", started=2, size="7B", resolved={"steps": 200}),
        ])
        assert got["groups"] == 2
        assert got["pairs"] == 0
        assert got["findings"] == []

    def test_a_changed_base_is_a_different_experiment(self):
        got = cmp.sweep([
            row("a", started=1, base="Qwen/A"),
            row("b", started=2, base="Qwen/B"),
        ])
        assert got["groups"] == 2 and got["pairs"] == 0

    def test_an_ungraded_run_is_counted_not_silently_dropped(self):
        """Built and never evaluated is a real state and a common one."""
        got = cmp.sweep([
            row("a", started=1, resolved={"steps": 100}),
            row("b", started=2, resolved={"steps": 200}, evaluated=False),
        ])
        assert got["findings"] == []
        assert got["skipped"]["not_evaluated"] == 1

    def test_the_pair_reads_oldest_first(self):
        """So a delta reads as a change in the direction time ran."""
        got = cmp.sweep([
            row("late", started=99, resolved={"steps": 200},
                headline={"mean_enrichment": 2.0}),
            row("early", started=1, resolved={"steps": 100},
                headline={"mean_enrichment": 1.0}),
        ])
        found, = got["findings"]
        assert (found["a"], found["b"]) == ("early", "late")
        assert found["moved"][0]["delta"] > 0


class TestByFieldIsTheInstrument:
    """Per knob, how many pairs tested it and how many moved anything.

    The difference between "run B scored better" and "target_steps is
    load-bearing and lr is provably flat in this history".
    """

    def history(self):
        return [
            row("a", started=1, resolved={"steps": 100, "lr": 1e-4},
                headline={"mean_enrichment": 1.0}),
            row("b", started=2, resolved={"steps": 200, "lr": 1e-4},
                headline={"mean_enrichment": 1.5}),
            row("c", started=3, resolved={"steps": 100, "lr": 2e-4},
                headline={"mean_enrichment": 1.0}),
        ]

    def test_a_load_bearing_knob_and_a_flat_one_are_told_apart(self):
        got = cmp.sweep(self.history())
        table = {e["field"]: e for e in got["by_field"]}
        assert table["steps"]["moved"] == 1 and table["steps"]["flat"] == 0
        assert table["lr"]["flat"] == 1 and table["lr"]["moved"] == 0

    def test_movers_sort_before_flat_knobs(self):
        got = cmp.sweep(self.history())
        assert got["by_field"][0]["field"] == "steps"

    def test_the_largest_movement_is_recorded(self):
        got = cmp.sweep(self.history())
        table = {e["field"]: e for e in got["by_field"]}
        assert table["steps"]["largest"] == pytest.approx(0.5)
        assert table["lr"]["largest"] == 0.0

    def test_nondeterminism_is_not_attributed_to_a_field(self):
        """It has no field by definition, and putting it in the table would
        credit a knob for movement nobody caused."""
        got = cmp.sweep([
            row("a", started=1, headline={"mean_enrichment": 1.0}),
            row("b", started=2, headline={"mean_enrichment": 2.0}),
        ])
        assert got["findings"][0]["kind"] == "nondeterminism"
        assert got["by_field"] == []


class TestTheSweepSaysWhatItDidNotDo:
    """A silently truncated sweep reports "nothing found" about runs it never
    looked at, which is this codebase's signature failure wearing a lab coat."""

    def test_it_reports_the_shape_of_the_work(self):
        got = cmp.sweep([row("a", started=1), row("b", started=2)])
        assert got["runs"] == 2 and got["groups"] == 1 and got["pairs"] == 1
        assert got["truncated"] is False

    def test_it_stops_at_the_cap_and_says_so(self, monkeypatch):
        monkeypatch.setattr(cmp, "SWEEP_MAX_PAIRS", 3)
        got = cmp.sweep([row(f"r{i}", started=i) for i in range(10)])
        assert got["truncated"] is True
        assert got["pairs"] == 3
        assert got["max_pairs"] == 3

    def test_an_empty_history_answers_rather_than_raising(self):
        got = cmp.sweep([])
        assert got["findings"] == [] and got["pairs"] == 0
        assert got["truncated"] is False

    def test_one_run_cannot_prove_anything_and_says_so_quietly(self):
        got = cmp.sweep([row("only")])
        assert got["pairs"] == 0 and got["findings"] == []


# ── the endpoint, against a real archive ────────────────────────────────────

class TestTheSweepEndpoint:
    """Every test above hands `sweep` dicts this file shaped. That proves the
    algorithm and nothing about whether it survives the round trip through
    sqlite, the stored manifest JSON and the eval projection the listing uses.

    A contract test proves two ends agree; it never proves either is connected.
    """

    def app(self, tmp_path, runs):
        import json as _json
        from fastapi.testclient import TestClient
        from seren_theatre.app import create_app
        from seren_theatre.config import TheatreConfig, ArchiveConfig
        from seren_theatre.archive.store import Archive

        db = tmp_path / "arc" / "archive.db"
        db.parent.mkdir(parents=True, exist_ok=True)
        arc = Archive(db)
        for i, (key, resolved, mean) in enumerate(runs):
            arc.put_surgery({
                "run_key": key, "build_id": f"b{i}", "stage": "S",
                "name": key, "run_path": str(tmp_path / "gone" / key),
                "started": float(i + 1), "finished": float(i + 2),
                "state": "finished", "ok": 1, "harvested": 1.0,
                "manifest": _json.dumps({
                    "resolved": dict({"size": "0.5B", "base": "Qwen/Q",
                                      "expert_names": ["py"]}, **resolved),
                    "build_id": f"b{i}", "knobs": {}}),
            })
            if mean is not None:
                arc.put_grading({
                    "grading_key": f"g{i}", "run_key": key, "build_id": f"b{i}",
                    "generated": 1.0, "provenance": "matches",
                    "report": _json.dumps({
                        "schema_version": 1, "build_id": f"b{i}",
                        "modes": ["routing"],
                        "routing": {"mean_enrichment": mean, "experts": []}}),
                })
        arc.close()
        cfg = TheatreConfig()
        cfg.archive = ArchiveConfig(enabled=True, dsn=f"sqlite:///{db}")
        return TestClient(create_app(cfg))

    def test_a_single_knob_finding_survives_the_round_trip(self, tmp_path):
        client = self.app(tmp_path, [
            ("r1", {"steps": 100}, 1.0),
            ("r2", {"steps": 200}, 1.75),
        ])
        got = client.get("/api/archive/sweep").json()
        assert got["runs"] == 2 and got["pairs"] == 1
        found, = got["findings"]
        assert found["field"] == "steps", (
            "the resolved config did not survive sqlite and the manifest JSON, "
            "so the sweep found nothing to attribute")
        assert found["moved"][0]["delta"] == pytest.approx(0.75)

    def test_it_reports_what_it_did_not_sweep(self, tmp_path):
        """`limit` bounds the rows read, and a shorter answer must say so."""
        client = self.app(tmp_path, [(f"r{i}", {"steps": i}, 1.0 * i)
                                     for i in range(5)])
        got = client.get("/api/archive/sweep?limit=2").json()
        assert got["runs"] == 2
        assert got["archive_total"] == 5, (
            "the sweep looked at 2 of 5 runs and the payload cannot say so - "
            "'no findings' would be indistinguishable from 'nothing to find'")

    def test_an_empty_archive_answers_rather_than_erroring(self, tmp_path):
        client = self.app(tmp_path, [])
        got = client.get("/api/archive/sweep")
        assert got.status_code == 200
        assert got.json()["findings"] == []

    def test_the_limit_is_clamped(self, tmp_path):
        client = self.app(tmp_path, [("r1", {"a": 1}, 1.0)])
        for value in ("0", "-5", "99999"):
            assert client.get(f"/api/archive/sweep?limit={value}").status_code \
                == 200

    def test_a_disabled_archive_says_so_rather_than_crashing(self, tmp_path):
        from fastapi.testclient import TestClient
        from seren_theatre.app import create_app
        from seren_theatre.config import TheatreConfig, ArchiveConfig
        cfg = TheatreConfig()
        cfg.archive = ArchiveConfig(enabled=False)
        client = TestClient(create_app(cfg), raise_server_exceptions=False)
        assert client.get("/api/archive/sweep").status_code == 503

