# Folder Structure Copier

A PyQt6 desktop application for copying a directory tree with selected file types. Copy full file contents or create empty placeholders with the same names; preview the structure and run a dry run before creating files.

Version 2.0 is a rewrite focused on speed: the scan/copy engine now lives in a Qt-free module, copies run in parallel, excluded folders are skipped during the walk, and the tree preview loads one folder at a time instead of blocking the window.

Maintained by **Zhiqiang Gu · zhiqiang.gu214@gmail.com**. Independent personal project, unrelated to my employer or my work for any company.

## Screenshot

![Folder Structure Copier desktop interface](screenshots/main_gui.png)

## Features

- Source and destination folder selection, with drag and drop onto the window.
- Lazy tree previews that apply the same filters as the copy job.
- Filters: extensions (`.py,.txt`), excluded folder names or globs, excluded file globs, minimum file size, modified-within-days.
- One click to prune the usual noise: `.git`, `node_modules`, `__pycache__`, `.venv`, `venv`, `.mypy_cache`, `.pytest_cache`.
- Copy contents or create zero-byte placeholder files; keep or remove filename extensions.
- **Estimate** button reporting file count, byte total and scan time before anything is written.
- Dry-run logging, byte-accurate progress with throughput and ETA, and a working Cancel button.
- Skip destination files that already match by size and timestamp, or by hash for an exact comparison.
- Adjustable number of parallel copy workers.
- Text/JSON structure export, saved presets, optional dark mode, saved logs.
- A command line interface for scripted or very large jobs.

## What changed in 2.0

| Version 1 | Version 2 |
|---|---|
| The source tree was walked twice with `os.walk` (once to count files, once to copy), then each file was stat'ed again for its size and timestamp | A single `os.scandir` pass produces the folder list, file list and byte total together, reusing the data `scandir` already carries |
| No way to skip `.git`, `node_modules` or `__pycache__` | Excluded folders are pruned during the walk, so they are never entered |
| Files were copied one at a time on a single thread | Copies run on a thread pool with a bounded submission window, so memory stays flat on very large trees and Cancel takes effect immediately |
| The preview recursed several levels deep before displaying anything, freezing the window on large folders | One `os.scandir` per folder you expand |
| A Qt signal and a `QTextEdit.append()` call for every single file | Progress and log updates are throttled and batched inside the engine |
| Progress counted files, so one large file looked like no progress | Progress is byte-based, with throughput and ETA |
| A running copy could not be stopped | Cancel button, backed by a cancellation flag every worker task checks |
| Duplicate detection hashed both files every time | Size and timestamp first; hashing only when explicitly requested |
| GUI only | The engine is importable and covered by tests; a CLI runs the same code without Qt |

Measured on 2,400 project files in 40 folders plus 2,000 files in a `.git`-style folder, same SSD, engine only:

```
version 1  (double os.walk, serial copy):        1.488 s   4400 files
version 2  (single scan, parallel copy):         0.566 s   4400 files    2.6x
version 2  (with .git pruned during the walk):   0.395 s   2400 files    3.8x
```

The difference is larger in the application itself, because version 1 also paid for a Qt signal and a text-widget append per file, and blocked while building previews.

## Install and run from source

```shell
git clone https://github.com/zgu214/folder-structure-copier.git
cd folder-structure-copier
python -m venv .venv
```

Activate the environment:

```powershell
# Windows PowerShell
.venv\Scripts\Activate.ps1
```

```shell
# macOS/Linux
source .venv/bin/activate
```

Then install the GUI dependency and launch:

```shell
python -m pip install -r requirements.txt
python folder_structure_gui.py
```

A graphical desktop and a Python version supported by the installed PyQt6 release are required. The command line interface needs neither PyQt6 nor a desktop session.

## Example: create a project template

1. Select the original project as **Source Folder**, or drag it onto the window.
2. Select a new, empty folder outside the source tree as **Destination Folder**.
3. Enter `.py,.txt` under **Include extensions**, or leave it empty to include all files.
4. Select **Use defaults** next to **Exclude folders** to skip `.git`, `node_modules` and similar.
5. Enable **Keep file extensions**. Clear **Copy file contents** to create empty placeholders.
6. Select **Estimate** to see how many files and bytes the current filters select.
7. Enable **Dry run**, select **Start Copy**, and review the log.
8. Clear **Dry run** and start again when the planned paths are correct.

Use **Refresh Preview** to rebuild the filtered tree, and **Save Preset** to reuse settings.

## Command line

The CLI uses the same engine with no Qt import and no per-file widget updates, which makes it the fastest way to run a large job and the only way to script one.

```shell
python fscopy_cli.py SRC DST --ext .py,.txt --default-excludes
python fscopy_cli.py SRC DST --no-content --strip-ext          # structure only
python fscopy_cli.py SRC DST --skip-duplicates --workers 8     # slow network share
python fscopy_cli.py SRC --scan-only --default-excludes        # report totals only
python fscopy_cli.py SRC --export structure.txt                # or structure.json
```

`python fscopy_cli.py --help` lists every option.

## Important behavior

- Existing destination files can be overwritten. Placeholder mode opens files for writing and can truncate an existing file. Use an empty destination for a new template.
- Keep the destination outside the source directory. The file list is captured before any copying starts, so the run terminates, but output written inside the source will still be mixed into it.
- Removing extensions can cause collisions, such as `report.txt` and `report.csv` both becoming `report`.
- Extension and exclusion matching is case-insensitive in this version; `.txt` also selects `.TXT`. Version 1 was case-sensitive.
- Filters apply to files. Folders that are traversed are still created at the destination even when no file inside them matches; excluded folders are not.
- Dry run simulates planned paths. It does not prove that a later write will succeed or preserve data.
- **Skip existing duplicates** compares size and modification time, which is a heuristic; enable **Verify with hash** when an exact comparison matters.
- More parallel workers help with many small files on a local disk and can hurt on a slow network share. The worker count is adjustable in both the GUI and the CLI.

## Repository layout

- `fscopy_core.py`: scan and copy engine, no Qt dependency.
- `folder_structure_gui.py`: PyQt6 application.
- `fscopy_cli.py`: command line interface.
- `tests/test_core.py`: engine tests; run with `python tests/test_core.py` (pytest optional).
- `screenshots/main_gui.png`: interface screenshot.
- `preset.json`: example saved settings; inspect paths before loading.

## Feedback and license

Report problems with your operating system, Python/PyQt6 versions, selected options and a small reproducible example. Do not include confidential folder names or file contents.

Licensed under the [MIT License](LICENSE). PyQt6 and other dependencies retain their own licenses.

[LinkedIn](https://www.linkedin.com/in/zhiqiang-gu-55813318/) · [Email](mailto:zhiqiang.gu214@gmail.com)
