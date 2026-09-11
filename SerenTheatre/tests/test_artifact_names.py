"""What a run's artifact directories are CALLED, pinned to the builder.

THE BUG THIS FILE EXISTS FOR. ms-moe-maker renamed every artifact directory
during the decomposition: `qwen_coder_*` became `specialist_*`, and the two
`fraunkenstein_*` directories became `moe_untrained` and `moe_trained`. Theatre
kept looking for the old names, so `scan_run` reported `specialists: []`,
`skeleton: False` and `final: False` for every healthy ms-moe-maker run there
has ever been, and `evaluable()` refused to offer an eval on a trained MoE
sitting right there in the directory it was reading.

Nothing looked broken. The manifest half of the reading kept working, so the
card said FINISHED in confident letters over a disk scrape of nothing at all -
which is the failure mode sources.py keeps naming: data present, not surfaced.

The comment above `_STAGES` PREDICTED this, by name, before it happened. What
was missing was never the warning; it was a test that could fail. So the class
that matters most here is TestThePinAgainstTheBuilder, which asks ms-moe-maker
what it calls its own directories and fails if Theatre disagrees. A contract
test written from Theatre's own constants can never catch a rename - it agrees
with itself by construction. This one has to ask the other end.

Everything here is a READ against tmp_path directories the test owns.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from seren_theatre import sources

# The two vocabularies, spelled out ON PURPOSE. Deriving these from _STAGES
# would make every test below agree with the table by construction, which is
# the exact mistake that let the rename land. A literal is the point.
CURRENT = {"specialists": "specialist_{expert}",
           "skeleton": "moe_untrained",
           "final": "moe_trained"}
LEGACY = {"specialists": "qwen_coder_{expert}",
          "skeleton": "fraunkenstein_moe_untrained",
          "final": "fraunkenstein_agent_final"}


def _run(tmp_path: Path, names: dict, *, experts=("python", "csharp"),
          skeleton: bool = True, final: bool = True) -> Path:
    """A run directory shaped the way one vocabulary shapes it.

    No manifest. That is deliberate: an uninstrumented run is a first-class
    thing to watch, and it is also the only case where the disk scrape is the
    ONLY reading available - so it is the case where a stale name does the most
    damage and the one worth testing.
    """
    root = tmp_path / "msmoe_run_0.5B"
    root.mkdir(parents=True, exist_ok=True)
    for expert in experts:
        d = root / names["specialists"].format(expert=expert)
        d.mkdir()
        (d / "config.json").write_text("{}", encoding="utf-8")
    if skeleton:
        d = root / names["skeleton"]
        d.mkdir()
        (d / "config.json").write_text(
            json.dumps({"expert_names": list(experts),
                        "mlp_only_layers": [0, 1]}), encoding="utf-8")
    if final:
        d = root / names["final"]
        d.mkdir()
        (d / "config.json").write_text("{}", encoding="utf-8")
    return root


class TestTheCurrentVocabularyIsRead:
    """ms-moe-maker's real directory names, as it writes them today."""

    def test_a_real_run_is_recognised_as_a_run(self, tmp_path):
        """Without this, the run does not appear in the payload AT ALL - no
        card, no logs attached, nothing. An uninstrumented ms-moe-maker run
        was invisible, which reads as "no runs here yet" over a full disk."""
        assert sources.looks_like_run(_run(tmp_path, CURRENT))

    def test_the_specialists_are_named_without_their_prefix(self, tmp_path):
        scan = sources.scan_run(_run(tmp_path, CURRENT))
        assert scan["specialists"] == ["csharp", "python"]

    def test_the_skeleton_and_the_trained_moe_are_both_seen(self, tmp_path):
        scan = sources.scan_run(_run(tmp_path, CURRENT))
        assert scan["skeleton"] is True
        assert scan["final"] is True

    def test_the_skeletons_config_is_still_read_for_the_expert_list(
            self, tmp_path):
        """The skeleton branch does more than set a boolean - it opens
        config.json for the expert names and the dense layers, and that is what
        the card prints. Deriving the marker from the table has to keep it."""
        scan = sources.scan_run(_run(tmp_path, CURRENT))
        assert scan["experts"] == ["python", "csharp"]
        assert scan["dense_layers"] == [0, 1]

    def test_a_trained_moe_is_evaluable(self, tmp_path):
        verdict = sources.evaluable(_run(tmp_path, CURRENT))
        assert verdict["ok"], verdict["reason"]
        assert verdict["path"].endswith(CURRENT["final"])


