"""Running an eval from Backstage, and the exit code that means four things.

WHY THIS FILE IS MOSTLY ABOUT ONE NUMBER. `ms-moe-maker eval` returns 2 for
"dead expert(s) found" - a finished measurement, and the entire finding - and
also for a crash inside run_eval, for "eval could not run", and (via argparse)
for a flag nobody supports. Four meanings, and exactly one of them is good
news. The build endpoint's own comment already refuses to report a legitimate
answer as a server error; for eval the temptation is worse, because the
legitimate answer is the one that looks most like a failure.

So the endpoint does not read a status off the number. It asks whether a
measurement HAPPENED - a report on disk, newer than the launch - and lets the
code refine a finding only once the evidence says there is one. Every test
below is a row of that matrix, run against a real forked process so the exit
codes and the timing are the real ones.
"""
from __future__ import annotations

import os
import stat
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# THE GATE, AND THIS FILE HAS TO ASK ABOUT IT BEFORE IMPORTING BACKSTAGE.
#
# seren_theatre/backstage.py imports ms_moe_maker at module scope on purpose:
# stagehand.py ships in the base wheel, so `try: from .backstage import router`
# in app.py always succeeded and a PLAIN VIEWER mounted the write routes. The
# builder import is the only thing that actually tells the two install shapes
# apart, and the import IS the assertion.
#
# Which means importing backstage from a test is importing the builder, and on
# a plain-viewer checkout that is an ImportError that takes the whole
# COLLECTION down - not one skipped module, the entire run. test_backstage.py
# has asked this question since it was written; this file was added without it
# and CI found out first, because the container it was developed in happened to
# have the builder installed. One install shape tested, two shipped.
pytest.importorskip(
    "ms_moe_maker",
    reason="[stagehand] not installed, so Backstage does not mount and there "
           "is no eval endpoint to test")

from seren_theatre import backstage, manifest, sources, stagehand  # noqa: E402
from seren_theatre.app import create_app  # noqa: E402
from seren_theatre.config import StageConfig, TheatreConfig  # noqa: E402
from seren_theatre.evalreport import EVAL_REPORT_NAME  # noqa: E402

RUN = "msmoe_run_0.5B"

# THE TRAINED-MOE DIRECTORY, taken from the table rather than typed. Typing it
# is what made this file lie: it said "fraunkenstein_agent_final", a directory
# ms-moe-maker stopped writing during the decomposition, so every test in here
# passed against a shape that no real run produces. The fixture and the code
# agreed with each other and neither agreed with the builder.
#
# tests/test_artifact_names.py is what actually pins the vocabulary to
# ms_moe_maker.run.stages; this just stops the eval tests inventing a third.
FINAL = next(p for name, p, _m in sources._STAGES
             if name == sources.FINAL_STAGE)


def _stage_with_a_built_model(tmp_path: Path, *, built: bool = True) -> Path:
    stage = tmp_path / "stage"
    run = stage / RUN
    run.mkdir(parents=True)
    # looks_like_run() wants a manifest or run-shaped artifacts - and the
    # MANIFEST NAME, not a plausible-looking one. This wrote
    # "run_manifest.json", which Theatre does not read, so with built=False the
    # run failed looks_like_run entirely and the 409 test below was passing
    # for "no such run" rather than for "nothing to evaluate here". Two
    # different refusals wearing one status code, in my own test.
    (run / manifest.MANIFEST_NAME).write_text("{}", encoding="utf-8")
    if built:
        final = run / FINAL
        final.mkdir()
        (final / "config.json").write_text("{}", encoding="utf-8")
    return stage


def _fake_builder(venv: Path, body: str) -> None:
    venv.mkdir(parents=True, exist_ok=True)
    script = venv / "ms-moe-maker"
    script.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP)


def _venv(tmp_path: Path) -> Path:
    """Where the fake builder lives: `<venv>/bin`, which is where stagehand
    looks (see _venv_candidates - it stays INSIDE the venv named, deliberately,
    rather than falling back to the system python and running a different
    install while reporting the config was honoured).

    Computed rather than read back off the config: cfg.pipeline is a
    PipelineConfig object, not the dict it was built from.
    """
    return tmp_path / "venv" / "bin"


def _cfg(tmp_path: Path, stage: Path) -> TheatreConfig:
    recipes = tmp_path / "recipes"
    recipes.mkdir(exist_ok=True)
    (recipes / "r.yaml").write_text("name: x\n", encoding="utf-8")
    cfg = TheatreConfig(archive={"dsn": str(tmp_path / "a.db")},
                        pipeline={"venv": str(tmp_path / "venv")},
                        recipes=str(recipes))
    cfg.stages = [StageConfig(name="S", path=str(stage))]
    return cfg


