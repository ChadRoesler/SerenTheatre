"""`pipeline.command` as argv: a build on another box, with no protocol.

A remote launch is not a transport problem, it is a command problem.
`resolve_command()` already returned a list and `build_argv` already appended
the verb and the recipe to it, so the whole feature is "let the configured
command be longer than one word".

THE ASSERTION THAT MATTERS MOST IS A NEGATIVE ONE. When the launcher is missing
Theatre must NOT fall back to a local ms-moe-maker. The single-path branch has
always refused to fall back because falling back describes the wrong install;
here it is worse than that - a fallback would run a nine-hour build on the
wrong machine, fill the wrong disk, and report success. So the fallback is
forbidden and this file proves it stays forbidden.

The second theme is the string/list split. A string is ONE path and is never
whitespace-split, because splitting it invents arguments nobody wrote and drags
shell quoting rules into a yaml file.
"""
from __future__ import annotations

import stat

import pytest

from seren_theatre import stagehand as sh
from seren_theatre.config import PipelineConfig, as_argv


@pytest.fixture
def launcher(tmp_path):
    """An executable file standing in for ssh."""
    path = tmp_path / "ssh"
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


@pytest.fixture(autouse=True)
def unconfigured():
    """Leave the module-level pipeline as it was, whatever a test sets."""
    before = sh._PIPELINE
    yield
    sh.configure(before)


def resolve_with(**kw):
    sh.configure(PipelineConfig(**kw))
    return sh.resolve()


# ── the coercion, which is one function for two readers ──────────────────────

class TestWhatACommandValueMeans:

    def test_a_string_is_one_argument_never_split(self):
        """`/opt/Ms MoE/bin/ms-moe-maker` is a path, not three arguments."""
        assert as_argv("/opt/Ms MoE/bin/ms-moe-maker") == \
            ["/opt/Ms MoE/bin/ms-moe-maker"]

    def test_a_list_is_argv(self):
        assert as_argv(["ssh", "spark", "/x/ms-moe-maker"]) == \
            ["ssh", "spark", "/x/ms-moe-maker"]

    def test_empty_entries_are_dropped(self):
        """`["ssh", "", "host"]` would exec with an empty argument.

        The error would then come out of ssh, several layers from the yaml that
        caused it.
        """
        assert as_argv(["ssh", "", "spark"]) == ["ssh", "spark"]

    @pytest.mark.parametrize("value", [None, "", 7, {"a": 1}, True, []])
    def test_anything_that_is_not_a_command_reads_as_unset(self, value):
        """Unset routes into the normal PATH search; a bogus argv would not."""
        assert as_argv(value) == []

    def test_a_bare_string_survives_the_reading_end_too(self, launcher):
        """THE FOOTGUN THIS EXISTS TO CLOSE.

        `list("/usr/bin/x")` is `['/','u','s','r',...]`. Every caller written
        before this field took a list passes a bare string, so resolve() has to
        coerce rather than list(), or a configured path resolves to a command
        named "/" with eleven arguments.
        """
        found = resolve_with(command=str(launcher))
        assert found.argv == [str(launcher)]
        assert found.error == ""


# ── argv resolution ─────────────────────────────────────────────────────────

class TestResolvingAnArgvCommand:

    def test_the_launcher_is_verified_and_the_rest_passed_through(self, launcher):
        found = resolve_with(command=[str(launcher), "spark128gb",
                                      "/mnt/nvme/msMoEMaker/bin/ms-moe-maker"])
        assert found.argv == [str(launcher), "spark128gb",
                              "/mnt/nvme/msMoEMaker/bin/ms-moe-maker"]
        assert "argv" in found.source

    def test_a_remote_path_is_not_checked_on_this_box(self, launcher):
        """The trailing path is on another machine.

        is_file() is what the single-path branch earns its trust with, and it
        can say nothing true about a path that only exists over ssh. Verifying
        it would make every correct remote config an error.
        """
        found = resolve_with(command=[str(launcher), "spark",
                                      "/definitely/not/here/ms-moe-maker"])
        assert found.argv and found.error == ""

    def test_a_bare_launcher_name_may_be_found_on_path(self, launcher, monkeypatch):
        monkeypatch.setenv("PATH", str(launcher.parent))
        found = resolve_with(command=["ssh", "spark", "/x/ms-moe-maker"])
        assert found.argv == [str(launcher), "spark", "/x/ms-moe-maker"]

    def test_nothing_past_the_launcher_is_tilde_expanded(self, launcher,
                                                         monkeypatch):
        """`~` in a remote argument is the REMOTE user's home.

        Expanding it here substitutes this box's home into a path that will be
        interpreted on another one - a wrong path that looks deliberate.
        """
        monkeypatch.setenv("HOME", "/home/theatrebox")
        found = resolve_with(command=[str(launcher), "spark",
                                      "~/msMoEMaker/bin/ms-moe-maker"])
        assert found.argv[-1] == "~/msMoEMaker/bin/ms-moe-maker"
        assert "/home/theatrebox" not in " ".join(found.argv)


