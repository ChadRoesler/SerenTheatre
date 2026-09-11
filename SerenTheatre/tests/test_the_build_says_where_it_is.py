"""The build's own `started` event, and the launch-to-run link it makes.

WHAT WAS BROKEN, AND IT WAS NOT A BUG. Two facts sat on one card with nothing
joining them: the marker said what stagehand LAUNCHED, and the scan said what
was ON DISK. The marker cannot name a run directory - stagehand does not know
the run layout and is deliberately never going to learn it - so the stage
picked its run by ORDER, newest first.

Newest-wins is wrong in the case a person cares about most: a hand-run build
that finishes while a launched one is still going is newer, so it takes the
stage and the launched run disappears from the one place somebody is watching.

The build already said which directory it chose. It says so in the `started`
event, in the events file the marker already names, in a file Theatre already
opened - `_last_error_event` reads it, keeps `event == "error"`, and drops the
other nine fields. Tenth instance of the same disease: data present, computed by
the producer, parsed by nobody.

THE DISCIPLINE THIS MUST KEEP. The marker is stagehand's account and this is the
builder's. Two accounts are fine; THREE OPINIONS ABOUT WHAT A RUN IS DOING is
how a dashboard starts disagreeing with itself, and stagehand's own docstring
said so first. So the event answers where and under what, and never status.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from seren_theatre import sources as src


def events_file(directory: Path, **over) -> Path:
    """A real events JSONL, started first, as ms-moe-maker writes it."""
    payload = {"event": "started", "recipe_id": "r1", "name": "gauntlet",
               "size": "0.5B", "experts": ["python", "csharp"],
               "run_dir": str(directory / "runs" / "msmoe_run_0.5B"),
               "data_root": str(directory / "data"),
               "command": "ms-moe-builder", "cwd": str(directory),
               "env_applied": {"LD_LIBRARY_PATH": "/usr/local/cuda/lib64"},
               "agreed": True}
    payload.update(over)
    path = directory / "msmoe-build-1.jsonl"
    path.write_text(
        json.dumps(payload) + "\n"
        + json.dumps({"event": "stage", "id": "preflight",
                      "status": "done"}) + "\n",
        encoding="utf-8")
    return path


def marker(directory: Path, events: Path) -> None:
    (directory / src.DETACHED_MARKER).write_text(json.dumps({
        "schema_version": src.MARKER_SCHEMA_VERSION, "pid": 4242,
        "argv": ["ms-moe-maker", "build", "r.yaml", "--json"],
        "command_line": "ms-moe-maker build r.yaml --json",
        "cwd": str(directory), "recipe": "r.yaml", "started": 1.0,
        "log_file": None, "events_file": str(events),
        "launch": "started", "exit_code": None, "error": None,
    }), encoding="utf-8")


def a_run(root: Path, name="msmoe_run_0.5B", state="running",
          started=1000.0) -> Path:
    """`started` is a PARAMETER because order_runs sorts on it.

    The first version of this helper hardcoded it, so two runs tied and the sort
    fell through to the name tiebreak - which happened to put the launched run
    first anyway. The race the headline test claims to create did not exist, and
    a mutation that disabled the whole feature passed. Ordering has to be set
    deliberately or the test is describing a situation it never built.
    """
    run = root / "runs" / name
    run.mkdir(parents=True)
    (run / "msmoe-run.json").write_text(json.dumps({
        "schema_version": 1, "name": name, "state": state,
        "started": started,
        "finished": started + 1 if state == "finished" else None,
        "ok": True if state == "finished" else None, "stages": []}),
        encoding="utf-8")
    return run


# ── reading the event ───────────────────────────────────────────────────────

class TestReadingTheBuildsAccount:

    def test_it_finds_the_started_object(self, tmp_path):
        got = src.read_started(events_file(tmp_path))
        assert got["run_dir"].endswith("msmoe_run_0.5B")
        assert got["env_applied"] == {"LD_LIBRARY_PATH": "/usr/local/cuda/lib64"}

    def test_a_missing_file_is_none_not_an_empty_dict(self, tmp_path):
        """None means THE BUILD SAID NOTHING STRUCTURED - a real answer.

        An empty dict would read to the caller as "started, with nothing in it",
        which is a claim nobody made.
        """
        assert src.read_started(tmp_path / "nope.jsonl") is None
        assert src.read_started("") is None
        assert src.read_started(None) is None

    def test_a_file_with_no_started_event_is_none(self, tmp_path):
        path = tmp_path / "e.jsonl"
        path.write_text('{"event": "stage", "id": "x"}\n', encoding="utf-8")
        assert src.read_started(path) is None

    def test_prose_and_half_lines_are_skipped_not_repaired(self, tmp_path):
        """A build's stdout can carry anything; a half-read event is not one."""
        path = tmp_path / "e.jsonl"
        path.write_text(
            "Loading model...\n"
            '{"event": "warning", "msg": "trunca\n'
            '{"event": "started", "run_dir": "/x/y"}\n', encoding="utf-8")
        assert src.read_started(path)["run_dir"] == "/x/y"

    def test_a_directory_in_place_of_a_file_never_raises(self, tmp_path):
        assert src.read_started(tmp_path) is None

    def test_the_read_is_bounded(self, tmp_path, monkeypatch):
        """A nine-hour build has a large events file and a dashboard must never
        be the reason the box is busy."""
        path = tmp_path / "e.jsonl"
        path.write_text('{"event": "started", "run_dir": "/x"}\n'
                        + "x" * (src.STARTED_MAX_BYTES * 2), encoding="utf-8")
        seen = {}
        real = open

        def counting(*a, **k):
            fh = real(*a, **k)
            if str(a[0]).endswith("e.jsonl"):
                inner = fh.read

                def capped(n=-1):
                    seen["n"] = n
                    return inner(n)
                fh.read = capped
            return fh

        monkeypatch.setattr("builtins.open", counting)
        assert src.read_started(path) is not None
        assert seen.get("n") == src.STARTED_MAX_BYTES, (
            "the whole events file was read into memory")

    def test_the_first_started_wins(self, tmp_path):
        """stagehand stamps a fresh events file per launch, so a second
        `started` means somebody appended two runs - and the earlier one is the
        one this file's marker describes."""
        path = tmp_path / "e.jsonl"
        path.write_text('{"event": "started", "run_dir": "/first"}\n'
                        '{"event": "started", "run_dir": "/second"}\n',
                        encoding="utf-8")
        assert src.read_started(path)["run_dir"] == "/first"


