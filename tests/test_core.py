"""
Tests for fscopy_core. No pytest needed (though pytest runs them fine):

    python tests/test_core.py
"""

import os
import shutil
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fscopy_core import (  # noqa: E402
    CopyOptions, ScanOptions, copy_tree, export_structure, file_passes,
    human_bytes, human_duration, is_excluded_dir, scan_tree,
)


def build_tree(base):
    """src/
         file0..4.txt, notes.md, build.tmp
         a/keep.py
         a/b/deep.log
         a/__pycache__/cached.pyc
         empty_dir/
    """
    src = os.path.join(base, "src")
    os.makedirs(os.path.join(src, "a", "b"))
    os.makedirs(os.path.join(src, "a", "__pycache__"))
    os.makedirs(os.path.join(src, "empty_dir"))
    for i in range(5):
        with open(os.path.join(src, f"file{i}.txt"), "w") as f:
            f.write("x" * 100)
    with open(os.path.join(src, "notes.md"), "w") as f:
        f.write("# notes")
    with open(os.path.join(src, "build.tmp"), "w") as f:
        f.write("junk")
    with open(os.path.join(src, "a", "keep.py"), "w") as f:
        f.write("print('hi')")
    with open(os.path.join(src, "a", "b", "deep.log"), "w") as f:
        f.write("log line")
    with open(os.path.join(src, "a", "__pycache__", "cached.pyc"), "w") as f:
        f.write("bytecode")
    return src


def names(scan):
    return sorted(e.name for e in scan.files)


def walk_rel(root):
    out = []
    for r, _d, fs in os.walk(root):
        for fn in fs:
            out.append(os.path.relpath(os.path.join(r, fn), root))
    return sorted(out)


def test_scan_basic(src):
    scan = scan_tree(src)
    assert len(scan.files) == 10, names(scan)
    assert sorted(scan.dir_rels) == [".", "a", os.path.join("a", "__pycache__"),
                                     os.path.join("a", "b"), "empty_dir"], scan.dir_rels
    assert scan.total_bytes == sum(e.size for e in scan.files)
    assert scan.total_bytes > 0


def test_scan_extension_filter(src):
    scan = scan_tree(src, ScanOptions(extensions=(".py", ".md")))
    assert names(scan) == ["keep.py", "notes.md"], names(scan)
    assert scan.filtered_out == 8, scan.filtered_out


def test_scan_prunes_excluded_dirs(src):
    scan = scan_tree(src, ScanOptions(exclude_dirs=("__pycache__",)))
    assert "cached.pyc" not in names(scan)
    assert scan.pruned_dirs == 1
    # the pruned folder must not appear in the directory list either
    assert not any("__pycache__" in rel for rel in scan.dir_rels), scan.dir_rels


def test_scan_exclude_file_globs(src):
    scan = scan_tree(src, ScanOptions(exclude_files=("*.tmp",)))
    assert "build.tmp" not in names(scan)
    assert len(scan.files) == 9


def test_scan_min_size(src):
    scan = scan_tree(src, ScanOptions(min_size_kb=1))  # everything here is < 1 KB
    assert scan.files == []


def test_scan_modified_within(src):
    old_file = os.path.join(src, "file0.txt")
    old = time.time() - 40 * 86400
    os.utime(old_file, (old, old))
    scan = scan_tree(src, ScanOptions(modified_within_days=7))
    assert "file0.txt" not in names(scan)
    os.utime(old_file, None)


def test_copy_full_tree(src, base):
    dst = os.path.join(base, "dst_full")
    stats = copy_tree(src, dst)
    assert stats.copied == 10, stats
    assert stats.errors == 0
    assert stats.bytes_copied > 0
    assert walk_rel(dst) == walk_rel(src)
    assert os.path.isdir(os.path.join(dst, "empty_dir")), "empty folders must be replicated"
    with open(os.path.join(dst, "a", "keep.py")) as f:
        assert f.read() == "print('hi')"


def test_copy_names_only_and_strip_ext(src, base):
    dst = os.path.join(base, "dst_names")
    stats = copy_tree(src, dst, ScanOptions(extensions=(".py",)),
                      CopyOptions(copy_content=False, keep_ext=False))
    assert stats.copied == 1, stats
    target = os.path.join(dst, "a", "keep")
    assert os.path.exists(target), walk_rel(dst)
    assert os.path.getsize(target) == 0


def test_dry_run_writes_nothing(src, base):
    dst = os.path.join(base, "dst_dry")
    stats = copy_tree(src, dst, None, CopyOptions(dry_run=True))
    assert stats.copied == 10
    assert not os.path.exists(dst), "dry run must not create anything"