# ── the negative assertion, which is the whole point ────────────────────────

class TestAMissingLauncherIsAnErrorNotAFallback:

    def test_a_missing_launcher_resolves_to_nothing(self, tmp_path):
        found = resolve_with(command=[str(tmp_path / "no-such-ssh"), "spark",
                                      "/x/ms-moe-maker"])
        assert not found.argv, (
            "a missing launcher resolved to SOMETHING; if that something is a "
            "local ms-moe-maker the build runs on the wrong box.")

    def test_the_error_says_the_build_would_land_on_the_wrong_machine(self,
                                                                     tmp_path):
        """The message has to name the consequence, not just the missing file.

        "no such file" invites the reader to fix the path; the thing they
        actually need to know is that Theatre refused to run it here.
        """
        found = resolve_with(command=[str(tmp_path / "no-such-ssh"), "spark",
                                      "/x/ms-moe-maker"])
        assert "no-such-ssh" in found.error
        assert "wrong machine" in found.error

    def test_it_does_not_fall_back_even_with_a_real_builder_on_path(
            self, tmp_path, monkeypatch):
        """THE MUTATION-PROOF VERSION.

        With ms-moe-maker sitting on PATH, a fallback would succeed and look
        entirely healthy. This is the arrangement that makes the refusal
        observable rather than incidental.
        """
        decoy = tmp_path / "ms-moe-maker"
        decoy.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        decoy.chmod(decoy.stat().st_mode | stat.S_IEXEC)
        monkeypatch.setenv("PATH", str(tmp_path))
        found = resolve_with(command=[str(tmp_path / "no-such-ssh"), "spark",
                                      "/x/ms-moe-maker"])
        assert not found.argv, (
            "Theatre fell back to the local builder on PATH. The operator "
            "asked for a build over a launcher; running it here spends the "
            "hours on the wrong box and reports success.")
        assert str(decoy) not in " ".join(found.argv or [])

    def test_resolve_still_never_raises(self, tmp_path):
        """The contract the whole module is built on."""
        for command in [[str(tmp_path / "gone"), "a"], ["", ""], ["ssh"]]:
            sh.configure(PipelineConfig(command=command))
            sh.resolve()          # must not raise


# ── the argv reaches the exec, which is the wiring half ─────────────────────

class TestTheArgvReachesTheBuild:

    def test_build_argv_appends_the_verb_and_recipe_after_the_launcher(
            self, launcher, tmp_path):
        """No field ships without a consumer. This is the consumer."""
        sh.configure(PipelineConfig(command=[str(launcher), "spark",
                                             "/x/ms-moe-maker"]))
        recipe = tmp_path / "recipe.yaml"
        argv = sh.build_argv(recipe)
        assert argv[:3] == [str(launcher), "spark", "/x/ms-moe-maker"], (
            "the configured argv must be the PREFIX; putting the verb before "
            "the remote command would ask ssh to run a verb.")
        assert argv[3] == sh.BUILD_VERB
        assert str(recipe) in argv
        assert "--json" in argv, (
            "--json is what puts the events JSONL on stdout, and stagehand "
            "owns the redirect - which is why the live view works with no "
            "mount at all.")

    def test_resolve_command_raises_the_named_extra_when_the_launcher_is_gone(
            self, tmp_path):
        sh.configure(PipelineConfig(command=[str(tmp_path / "gone"), "spark"]))
        with pytest.raises(sh.StagehandUnavailable) as caught:
            sh.resolve_command()
        assert "wrong machine" in str(caught.value)


class TestOneWordStaysExactlyWhatItWas:
    """A single-element argv must take the old path, unchanged.

    The regression risk of this change is entirely here: the overwhelming
    majority of installs name one file, and they must not start going through
    launcher logic that skips the is_file() verification.
    """

    def test_a_named_file_that_is_missing_is_still_an_error(self, tmp_path):
        found = resolve_with(command=[str(tmp_path / "nope" / "ms-moe-maker")])
        assert not found.argv
        assert "will not fall back" in found.error

    def test_a_named_file_that_exists_still_resolves(self, launcher):
        found = resolve_with(command=[str(launcher)])
        assert found.argv == [str(launcher)]
        assert found.source == "pipeline.command"
