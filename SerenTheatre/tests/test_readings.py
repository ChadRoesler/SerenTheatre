"""The two readings this viewer was getting wrong, pinned.

BOTH OF THESE ARE THE SAME BUG IN DIFFERENT CLOTHES: a file whose presence was
being read as a verdict it does not carry.

  * `.smoketest.txt` is the smoke test's LOG. The writer opens it before the
    checks that can fail, so it is sitting there for a GGUF that flunked. The
    viewer printed "smoke-tested" over exactly those models. `.smokepass.txt`
    is the proof, and the three states are kept apart here.
  * `.stagehand-run.json` records what a detached build LAUNCHED, including
    whether the child survived being started. It was written and read by
    nothing at all, so a build that died on arrival was invisible: no rung, no
    manifest, no log, and a stage card reading "No runs here yet".

Everything below is a READ. No test in this file writes into anything Theatre
is configured to watch except through tmp_path fixtures it owns, and
stageguard's invariant is untouched.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from seren_theatre import sources
from seren_theatre.app import create_app
from seren_theatre.config import StageConfig, TheatreConfig


# ── fixtures ────────────────────────────────────────────────────────────────

def _rung(root: Path, name: str = "dryrun_0.5B") -> Path:
    """A directory that looks_like_rung agrees is a rung, via its GGUF."""
    d = root / name
    d.mkdir(parents=True)
    (d / "model.gguf").write_bytes(b"\0" * 16)
    return d


def _gguf(rung: Path) -> Path:
    return rung / "model.gguf"


# ── ① the smoke test has three states ───────────────────────────────────────

def test_the_proof_is_a_pass(tmp_path):
    rung = _rung(tmp_path)
    (rung / "model.gguf.smokepass.txt").write_text("all checks passed")
    out = sources.scan_rung(rung)
    assert out["smoke"]["state"] == sources.SMOKE_PASSED
    assert out["smoketested"] is True


def test_the_log_alone_is_not_a_pass(tmp_path):
    """THE BUG, pinned. This is the state that was reported as success.

    The log is written before the checks run, so it exists for a GGUF whose
    llama-cli exited non-zero or which failed the degenerate-output check. If
    this assertion ever flips back to True, the viewer has resumed telling a
    person their broken model is fine.
    """
    rung = _rung(tmp_path)
    (rung / "model.gguf.smoketest.txt").write_text("... FAILED: degenerate output")
    out = sources.scan_rung(rung)
    assert out["smoketested"] is False
    assert out["smoke"]["state"] == sources.SMOKE_UNPROVEN
    assert out["smoke"]["log"] == "model.gguf.smoketest.txt"
    assert out["smoke"]["proof"] is None


def test_the_middle_state_is_not_called_failed(tmp_path):
    """It is 'failed or unproven', and the wording is load-bearing.

    Disk cannot tell a failed test from a run that predates the proof file, and
    an older run is the common case on any box that has been building for a
    while. Calling that state `failed` would be the same invention as calling
    it `passed`, pointed the other way.
    """
    assert sources.SMOKE_UNPROVEN != "failed"
    assert "unproven" in sources.SMOKE_UNPROVEN


def test_neither_file_is_not_run(tmp_path):
    out = sources.scan_rung(_rung(tmp_path))
    assert out["smoke"]["state"] == sources.SMOKE_NOT_RUN
    assert out["smoketested"] is False


def test_the_proof_wins_even_when_the_log_is_there(tmp_path):
    """The normal shape of a PASS: the writer wrote both files."""
    rung = _rung(tmp_path)
    (rung / "model.gguf.smoketest.txt").write_text("...")
    (rung / "model.gguf.smokepass.txt").write_text("ok")
    out = sources.scan_rung(rung)
    assert out["smoke"]["state"] == sources.SMOKE_PASSED
    assert out["smoke"]["log"] and out["smoke"]["proof"]


def test_a_rung_with_no_gguf_has_no_smoke_reading(tmp_path):
    """`None`, not a state. There is nothing to have smoke-tested."""
    d = tmp_path / "dryrun_0.5B"
    (d / "fraunkenstein_moe_untrained").mkdir(parents=True)
    (d / "fraunkenstein_moe_untrained" / "config.json").write_text("{}")
    out = sources.scan_rung(d)
    assert out["smoke"] is None and out["smoketested"] is False


# ── ② the launch marker gets a seat ─────────────────────────────────────────

def _marker(stage: Path, **over) -> Path:
    payload = {
        "schema_version": 1,
        "pid": 4242,
        "argv": ["ms-moe-maker", "build", "/r/x.yaml", "--json"],
        "command_line": "ms-moe-maker build /r/x.yaml --json",
        "cwd": str(stage),
        "recipe": "/r/x.yaml",
        "started": time.time() - 600,
        "log_file": str(stage / "msmoe-x.log"),
        "events_file": None,
        "launch": "started",
        "exit_code": None,
        "error": None,
        "log_tail": None,
    }
    payload.update(over)
    path = stage / sources.DETACHED_MARKER
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _stage(tmp_path: Path) -> Path:
    d = tmp_path / "lab"
    d.mkdir()
    return d


def _scan(stage: Path):
    return sources.scan_stage("Lab", stage, ["*.log"], ["dryrun_*"], 4096)


def test_no_marker_is_not_an_error(tmp_path):
    """The overwhelmingly common case: nobody launched from here."""
    out = _scan(_stage(tmp_path))
    assert out["launch"] is None
    assert out["launch_error"] is None


def test_a_started_launch_is_reported(tmp_path):
    stage = _stage(tmp_path)
    _marker(stage)
    L = _scan(stage)["launch"]
    assert L["launched"] is True
    assert L["launch_state"] == "started"
    assert L["pid"] == 4242
    assert L["command_line"] == "ms-moe-maker build /r/x.yaml --json"
    assert L["recipe"] == "/r/x.yaml"


def test_a_failed_launch_is_reported_with_its_dying_words(tmp_path):
    """The state the room most needs to show, because it leaves nothing else.

    A build that dies on arrival creates no rung, no manifest and often no log,
    so before this the stage card read "No runs here yet" - a true sentence
    that says nothing about why.
    """
    stage = _stage(tmp_path)
    _marker(stage, launch="failed", exit_code=2,
            error="unrecognized arguments: --log-file",
            log_tail="usage: ms-moe-maker build ...")
    L = _scan(stage)["launch"]
    assert L["launched"] is False
    assert L["exit_code"] == 2
    assert "unrecognized" in L["launch_detail"]
    assert L["log_tail"].startswith("usage:")


def test_finished_is_a_success_not_a_dead_launch(tmp_path):
    """The correction that cost the writer a 500, pinned on the reader's side.

    `ms-moe-maker build r.yaml --json --plan` resolves the config, prints its
    stages and exits 0 in about a third of a second - inside the window
    stagehand checks liveness in. "Already gone" is not "died", and the exit
    code is the whole discriminator: no successful build exits non-zero.

    So it launched (True), and the WORD survives in launch_state so the room
    can say "ran and finished immediately" rather than painting a live-looking
    panel over a pid that ended before the page loaded.
    """
    stage = _stage(tmp_path)
    _marker(stage, launch="finished", exit_code=0, error=None, log_tail=None)
    L = _scan(stage)["launch"]
    assert L["launched"] is True
    assert L["launch_state"] == "finished"
    assert L["exit_code"] == 0
    assert L["launch_detail"] is None and L["log_tail"] is None


def test_an_exit_code_is_not_by_itself_a_failure(tmp_path):
    """`exit_code` is recorded whenever the child was already GONE, which
    includes the good case. Nothing may read its mere presence as bad news."""
    stage = _stage(tmp_path)
    _marker(stage, launch="finished", exit_code=0)
    assert _scan(stage)["launch"]["launched"] is True


def test_an_unrecognised_launch_word_is_neither_true_nor_false(tmp_path):
    """A word from a newer writer is reported AS ITSELF.

    Bucketing it into `started` would be inventing a reading, and bucketing it
    into `failed` would cry wolf. The viewer paints the raw word.
    """
    stage = _stage(tmp_path)
    _marker(stage, launch="quiesced")
    L = _scan(stage)["launch"]
    assert L["launched"] is None
    assert L["launch_state"] == "quiesced"


def test_a_marker_with_no_verdict_says_it_does_not_say(tmp_path):
    """An older marker, from before the launch outcome was recorded.

    None means "the marker does not say" and must never quietly mean "fine" -
    that is the identical bug to reading a smoke LOG as a pass.
    """
    stage = _stage(tmp_path)
    _marker(stage, launch=None, error=None, exit_code=None)
    L = _scan(stage)["launch"]
    assert L["launched"] is None
    assert L["launch_state"] is None


def test_a_corrupt_marker_is_reported_not_raised(tmp_path):
    stage = _stage(tmp_path)
    (stage / sources.DETACHED_MARKER).write_text("{not json", encoding="utf-8")
    out = _scan(stage)
    assert out["launch"] is None
    assert "not readable JSON" in out["launch_error"]
    # and the rest of the stage reading is untouched
    assert out["rungs"] == [] and out["exists"] is True


def test_a_marker_that_is_not_an_object_is_reported_not_raised(tmp_path):
    stage = _stage(tmp_path)
    (stage / sources.DETACHED_MARKER).write_text("[1, 2, 3]", encoding="utf-8")
    out = _scan(stage)
    assert out["launch"] is None
    assert "not an object" in out["launch_error"]


def test_activity_is_a_mtime_and_absence_is_a_reading(tmp_path):
    """Both halves: a file that grew, and one that never appeared."""
    stage = _stage(tmp_path)
    log = stage / "msmoe-x.log"
    log.write_text("x" * 100)
    _marker(stage, events_file=str(stage / "never.jsonl"))
    L = _scan(stage)["launch"]
    roles = {f["role"]: f for f in L["files"]}
    assert roles["log"]["exists"] is True
    assert roles["log"]["size"] == 100
    assert roles["events"]["exists"] is False
    assert roles["events"]["mtime"] is None
    assert L["last_activity"] == pytest.approx(log.stat().st_mtime)
    assert L["since_activity"] >= 0


def test_nothing_in_the_launch_reading_claims_the_run_is_alive_or_dead(tmp_path):
    """The precedence rule, asserted rather than trusted to a comment.

    The marker says what was LAUNCHED. The manifest stays authoritative for
    what the run is DOING, and a second opinion about progress is how a
    dashboard starts disagreeing with itself. So: no key here is named for a
    live/dead conclusion, and `source` - the field that says which reading the
    rung display is entitled to - is not touched by any of this.
    """
    stage = _stage(tmp_path)
    _marker(stage)
    L = _scan(stage)["launch"]
    for forbidden in ("alive", "dead", "state", "status", "progress",
                      "running", "finished"):
        assert forbidden not in L, (
            f"{forbidden!r} on the launch reading invites the viewer to paint "
            f"a run state from a launch record")


def test_a_stage_that_vanished_still_answers(tmp_path):
    out = sources.scan_stage("Gone", tmp_path / "nope", ["*.log"], ["*"], 4096)
    assert out["exists"] is False
    assert out["launch"] is None and out["launch_error"] is None


# ── the marker filename is a wire format, and both ends must agree ──────────

def test_the_marker_name_matches_the_writers(tmp_path):
    """sources spells `.stagehand-run.json` out itself instead of importing it.

    Same bargain manifest.py takes with msmoe-run.json, for the same reason:
    sources is on the viewer's import graph and stagehand deliberately is not,
    so the room stays installable on a box with no build tooling. The cost of
    a second copy of a constant is drift, and this is where that cost is paid.
    """
    from seren_theatre import stagehand
    assert sources.DETACHED_MARKER == stagehand.DETACHED_MARKER


def test_every_key_this_reader_wants_is_a_key_the_writer_writes():
    """The drift check that actually bites: a RENAMED key.

    A wrong filename blanks the panel loudly (nothing renders). A renamed field
    - `log_file` -> `logfile` - blanks one row silently, which is the failure
    this codebase keeps writing tests about. stagehand publishes MARKER_KEYS;
    this asserts the reader is asking for a subset of it.

    Skipped, NAMING WHAT IT WAITS FOR, until that constant exists - a skip
    whose reason is a sentence is recoverable; a silent one is how a contract
    check goes blind.
    """
    from seren_theatre import stagehand
    keys = getattr(stagehand, "MARKER_KEYS", None)
    if keys is None:
        pytest.skip("stagehand.MARKER_KEYS does not exist yet; this check is "
                    "waiting for it, and until then the marker's field names "
                    "are unpinned in both directions")
    wanted = {"schema_version", "pid", "argv", "command_line", "cwd", "recipe",
              "started", "log_file", "events_file", "launch", "exit_code",
              "error", "log_tail"}
    assert wanted <= set(keys), (
        f"this reader asks for {sorted(wanted - set(keys))}, which the writer "
        f"does not list in MARKER_KEYS. Either it was renamed - in which case "
        f"the panel is about to go quietly half-empty - or this reader is "
        f"asking for something that was never written.")


def test_the_launch_words_are_the_writers_three():
    """started / finished / failed, and no two of them mean the same thing.

    `finished` in particular is a SUCCESS - already exited 0 inside the
    liveness window - and mapping it to False would turn `--plan` into a
    reported dead launch, which is the exact bug the writer hit going the
    other way.
    """
    assert sources._LAUNCH_WORDS["started"] is True
    assert sources._LAUNCH_WORDS["finished"] is True
    assert sources._LAUNCH_WORDS["failed"] is False


def test_every_word_the_writer_can_write_is_a_word_this_reader_maps():
    """The other half of the drift check, and the sneakier half.

    A renamed KEY blanks a row. A renamed WORD is worse: it lands in the
    unrecognised branch, which is honest but useless - the panel would report
    `launched: null` for a build it could have read perfectly. stagehand
    publishes its three as constants; this asserts none of them has drifted out
    of the mapping.

    Skipped, NAMING WHAT IT WAITS FOR, until those constants exist. A skip with
    a sentence in it is recoverable; a silent one is how a contract check goes
    blind.
    """
    from seren_theatre import stagehand
    names = ("LAUNCH_STARTED", "LAUNCH_FINISHED", "LAUNCH_FAILED")
    words = {n: getattr(stagehand, n, None) for n in names}
    missing = [n for n, w in words.items() if not isinstance(w, str)]
    if missing:
        pytest.skip(f"stagehand does not publish {', '.join(missing)} yet; "
                    f"this check is waiting for them, and until then the "
                    f"launch vocabulary is pinned only by the literals above")
    for name, word in words.items():
        assert word.lower() in sources._LAUNCH_WORDS, (
            f"stagehand.{name} is {word!r}, which this reader does not map. It "
            f"would be reported as an unrecognised word - honest, but the room "
            f"would show 'outcome not recorded' for a launch it could read.")
    # And the two that are not failures must not be mapped as failures.
    assert sources._LAUNCH_WORDS[words["LAUNCH_STARTED"].lower()] is True
    assert sources._LAUNCH_WORDS[words["LAUNCH_FINISHED"].lower()] is True
    assert sources._LAUNCH_WORDS[words["LAUNCH_FAILED"].lower()] is False


def test_reading_the_marker_did_not_drag_stagehand_onto_the_viewer(tmp_path):
    """The reason the constant above is duplicated, checked the only way it can
    be: in a subprocess, because this module imports stagehand itself."""
    code = ("import sys; import seren_theatre.sources; "
            "print('seren_theatre.stagehand' in sys.modules)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True,
                         cwd=str(Path(__file__).resolve().parent.parent))
    assert out.stdout.strip() == "False", out.stderr


# ── and it all arrives on /api/state ────────────────────────────────────────

def test_api_state_carries_both_readings(tmp_path):
    stage = _stage(tmp_path)
    rung = _rung(stage)
    (rung / "model.gguf.smoketest.txt").write_text("FAILED")
    _marker(stage, launch="failed", error="bad interpreter")

    cfg = TheatreConfig()
    cfg.stages = [StageConfig(name="Lab", path=str(stage))]
    body = TestClient(create_app(cfg)).get("/api/state").json()
    st = body["stages"][0]
    assert st["launch"]["launched"] is False
    assert st["rungs"][0]["smoketested"] is False
    assert st["rungs"][0]["smoke"]["state"] == sources.SMOKE_UNPROVEN


def test_the_viewer_pack_stopped_reading_the_log_as_a_pass():
    """The room's half of ①, checked as text because the pack is what ships."""
    js = (Path(sources.__file__).resolve().parent / "viewer" / "ui"
          / "scripts.js").read_text(encoding="utf-8")
    assert "renderSmoke" in js
    assert "smokepass" in js, "the viewer no longer names the proof file"
    assert "r.smoketested ? ' · smoke-tested'" not in js, (
        "the old boolean ternary is back: a smoke LOG is being rendered as a "
        "pass again")


