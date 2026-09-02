"""Where the zips go. Content-addressed, so the directory is opaque by design.

THE COMPLAINT THIS ANSWERS was "flat on disk gets cluttered", and the fix is
not to move the bytes into a database - it is to stop needing to look at them.
Sprawl is a NAMING problem. A store whose filenames are hashes is a directory
you never open, never sort, and never have to name a file in:

    blobs/<first two hex>/<full sha256>

Four things fall out of that, and the last two are why this beats a BLOB column:

  * no sprawl - there is nothing to read in there, ever
  * dedup for free - two prompt books sharing a corpus store one copy, because
    identical bytes have identical names
  * INTEGRITY FOR FREE - the filename IS the checksum, so verifying is
    re-hashing, and there is no second field to fall out of sync with the data
  * streaming - a multi-gigabyte corpus is copied a megabyte at a time. The
    same object in a database column is a whole-blob-in-RAM on insert AND on
    read, plus max_allowed_packet, plus a dump that grows from megabytes to
    gigabytes, plus you can no longer just `unzip` one without the app.

And it is the same primitive already trusted one layer down: the run manifest
stamps a sha256 of each defaults file, because "which version of that file did
this run inherit" is the 2am question. This is that move, one layer out.

THE ROW HOLDS THE HASH; THIS HOLDS THE BYTES. Neither is complete alone, and
that split is deliberate - a database that loses its blobs still tells you
exactly what it is missing, by name.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import Iterator, List, Optional

CHUNK = 1 << 20
# A hex sha256 and nothing else. Used to refuse a caller-supplied "hash" before
# it is ever turned into a path - the only untrusted input this module takes.
_HEX = set("0123456789abcdef")


class BlobError(Exception):
    """The store could not be read or written. Never swallowed."""


def _valid(digest: str) -> bool:
    return len(digest) == 64 and all(c in _HEX for c in digest)


class Blobs:
    """A content-addressed store rooted at one directory."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def path_for(self, digest: str) -> Path:
        """Where a digest lives. Refuses anything that is not a digest.

        A hash arriving from a database row is still input, and `../..` in a
        column is exactly as effective as `../..` in a URL. Checking the SHAPE
        rather than the resolved path is the stronger guard: a string that
        cannot contain a separator cannot express a traversal.
        """
        if not _valid(digest):
            raise BlobError(f"{digest!r} is not a sha256 digest")
        return self.root / digest[:2] / digest

    def has(self, digest: str) -> bool:
        return self.path_for(digest).is_file()

    def put(self, source: Path) -> str:
        """Store a file, return its digest. Existing content is left alone.

        WRITTEN TO A TEMPORARY NAME AND RENAMED, because a reader that finds a
        half-written blob finds a file whose contents do not match its own name
        - which is the one thing content addressing is supposed to make
        impossible. os.replace is atomic within a filesystem, and the temp file
        is deliberately created inside the destination directory so it is.
        """
        source = Path(source)
        digest = self.hash_file(source)
        target = self.path_for(digest)
        if target.is_file():
            return digest               # identical bytes; nothing to do
        target.parent.mkdir(parents=True, exist_ok=True)
        handle, tmp = tempfile.mkstemp(dir=str(target.parent), suffix=".part")
        os.close(handle)
        try:
            shutil.copyfile(source, tmp)
            os.replace(tmp, target)
        except OSError as exc:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise BlobError(f"storing {source.name}: {exc}") from exc
        return digest

    def open(self, digest: str):
        path = self.path_for(digest)
        try:
            return open(path, "rb")
        except OSError as exc:
            raise BlobError(f"{digest}: {exc}") from exc

    def size(self, digest: str) -> int:
        try:
            return self.path_for(digest).stat().st_size
        except OSError as exc:
            raise BlobError(f"{digest}: {exc}") from exc

    def delete(self, digest: str) -> bool:
        """Remove one blob. Callers are expected to have checked the rows first.

        DEDUP CUTS BOTH WAYS and this is where it bites: two prompt books
        sharing a corpus share the blob, so deleting one book must not delete
        bytes the other still names. This method does not and cannot know that
        - it takes an instruction. The refcount lives in the rows, and the
        caller owns the question.
        """
        path = self.path_for(digest)
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise BlobError(f"deleting {digest}: {exc}") from exc

    @staticmethod
    def hash_file(path: Path) -> str:
        """sha256, a megabyte at a time. Never reads the file into memory."""
        digest = hashlib.sha256()
        try:
            with open(path, "rb") as fh:
                while chunk := fh.read(CHUNK):
                    digest.update(chunk)
        except OSError as exc:
            raise BlobError(f"hashing {path}: {exc}") from exc
        return digest.hexdigest()

    def verify(self, digest: str) -> bool:
        """Re-hash a blob and check it against its own name.

        Cheap to write because of the design and impossible without it: with a
        BLOB column there is no independent name to check the bytes against, so
        corruption is undetectable until something downstream chokes on it.
        """
        try:
            return self.hash_file(self.path_for(digest)) == digest
        except BlobError:
            return False

    def walk(self) -> Iterator[str]:
        """Every digest present. For an orphan sweep, which is the caller's job."""
        if not self.root.is_dir():
            return
        for shard in sorted(self.root.iterdir()):
            if not shard.is_dir():
                continue
            for blob in sorted(shard.iterdir()):
                if blob.is_file() and _valid(blob.name):
                    yield blob.name

    def orphans(self, referenced: List[str]) -> List[str]:
        """Blobs no row names any more. REPORTED, never swept automatically.

        A sweep that runs itself is a delete nobody asked for, and the failure
        mode is losing the one corpus somebody needed because a row was missing
        for an unrelated reason. Hands on the surface.
        """
        keep = {d for d in referenced if _valid(d)}
        return [d for d in self.walk() if d not in keep]
