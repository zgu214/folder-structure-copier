"""
Core scan/copy engine for Folder Structure Copier 2.0.

Deliberately Qt-free: the GUI (folder_structure_gui.py), the CLI
(fscopy_cli.py) and the tests all sit on top of this module.

Design notes (these are the reasons this is fast):

  * One os.scandir pass produces the directory list, the file list, and the
    byte total at once. Earlier versions walked the tree twice with os.walk
    and re-stat'ed every file through os.path.getsize/getmtime.
  * Excluded directories are pruned during the walk, not filtered afterwards,
    so a .git or node_modules folder costs nothing instead of being fully
    enumerated and then thrown away.
  * Copies run on a thread pool with a bounded submission window, so a
    million-file tree does not materialise a million Future objects, and
    cancelling stops promptly instead of draining a huge queue.
  * Progress and log callbacks are time-throttled inside the engine, so the
    UI cannot be flooded by per-file events.

Author:  Zhiqiang Gu <zhiqiang.gu214@gmail.com>
Project: https://github.com/zgu214/folder-structure-copier
License: MIT (see LICENSE)
"""

from __future__ import annotations

import os
import shutil
import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor, FIRST_COMPLETED, wait
from dataclasses import dataclass, field
from fnmatch import fnmatchcase

APP_NAME = "Folder Structure Copier"
__version__ = "2.0.0"
AUTHOR = "Zhiqiang Gu"
AUTHOR_EMAIL = "zhiqiang.gu214@gmail.com"
PROJECT_URL = "https://github.com/zgu214/folder-structure-copier"
LICENSE = "MIT"

DEFAULT_MAX_WORKERS = min(32, (os.cpu_count() or 4) * 4)
DEFAULT_EXCLUDES = (".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache")
MTIME_TOLERANCE = 2.0  # seconds; FAT/exFAT timestamp granularity


# --------------------------------------------------------------------------
# Options / results
# --------------------------------------------------------------------------

@dataclass
class ScanOptions:
    extensions: tuple = ()          # e.g. (".py", ".txt"); empty = all files
    exclude_dirs: tuple = ()        # names or glob patterns, matched on the folder name
    exclude_files: tuple = ()       # glob patterns, matched on the file name
    min_size_kb: int = 0
    modified_within_days: int = 0

    @staticmethod
    def from_text(ext_text="", exclude_dir_text="", exclude_file_text="",
                  min_size_kb=0, modified_within_days=0):
        """Build options from comma-separated UI/CLI strings."""
        def split(text):
            return tuple(p.strip().lower() for p in (text or "").split(",") if p.strip())
        return ScanOptions(
            extensions=split(ext_text),
            exclude_dirs=split(exclude_dir_text),
            exclude_files=split(exclude_file_text),
            min_size_kb=min_size_kb,
            modified_within_days=modified_within_days,
        )


@dataclass
class FileEntry:
    src: str
    rel: str        # relative directory, "." for the source root
    name: str
    size: int
    mtime: float


@dataclass
class ScanResult:
    dir_rels: list = field(default_factory=list)
    files: list = field(default_factory=list)
    total_bytes: int = 0
    pruned_dirs: int = 0
    filtered_out: int = 0
    elapsed: float = 0.0


@dataclass
class CopyOptions:
    copy_content: bool = True
    keep_ext: bool = True
    dry_run: bool = False
    skip_duplicates: bool = False
    verify_hash: bool = False
    max_workers: int = DEFAULT_MAX_WORKERS


@dataclass
class Progress:
    done_files: int = 0
    total_files: int = 0
    done_bytes: int = 0
    total_bytes: int = 0
    elapsed: float = 0.0

    @property
    def pct(self):
        if self.total_bytes:
            return min(100, int(self.done_bytes * 100 / self.total_bytes))
        if self.total_files:
            return min(100, int(self.done_files * 100 / self.total_files))
        return 0

    @property
    def bytes_per_sec(self):
        return self.done_bytes / self.elapsed if self.elapsed > 0 else 0.0

    @property
    def eta_seconds(self):
        rate = self.bytes_per_sec
        if rate <= 0 or self.done_bytes >= self.total_bytes:
            return 0.0
        return (self.total_bytes - self.done_bytes) / rate


@dataclass
class CopyStats:
    copied: int = 0
    skipped: int = 0
    errors: int = 0
    bytes_copied: int = 0
    total_files: int = 0
    elapsed: float = 0.0
    cancelled: bool = False


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def human_bytes(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def human_duration(seconds):
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60}m"