# ── ② staleness: the readings, not the conclusion ──────────────────
#
# The manifest is written on STAGE TRANSITIONS and a fine-tune stage runs about
# 58 minutes, so a fifteen-minute threshold on `updated` fired on every healthy
# fine-tune - in red, claiming the process "was probably killed", eight times
# an evening on an 8-expert gauntlet. The log and events files were being
# written continuously the whole time and nothing read them.

def _activity(**ago):
    """_file_activity-shaped readings. `ago` seconds since; None = absent."""
    now = time.time()
    return [{"role": role, "path": "/lab/" + role,
             "exists": since is not None,
             "mtime": (now - since) if since is not None else None,
             "size": 1, "since": since}
            for role, since in ago.items()]


def test_a_long_stage_is_not_stalled_while_the_log_is_writing():
    """THE FALSE ALARM, pinned. 46 minutes of manifest silence and a log that
    wrote three seconds ago is a fine-tune, not a corpse."""
    q = sources.quiet_reading(time.time() - 46 * 60, _activity(log=3, events=3))
    assert q["manifest_quiet_for"] > q["after_seconds"]
    assert q["recent_write"] is True
    assert sources.activity_state("stalled", q) == "running"


def test_silence_everywhere_leaves_the_manifest_word_standing():
    q = sources.quiet_reading(time.time() - 4 * 3600, _activity(log=4 * 3600))
    assert q["recent_write"] is False
    assert sources.activity_state("stalled", q) == "stalled"