def _post(cfg, body: dict):
    stagehand.configure(cfg.pipeline)
    with TestClient(create_app(cfg), raise_server_exceptions=False) as c:
        return c.post("/api/backstage/eval", json=body)


# ── is there anything to measure ────────────────────────────────────────────

class TestWhatCountsAsSomethingToEvaluate:
    """ASK THE DISK, NOT THE HISTORY.

    "Did a build finish successfully" is the obvious gate and wrong three ways:
    a manifest rotates and a model sitting right there becomes un-evaluable; a
    build that exited non-zero for want of llama.cpp has a perfectly evaluable
    MoE; and a success whose output directory moved offers a button that only
    409s. eval's own CLI asks whether the trained MoE is on disk, so this asks
    the same question from the same table the run scan reads.
    """

    def test_a_trained_moe_is_evaluable(self, tmp_path):
        stage = _stage_with_a_built_model(tmp_path)
        assert sources.evaluable(stage / RUN)["ok"]

    def test_a_run_with_no_moe_is_not_and_says_why(self, tmp_path):
        stage = _stage_with_a_built_model(tmp_path, built=False)
        verdict = sources.evaluable(stage / RUN)
        assert not verdict["ok"]
        assert FINAL in verdict["reason"]
        assert "Build first" in verdict["reason"]

    def test_a_directory_without_the_marker_does_not_count(self, tmp_path):
        """The directory existing is not the model existing - a stitch that
        died halfway leaves the folder and no config.json."""
        stage = _stage_with_a_built_model(tmp_path, built=False)
        (stage / RUN / FINAL).mkdir()
        assert not sources.evaluable(stage / RUN)["ok"]

    def test_nothing_to_evaluate_is_a_409_that_explains_itself(self, tmp_path):
        stage = _stage_with_a_built_model(tmp_path, built=False)
        cfg = _cfg(tmp_path, stage)
        _fake_builder(_venv(tmp_path), "exit 0\n")
        r = _post(cfg, {"name": "r.yaml"})
        assert r.status_code == 409
        detail = str(r.json()["detail"])
        assert "Build first" in detail
        # WHICH REFUSAL, not just that one happened. Both 409 branches end with
        # "Build first." - the stage-level one and evaluable()'s - so asserting
        # only that phrase cannot tell "this stage has no built run" from "the
        # run you named has no MoE". That is the overloaded-exit-code problem
        # wearing a status message, and this test was sitting on the ambiguity.
        assert stage.name in detail

    def test_a_named_run_that_is_not_built_names_itself(self, tmp_path):
        stage = _stage_with_a_built_model(tmp_path, built=False)
        cfg = _cfg(tmp_path, stage)
        _fake_builder(_venv(tmp_path), "exit 0\n")
        r = _post(cfg, {"name": "r.yaml", "run": RUN})
        assert r.status_code == 409
        detail = str(r.json()["detail"])
        assert RUN in detail
        # THE OTHER BRANCH, pinned by what only it can say: this path surfaces
        # evaluable()'s reason, which names the directory eval measures. The
        # stage-level refusal never mentions it.
        assert FINAL in detail

    def test_a_run_built_the_way_the_builder_builds_one_is_accepted(
            self, tmp_path):
        """THE WIRING TEST THIS FILE DID NOT HAVE.

        Every other test here proves the endpoint agrees with `_stage_with_a_
        built_model`, and that fixture used to spell the trained-MoE directory
        by hand as `fraunkenstein_agent_final` - a name ms-moe-maker stopped
        writing during the decomposition. So the whole class agreed with a
        shape no real run produces, and the eval button would have refused
        every genuine build on Chad's box while the suite sat green.

        This builds the run from ms_moe_maker's OWN constants and asserts the
        endpoint launches. It skips on a plain viewer install, where there is
        no builder to quote.
        """
        ms = pytest.importorskip(
            "ms_moe_maker.run.stages",
            reason="ms-moe-maker is not installed, so there is no builder to "
                   "quote the real artifact names from")
        stage = tmp_path / "stage"
        run = stage / RUN
        run.mkdir(parents=True)
        (run / manifest.MANIFEST_NAME).write_text("{}", encoding="utf-8")
        final = run / ms.ARTIFACTS[ms.ROUTER]
        final.mkdir()
        (final / "config.json").write_text("{}", encoding="utf-8")

        cfg = _cfg(tmp_path, stage)
        _fake_builder(_venv(tmp_path), "sleep 5\n")
        r = _post(cfg, {"name": "r.yaml"})
        assert r.status_code == 200, r.json()
        assert r.json()["run"] == RUN


# ── the argv ────────────────────────────────────────────────────────────────

