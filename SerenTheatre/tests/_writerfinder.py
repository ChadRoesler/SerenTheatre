"""Finding the writing half of a wire format, without naming it.

Shared by every contract test - the run manifest, the eval sidecar, and
whatever comes next. It is one module rather than a copied block for the exact
reason the manifest contract test exists at all: two copies of a lookup drift,
and a drifting lookup fails OPEN. It stops finding things and the suite goes
green.

THE HISTORY, because it is the argument for the design:

The first version of this logic was inline in test_manifest_contract.py and
hardcoded `MsMoE/MsMoE/ms_moe_maker/manifest.py`. It also carried a guard whose
whole job was to notice if that path went stale - and the guard checked for a
directory called `MsMoE`. So when the project was renamed to MsMoEMaker, one
edit disabled both the finder AND the thing watching the finder. Eleven
assertions went to sleep and nothing went red.

Hence: NO NAMES. The writer's identity is derived from what SerenTheatre
declares it depends on - the sole requirement of the [stagehand] extra - and
every lookup follows from that one derived string. Rename the project and the
finder, the guards and the skip reasons all move together, because they are
all reading the same declaration.
"""
from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path
from typing import Dict, List, Optional

# The one name here, and it names a section of SerenTheatre's OWN pyproject
# rather than anything about the other project - so renaming the writer cannot
# invalidate it.
WRITER_EXTRA = "stagehand"
_HERE = Path(__file__).resolve()


def token(name: str) -> str:
    """Collapse a name to its identity, ignoring how it is spelled.

    `MsMoEMaker`, `ms-moe-maker` and `ms_moe_maker` are one project written
    three ways - repo directory, distribution, import package. Comparing on the
    collapsed token lets the directory search, the module search and the guards
    agree without any of them hardcoding a spelling.
    """
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _declared_writer() -> Optional[str]:
    """The distribution declared under [stagehand], from metadata or pyproject."""
    try:
        from importlib.metadata import distribution
        for req in distribution("seren-theatre").requires or []:
            if f'extra == "{WRITER_EXTRA}"' in req or \
               f"extra == '{WRITER_EXTRA}'" in req:
                return re.split(r"[<>=!~;\s\[]", req.strip(), 1)[0] or None
    except Exception:  # noqa: BLE001 - source checkout, or no metadata
        pass

    # Fallback: the source tree's own pyproject. Regex rather than tomllib,
    # which is 3.11+ while this package supports 3.10.
    for parent in _HERE.parents:
        pyproject = parent / "pyproject.toml"
        if not pyproject.is_file():
            continue
        block = re.search(rf"^{WRITER_EXTRA}\s*=\s*\[(.*?)\]",
                          pyproject.read_text(encoding="utf-8"), re.S | re.M)
        if not block:
            continue
        first = re.search(r"[\"']([^\"']+)[\"']", block.group(1))
        if first:
            return re.split(r"[<>=!~;\s\[]", first.group(1).strip(), 1)[0] or None
    return None


WRITER_DIST = _declared_writer()
WRITER_MODULE = WRITER_DIST.replace("-", "_") if WRITER_DIST else None
WRITER_TOKEN = token(WRITER_DIST) if WRITER_DIST else ""


def checkout_roots() -> List[Path]:
    """Sibling directories whose name IS the writer, however it is spelled."""
    if not WRITER_TOKEN:
        return []
    found: List[Path] = []
    for parent in _HERE.parents:
        try:
            children = list(parent.iterdir())
        except OSError:
            continue
        found.extend(c for c in children
                     if c.is_dir() and token(c.name) == WRITER_TOKEN)
    return found


def is_importable() -> bool:
    """A signal INDEPENDENT of the filesystem walk - metadata / site-packages.

    Two unrelated ways to know the writer is present is what makes the
    anti-silence guard trustworthy. The old one shared its only signal with the
    finder it was guarding, so they went blind together.
    """
    if not WRITER_MODULE:
        return False
    try:
        return importlib.util.find_spec(WRITER_MODULE) is not None
    except (ImportError, ValueError):
        return False


def _installed(basename: str) -> Optional[Path]:
    """`basename` inside the installed package, WITHOUT importing it.

    find_spec on a top-level package resolves its location from the finders
    without executing the package body, so this stays inside the no-import
    rule that the whole two-implementations arrangement depends on.
    """
    if not WRITER_MODULE:
        return None
    try:
        spec = importlib.util.find_spec(WRITER_MODULE)
    except (ImportError, ValueError):
        return None
    for location in list(getattr(spec, "submodule_search_locations", None) or []):
        candidate = Path(location) / basename
        if candidate.is_file():
            return candidate
    return None


def find(basename: str) -> Optional[Path]:
    """Locate one module file in the writer, installed or checked out nearby."""
    installed = _installed(basename)
    if installed:
        return installed
    if not WRITER_MODULE:
        return None
    for root in checkout_roots():
        # Bounded: the package sits a level or two down (repo/repo/pkg/), and
        # an unbounded rglob over a training repo would wander into corpora and
        # run directories holding a great many files.
        for depth in ("", "*/", "*/*/"):
            for candidate in root.glob(f"{depth}{WRITER_MODULE}/{basename}"):
                if candidate.is_file():
                    return candidate
    return None


# ── reading constants out of source, without executing it ───────────────────

def literal(node: ast.AST, env: Optional[dict] = None):
    """Evaluate a constant expression against already-seen constants.

    Three things `ast.literal_eval` alone cannot do, each of which produced a
    test that was green for the wrong reason:

    1. ARITHMETIC. `STALE_AFTER_SECONDS = 15 * 60` is a BinOp, so literal_eval
       raises and the constant reads as None - and the contract test then
       reports a drift that does not exist. A test that cries wolf gets muted.

    2. NAME REFERENCES. `STATUSES = (PENDING, RUNNING, ...)` is a tuple of
       NAMES. literal_eval raises, the constant never enters the dict, and a
       difference-check against an empty set passes unconditionally. That is
       exactly how the status-vocabulary assertion spent its whole life green
       without ever comparing anything.

    3. TUPLES/LISTS built from either of the above.

    Module-level assignment is sequential, so by the time a tuple of names is
    reached, the names are already bound. Still no execution, no import,
    nothing but the syntax tree.
    """
    env = env if env is not None else {}
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError):
        pass

    if isinstance(node, ast.Name):
        return env.get(node.id)

    if isinstance(node, (ast.Tuple, ast.List)):
        items = [literal(e, env) for e in node.elts]
        # All-or-nothing: a half-resolved vocabulary is worse than an absent
        # one, because a partial set makes a difference-check look clean.
        if any(i is None for i in items):
            return None
        return tuple(items)

    if isinstance(node, ast.BinOp):
        left, right = literal(node.left, env), literal(node.right, env)
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


def constants(path: Path) -> Dict[str, object]:
    """Module-level constant assignments, in source order, without executing.

    Each result feeds back in as the environment for the next, which is what
    lets a tuple of names resolve.
    """
    out: Dict[str, object] = {}
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                value = literal(node.value, out)
                if value is not None:
                    out[target.id] = value
    return out