def test_no_evidence_at_all_never_downgrades_stalled():
    """An empty set is not agreement. "There is no log to read" must never
    render as "still going" - the word is only withdrawn when a file was
    positively seen to have been written."""
    q = sources.quiet_reading(time.time() - 4 * 3600, [])
    assert sources.activity_state("stalled", q) == "stalled"
    assert sources.activity_state("stalled", None) == "stalled"


def test_a_file_that_never_appeared_is_not_a_write():
    q = sources.quiet_reading(time.time() - 46 * 60, _activity(events=None))
    assert q["files"][0]["exists"] is False
    assert q["files"][0]["quiet_for"] is None
    assert q["recent_write"] is False


@pytest.mark.parametrize("state", ["running", "finished", "failed", "idle"])
def test_only_stalled_is_ever_revisited(state):
    """The log gets a vote on one word and no others. A finished run is
    finished however busy the directory looks."""
    q = sources.quiet_reading(time.time() - 4 * 3600, _activity(log=1))
    assert sources.activity_state(state, q) == state


def test_the_quiet_reading_claims_neither_alive_nor_dead():
    """The same line test_nothing_in_the_launch_reading... holds one panel
    over. A stat() cannot tell you a process died, so no key here is named for
    that conclusion; the numbers are printed and the reader concludes."""
    q = sources.quiet_reading(time.time(), _activity(log=1))
    for forbidden in ("alive", "dead", "killed", "stalled", "state", "status",
                      "running", "finished"):
        assert forbidden not in q