class TestItRunsTheEvalVerbAndInventsNoFlags:
    def test_the_verb_is_eval_not_build(self, tmp_path, monkeypatch):
        seen = {}

        def spy(recipe, **kw):
            seen.update(kw)
            return {"launch": stagehand.LAUNCH_STARTED, "pid": 1,
                    "exit_code": None, "started": time.time(),
                    "command_line": "", "argv": []}

        monkeypatch.setattr(stagehand, "run_detached", spy)
        stage = _stage_with_a_built_model(tmp_path)
        cfg = _cfg(tmp_path, stage)
        _fake_builder(_venv(tmp_path), "exit 0\n")
        assert _post(cfg, {"name": "r.yaml"}).status_code == 200
        assert seen["verb"] == stagehand.EVAL_VERB
        assert seen["verb"] == "eval"

    def test_it_asks_not_to_be_raised_at(self, tmp_path, monkeypatch):
        """A fast non-zero may be the finding, so the endpoint takes facts."""
        seen = {}
        monkeypatch.setattr(
            stagehand, "run_detached",
            lambda recipe, **kw: (seen.update(kw) or
                                  {"launch": stagehand.LAUNCH_STARTED,
                                   "pid": 1, "exit_code": None,
                                   "started": time.time(),
                                   "command_line": "", "argv": []}))
        stage = _stage_with_a_built_model(tmp_path)
        cfg = _cfg(tmp_path, stage)
        _fake_builder(_venv(tmp_path), "exit 0\n")
        _post(cfg, {"name": "r.yaml"})
        assert seen["raise_on_early_exit"] is False

    def test_the_mode_reaches_the_argv_only_when_asked_for(self, tmp_path):
        stage = _stage_with_a_built_model(tmp_path)
        cfg = _cfg(tmp_path, stage)
        # The fake records its own argv, which is the only witness that cannot
        # be faked by the code under test.
        _fake_builder(_venv(tmp_path),
                      'echo "$@" > "%s/argv.txt"\nexit 0\n' % stage)
        _post(cfg, {"name": "r.yaml", "mode": "routing"})
        argv = (stage / "argv.txt").read_text(encoding="utf-8")
        assert "eval" in argv and "--mode routing" in argv

        _post(cfg, {"name": "r.yaml"})
        argv = (stage / "argv.txt").read_text(encoding="utf-8")
        assert "--mode" not in argv, (
            "no mode asked for, so the recipe's eval.mode must decide - "
            "Theatre does not invent a default the recipe has an opinion about")

    def test_an_unknown_mode_is_refused_before_it_reaches_argparse(self, tmp_path):
        """AND THIS IS NOT PEDANTRY. argparse exits 2 on an unknown flag value,
        and 2 is also what "dead expert(s) found" returns. A typo would come
        back looking exactly like the finding this endpoint exists to surface.
        """
        stage = _stage_with_a_built_model(tmp_path)
        cfg = _cfg(tmp_path, stage)
        _fake_builder(_venv(tmp_path), "exit 0\n")
        r = _post(cfg, {"name": "r.yaml", "mode": "rooting"})
        assert r.status_code == 422
        assert "routing" in str(r.json()["detail"])


# ── the matrix ──────────────────────────────────────────────────────────────

def _writes_report_then_exits(code: int, run: Path) -> str:
    """A builder that measures something and then exits `code`.

    The report is written BEFORE the exit, which is what the real one does and
    the reason evidence is trustworthy: "PERSIST BEFORE PRINTING, and the order
    is the point."
    """
    return ('echo "{}" > "%s"\nexit %d\n'
            % ((run / EVAL_REPORT_NAME), code))


@pytest.fixture(autouse=True)
def _quick_settle(monkeypatch):
    """The launch window, shortened. Three seconds per case is a slow suite and
    the window's LENGTH is not what any of these tests is about."""
    monkeypatch.setattr(stagehand, "LAUNCH_SETTLE_SECONDS", 0.6)


