"""
Headless CLI for Folder Structure Copier 2.0.

Same engine as the GUI, no Qt import and no per-file widget updates, so this
is the fastest way to run a large job (and the only way to script one):

    python fscopy_cli.py SRC DST --ext .py,.txt --exclude-dirs .git,node_modules
    python fscopy_cli.py SRC DST --dry-run --quiet
    python fscopy_cli.py SRC --export structure.txt

Author:  Zhiqiang Gu <zhiqiang.gu214@gmail.com>
Project: https://github.com/zgu214/folder-structure-copier
License: MIT (see LICENSE)
"""

from __future__ import annotations

import argparse
import sys
import threading

from fscopy_core import (
    APP_NAME, AUTHOR, DEFAULT_EXCLUDES, DEFAULT_MAX_WORKERS, PROJECT_URL,
    CopyOptions, ScanOptions, __version__, copy_tree, export_structure,
    human_bytes, human_duration, scan_tree,
)


def build_parser():
    p = argparse.ArgumentParser(
        prog="fscopy",
        description="Copy a folder structure (optionally without file contents), fast.",
        epilog=f"{APP_NAME} {__version__} by {AUTHOR} — {PROJECT_URL}",
    )
    p.add_argument("--version", action="version",
                   version=f"{APP_NAME} {__version__} by {AUTHOR} — {PROJECT_URL}")
    p.add_argument("source", help="Source folder")
    p.add_argument("destination", nargs="?", help="Destination folder (omit with --export or --scan-only)")
    p.add_argument("--ext", default="", help="Only include these extensions, e.g. .py,.txt")
    p.add_argument("--exclude-dirs", default="", help="Folder names/globs to prune, e.g. .git,node_modules")
    p.add_argument("--exclude-files", default="", help="File globs to skip, e.g. *.tmp,~$*")
    p.add_argument("--default-excludes", action="store_true",
                   help=f"Also prune the usual noise: {', '.join(DEFAULT_EXCLUDES)}")
    p.add_argument("--min-size-kb", type=int, default=0, help="Skip files smaller than this")
    p.add_argument("--modified-days", type=int, default=0, help="Only files modified within N days")
    p.add_argument("--no-content", action="store_true", help="Create empty placeholder files instead of copying data")
    p.add_argument("--strip-ext", action="store_true", help="Drop file extensions in the destination")
    p.add_argument("--skip-duplicates", action="store_true", help="Skip destination files matching size+mtime")
    p.add_argument("--verify-hash", action="store_true", help="With --skip-duplicates, compare by hash instead")
    p.add_argument("--workers", type=int, default=DEFAULT_MAX_WORKERS, help=f"Parallel copy workers (default {DEFAULT_MAX_WORKERS})")
    p.add_argument("--dry-run", action="store_true", help="Report what would happen, write nothing")
    p.add_argument("--scan-only", action="store_true", help="Scan and print totals, then exit")
    p.add_argument("--export", metavar="PATH", help="Write the structure to a .txt or .json file")
    p.add_argument("-v", "--verbose", action="store_true", help="Print every file")
    p.add_argument("-q", "--quiet", action="store_true", help="Only print the final summary")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)

    exclude_dirs = args.exclude_dirs
    if args.default_excludes:
        exclude_dirs = ",".join(filter(None, [exclude_dirs, ",".join(DEFAULT_EXCLUDES)]))

    scan_opts = ScanOptions.from_text(
        ext_text=args.ext,
        exclude_dir_text=exclude_dirs,
        exclude_file_text=args.exclude_files,
        min_size_kb=args.min_size_kb,
        modified_within_days=args.modified_days,
    )

    if args.scan_only or args.export or not args.destination:
        scan = scan_tree(args.source, scan_opts)
        if not args.quiet:
            print(f"Scanned {len(scan.dir_rels)} folders, {len(scan.files)} files, "
                  f"{human_bytes(scan.total_bytes)} in {scan.elapsed:.2f}s "
                  f"({scan.pruned_dirs} folders pruned, {scan.filtered_out} files filtered out)")
        if args.export:
            export_structure(args.source, args.export, scan=scan)
            print(f"Structure written to {args.export}")
        elif not args.destination and not args.scan_only:
            print("Nothing to do: give a destination, --scan-only, or --export.", file=sys.stderr)
            return 2
        return 0

    copy_opts = CopyOptions(
        copy_content=not args.no_content,
        keep_ext=not args.strip_ext,
        dry_run=args.dry_run,
        skip_duplicates=args.skip_duplicates,
        verify_hash=args.verify_hash,
        max_workers=args.workers,
    )

    cancel_event = threading.Event()
    show_progress = not args.quiet and sys.stderr.isatty()

    def on_progress(p):
        if not show_progress:
            return
        eta = f" ETA {human_duration(p.eta_seconds)}" if p.eta_seconds else ""
        sys.stderr.write(
            f"\r{p.pct:3d}%  {p.done_files}/{p.total_files} files  "
            f"{human_bytes(p.done_bytes)}/{human_bytes(p.total_bytes)}  "
            f"{human_bytes(p.bytes_per_sec)}/s{eta}   "
        )
        sys.stderr.flush()

    def on_log(lines):
        if args.verbose:
            print("\n".join(lines))

    def on_scan_done(scan):
        if not args.quiet:
            print(f"Scanned {len(scan.dir_rels)} folders, {len(scan.files)} files, "
                  f"{human_bytes(scan.total_bytes)} in {scan.elapsed:.2f}s "
                  f"({scan.pruned_dirs} folders pruned)")

    try:
        stats = copy_tree(
            args.source, args.destination, scan_opts, copy_opts,
            on_progress=on_progress, on_log=on_log, on_scan_done=on_scan_done,
            cancel_event=cancel_event,
        )
    except KeyboardInterrupt:
        cancel_event.set()
        print("\nCancelled.", file=sys.stderr)
        return 130

    if show_progress:
        sys.stderr.write("\r" + " " * 100 + "\r")

    rate = stats.bytes_copied / stats.elapsed if stats.elapsed else 0
    verb = "would copy" if args.dry_run else "copied"
    print(f"{verb} {stats.copied} files ({human_bytes(stats.bytes_copied)}), "
          f"{stats.skipped} skipped, {stats.errors} errors, "
          f"in {stats.elapsed:.2f}s ({human_bytes(rate)}/s)")
    return 1 if stats.errors else 0


if __name__ == "__main__":
    sys.exit(main())