def _matches_any(name_lower, patterns):
    for pat in patterns:
        if name_lower == pat or fnmatchcase(name_lower, pat):
            return True
    return False


def is_excluded_dir(name, exclude_dirs):
    return bool(exclude_dirs) and _matches_any(name.lower(), exclude_dirs)


def file_passes(name, exclude_files, extensions):
    """Name-only checks, shared by the scanner and the GUI's preview tree."""
    lower = name.lower()
    if exclude_files and _matches_any(lower, exclude_files):
        return False
    if extensions and not lower.endswith(tuple(extensions)):
        return False
    return True


def file_hash(path, block_size=1 << 20):
    hasher = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(block_size), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


# --------------------------------------------------------------------------
# Scanning
# --------------------------------------------------------------------------

def scan_tree(source, opts=None, cancel_event=None):
    """Walk `source` once with os.scandir and return a ScanResult.

    Excluded directories are pruned before they are ever entered.
    """
    opts = opts or ScanOptions()
    t0 = time.time()
    result = ScanResult()
    now = time.time()
    min_bytes = opts.min_size_kb * 1024
    cutoff = now - opts.modified_within_days * 86400 if opts.modified_within_days else None
    stack = [(source, ".")]

    while stack:
        if cancel_event is not None and cancel_event.is_set():
            break
        current, rel = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        result.dir_rels.append(rel)
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    if is_excluded_dir(entry.name, opts.exclude_dirs):
                        result.pruned_dirs += 1
                        continue
                    child_rel = entry.name if rel == "." else os.path.join(rel, entry.name)
                    stack.append((entry.path, child_rel))
                elif entry.is_file(follow_symlinks=False):
                    if not file_passes(entry.name, opts.exclude_files, opts.extensions):
                        result.filtered_out += 1
                        continue
                    st = entry.stat()
                    if min_bytes and st.st_size < min_bytes:
                        result.filtered_out += 1
                        continue
                    if cutoff is not None and st.st_mtime < cutoff:
                        result.filtered_out += 1
                        continue
                    result.files.append(FileEntry(entry.path, rel, entry.name, st.st_size, st.st_mtime))
                    result.total_bytes += st.st_size
            except OSError:
                continue

    result.elapsed = time.time() - t0
    return result


# --------------------------------------------------------------------------
# Copying
# --------------------------------------------------------------------------

def _is_duplicate(src_entry, dest_path, verify_hash):
    """Fast duplicate test: size + mtime first (shutil.copy2 preserves both),
    full hash only when explicitly requested."""
    try:
        st = os.stat(dest_path)
    except OSError:
        return False
    if st.st_size != src_entry.size:
        return False
    if verify_hash:
        return file_hash(src_entry.src) == file_hash(dest_path)
    return abs(st.st_mtime - src_entry.mtime) <= MTIME_TOLERANCE


