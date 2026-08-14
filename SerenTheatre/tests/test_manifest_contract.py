"""Pin Theatre's manifest reader against the writer's.

seren_theatre.manifest is a SECOND implementation of a format ms-moe-maker
owns, and that is deliberate: importing the writer would make a viewer depend
on a training pipeline, and Theatre's `requires` is empty on purpose. Two
implementations of one wire format is the normal cost of a protocol.

The cost of two implementations is drift, and this is where it gets paid.
Exactly the same bargain as the installer/module `--describe` parity check:
two sources that can disagree are only useful if something compares them.

Read via `ast`, never by importing the writer - importing it here would create
the dependency this whole arrangement exists to avoid, and the test would then
pass for the wrong reason.

────────────────────────────────────────────────────────────────────────────
WHY THIS FILE HAS NO NAMES IN IT

The previous version hardcoded `MsMoE/MsMoE/ms_moe_maker/manifest.py`, and it
also carried a guard whose whole job was to notice if that path went stale.
The guard checked for a directory called `MsMoE`.

So when the repo was renamed to MsMoEMaker, the finder stopped finding the
writer AND the guard stopped believing there was anything to find. Eleven
assertions went to sleep and the suite stayed green. A guard against staleness
must not be keyed on the same thing that goes stale - mine shared the finder's
assumption, so a single rename disabled both at once with one edit.

The fix is that nothing here is spelled out. The writer's identity is DERIVED
from what Theatre declares it depends on - the sole requirement of the
[stagehand] extra - and every lookup below follows from that one derived
string. A rename now updates the finder, the guard and the skip reason
together, because they all read the same declaration. There is no literal left
to forget to change.
────────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path
from typing import Optional

import pytest

from seren_theatre import manifest as mf

# The extra whose sole requirement IS the writer. This is the one name in the
# file, and it names a section of Theatre's own pyproject rather than anything
# about the other project - so renaming ms-moe-maker cannot invalidate it.
WRITER_EXTRA = "stagehand"
_HERE = Path(__file__).resolve()


def _token(name: str) -> str:
    """Collapse a name to its identity, ignoring how it happens to be spelled.

    `MsMoEMaker`, `ms-moe-maker` and `ms_moe_maker` are one project written
    three ways - repo directory, distribution, import package. Comparing on
    the collapsed token means the directory search, the module search and the
    guard all agree without any of them hardcoding a spelling.
    """
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _declared_writer() -> Optional[str]:
    """The distribution Theatre declares under [stagehand], from metadata or
    pyproject - whichever is available. Installed metadata first, because in a
    built/installed environment there may be no pyproject.toml on disk."""
    try:
        from importlib.metadata import distribution
        for req in distribution("seren-theatre").requires or []:
            if f'extra == "{WRITER_EXTRA}"' in req or \
               f"extra == '{WRITER_EXTRA}'" in req:
                return re.split(r"[<>=!~;\s\[]", req.strip(), 1)[0] or None
    except Exception:  # noqa: BLE001 - source checkout, or no metadata
        pass

    # Fallback: the source tree's own pyproject. Deliberately regex rather than
    # tomllib, which is 3.11+ while this package supports 3.10.
    for parent in _HERE.parents:
        pyproject = parent / "pyproject.toml"
        if not pyproject.is_file():
            continue
        text = pyproject.read_text(encoding="utf-8")
        block = re.search(rf"^{WRITER_EXTRA}\s*=\s*\[(.*?)\]",
                          text, re.S | re.M)
        if not block:
            continue
        first = re.search(r"[\"']([^\"']+)[\"']", block.group(1))
        if first:
            return re.split(r"[<>=!~;\s\[]", first.group(1).strip(), 1)[0] or None
    return None


WRITER_DIST = _declared_writer()
WRITER_MODULE = WRITER_DIST.replace("-", "_") if WRITER_DIST else None
WRITER_TOKEN = _token(WRITER_DIST) if WRITER_DIST else ""


def _checkout_roots() -> list[Path]:
    """Sibling directories whose name IS the writer, however it's spelled.

    Independent of where manifest.py sits inside them - that is the finder's
    problem. This answers only "is the writer's source anywhere near us",
    which is what the guard needs to know.
    """
    if not WRITER_TOKEN:
        return []
    found: list[Path] = []
    for parent in _HERE.parents:
        try:
            children = list(parent.iterdir())
        except OSError:
            continue
        found.extend(c for c in children
                     if c.is_dir() and _token(c.name) == WRITER_TOKEN)
    return found


def _installed_manifest() -> Optional[Path]:
    """The writer's manifest.py via the import system, WITHOUT importing it.

    find_spec on a top-level package resolves its location from the finders
    without executing the package body, so this stays inside the no-import
    rule. This is a genuinely separate signal from walking the filesystem:
    it works when the writer is pip-installed and no checkout is nearby.
    """
    if not WRITER_MODULE:
        return None
    try:
        spec = importlib.util.find_spec(WRITER_MODULE)
    except (ImportError, ValueError):
        return None
    for location in list(getattr(spec, "submodule_search_locations", None) or []):
        candidate = Path(location) / "manifest.py"
        if candidate.is_file():
            return candidate
    return None


def _find_writer_manifest() -> Optional[Path]:
    installed = _installed_manifest()
    if installed:
        return installed
    if not WRITER_MODULE:
        return None
    for root in _checkout_roots():
        # Bounded: the package sits a level or two down (repo/repo/pkg/), and
        # an unbounded rglob over a training repo would wander into corpora
        # and run directories holding a great many files.
        for depth in ("", "*/", "*/*/"):
            for candidate in root.glob(f"{depth}{WRITER_MODULE}/manifest.py"):
                if candidate.is_file():
                    return candidate
    return None


def _literal(node: ast.AST, env: Optional[dict] = None):
    """Evaluate a constant expression against already-seen constants.

    Three things `ast.literal_eval` alone cannot do, each of which produced a
    test that was green for the wrong reason:

    1. ARITHMETIC. The writer spells its timeout `15 * 60`, which is a BinOp,
       so literal_eval raises and the constant reads as None - and this file
       then reports a drift that does not exist. A contract test that cries
       wolf gets muted, and a muted contract test is worse than none.

    2. NAME REFERENCES. The writer spells its vocabulary
       `STATUSES = (PENDING, RUNNING, DONE, SKIPPED, FAILED, REFUSED)` - a
       tuple of NAMES, not strings. literal_eval raises on those too, so
       STATUSES never entered the constants dict at all, and
       test_no_status_exists_that_the_viewer_cannot_name did
       `writer.get("STATUSES") or ()` - an empty set, minus anything, is
       empty, so the assertion could not fail. It had never once compared the
       two vocabularies. Found by trying to break it deliberately and watching
       it stay green.

    3. TUPLES/LISTS built from either of the above.

    Module-level assignment is sequential, so by the time STATUSES is reached
    the six names it refers to are already in `env`. Still no execution, still
    no import, still nothing but the syntax tree.
    """
    env = env if env is not None else {}
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError):
        pass

    if isinstance(node, ast.Name):
        return env.get(node.id)

    if isinstance(node, (ast.Tuple, ast.List)):
        items = [_literal(e, env) for e in node.elts]
        # All-or-nothing: a half-resolved vocabulary is worse than an absent
        # one, because a partial set makes the difference-check look clean.
        if any(i is None for i in items):
            return None
        return tuple(items)

    if isinstance(node, ast.BinOp):
        left, right = _literal(node.left, env), _literal(node.right, env)
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            op = type(node.op)
            if op is ast.Mult:
                return left * right
            if op is ast.Add:
                return left + right
            if op is ast.Sub:
                return left - right
            if op is ast.Div:
                return left / right
    return None


def _constants(path: Path) -> dict:
    """Module-level constant assignments, without executing anything.

    In source order, feeding each result back in as the environment for the
    next - which is what lets a tuple of names resolve.
    """
    out: dict = {}
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                value = _literal(node.value, out)
                if value is not None:
                    out[target.id] = value
    return out


source = _find_writer_manifest()

needs_writer = pytest.mark.skipif(
    source is None,
    reason=f"{WRITER_DIST or 'the writer'} is neither installed nor checked "
           f"out nearby; the writing half of the format isn't here to compare "
           f"against.")


@pytest.fixture(scope="module")
def writer() -> dict:
    return _constants(source)


# ── the contract ────────────────────────────────────────────────────────────

@needs_writer
@pytest.mark.parametrize("name", ["MANIFEST_NAME", "SCHEMA_VERSION",
                                  "STALE_AFTER_SECONDS"])
def test_format_constants_match(writer, name):
    theirs = writer.get(name)
    ours = getattr(mf, name)
    assert theirs == ours, (
        f"{name}: {WRITER_DIST} writes {theirs!r}, seren-theatre reads "
        f"{ours!r}. The two ends of the format have drifted - a viewer that "
        f"looks for the wrong filename reports every instrumented run as "
        f"uninstrumented, silently.")


@needs_writer
@pytest.mark.parametrize("name", ["PENDING", "RUNNING", "DONE", "SKIPPED",
                                  "FAILED", "REFUSED"])
def test_status_vocabulary_matches(writer, name):
    assert writer.get(name) == getattr(mf, name), (
        f"status {name} differs between writer and reader; the viewer would "
        f"paint a state it thinks it does not recognise.")


@needs_writer
def test_no_status_exists_that_the_viewer_cannot_name(writer):
    theirs = set(writer.get("STATUSES") or ())
    ours = set(mf.STATUSES)

    # THE EMPTY SET IS NOT AGREEMENT. Without this line the comparison below
    # passes whenever STATUSES failed to parse, because nothing minus anything
    # is nothing - which is exactly how this test spent its whole life green
    # without ever comparing the two vocabularies. Assert we HAVE the thing
    # before asserting anything about it.
    assert theirs, (
        f"could not read STATUSES out of {source} - so the comparison below "
        f"would be an empty set against a full one, which always passes. The "
        f"writer has probably started spelling its vocabulary in a way "
        f"_literal cannot fold; teach it, do not leave this vacuous.")

    missing = theirs - ours
    assert not missing, (
        f"{WRITER_DIST} can emit {sorted(missing)} and seren-theatre does not "
        f"know those statuses. They would render as 'unknown' - which is "
        f"honest, but this is the moment to teach the viewer instead.")


# ── the guards on the guard ─────────────────────────────────────────────────

def test_the_writer_is_still_derivable_from_what_theatre_declares():
    """The derivation itself must work, or everything above is decoration.

    This is the assertion the old version was missing. It does not care what
    the writer is CALLED - only that Theatre still declares one under
    [stagehand] and that a module name follows from it. Rename the project as
    often as you like; this stays true. Delete the extra, or rename the extra,
    and it fails loudly instead of quietly skipping eleven assertions.
    """
    assert WRITER_DIST, (
        f"no distribution is declared under the [{WRITER_EXTRA}] extra, so "
        f"this file cannot work out whose format it is checking. Either the "
        f"extra was renamed - update WRITER_EXTRA - or stagehand lost its "
        f"only dependency, which is a bigger problem than this test.")
    assert WRITER_MODULE and WRITER_TOKEN


def test_the_skip_cannot_become_permanent_and_silent():
    """A skip that fires forever is not a test.

    Two INDEPENDENT signals that the writer is present: it imports (metadata /
    site-packages) or its checkout is a sibling on disk. If either says yes
    and the finder still came up empty, the contract check has gone blind and
    this says so - rather than the suite staying green while nothing is
    compared, which is precisely what happened last time.
    """
    roots = _checkout_roots()
    importable = WRITER_MODULE is not None and \
        importlib.util.find_spec(WRITER_MODULE) is not None
    if not roots and not importable:
        pytest.skip(f"{WRITER_DIST} is genuinely not here; nothing to assert")
    assert source is not None, (
        f"{WRITER_DIST} IS present (checkouts={[str(r) for r in roots]}, "
        f"importable={importable}) but {WRITER_MODULE}/manifest.py was not "
        f"found. The contract check is now blind.")
