"""Entry point for `python -m seren_theatre` or the `seren-theatre` script.

Accepts --config / -c to match the SerenMemory convention (Memory leads, the
rest follow), so the installer can pass the config path explicitly and a buddy
who learned one service knows this one.

--describe exists here as well as in the installer because Starwright runs it
on `seren-*-setup.sh`, and having the SERVICE able to answer the same question
means the two can be checked against each other. That is not belt-and-braces:
the `seren/port-map` fact records that Workbench's code said 7425 while its
installer said 7444, so an installed node answered where the docs did not. Two
sources that can disagree are only useful if something compares them.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _force_utf8_stdio() -> None:
    """Make stdout/stderr UTF-8 regardless of OS locale.

    Parity with SerenMargin/SerenLoci. On Windows the console defaults to a
    legacy codepage, so a smart quote in a config path can raise
    UnicodeEncodeError mid-work. PYTHONUTF8=1 in the service env is the primary
    fix; this is the backstop for the hand-run case.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


#: Verbs that are NOT "run the server". Dispatched by looking at argv[1]
#: before the serve parser is built, and that shape is deliberate.
#:
#: BARE INVOCATION MUST KEEP MEANING "SERVE". People launch this from a shell
#: script or a service wrapper - `run-seren-theatre.sh`, an NSSM unit, a
#: systemd unit - and every one of those calls `seren-theatre` or
#: `seren-theatre --config x`. Turning the serve path into a subcommand that
#: argparse requires would break all of them at once, for the convenience of a
#: tidier parser. So the verbs are additive: argv[1] is a verb or it is not,
#: and if it is not, nothing about the old invocation changed.
VERBS = ("archive",)


def _archive_main(argv: list) -> int:
    """`seren-theatre archive <backup|merge|checkpoint>`.

    WHY THESE ARE COMMANDS AND NOT ROUTES. Theatre exposes no write surface -
    `test_no_route_can_write` enforces it, and an endpoint that writes a file
    somebody names is the widest possible exception to that. The archive is also
    the one thing here whose whole job is to outlive the process, so operating
    on it belongs in the same place as the rest of an operator's muscle memory:
    a command, in a shell, next to the backups of everything else.
    """
    parser = argparse.ArgumentParser(
        prog="seren-theatre archive",
        description="Operate on the run archive - the record that outlives the "
                    "run directories.")
    parser.add_argument("--config", "-c", default=None,
                        help="Path to seren-theatre.yaml.")
    sub = parser.add_subparsers(dest="verb", required=True)

    p_backup = sub.add_parser(
        "backup",
        help="Consistent snapshot of the live archive. Blobs too, unless "
             "--no-blobs.",
        description="Uses sqlite's online backup API, so Theatre does not have "
                    "to be stopped. A plain `cp` of a WAL database can copy an "
                    "EMPTY file - see Archive.checkpoint for why.")
    p_backup.add_argument("dest", help="Destination .db path.")
    p_backup.add_argument("--no-blobs", action="store_true",
                          help="Database only. The blobs are what the rows "
                               "point AT, so this makes an incomplete backup "
                               "on purpose.")

    p_merge = sub.add_parser(
        "merge",
        help="Fold another archive into this one. Idempotent.",
        description="Rows are keyed by content-derived ids and every write is "
                    "an upsert, so the union is well defined and running it "
                    "twice changes nothing the second time. This is how a box "
                    "swap or a reformat is survived.")
    p_merge.add_argument("source", help="The other archive's .db path.")
    p_merge.add_argument("--blobs", default=None,
                         help="That archive's blobs directory, if it has one.")

    sub.add_parser(
        "checkpoint",
        help="Fold the WAL back into the database file.",
        description="Rarely needed by hand - Theatre does this when it stops. "
                    "Useful when Theatre was killed and you want the file on "
                    "disk to be complete before copying it.")

    args = parser.parse_args(argv)

    from ._diag import diag
    from .archive.store import Archive, ArchiveError, connect
    from .config import load_config

    cfg = load_config(args.config)
    if not getattr(cfg, "archive", None) or not cfg.archive.enabled:
        print("archive: disabled in config, so there is nothing to operate on.",
              file=sys.stderr)
        return 2

    try:
        archive = connect(cfg.archive.resolved_dsn(), cfg)
    except ArchiveError as exc:
        print(f"archive: {exc}", file=sys.stderr)
        return 2

    try:
        if args.verb == "checkpoint":
            ran = archive.checkpoint()
            print(f"{'checkpointed' if ran else 'could not checkpoint'} "
                  f"{archive.path}")
            return 0 if ran else 1

        if args.verb == "backup":
            written = archive.backup(Path(args.dest))
            rows = archive.count("surgeries")
            print(f"{rows} run(s) -> {written}")
            if not args.no_blobs:
                copied = _copy_blobs(cfg.archive.blobs_dir(),
                                     Path(args.dest))
                print(copied)
            return 0

        # merge
        source = Path(args.source)
        if not source.is_file():
            print(f"no archive at {source}", file=sys.stderr)
            return 2
        other = Archive(source)
        try:
            moved = archive.merge(other)
        finally:
            other.close()
        print("merged " + ", ".join(f"{n} {t}" for t, n in sorted(moved.items())))
        if args.blobs:
            print(_merge_blobs(Path(args.blobs),
                               cfg.archive.blobs_dir()))
        return 0
    except ArchiveError as exc:
        print(f"archive: {exc}", file=sys.stderr)
        return 1
    finally:
        # Checkpoint on the way out here too. A command that leaves the file it
        # just operated on in the stub-plus-WAL shape would be undoing its own
        # point.
        archive.close()
        diag("")