def copy_tree(source, destination, scan_opts=None, copy_opts=None,
              on_progress=None, on_log=None, on_scan_done=None,
              cancel_event=None, progress_interval=0.1, log_interval=0.15,
              log_batch_size=200):
    """Scan `source` and copy it to `destination`. Returns CopyStats.

    on_progress(Progress) and on_log(list[str]) are called from this thread,
    already throttled/batched.
    """
    scan_opts = scan_opts or ScanOptions()
    copy_opts = copy_opts or CopyOptions()
    cancel_event = cancel_event or threading.Event()

    t0 = time.time()
    scan = scan_tree(source, scan_opts, cancel_event)
    if on_scan_done is not None:
        on_scan_done(scan)

    if not copy_opts.dry_run:
        for rel in scan.dir_rels:
            dest_dir = destination if rel == "." else os.path.join(destination, rel)
            try:
                os.makedirs(dest_dir, exist_ok=True)
            except OSError as e:
                if on_log:
                    on_log([f"Error creating folder {dest_dir}: {e}"])

    stats = CopyStats(total_files=len(scan.files))
    if not scan.files or cancel_event.is_set():
        stats.elapsed = time.time() - t0
        stats.cancelled = cancel_event.is_set()
        if on_progress:
            on_progress(Progress(0, 0, 0, 0, stats.elapsed))
        return stats

    def copy_one(entry):
        if cancel_event.is_set():
            return None
        dest_dir = destination if entry.rel == "." else os.path.join(destination, entry.rel)
        clean_name = entry.name if copy_opts.keep_ext else os.path.splitext(entry.name)[0]
        dest_path = os.path.join(dest_dir, clean_name)
        try:
            if copy_opts.dry_run:
                return ("dry", entry.size, f"Would create: {dest_path}")
            if copy_opts.skip_duplicates and _is_duplicate(entry, dest_path, copy_opts.verify_hash):
                return ("skip", entry.size, f"Skipped duplicate: {dest_path}")
            if copy_opts.copy_content:
                shutil.copy2(entry.src, dest_path)
                return ("copy", entry.size, f"Copied: {entry.src} -> {dest_path}")
            with open(dest_path, "w"):
                pass
            return ("copy", 0, f"Created empty file: {dest_path}")
        except Exception as e:  # noqa: BLE001 - report, never abort the whole run
            return ("error", entry.size, f"Error copying {entry.src}: {e}")

    log_buffer = []
    last_log = last_progress = time.time()
    progress = Progress(total_files=len(scan.files), total_bytes=scan.total_bytes)

    def flush_log(force=False):
        nonlocal log_buffer, last_log
        if not log_buffer:
            return
        now = time.time()
        if force or len(log_buffer) >= log_batch_size or (now - last_log) >= log_interval:
            if on_log:
                on_log(log_buffer)
            log_buffer = []
            last_log = now

    def push_progress(force=False):
        nonlocal last_progress
        now = time.time()
        if force or (now - last_progress) >= progress_interval:
            progress.elapsed = now - t0
            if on_progress:
                on_progress(progress)
            last_progress = now

    # Bounded submission window: keeps memory flat and cancellation responsive
    # regardless of how many files the tree holds.
    workers = max(1, copy_opts.max_workers)
    window = workers * 8
    entries = iter(scan.files)
    pending = set()

    with ThreadPoolExecutor(max_workers=workers) as ex:
        while True:
            if cancel_event.is_set():
                for fut in pending:
                    fut.cancel()
                break
            while len(pending) < window:
                try:
                    pending.add(ex.submit(copy_one, next(entries)))
                except StopIteration:
                    break
            if not pending:
                break
            done, pending = wait(pending, return_when=FIRST_COMPLETED)
            for fut in done:
                if fut.cancelled():
                    continue
                res = fut.result()
                if res is None:
                    continue
                kind, size, msg = res
                progress.done_files += 1
                log_buffer.append(msg)
                if kind in ("copy", "dry"):
                    stats.copied += 1
                    stats.bytes_copied += size
                    progress.done_bytes += size
                elif kind == "skip":
                    stats.skipped += 1
                    progress.done_bytes += size
                else:
                    stats.errors += 1
                    progress.done_bytes += size
            flush_log()
            push_progress()

    stats.cancelled = cancel_event.is_set()
    if stats.cancelled:
        log_buffer.append(f"Cancelled after {progress.done_files}/{progress.total_files} files.")
    flush_log(force=True)
    push_progress(force=True)
    stats.elapsed = time.time() - t0
    return stats


# --------------------------------------------------------------------------
# Structure export
# --------------------------------------------------------------------------

def export_structure(source, path, scan_opts=None, scan=None):
    """Write the source structure to `path` as .json or plain text."""
    import json

    scan = scan or scan_tree(source, scan_opts)
    files_by_dir = {rel: [] for rel in scan.dir_rels}
    for entry in scan.files:
        files_by_dir.setdefault(entry.rel, []).append(entry.name)

    if path.lower().endswith(".json"):
        structure = [{"path": rel, "files": sorted(files_by_dir.get(rel, []))} for rel in scan.dir_rels]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(structure, f, indent=2)
        return path

    tree = {}
    for rel in scan.dir_rels:
        parts = [] if rel == "." else rel.split(os.sep)
        node = tree
        for part in parts:
            node = node.setdefault(part, {})
        node["_files"] = sorted(files_by_dir.get(rel, []))

    with open(path, "w", encoding="utf-8") as f:
        def render(node, prefix=""):
            keys = sorted(k for k in node if k != "_files")
            for i, key in enumerate(keys):
                is_last = (i == len(keys) - 1)
                f.write(f"{prefix}{'+-- ' if is_last else '|-- '}{key}/\n")
                sub_prefix = prefix + ("    " if is_last else "|   ")
                for file_name in node[key].get("_files", []):
                    f.write(f"{sub_prefix}{file_name}\n")
                render(node[key], sub_prefix)

        f.write(f"{os.path.basename(os.path.normpath(source))}/\n")
        for file_name in tree.get("_files", []):
            f.write(f"    {file_name}\n")
        render(tree)
    return path