# ── the inverse translation ─────────────────────────────────────────────────

class TestLocalPath:

    def test_a_builder_path_becomes_a_local_one(self):
        assert src.local_path("/mnt/nvme/msMoEMaker/runs/x",
                              Path("/mnt/spark/msMoEMaker"),
                              "/mnt/nvme/msMoEMaker") == \
            "/mnt/spark/msMoEMaker/runs/x"

    def test_it_is_the_inverse_of_builder_path(self):
        root, prefix = Path("/mnt/spark/msMoEMaker"), "/mnt/nvme/msMoEMaker"
        local = "/mnt/spark/msMoEMaker/runs/x"
        assert src.local_path(src.builder_path(local, root, prefix),
                              root, prefix) == local

    def test_no_prefix_is_the_identity(self):
        assert src.local_path("/a/b", Path("/a"), "") == "/a/b"

    def test_a_path_outside_the_prefix_is_untouched(self):
        """A forced path would be confidently wrong and would match no run."""
        assert src.local_path("/elsewhere/x", Path("/mnt/spark"),
                              "/mnt/nvme") == "/elsewhere/x"

    @pytest.mark.parametrize("value", [None, "", 0])
    def test_nothing_in_nothing_out(self, value):
        assert src.local_path(value, Path("/a"), "/b") == ""


# ── the link, which is the whole point ──────────────────────────────────────

