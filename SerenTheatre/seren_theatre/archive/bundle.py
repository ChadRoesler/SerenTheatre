"""Reading a prompt book. Theatre's share of a format it does not own.

Same arrangement as manifest.py, evalrecord.py and evalreport.py, for the same
reason: importing ms-moe-maker would make a viewer depend on a training
pipeline, and Theatre's `requires` is empty on purpose. Both ends implement the
format and tests/test_bundle_contract.py pins the constants.

READ-ONLY, ABSOLUTELY. There is no writer here. `ms-moe-maker bundle` makes
these; Theatre receives them.

────────────────────────────────────────────────────────────────────────────
THIS FILE HANDLES SOMEBODY ELSE'S ARCHIVE, WHICH IS THE DANGEROUS DIRECTION.

A prompt book arrives from a person. Two separate hazards live in that
sentence and conflating them is how one of them gets missed:

THE CONTAINER. Zip entries have been writing files outside their destination
since the format existed. `../..`, absolute paths, and symlinks - which are a
write primitive whose NAME looks completely harmless. Checked before a byte is
read, and the whole archive is refused rather than the entry skipped: an
archive containing one of these is not a bundle with a flaw.

THE CONTENTS. A recipe names an `eval.script`, and the harness runs it with the
interpreter. **A recipe from somebody else is executable content by design.**
That is fine between friends and it is not fine silently, so `executes` names
any such field and the UI is expected to put it in front of a person. Nothing
here decides for them; it refuses to let them not know.

The duplication of these checks across two packages is the price of `requires`
being empty, and it is the right price: a viewer that had to install a training
pipeline to open a file somebody sent it would not get installed.
────────────────────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any, Dict, List

# Pinned against ms_moe_maker.bundle.pack by tests/test_bundle_contract.py.
SCHEMA_VERSION = 1
MANIFEST_NAME = "bundle.json"
RECIPE_NAME = "recipe.yaml"
DATA_DIR = "data"
NOTES_NAME = "NOTES.md"

MAX_RECIPE_BYTES = 512 * 1024
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
# A prompt book is a recipe plus, at most, the corpora it needs. Past this it
# is somebody handing you a model, and the honest answer is that this is not
# the tool for that.
MAX_BUNDLE_BYTES = 4 * 1024 * 1024 * 1024

# Recipe fields that cause code to run. See the module docstring: reported,
# never blocked.
EXECUTABLE_FIELDS = (("eval", "script"), ("smoke", "script"),
                     ("gguf", "smoke_script"))


class UnreadableBundle(Exception):
    """Not a bundle, or one that must not be opened."""


def safe_members(zf: zipfile.ZipFile) -> List[zipfile.ZipInfo]:
    """Every member, or a refusal. Checked BEFORE a byte is extracted."""
    out: List[zipfile.ZipInfo] = []
    for info in zf.infolist():
        name = info.filename.replace("\\", "/")
        if name.startswith("/") or (len(name) > 1 and name[1] == ":"):
            raise UnreadableBundle(
                f"{info.filename!r} is an absolute path. A bundle names files "
                f"relative to itself; this one is choosing where it lands.")
        if ".." in [p for p in name.split("/") if p not in ("", ".")]:
            raise UnreadableBundle(
                f"{info.filename!r} climbs out of the bundle with `..`. "
                f"Nothing legitimate needs that.")
        if (info.external_attr >> 16) & 0xF000 == 0xA000:
            raise UnreadableBundle(
                f"{info.filename!r} is a symlink. A bundle carries files, not "
                f"pointers to files somewhere on your disk.")
        out.append(info)
    return out


def executes(recipe_text: str) -> List[str]:
    """Fields in this recipe that run somebody else's code. Never blocked."""
    found: List[str] = []
    try:
        import yaml
        data = yaml.safe_load(recipe_text) or {}
    except Exception:                                   # noqa: BLE001
        return found
    if not isinstance(data, dict):
        return found
    for block, key in EXECUTABLE_FIELDS:
        section = data.get(block)
        if isinstance(section, dict) and section.get(key):
            found.append(f"{block}.{key} = {section[key]!r}")
    return found


