"""Finding runs without being told where they are.

THE BUG, TWICE, WITH THE CONFIG OPEN.

`runs` was a whitelist of globs and a whitelist is only ever wrong about the
name nobody listed. It went wrong the first time against a shipped default that
did not match `msmoe_run_{size}`, the pipeline's own output directory: install
both projects, change nothing, get an empty stage. It went wrong the second
time on a real box, where a config listing `gauntlet-runs/*` met a recipe
writing to `gauntlet-nano-runs/0.5B` - written the same evening by someone who
had read that config an hour earlier.

WHY IT IS NOT A COSMETIC MISS. `archive.harvest.harvest_stage` iterates the
runs the SCAN found. A run outside the whitelist is therefore never archived -
not late, never - and deleting its directory to reclaim forty-five gigabytes
destroys the record with nothing said. The whitelist was silently deciding what
got remembered. TestTheRunThatWasLost is the half of this file that matters.

So the default stopped being a list of names. `looks_like_run` was always the
real gate; the globs only ever bounded the walk, and the walk is now bounded by
two better things: stop at a run, because its children are artifacts, and stop
at a depth. A list still wins when someone sets one, because narrowing is a
real thing to want. A door, not a requirement.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from seren_theatre import sources
from seren_theatre.config import StageConfig

# Chad's real config, verbatim, on the night the nano run vanished.
REAL_GLOBS = ["msmoe_*", "gauntlet-runs/*"]


def run(at: Path, *, manifest: bool = True, trained: bool = True) -> Path:
    """A run the way the pipeline leaves one.

    The manifest lands in the run directory's first second - `manifest.write`
    mkdirs its own parent - so an instrumented run is identifiable immediately,
    before a single artifact exists. `trained` adds what a finished one has.
    """
    at.mkdir(parents=True, exist_ok=True)
    if manifest:
        (at / "msmoe-run.json").write_text(json.dumps(
            {"schema_version": 1, "name": at.name, "state": "finished",
             "started": 1.0, "updated": 2.0, "finished": 3.0, "ok": True,
             "stages": []}), encoding="utf-8")
    if trained:
        d = at / "moe_trained"
        d.mkdir(exist_ok=True)
        (d / "config.json").write_text("{}", encoding="utf-8")
    return at


def noise(root: Path) -> None:
    """The neighbours a real workbench has, none of which are runs."""
    for name, files in (("shard_cache", 40), ("gauntlet-data", 0),
                        ("llama.cpp", 5)):
        d = root / name
        d.mkdir(parents=True, exist_ok=True)
        for i in range(files):
            (d / f"f{i}.jsonl").write_text("x", encoding="utf-8")
    corpus = root / "gauntlet-data" / "0.5B" / "python"
    corpus.mkdir(parents=True, exist_ok=True)
    (corpus / "part-0.jsonl").write_text("x", encoding="utf-8")


class TestTheRunThatWasLost:
    """The exact scenario, with the exact config, both ways."""

    def test_the_real_config_misses_the_real_run(self, tmp_path):
        """THE BUG, REPRODUCED. Not a paraphrase of it - these are the two
        directories that were on the box and the two globs that were in the
        yaml. `gauntlet-runs/*` does not match `gauntlet-nano-runs/0.5B`."""
        stage = tmp_path / "msMoEMaker"
        run(stage / "gauntlet-runs" / "0.5B")
        run(stage / "gauntlet-nano-runs" / "0.5B")

        found = sources.resolve_runs(stage, REAL_GLOBS)
        names = {p.relative_to(stage).as_posix() for p in found}
        assert names == {"gauntlet-runs/0.5B"}, (
            "if this ever finds both, the glob semantics changed and the rest "
            "of this file is testing a bug that no longer exists")

    def test_discovery_finds_it_with_no_config_at_all(self, tmp_path):
        stage = tmp_path / "msMoEMaker"
        run(stage / "gauntlet-runs" / "0.5B")
        run(stage / "gauntlet-nano-runs" / "0.5B")
        noise(stage)

        cfg = StageConfig(name="x", path=str(stage))
        found = sources.resolve_runs(stage, cfg.runs)
        names = {p.relative_to(stage).as_posix() for p in found}
        assert names == {"gauntlet-runs/0.5B", "gauntlet-nano-runs/0.5B"}

    def test_a_name_nobody_would_ever_whitelist_is_found(self, tmp_path):
        """The point is not that the nano name is handled. It is that the name
        stopped being load-bearing."""
        stage = tmp_path / "msMoEMaker"
        run(stage / "tuesday")
        run(stage / "please-work" / "final-FINAL-v2")
        found = sources.resolve_runs(stage, [])
        assert {p.relative_to(stage).as_posix() for p in found} == {
            "tuesday", "please-work/final-FINAL-v2"}

    def test_a_run_that_has_only_just_started_is_already_visible(
            self, tmp_path):
        """No artifacts yet, and it still counts. The runner writes the
        manifest before the first stage, so a build is watchable from its first
        second - which is also when a person is most likely to go looking."""
        stage = tmp_path / "msMoEMaker"
        run(stage / "brand-new" / "0.5B", trained=False)
        found = sources.resolve_runs(stage, [])
        assert [p.relative_to(stage).as_posix() for p in found] == \
            ["brand-new/0.5B"]


class TestTheRecordItWasCosting:
    """Discovery is a retention feature. This is the half that proves it."""

    def test_an_undiscovered_run_is_never_archived(self, tmp_path):
        """WHY THE WHITELIST WAS DANGEROUS AND NOT MERELY ANNOYING.

        harvest_stage iterates the runs the scan found. With the real config,
        the nano run is not among them, so its manifest and eval report never
        reach the archive - and the moment somebody reclaims that disk the
        record is gone, with no error anywhere.
        """
        from seren_theatre.archive import harvest, store

        stage_dir = tmp_path / "msMoEMaker"
        run(stage_dir / "gauntlet-runs" / "0.5B")
        run(stage_dir / "gauntlet-nano-runs" / "0.5B")

        with store.Archive(tmp_path / "whitelisted.db") as archive:
            scanned = sources.scan_stage("S", stage_dir, ["*.log"],
                                         REAL_GLOBS, 1024)
            harvest.harvest_stage(archive, scanned)
            kept = len(archive.surgeries())
        assert kept == 1, "the whitelist decided what got remembered"

    def test_discovery_archives_every_run_on_the_disk(self, tmp_path):
        from seren_theatre.archive import harvest, store

        stage_dir = tmp_path / "msMoEMaker"
        run(stage_dir / "gauntlet-runs" / "0.5B")
        run(stage_dir / "gauntlet-nano-runs" / "0.5B")
        noise(stage_dir)

        with store.Archive(tmp_path / "discovered.db") as archive:
            scanned = sources.scan_stage("S", stage_dir, ["*.log"], [], 1024)
            harvest.harvest_stage(archive, scanned)
            kept = len(archive.surgeries())
        assert kept == 2


class TestTheWalkStaysBounded:
    """A viewer polling every five seconds must not be the busiest thing on
    the box. These are structural checks, not wall-clock ones - a timing
    assertion on shared CI is a coin flip that fails on Tuesdays."""

    def test_it_does_not_descend_into_a_run(self, tmp_path, monkeypatch):
        """A run's children are artifacts, never runs, and they are where the
        forty-five gigabytes lives. Reading them every refresh is the cost this
        whole approach has to avoid, so the prune is asserted directly.

        THE RUN HERE IS UNINSTRUMENTED ON PURPOSE, and the first draft of this
        test was not - which made it pass for the wrong reason. An instrumented
        run answers from its manifest and returns NO subdirectories, so it can
        never be descended into whether the prune exists or not; deleting the
        prune left this test green. Only a run recognised by its artifacts
        hands back children, and it is the only shape that exercises the line.
        """
        stage = tmp_path / "msMoEMaker"
        # `made`, not `run` - the factory is called `run()` since the rename and
        # a local of the same name shadows it for the whole method body.
        made = run(stage / "gauntlet-runs" / "0.5B", manifest=False)
        # A directory INSIDE the run that would itself pass the predicate.
        run(made / "moe_untrained_backup")

        seen: list[str] = []
        real = os.scandir

        def spy(path=".", *a, **kw):
            seen.append(str(path))
            return real(path, *a, **kw)

        monkeypatch.setattr(os, "scandir", spy)
        found = sources.resolve_runs(stage, [])

        assert [p.relative_to(stage).as_posix() for p in found] == \
            ["gauntlet-runs/0.5B"], "nested directory reported as a run"
        assert not any(str(made) in s and s != str(made) for s in seen), (
            f"discovery read inside the run: "
            f"{[s for s in seen if str(made) in s and s != str(made)]}")

    def test_a_manifest_answers_without_reading_the_directory(self, tmp_path):
        """One stat, not an enumeration. This is what keeps the common case -
        an instrumented run - cheap no matter how many files it holds."""
        made = run(tmp_path / "r")          # local renamed; see the note above
        for i in range(50):
            (made / f"junk{i}.bin").write_text("x", encoding="utf-8")

        calls: list[str] = []
        real = os.scandir

        import seren_theatre.sources as S
        S_os = S.os

        def spy(path=".", *a, **kw):
            calls.append(str(path))
            return real(path, *a, **kw)

        original = S_os.scandir
        try:
            S_os.scandir = spy
            assert sources.looks_like_run(made) is True
        finally:
            S_os.scandir = original
        assert calls == [], "manifest found and the directory read anyway"

    def test_depth_is_bounded_and_raisable(self, tmp_path):
        stage = tmp_path / "msMoEMaker"
        deep = stage / "a" / "b" / "c" / "d"
        run(deep)
        assert sources.resolve_runs(stage, []) == [], (
            "a run four levels down was found at the default depth of three")
        found = sources.resolve_runs(stage, [], max_depth=4)
        assert [p.relative_to(stage).as_posix() for p in found] == ["a/b/c/d"]

    def test_files_never_reach_the_pattern_matcher(self, tmp_path,
                                                   monkeypatch):
        """THE MEASUREMENT THIS APPROACH RESTS ON, pinned by counting rather
        than by a clock.

        The predicate used to fnmatch every ENTRY of every directory against
        every row of the stage table - on a realistic workbench that was
        295,200 calls and 70% of the cost of a discovery pass, which is what
        made walking the tree look too expensive to consider. A stage artifact
        is a DIRECTORY, so the dirent type settles it first and a cache full of
        files never reaches a regex.

        Deleting that check leaves the answers correct and the cost back where
        it was, which no assertion about correctness can see. Hence a counter.
        """
        seen: list[str] = []
        counted = tuple(
            (stage, (lambda n, _m=match: (seen.append(n), _m(n))[1]), marker)
            for stage, match, marker in sources._matchers())
        monkeypatch.setattr(sources, "_matchers", lambda: counted)

        cache = tmp_path / "shard_cache"
        cache.mkdir()
        # Named to MATCH a stage pattern, so the only thing keeping them away
        # from the matcher is the dirent check.
        for i in range(200):
            (cache / f"specialist_{i}.jsonl").write_text("x", encoding="utf-8")

        assert sources.looks_like_run(cache) is False
        assert seen == [], (
            f"{len(seen)} file names were pattern-matched; the dirent check "
            f"is what keeps a cache directory cheap")

    def test_the_order_is_the_same_on_two_boxes(self, tmp_path, monkeypatch):
        """Determinism is not a nicety when two people compare screens.

        Directory order is whatever the filesystem feels like, so this hands
        discovery its entries backwards and expects the answer to come back
        sorted anyway. Without that, one box's `current` run and another's are
        both right and they disagree, and nobody can reproduce either.
        """
        stage = tmp_path / "msMoEMaker"
        for name in ("c_run", "a_run", "b_run"):
            run(stage / name)

        real = sources.os.scandir

        class _Reversed:
            def __init__(self, path):
                self._path = path

            def __enter__(self):
                with real(self._path) as it:
                    return list(reversed(sorted(it, key=lambda e: e.name)))

            def __exit__(self, *exc):
                return False

        monkeypatch.setattr(sources.os, "scandir", _Reversed)
        found = sources.resolve_runs(stage, [])
        assert [p.name for p in found] == ["a_run", "b_run", "c_run"]

    def test_the_neighbours_on_a_real_workbench_are_not_runs(self, tmp_path):
        stage = tmp_path / "msMoEMaker"
        noise(stage)
        assert sources.resolve_runs(stage, []) == []


class TestAnExplicitListStillWins:
    """Mandate is not ethos. Narrowing is a real thing to want, and someone who
    has said exactly where to look has said something worth obeying."""

    def test_globs_override_discovery_entirely(self, tmp_path):
        stage = tmp_path / "msMoEMaker"
        run(stage / "keep_me")
        run(stage / "hide_me")
        found = sources.resolve_runs(stage, ["keep_*"])
        assert [p.relative_to(stage).as_posix() for p in found] == ["keep_me"]

    def test_overlapping_globs_still_yield_one_row(self, tmp_path):
        """Deduped. Two patterns matching one directory used to scan it twice,
        render it twice, count a phantom run in `earlier`, and hand harvest the
        same run twice - none of which announces itself."""
        stage = tmp_path / "msMoEMaker"
        run(stage / "msmoe_run_0.5B")
        found = sources.resolve_runs(
            stage, ["msmoe_*", "msmoe_run_*", "*_0.5B"])
        assert [p.relative_to(stage).as_posix() for p in found] == \
            ["msmoe_run_0.5B"]

    def test_the_glob_still_only_proposes(self, tmp_path):
        """The predicate is what decides, under either mode. `msmoe_*` catches
        the shared corpus root, and that has always been fine."""
        stage = tmp_path / "msMoEMaker"
        run(stage / "msmoe_run_0.5B")
        (stage / "msmoe_data").mkdir(parents=True)
        (stage / "msmoe_data" / "p0.jsonl").write_text("x", encoding="utf-8")
        found = sources.resolve_runs(stage, ["msmoe_*"])
        assert [p.relative_to(stage).as_posix() for p in found] == \
            ["msmoe_run_0.5B"]


class TestOneAnswerForEveryCaller:
    """scan_stage draws the cards and the eval endpoint decides what may be
    measured. They used to walk the tree separately."""

    def test_the_eval_endpoint_sees_what_the_cards_show(self, tmp_path):
        pytest.importorskip(
            "ms_moe_maker",
            reason="[stagehand] not installed, so Backstage does not mount")
        from seren_theatre import backstage

        stage_dir = tmp_path / "msMoEMaker"
        run(stage_dir / "gauntlet-nano-runs" / "0.5B")
        cfg = StageConfig(name="S", path=str(stage_dir))

        drawn = {r["path"] for r in
                 sources.scan_stage("S", stage_dir, ["*.log"], cfg.runs, 1024,
                                    run_depth=cfg.run_depth)["runs"]}
        evaluable = {r["path"] for r in backstage._evaluable_runs(cfg)}
        assert drawn and drawn == evaluable

    def test_looks_like_run_is_the_same_rule_discovery_uses(self, tmp_path):
        """One function, two callers. A second copy of this rule is how the
        artifact-name table drifted from the builder for months."""
        cases = [run(tmp_path / "a"), run(tmp_path / "b", manifest=False),
                 tmp_path / "c", tmp_path / "missing"]
        (tmp_path / "c").mkdir()
        for path in cases:
            assert sources.looks_like_run(path) == \
                sources._run_reading(path)[0], path