class TestTheOldVocabularyStillReads:
    """A viewer whose promise is "a stage is a directory" must not go blind on
    a run somebody built before the rename. The old names are kept for that,
    and kept means tested - otherwise they are decoration that rots."""

    def test_a_fraunkenstein_era_run_is_still_a_run(self, tmp_path):
        assert sources.looks_like_run(_run(tmp_path, LEGACY))

    def test_its_specialists_are_still_named_without_their_prefix(
            self, tmp_path):
        scan = sources.scan_run(_run(tmp_path, LEGACY))
        assert scan["specialists"] == ["csharp", "python"]

    def test_its_skeleton_and_final_are_still_seen(self, tmp_path):
        scan = sources.scan_run(_run(tmp_path, LEGACY))
        assert scan["skeleton"] is True
        assert scan["final"] is True

    def test_its_trained_moe_is_still_evaluable(self, tmp_path):
        """THE REGRESSION THAT MOTIVATED THE LOOP. evaluable() took the FIRST
        'final' row off the table, so adding the current vocabulary in front
        would have quietly un-evaluated every older run on disk. Both, or the
        fix is just the same bug pointing the other way."""
        verdict = sources.evaluable(_run(tmp_path, LEGACY))
        assert verdict["ok"], verdict["reason"]
        assert verdict["path"].endswith(LEGACY["final"])


class TestWhatIsNotAnArtifact:
    """The glob proposes; the marker decides. Unchanged by this rework, and
    worth holding still while the loop underneath it changes shape."""

    def test_a_directory_without_the_marker_does_not_count(self, tmp_path):
        root = _run(tmp_path, CURRENT, experts=(), skeleton=False,
                     final=False)
        (root / CURRENT["final"]).mkdir()          # a stitch that died halfway
        assert not sources.evaluable(root)["ok"]
        assert sources.scan_run(root)["final"] is False

    def test_a_prefix_neighbour_is_not_a_specialist(self, tmp_path):
        """`specialist_*` matches things that are not specialists, the same way
        `dryrun_*` matched `dryrun_data`. The marker is what settles it."""
        root = _run(tmp_path, CURRENT, experts=("python",), skeleton=False,
                     final=False)
        (root / "specialist_notes_scratch").mkdir()
        assert sources.scan_run(root)["specialists"] == ["python"]

    def test_the_refusal_names_every_vocabulary_it_looked_for(self, tmp_path):
        """A reader with an older run on disk needs to know which name Theatre
        wanted. A message naming only the current one is how somebody concludes
        the viewer is broken when it is their directory that is old."""
        root = _run(tmp_path, CURRENT, experts=(), skeleton=False,
                     final=False)
        reason = sources.evaluable(root)["reason"]
        assert CURRENT["final"] in reason
        assert LEGACY["final"] in reason
        assert "Build first" in reason


class TestExpertNamesComeOffTheirPrefix:
    """_expert_from does arithmetic on a glob, so it gets its own tests rather
    than being inferred from the two that happen to exercise it."""

    def test_a_trailing_star_prefix_is_removed(self):
        assert sources._expert_from("specialist_python",
                                    "specialist_*") == "python"

    def test_a_literal_pattern_leaves_the_name_alone(self):
        """A row whose pattern is a literal name has no prefix to strip, and
        guessing one off would mangle it."""
        assert sources._expert_from("moe_trained", "moe_trained") == \
            "moe_trained"

    def test_an_interior_star_is_not_treated_as_a_prefix(self):
        """`a_*_b` has no removable prefix. Half-stripping is worse than not
        stripping, so the name is reported whole."""
        assert sources._expert_from("a_x_b", "a_*_b") == "a_x_b"