def test_the_evidence_is_the_marker_files_plus_the_newest_log(tmp_path):
    """activity_files reuses read_launch's machinery rather than a second copy,
    and adds the newest glob log because a HAND-RUN build drops no marker - and
    the hand-run path is the documented one."""
    stage = _stage(tmp_path)
    (stage / "msmoe-x.log").write_text("x", encoding="utf-8")
    (stage / "older.log").write_text("x", encoding="utf-8")
    _marker(stage)
    out = _scan(stage)
    files = sources.activity_files(out["launch"], [])
    assert [f["role"] for f in files] == ["log"]
    # The marker names msmoe-x.log, so the glob's newest must not be added a
    # second time under a different reading of the same file.
    logs = sorted((stage / n for n in ("msmoe-x.log", "older.log")))
    parsed = [sources.parse_run_log(p, 4096) for p in logs]
    parsed.sort(key=lambda r: r.mtime, reverse=True)
    both = sources.activity_files(out["launch"], parsed)
    assert len({f["path"] for f in both}) == len(both), "a file was counted twice"


# ── ③ one run on stage, and the rest still in the payload ───────────

def test_the_newest_run_by_manifest_started_is_the_current_one():
    rungs = [{"name": "b", "path": "/x/b", "mtime": 5.0,
              "manifest": {"started": 100.0}},
             {"name": "a", "path": "/x/a", "mtime": 9.0,
              "manifest": {"started": 900.0}}]
    assert [r["name"] for r in sources.order_rungs(rungs)] == ["a", "b"]