class TestAnExitCodeIsNotAStatus:
    """The four meanings of 2, told apart by evidence."""

    def _run(self, tmp_path, body: str, *, post=None):
        stage = _stage_with_a_built_model(tmp_path)
        cfg = _cfg(tmp_path, stage)
        _fake_builder(_venv(tmp_path), body)
        return stage, _post(cfg, post or {"name": "r.yaml"})

    def test_dead_experts_is_a_RESULT_not_a_server_error(self, tmp_path):
        """Exit 2 with a report is the finding: 7 of 9 own their ground and two
        do not. Reporting that as 500 would make the operator's log say ERROR
        and point at Theatre, when the thing to look at is the router."""
        stage = _stage_with_a_built_model(tmp_path)
        cfg = _cfg(tmp_path, stage)
        _fake_builder(_venv(tmp_path),
                      _writes_report_then_exits(2, stage / RUN))
        r = _post(cfg, {"name": "r.yaml"})
        assert r.status_code == 200, r.json()
        body = r.json()
        assert body["measured"] is True
        assert body["finding"] == "dead experts"

    def test_unmeasurable_is_also_a_result(self, tmp_path):
        stage = _stage_with_a_built_model(tmp_path)
        cfg = _cfg(tmp_path, stage)
        _fake_builder(_venv(tmp_path),
                      _writes_report_then_exits(3, stage / RUN))
        r = _post(cfg, {"name": "r.yaml"})
        assert r.status_code == 200
        assert r.json()["finding"] == "unmeasurable"

    def test_exit_2_with_NO_report_is_breakage(self, tmp_path):
        """Same number, opposite meaning: argparse on a bad flag, or a crash
        inside run_eval. Nothing was measured, so nothing is reported as a
        measurement."""
        _, r = self._run(tmp_path, "echo 'unrecognized arguments' >&2\nexit 2\n")
        assert r.status_code == 500
        detail = r.json()["detail"]
        assert detail["measured"] is False
        assert detail["exit_code"] == 2

    def test_a_STALE_report_does_not_vouch_for_this_run(self, tmp_path):
        """THE REASSURING-DIRECTION FAILURE, and the one worth a test of its own.

        A run evaluated last week has a report in it. Without a freshness test
        that report would make today's argparse error look like a successful
        measurement - wrong, and wrong in the direction nobody checks.
        """
        stage = _stage_with_a_built_model(tmp_path)
        stale = stage / RUN / EVAL_REPORT_NAME
        stale.write_text("{}", encoding="utf-8")
        old = time.time() - 7 * 24 * 3600
        os.utime(stale, (old, old))
        cfg = _cfg(tmp_path, stage)
        _fake_builder(_venv(tmp_path), "exit 2\n")
        r = _post(cfg, {"name": "r.yaml"})
        assert r.status_code == 500, (
            "a week-old report was accepted as evidence for this run")
        assert r.json()["detail"]["measured"] is False

    def test_a_recipe_that_will_not_load_is_a_409(self, tmp_path):
        """Exit 1 is the recipe refusing, before any measuring. A conflict with
        what this box can do, not a Theatre fault - the same reading the build
        endpoint already gives it."""
        _, r = self._run(tmp_path, "echo 'bad recipe' >&2\nexit 1\n")
        assert r.status_code == 409
        assert r.json()["detail"]["exit_code"] == 1

    def test_a_clean_fast_exit_is_fine(self, tmp_path):
        """`--plan`-shaped: does its job and is gone before the window ends."""
        _, r = self._run(tmp_path, "exit 0\n")
        assert r.status_code == 200

    def test_a_run_that_outlives_the_window_is_just_running(self, tmp_path):
        _, r = self._run(tmp_path, "sleep 5\n")
        assert r.status_code == 200
        assert r.json()["launch"] == stagehand.LAUNCH_STARTED
        assert r.json()["run"] == RUN

    def test_the_failure_always_carries_its_reason(self, tmp_path):
        """A status code with no explanation only moves the silence."""
        _, r = self._run(tmp_path, "echo 'the log says this' >&2\nexit 2\n")
        detail = r.json()["detail"]
        assert detail["command_line"]
        assert "the log says this" in (detail["log_tail"] or "")


class TestTheEvidenceCheckOnItsOwn:
    def test_no_report_is_no_evidence(self, tmp_path):
        assert not backstage._eval_left_a_reading(tmp_path, since=0.0)

    def test_a_report_newer_than_the_launch_counts(self, tmp_path):
        (tmp_path / EVAL_REPORT_NAME).write_text("{}", encoding="utf-8")
        assert backstage._eval_left_a_reading(tmp_path, since=time.time() - 60)

    def test_a_report_older_than_the_launch_does_not(self, tmp_path):
        path = tmp_path / EVAL_REPORT_NAME
        path.write_text("{}", encoding="utf-8")
        old = time.time() - 3600
        os.utime(path, (old, old))
        assert not backstage._eval_left_a_reading(tmp_path, since=time.time())

    def test_it_does_not_reach_for_the_streaming_sidecar(self):
        """evalrecord reads a per-item sidecar whose format is pinned at both
        ends and which NOTHING writes - see UNWIRED in
        test_nothing_is_built_and_unwired. Reaching for it here would always
        find nothing while looking like the streaming case was handled, and
        would delete an honest exemption to do it.

        Asserted on the IMPORTS, not on the function's prose - the first
        version of this test matched its own explanatory docstring, which is a
        test that passes because of what it says about itself.
        """
        assert not hasattr(backstage, "evalrecord"), (
            "backstage imports evalrecord again - if the harness now streams, "
            "wire it properly and delete the UNWIRED entry; if it does not, "
            "this is a use that finds nothing")
        assert hasattr(backstage, "evalreport")