class TestTheLaunchIsLinkedToItsRun:

    def test_the_launched_run_is_identified(self, tmp_path):
        a_run(tmp_path)
        marker(tmp_path, events_file(tmp_path))
        got = src.scan_stage("S", tmp_path, ["*.log"], [], 100)
        launched = [r for r in got["runs"] if r["launched"]]
        assert len(launched) == 1
        assert launched[0]["path"].endswith("msmoe_run_0.5B")
        assert got["launch"]["run_path"] == launched[0]["path"]

    def test_the_launched_run_takes_the_stage_over_a_newer_one(self, tmp_path):
        """THE ASSERTION THIS FEATURE EXISTS FOR.

        A hand-run build finishing later is NEWER, so newest-wins put it on the
        stage and the launched run - the one somebody is actually watching -
        vanished from the only place they were looking.
        """
        a_run(tmp_path, "msmoe_run_0.5B", state="running", started=1000.0)
        a_run(tmp_path, "handrun_7B", state="running", started=5000.0)
        marker(tmp_path, events_file(tmp_path))
        got = src.scan_stage("S", tmp_path, ["*.log"], [], 100)
        # The race has to be real, or this proves nothing: without the link,
        # newest-wins puts the hand-run build on the stage.
        assert got["runs"][0]["path"].endswith("handrun_7B"), (
            "fixture is not creating the race - the hand-run build must sort "
            "first for the launched run to have anything to win against")
        assert got["on_stage"]["path"].endswith("msmoe_run_0.5B"), (
            f"the stage drew {got['on_stage']['path']} - the build stagehand "
            f"launched lost the stage to a newer unrelated run")

    def test_a_finished_launch_hands_the_stage_back(self, tmp_path):
        """`finished` is still the ONLY state that comes off the stage.

        Without this, a launch would pin a completed card there forever.
        """
        a_run(tmp_path, "msmoe_run_0.5B", state="finished")
        marker(tmp_path, events_file(tmp_path))
        got = src.scan_stage("S", tmp_path, ["*.log"], [], 100)
        assert got["on_stage"] is None

    def test_with_no_launch_it_is_exactly_newest_wins(self, tmp_path):
        """Strictly better-informed, never different without the information."""
        a_run(tmp_path, "one", state="running")
        got = src.scan_stage("S", tmp_path, ["*.log"], [], 100)
        assert got["on_stage"]["path"].endswith("one")
        assert all(r["launched"] is False for r in got["runs"])

    def test_a_launch_whose_directory_is_not_here_links_nothing(self, tmp_path):
        """A remote build with no remote_prefix. Reported as unlinked, not
        force-matched onto whatever run happens to be present."""
        a_run(tmp_path, "msmoe_run_0.5B")
        marker(tmp_path, events_file(tmp_path,
                                     run_dir="/mnt/nvme/elsewhere/runs/q"))
        got = src.scan_stage("S", tmp_path, ["*.log"], [], 100)
        assert all(r["launched"] is False for r in got["runs"])
        assert got["launch"]["run"]["run_dir"] == "/mnt/nvme/elsewhere/runs/q"

    def test_a_remote_prefix_makes_the_link(self, tmp_path):
        """The forward translation, with the consumer it was missing."""
        a_run(tmp_path, "msmoe_run_0.5B")
        marker(tmp_path, events_file(
            tmp_path, run_dir="/mnt/nvme/msMoEMaker/runs/msmoe_run_0.5B"))
        got = src.scan_stage("S", tmp_path, ["*.log"], [], 100,
                             remote_prefix="/mnt/nvme/msMoEMaker")
        launched = [r for r in got["runs"] if r["launched"]]
        assert len(launched) == 1, (
            "remote_prefix did not translate the builder's run_dir, so a "
            "cross-box launch cannot be linked to the run it produced")

    def test_the_key_is_always_present_on_every_run(self, tmp_path):
        """A consumer must not have to tell False from "this scan is too old"."""
        a_run(tmp_path)
        for got in (src.scan_stage("S", tmp_path, ["*.log"], [], 100),):
            assert all("launched" in r for r in got["runs"])

    def test_a_build_without_json_events_still_renders(self, tmp_path):
        """The normal state for a build started by hand in a terminal."""
        a_run(tmp_path)
        marker(tmp_path, tmp_path / "never-written.jsonl")
        got = src.scan_stage("S", tmp_path, ["*.log"], [], 100)
        assert got["launch"]["run"] is None
        assert got["launch"].get("run_path") in (None, "")


class TestItNeverBecomesASecondOpinion:
    """The discipline. Two accounts of a launch is fine; a second account of
    PROGRESS is how a dashboard starts disagreeing with itself."""

    def test_the_event_feeds_no_state_or_status(self, tmp_path):
        """A started event claiming a wild state must change nothing."""
        a_run(tmp_path, state="running")
        marker(tmp_path, events_file(tmp_path, state="finished", ok=False,
                                     status="exploded"))
        got = src.scan_stage("S", tmp_path, ["*.log"], [], 100)
        run = got["on_stage"]
        assert run["state"] != "finished"
        assert got["launch"]["run"].get("status") == "exploded", (
            "the field should be readable as data")
        assert "status" not in run and "exploded" not in str(run.get("state"))
