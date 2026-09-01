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