def test_directory_mtime_orders_a_run_with_no_manifest():
    """Scraping is a first-class reading here, so an uninstrumented run still
    has to be datable - otherwise "the most recent" would silently mean "the
    most recent instrumented one"."""
    rungs = [{"name": "b", "path": "/x/b", "mtime": 500.0, "manifest": None},
             {"name": "a", "path": "/x/a", "mtime": 100.0, "manifest": None}]
    assert sources.order_rungs(rungs)[0]["name"] == "b"


def test_ordering_is_stable_when_nothing_can_be_dated():
    rungs = [{"name": "a", "path": "/x/a", "mtime": None, "manifest": None},
             {"name": "b", "path": "/x/b", "mtime": None, "manifest": None}]
    assert [r["name"] for r in sources.order_rungs(rungs)] == ["b", "a"]
    assert sources.run_started(rungs[0]) == 0.0


def _instrumented(stage: Path, name: str, started: float, **over) -> Path:
    run = stage / name
    run.mkdir(parents=True)
    payload = {"schema_version": 1, "name": name, "started": started,
               "updated": started,
               "stages": [{"id": "preflight", "label": "Preflight",
                           "status": "done"}]}
    payload.update(over)
    (run / "msmoe-run.json").write_text(json.dumps(payload), encoding="utf-8")
    return run