def read(path: Path) -> Dict[str, Any]:
    """Open a bundle and report what is in it. Extracts NOTHING.

    Reading and extracting are separate on purpose: a person deciding whether
    to accept a bundle needs to see the recipe, the claim and the warnings
    first, and a design where you must write it to disk to find out what it is
    has the consent backwards.
    """
    path = Path(path)
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise UnreadableBundle(f"{path.name}: {exc}") from exc
    if size > MAX_BUNDLE_BYTES:
        raise UnreadableBundle(
            f"{path.name} is {size / 1e9:.1f} GB. A prompt book is a recipe "
            f"and the corpora it needs; past a few gigabytes somebody is "
            f"handing you a model, and this is not the tool for that.")

    try:
        zf = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise UnreadableBundle(f"{path.name}: not a readable zip ({exc})") from exc
    with zf:
        members = safe_members(zf)
        names = {m.filename for m in members}
        if RECIPE_NAME not in names:
            raise UnreadableBundle(
                f"{path.name} has no {RECIPE_NAME}, so it is an archive rather "
                f"than a prompt book. Nothing here knows what to stage from it.")
        if zf.getinfo(RECIPE_NAME).file_size > MAX_RECIPE_BYTES:
            raise UnreadableBundle(
                f"{RECIPE_NAME} is a document; this one is a dataset wearing "
                f"its name.")

        # DECOMPRESSED SIZE, NOT COMPRESSED. A zip bomb is small on disk and
        # enormous on read, and the header says so before anything is read -
        # which is the only moment refusing is free.
        total = sum(m.file_size for m in members)
        if total > MAX_BUNDLE_BYTES:
            raise UnreadableBundle(
                f"{path.name} unpacks to {total / 1e9:.1f} GB from "
                f"{size / 1e6:.1f} MB on disk. That ratio is a zip bomb, not a "
                f"corpus.")

        recipe_text = zf.read(RECIPE_NAME).decode("utf-8", errors="replace")

        meta: Dict[str, Any] = {}
        meta_error = ""
        if MANIFEST_NAME in names:
            if zf.getinfo(MANIFEST_NAME).file_size > MAX_MANIFEST_BYTES:
                meta_error = f"{MANIFEST_NAME} is implausibly large; ignored."
            else:
                try:
                    loaded = json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
                    if isinstance(loaded, dict):
                        meta = loaded
                    else:
                        meta_error = f"{MANIFEST_NAME} is not an object."
                except ValueError as exc:
                    meta_error = f"{MANIFEST_NAME} is not valid JSON ({exc})."
        else:
            # NOT AN ERROR. A hand-rolled zip with a recipe in it is a fine
            # thing to make, and refusing it would turn the format into a gate
            # rather than a convenience. The reader is simply told there is no
            # claim here to compare anything against.
            meta_error = (f"no {MANIFEST_NAME}: this bundle makes no claim "
                          f"about what it built, so there is nothing to "
                          f"compare against.")

        version = meta.get("schema_version")
        if isinstance(version, int) and version > SCHEMA_VERSION:
            raise UnreadableBundle(
                f"{path.name} is schema_version {version}; this seren-theatre "
                f"understands {SCHEMA_VERSION}. Upgrade rather than being "
                f"shown a guess about somebody else's build.")

        notes = ""
        if NOTES_NAME in names:
            notes = zf.read(NOTES_NAME).decode("utf-8", errors="replace")

        data_experts = sorted({m.filename.split("/")[1] for m in members
                               if m.filename.startswith(DATA_DIR + "/")
                               and len(m.filename.split("/")) > 2})

        return {"recipe": recipe_text, "meta": meta, "meta_error": meta_error,
                "notes": notes, "data_experts": data_experts,
                "bytes": size, "unpacked_bytes": total,
                # See the module docstring. This goes in front of a person.
                "executes": executes(recipe_text)}


def digest(path: Path) -> str:
    """sha256, a megabyte at a time. The blob store's key."""
    out = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(1 << 20):
            out.update(chunk)
    return out.hexdigest()
