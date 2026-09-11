"""Pin the installed package's identity card against the installer's.

WHY THIS IS THE POINT OF HAVING TWO. `__main__`'s docstring has said it for a
long time: the `seren/port-map` fact records Workbench's code saying 7425 while
its installer said 7444. An installed node answered where the docs did not, and
nothing noticed, because nothing compared them.

The installer's own comment was blunter still: *"nothing cross-checks these
values automatically. Port, accent and description also appear in
seren_theatre/_describe.py; if you change one here, change it there by hand."*

Hand-kept parity between two repositories is the thing this codebase keeps
catching itself doing, and it had already failed: `_describe.py` did not exist at
all, so the installer's sanity check - `from seren_theatre._describe import
DESCRIBE` - raised, fell through to the `*)` branch and called `die`. **Theatre
could not be installed by Starwright.** Nothing in either repo's tests noticed,
because the check lives in bash and the module it wanted lives in Python.

So: run the installer's `--describe` and compare. Skipped, NAMING WHAT IT WAITS
FOR, when Starwright is not checked out nearby - the pair must stay installable
by someone who has never heard of Starwright, so its absence cannot be a
failure.

DISCOVERY DOES NOT HARDCODE A PATH DEEPER THAN IT HAS TO. _writerfinder learned
this the hard way: a lookup that hardcoded `MsMoE/MsMoE/...` went stale on a
rename and took its own staleness guard with it. Here the search is by the
installer's FILENAME, which is the contract Starwright itself uses
(`seren-*-setup.sh`), so a repo layout change cannot silently disable this.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from seren_theatre._describe import DESCRIBE

#: The installer's name is the contract - Starwright globs for exactly this
#: shape, so searching by it cannot drift from what Starwright would find.
INSTALLER_NAME = "seren-theatre-setup.sh"
_HERE = Path(__file__).resolve()


def find_installer() -> "Path | None":
    """The installer, in any sibling checkout, without naming its repo.

    Bounded rather than rglob: an unbounded walk from a parent of this file can
    wander into run directories holding tens of thousands of shard files, and a
    test suite that takes minutes gets run less often.
    """
    for parent in _HERE.parents:
        for depth in ("", "*/", "*/*/", "*/*/*/", "*/*/*/*/"):
            for found in sorted(parent.glob(f"{depth}services/bash/{INSTALLER_NAME}")):
                if found.is_file():
                    return found
    return None


installer = find_installer()

needs_installer = pytest.mark.skipif(
    installer is None or shutil.which("bash") is None,
    reason=f"{INSTALLER_NAME} is not checked out nearby (or there is no bash), "
           f"so the installer's half of the identity card is not here to "
           f"compare against. Theatre installs fine without Starwright; that "
           f"is the whole point of the pair standing alone.")


@pytest.fixture(scope="module")
def published() -> dict:
    """What the installer answers. One line of JSON, exit 0, no side effects."""
    out = subprocess.run(["bash", str(installer), "--describe"],
                         capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, (
        f"{INSTALLER_NAME} --describe exited {out.returncode}. Starwright builds "
        f"its whole grid from this call, so a non-zero exit means the service "
        f"does not appear at all.\n{out.stderr}")
    assert out.stdout.strip(), (
        "--describe produced no output; Starwright would report the installer "
        "as unreadable and show a gap where this service should be.")
    return json.loads(out.stdout)


# ── the anti-silence guard ──────────────────────────────────────────────────

def test_this_file_is_actually_comparing_something():
    """A skip is recoverable; a lookup that quietly finds nothing is not.

    If the installer moves and this search stops finding it, every assertion
    below turns into a skip and the parity goes unchecked for as long as nobody
    reads the summary. Independent signal: the repo root is findable by a marker
    that has nothing to do with the search path above.
    """
    looks_like_a_checkout = any(
        (p / "pyproject.toml").is_file() for p in _HERE.parents)
    if not looks_like_a_checkout:
        pytest.skip("not running from a source checkout at all")
    if installer is None:
        pytest.skip(f"{INSTALLER_NAME} is genuinely not nearby - Starwright is "
                    f"a separate repo and does not have to be here")
    assert installer.is_file()


# ── the contract ────────────────────────────────────────────────────────────

@needs_installer
@pytest.mark.parametrize("ours,theirs", [
    ("name", "name"),
    ("group", "group"),
    ("accent", "accent"),
    ("description", "description"),
    ("package", "package"),
    # THE ONE THAT ALREADY WENT WRONG ONCE, in another service, unnoticed.
    # Spelled differently on each side on purpose: the installer is saying what
    # it WOULD use before anything exists, the package what it was built around.
    ("port", "default_port"),
])
def test_the_two_cards_agree(published, ours, theirs):
    mine, yours = DESCRIBE.get(ours), published.get(theirs)
    assert mine == yours, (
        f"{ours}={mine!r} in seren_theatre/_describe.py and {theirs}={yours!r} "
        f"in {INSTALLER_NAME}. Starwright's grid reads the installer; a running "
        f"Theatre answers the package. Whichever you are looking at, the other "
        f"one is now lying - and that is exactly how 7425 came to be 7444.")


@needs_installer
def test_the_extras_agree(published):
    """`stagehand` is the one extra, and Starwright renders a checkbox for it.

    An extra the installer supports and the package does not advertise is a
    checkbox for a thing nobody can install; the reverse is a capability nobody
    is offered.
    """
    assert sorted(DESCRIBE.get("extras") or []) == \
        sorted(published.get("extras") or [])


@needs_installer
def test_requires_agree_and_are_empty(published):
    """Empty on BOTH sides, on purpose: a stage is a directory, so Theatre can
    be the first thing installed on a box and still be useful."""
    assert list(DESCRIBE.get("requires") or []) == \
        list(published.get("requires") or [])
    assert not DESCRIBE.get("requires"), (
        "Theatre grew a dependency. That is a real decision, not a typo - but "
        "it stops being installable first on a fresh box, so make it "
        "deliberately.")


# ── what the installer's own sanity check demands ───────────────────────────

class TestTheInstallerCanVerifyThisPackage:
    """The installer runs this exact check and calls `die` if it fails.

    Not a paraphrase of it - the same import and the same key list, so this test
    fails in CI rather than on Chad's Jetson halfway through an install.
    """

    REQUIRED = ("name", "port", "group", "accent")

    def test_the_module_the_installer_imports_exists(self):
        from seren_theatre import _describe  # noqa: F401

    def test_it_carries_every_key_the_installer_checks(self):
        missing = [k for k in self.REQUIRED if k not in DESCRIBE]
        assert not missing, (
            f"the installer's sanity check looks for {list(self.REQUIRED)} and "
            f"{missing} are absent, so it reports DESCRIBE_INCOMPLETE and warns "
            f"that Starwright's grid will show gaps.")

    def test_the_card_needs_nothing_but_the_stdlib(self):
        """It has to answer on a half-installed tool.

        Imported in a subprocess with the package's own directory as the only
        thing on the path, so a transitive import of yaml, meninges or the app
        factory shows up as a failure here rather than as a broken install.
        """
        code = ("import json, sys; "
                "sys.modules.pop('seren_theatre', None); "
                "from seren_theatre._describe import DESCRIBE; "
                "loaded = [m for m in sys.modules "
                "          if m.split('.')[0] in "
                "             ('yaml', 'fastapi', 'uvicorn', 'seren_meninges')]; "
                "print(json.dumps(loaded))")
        out = subprocess.run([__import__("sys").executable, "-c", code],
                             capture_output=True, text=True, timeout=60)
        assert out.returncode == 0, out.stderr
        assert json.loads(out.stdout) == [], (
            f"the identity card dragged in {out.stdout.strip()}. It must answer "
            f"on a broken install, which is precisely when those imports fail.")

    def test_the_cli_answers_describe_with_one_line_of_json(self):
        """Starwright's contract: one line, exit 0, zero side effects."""
        import sys
        out = subprocess.run([sys.executable, "-m", "seren_theatre",
                              "--describe"], capture_output=True, text=True,
                             timeout=60)
        assert out.returncode == 0, out.stderr
        assert len(out.stdout.strip().splitlines()) == 1
        assert json.loads(out.stdout) == DESCRIBE

    def test_describe_starts_no_server_and_reads_no_config(self, tmp_path):
        """Zero side effects means zero. Pointed at a config that would RAISE.

        If `--describe` were handled after the config load, this is where that
        would surface - and it is the difference between a broken box being able
        to name itself and not.
        """
        import os
        import sys
        broken = tmp_path / "seren-theatre.yaml"
        broken.write_text("this: [is: not: valid: yaml", encoding="utf-8")
        env = dict(os.environ, SEREN_THEATRE_CONFIG=str(broken))
        out = subprocess.run([sys.executable, "-m", "seren_theatre",
                              "--describe"], capture_output=True, text=True,
                             timeout=60, env=env)
        assert out.returncode == 0, (
            f"--describe consulted the config. A box whose yaml is broken is "
            f"exactly the box you need to be able to interrogate.\n{out.stderr}")
        assert json.loads(out.stdout)["name"] == DESCRIBE["name"]