def test_api_state_puts_one_run_first_and_still_carries_the_rest(tmp_path):
    """The seam. A viewer that silently hides data is the thing this codebase
    keeps fixing, so the earlier runs stay on /api/state and get COUNTED."""
    stage = _stage(tmp_path)
    _instrumented(stage, "dryrun_old", 100.0)
    _instrumented(stage, "dryrun_new", 900.0)
    cfg = TheatreConfig()
    cfg.stages = [StageConfig(name="Lab", path=str(stage))]
    st = TestClient(create_app(cfg)).get("/api/state").json()["stages"][0]
    assert st["rungs"][0]["name"] == "dryrun_new"
    assert st["current"].endswith("dryrun_new")
    assert st["earlier"] == 1
    assert len(st["rungs"]) == 2, "an earlier run was dropped from the payload"


def test_only_the_current_run_is_handed_the_stage_level_readings(tmp_path):
    """The log sits at STAGE level, so it is evidence about the run happening
    NOW. Hanging a live log's mtime on a rung that finished last Tuesday would
    be inventing a reading."""
    stage = _stage(tmp_path)
    _instrumented(stage, "dryrun_old", 100.0)
    _instrumented(stage, "dryrun_new", time.time() - 46 * 60)
    (stage / "msmoe-x.log").write_text("still going", encoding="utf-8")
    out = _scan(stage)
    assert "quiet" in out["rungs"][0]
    assert "quiet" not in out["rungs"][1]