class TestThePinAgainstTheBuilder:
    """THE TEST WHOSE ABSENCE LET THE RENAME LAND.

    Every other test in this file is written from Theatre's side, and none of
    them could have caught this: a fixture built from Theatre's own constants
    agrees with Theatre's own constants no matter how wrong both are. That is a
    contract test, and a contract test proves two ends agree, never that either
    is connected to anything.

    So this asks ms-moe-maker. It is the only place in the suite that imports
    the builder to read a VALUE out of it, and it skips cleanly on a plain
    viewer install, where there is no other end to ask.
    """

    @staticmethod
    def _stages():
        return pytest.importorskip(
            "ms_moe_maker.run.stages",
            reason="ms-moe-maker is not installed, so there is no builder to "
                   "ask what it names its own directories. This is a plain "
                   "viewer install; the check needs a [stagehand] one.")

    def _patterns(self, stage: str):
        return [p for name, p, _m in sources._STAGES if name == stage]

    def test_the_builders_specialist_directory_is_understood(self):
        ms = self._stages()
        real = ms.FINETUNE_ARTIFACT.format(expert="python")
        from fnmatch import fnmatch
        assert any(fnmatch(real, p)
                   for p in self._patterns(sources.SPECIALISTS_STAGE)), (
            f"ms-moe-maker writes {real!r} and no 'specialists' row in "
            f"_STAGES matches it. Theatre will report 'no specialists' for "
            f"every healthy run. Add the pattern.")

    def test_the_builders_skeleton_directory_is_understood(self):
        ms = self._stages()
        real = ms.ARTIFACTS[ms.STITCH]
        assert real in self._patterns(sources.SKELETON_STAGE), (
            f"ms-moe-maker stitches into {real!r} and no 'skeleton' row in "
            f"_STAGES names it.")

    def test_the_builders_trained_moe_is_understood_as_the_final_stage(self):
        """THE SEMANTIC HALF, and the one that matters for the eval button.

        Presence somewhere in the table is not enough: `evaluable()` asks the
        FINAL rows specifically, because eval measures the router-trained MoE.
        The builder's own skip message calls that directory "trained MoE
        (final)", so the two ends already agree on the word - this pins it.
        """
        ms = self._stages()
        real = ms.ARTIFACTS[ms.ROUTER]
        assert real in self._patterns(sources.FINAL_STAGE), (
            f"ms-moe-maker router-trains into {real!r} and no 'final' row in "
            f"_STAGES names it, so evaluable() will refuse an eval on a "
            f"finished model and the button will never appear.")

    def test_every_artifact_the_builder_declares_is_matched_somewhere(self):
        """The catch-all, so a NEW artifact row added upstream fails here
        rather than being discovered by a blank card months later."""
        ms = self._stages()
        from fnmatch import fnmatch
        every = [p for _n, p, _m in sources._STAGES]
        missing = [a for a in ms.ARTIFACTS.values()
                   if not any(fnmatch(a, p) for p in every)]
        assert not missing, (
            f"ms_moe_maker.run.stages.ARTIFACTS declares {missing}, which no "
            f"_STAGES pattern matches. Theatre cannot see these on disk.")

    def test_a_run_built_from_the_builders_own_constants_reads_whole(
            self, tmp_path):
        """END TO END, with every name quoted from the builder rather than
        typed here. If this passes, a real run is legible to the viewer."""
        ms = self._stages()
        root = tmp_path / "msmoe_run_0.5B"
        root.mkdir(parents=True)
        for expert in ("python", "csharp"):
            d = root / ms.FINETUNE_ARTIFACT.format(expert=expert)
            d.mkdir()
            (d / "config.json").write_text("{}", encoding="utf-8")
        for artifact in (ms.ARTIFACTS[ms.STITCH], ms.ARTIFACTS[ms.ROUTER]):
            d = root / artifact
            d.mkdir()
            (d / "config.json").write_text("{}", encoding="utf-8")

        assert sources.looks_like_run(root)
        scan = sources.scan_run(root)
        assert scan["specialists"] == ["csharp", "python"]
        assert scan["skeleton"] is True
        assert scan["final"] is True
        assert sources.evaluable(root)["ok"]