def _blob_dest(dest_db: Path) -> Path:
    """Where a backup's blobs go: beside the .db, named after it.

    `archive-2026-09-10.db` -> `archive-2026-09-10.blobs/`. Beside rather than
    inside a wrapper directory so that copying one file and one directory to a
    NAS is the whole operation, and so the pairing is obvious months later.
    """
    return dest_db.with_suffix(dest_db.suffix + ".blobs")


def _copy_blobs(src: Path, dest_db: Path) -> str:
    import shutil
    dest = _blob_dest(dest_db)
    if not src.is_dir():
        return f"no blobs at {src} - database only"
    try:
        shutil.copytree(src, dest, dirs_exist_ok=True)
    except OSError as exc:
        return f"blobs NOT copied ({exc}) - the database backup is incomplete"
    return f"blobs -> {dest}"


def _merge_blobs(src: Path, dest: Path) -> str:
    """Content-addressed, so a copy IS a merge.

    Identical bytes have identical names - one of the four things blobs.py says
    that layout buys - so there is nothing to reconcile and no way to clobber a
    different object with the same name.
    """
    import shutil
    if not src.is_dir():
        return f"no blobs at {src}"
    try:
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src, dest, dirs_exist_ok=True)
    except OSError as exc:
        return f"blobs NOT merged ({exc})"
    return f"blobs merged from {src}"


def main() -> None:
    _force_utf8_stdio()
    if len(sys.argv) > 1 and sys.argv[1] in VERBS:
        raise SystemExit(_archive_main(sys.argv[2:]))
    parser = argparse.ArgumentParser(
        prog="seren_theatre",
        description="SerenTheatre - the room where you watch the thing being made.")
    parser.add_argument(
        "--config", "-c", default=None,
        help="Path to seren-theatre.yaml (default: $SEREN_THEATRE_CONFIG, then "
             "~/seren-theatre/seren-theatre.yaml, falling back to built-in "
             "defaults).")
    args = parser.parse_args()

    # Everything heavy is imported LATE, below the --describe return above.
    import uvicorn

    from ._diag import diag
    from .app import create_app
    from .config import load_config

    cfg = load_config(args.config)
    app = create_app(cfg)

    diag(f"[seren-theatre] listening on {cfg.host}:{cfg.port}  -> "
         f"http://{cfg.host}:{cfg.port}/viewer")
    if not cfg.stages:
        diag("[seren-theatre] no stages configured - the room is empty. Set "
             "SEREN_THEATRE_STAGE=/path/to/lab or add a stages: block.")
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="info")


if __name__ == "__main__":
    main()