def test_a_quiet_manifest_beside_a_live_log_is_not_reported_stalled(tmp_path):
    """End to end, on the shape that produced eight false alarms an evening."""
    stage = _stage(tmp_path)
    started = time.time() - 46 * 60
    _instrumented(stage, "dryrun_0.5B", started,
                  stages=[{"id": "finetune.python", "label": "Fine-tune python",
                           "status": "running", "started": started}])
    (stage / "msmoe-x.log").write_text("step 601/602", encoding="utf-8")
    current = _scan(stage)["rungs"][0]
    assert current["manifest"]["state"] == "stalled", (
        "the manifest-only reading is unchanged and still says what it says")
    assert current["state"] == "running"
    assert current["state_source"] == "manifest + file activity"
    assert current["quiet"]["recent_write"] is True


def test_a_run_that_really_did_go_quiet_still_says_so(tmp_path):
    stage = _stage(tmp_path)
    started = time.time() - 4 * 3600
    run = _instrumented(stage, "dryrun_0.5B", started,
                        stages=[{"id": "finetune.python",
                                 "label": "Fine-tune python",
                                 "status": "running", "started": started}])
    # The rung DIRECTORY is evidence now (sources.rung_activity), so a fixture
    # that claims four hours of silence has to actually be four hours old. It
    # was written a millisecond ago and claiming otherwise in the manifest,
    # which no real run does: the manifest write is what set the directory
    # mtime. Backdating makes the fixture model the run it is named for. The
    # assertions below are untouched.
    _backdate(run, started)
    current = _scan(stage)["rungs"][0]
    assert current["state"] == "stalled"
    assert current["state_source"] == "manifest"


# ── ④ the rung directory is evidence too ────────────────────────────────────
#
# THE LIE THIS REPLACES, and it is ② all over again: the evidence was on disk
# and nobody looked. A build run BY HAND sends stdout to the terminal, so there
# is no `*.log` in the stage and stagehand dropped no marker - activity_files
# found nothing, and a router training at 3157/4000 at 3.47 s/it was reported,
# in red, as "Nothing here has been written for 2h 42m".
#
# Meanwhile the rung directory was churning: `tmp_<expert>/checkpoint-N/`,
# `moe_trained/`, the stitch and export artifacts, all immediate children, and
# creating a subdirectory bumps its parent's mtime.
#
# THE CONSERVATISM IS UNCHANGED. This is one more source of POSITIVE evidence;
# absent evidence still leaves the manifest's word exactly where it was.


def _backdate(path: Path, when: float) -> None:
    """Age a fixture directory and everything directly in it. A test helper."""
    os.utime(path, (when, when))
    for child in path.iterdir():
        os.utime(child, (when, when))


def test_a_churning_rung_directory_is_a_reading(tmp_path):
    """THE HAND-RUN CASE, pinned. No log, no marker, and the run is alive."""
    rung = tmp_path / "dryrun_0.5B"
    (rung / "tmp_python" / "checkpoint-3157").mkdir(parents=True)
    out = sources.rung_activity(rung)
    assert out["role"] == sources.ARTIFACTS_ROLE
    assert out["exists"] is True
    assert out["since"] < 60
    assert out["entries"] == 1


def test_a_quiet_rung_directory_is_also_a_reading(tmp_path):
    rung = tmp_path / "dryrun_0.5B"
    (rung / "moe_trained").mkdir(parents=True)
    _backdate(rung, time.time() - 4 * 3600)
    out = sources.rung_activity(rung)
    assert out["exists"] is True
    assert out["since"] > 3 * 3600


def test_a_rung_directory_that_is_not_there_is_reported_not_guessed(tmp_path):
    out = sources.rung_activity(tmp_path / "never-existed")
    assert out["exists"] is False
    assert out["mtime"] is None and out["since"] is None