def test_skip_duplicates(src, base):
    dst = os.path.join(base, "dst_dup")
    first = copy_tree(src, dst)
    assert first.copied == 10
    second = copy_tree(src, dst, None, CopyOptions(skip_duplicates=True))
    assert second.skipped == 10 and second.copied == 0, second
    # a changed source file must not be skipped
    with open(os.path.join(src, "notes.md"), "a") as f:
        f.write("\nmore text")
    third = copy_tree(src, dst, None, CopyOptions(skip_duplicates=True))
    assert third.copied == 1 and third.skipped == 9, third


def test_skip_duplicates_hash_mode(src, base):
    dst = os.path.join(base, "dst_hash")
    copy_tree(src, dst)
    stats = copy_tree(src, dst, None, CopyOptions(skip_duplicates=True, verify_hash=True))
    assert stats.skipped == 10 and stats.copied == 0, stats


def test_progress_and_callbacks(src, base):
    dst = os.path.join(base, "dst_cb")
    seen_progress = []
    seen_logs = []
    scan_seen = []
    stats = copy_tree(src, dst,
                      on_progress=lambda p: seen_progress.append((p.done_files, p.done_bytes)),
                      on_log=lambda lines: seen_logs.extend(lines),
                      on_scan_done=scan_seen.append,
                      progress_interval=0.0, log_interval=0.0)
    assert scan_seen and len(scan_seen[0].files) == 10
    assert seen_progress, "progress callback never fired"
    assert seen_progress[-1][0] == 10, seen_progress[-1]
    assert len(seen_logs) == 10, len(seen_logs)
    final = seen_progress[-1]
    assert final[1] == stats.bytes_copied


def test_cancel(src, base):
    dst = os.path.join(base, "dst_cancel")
    ev = threading.Event()
    ev.set()
    stats = copy_tree(src, dst, None, CopyOptions(max_workers=1), cancel_event=ev)
    assert stats.cancelled is True
    assert stats.copied == 0, stats


def test_export_txt_and_json(src, base):
    txt = os.path.join(base, "structure.txt")
    export_structure(src, txt)
    content = open(txt, encoding="utf-8").read()
    assert "keep.py" in content and "empty_dir/" in content, content

    js = os.path.join(base, "structure.json")
    export_structure(src, js, ScanOptions(extensions=(".py",)))
    import json
    data = json.load(open(js, encoding="utf-8"))
    all_files = [f for entry in data for f in entry["files"]]
    assert all_files == ["keep.py"], all_files


def test_helpers():
    assert human_bytes(0) == "0 B"
    assert human_bytes(2048) == "2.0 KB"
    assert human_bytes(5 * 1024 ** 3).endswith("GB")
    assert human_duration(45) == "45s"
    assert human_duration(125) == "2m 5s"
    assert human_duration(4000) == "1h 6m"
    assert is_excluded_dir(".GIT", (".git",)) is True     # case-insensitive
    assert is_excluded_dir("src", (".git",)) is False
    assert file_passes("a.PY", (), (".py",)) is True      # case-insensitive
    assert file_passes("a.tmp", ("*.tmp",), ()) is False
    assert file_passes("~$doc.docx", ("~$*",), ()) is False


def test_options_from_text():
    opts = ScanOptions.from_text(" .PY , .txt ", ".git , node_modules", "*.tmp", 5, 3)
    assert opts.extensions == (".py", ".txt")
    assert opts.exclude_dirs == (".git", "node_modules")
    assert opts.exclude_files == ("*.tmp",)
    assert opts.min_size_kb == 5 and opts.modified_within_days == 3


def main():
    base = tempfile.mkdtemp(prefix="fscopy_tests_")
    try:
        src = build_tree(base)
        tests = [
            (test_scan_basic, (src,)),
            (test_scan_extension_filter, (src,)),
            (test_scan_prunes_excluded_dirs, (src,)),
            (test_scan_exclude_file_globs, (src,)),
            (test_scan_min_size, (src,)),
            (test_scan_modified_within, (src,)),
            (test_copy_full_tree, (src, base)),
            (test_copy_names_only_and_strip_ext, (src, base)),
            (test_dry_run_writes_nothing, (src, base)),
            (test_skip_duplicates, (src, base)),
            (test_skip_duplicates_hash_mode, (src, base)),
            (test_progress_and_callbacks, (src, base)),
            (test_cancel, (src, base)),
            (test_export_txt_and_json, (src, base)),
            (test_helpers, ()),
            (test_options_from_text, ()),
        ]
        failures = 0
        for fn, fn_args in tests:
            try:
                fn(*fn_args)
                print(f"PASS  {fn.__name__}")
            except AssertionError as e:
                failures += 1
                print(f"FAIL  {fn.__name__}: {e}")
            except Exception as e:  # noqa: BLE001
                failures += 1
                print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
        print(f"\n{len(tests) - failures}/{len(tests)} passed")
        return 1 if failures else 0
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