def test_no_rung_means_exactly_the_two_readings_there_always_were(tmp_path):
    """The default is the old behaviour, so every existing caller is unmoved."""
    stage = _stage(tmp_path)
    (stage / "msmoe-x.log").write_text("x", encoding="utf-8")
    parsed = [sources.parse_run_log(stage / "msmoe-x.log", 4096)]
    assert [f["role"] for f in sources.activity_files(None, parsed)] == ["log"]


def test_the_artifacts_reading_is_its_own_row_and_not_folded_into_the_log(tmp_path):
    stage = _stage(tmp_path)
    (stage / "msmoe-x.log").write_text("x", encoding="utf-8")
    rung = stage / "dryrun_0.5B"
    rung.mkdir()
    parsed = [sources.parse_run_log(stage / "msmoe-x.log", 4096)]
    roles = [f["role"] for f in sources.activity_files(None, parsed, rung)]
    assert roles == ["log", sources.ARTIFACTS_ROLE], (
        "the rung directory was merged into the log's reading, which would "
        "report 'the log wrote 14s ago' about a run that has no log")


def test_the_artifacts_reading_names_no_conclusion(tmp_path):
    """Same line test_the_quiet_reading_claims_neither_alive_nor_dead holds one
    reading over. A stat() on a directory cannot tell you a process is alive."""
    rung = tmp_path / "dryrun_0.5B"
    rung.mkdir()
    out = sources.rung_activity(rung)
    for forbidden in ("alive", "dead", "killed", "stalled", "state", "status",
                      "running", "finished"):
        assert forbidden not in out


def test_a_hand_run_build_with_no_log_is_no_longer_reported_stalled(tmp_path):
    """END TO END, on the exact shape the author was looking at: a manifest
    quiet for hours, no log in the stage, no marker, and checkpoints landing in
    the rung directory the whole time."""
    stage = _stage(tmp_path)
    started = time.time() - 4 * 3600
    run = _instrumented(stage, "dryrun_0.5B", started,
                        stages=[{"id": "router", "label": "Train router",
                                 "status": "running", "started": started}])
    _backdate(run, started)
    (run / "moe_trained").mkdir()          # written seconds ago, like a live run
    current = _scan(stage)["rungs"][0]
    assert current["manifest"]["state"] == "stalled", (
        "the manifest-only reading is unchanged and still says what it says")
    assert current["state"] == "running"
    assert current["state_source"] == "manifest + file activity"
    roles = [f["role"] for f in current["quiet"]["files"]]
    assert sources.ARTIFACTS_ROLE in roles


def test_the_rung_directory_is_read_one_level_deep_and_no_further(
        tmp_path, monkeypatch):
    """~45 GB of shards live under here. ONE scandir, then stat the entries.

    Counted rather than asserted in prose: a recursive walk would scale with
    the tree, and the whole promise is that this scales with the top level
    only. The README calls a dashboard that makes the box busy "an unusually
    stupid way to perturb a measurement", so this is the check that keeps it
    from becoming one.
    """
    rung = tmp_path / "dryrun_0.5B"
    for expert in ("python", "rust", "go"):
        for step in range(20):
            (rung / f"tmp_{expert}" / f"checkpoint-{step}" / "deep").mkdir(
                parents=True)
    scanned = []
    real_scandir = os.scandir
    monkeypatch.setattr(os, "scandir",
                        lambda p: (scanned.append(str(p)), real_scandir(p))[1])
    out = sources.rung_activity(rung)
    assert out["entries"] == 3, "the top level is three tmp_* directories"
    assert scanned == [str(rung)], (
        f"the reader descended into {len(scanned)} directories - something "
        f"started recursing, and this tree is 45 GB of shards")


def test_the_room_no_longer_concludes_the_process_was_killed():
    """The wording half, checked as text because the pack is what ships. One
    mtime cannot support "probably killed", and the room said it in red on
    every healthy hour-long stage."""
    js = (Path(sources.__file__).resolve().parent / "viewer" / "ui"
          / "scripts.js").read_text(encoding="utf-8")
    assert "probably killed" not in js, (
        "the viewer is drawing a conclusion no stat() can support")
    assert "renderQuiet" in js and "recent_write" in js, (
        "the viewer is no longer reading the activity evidence at all")
