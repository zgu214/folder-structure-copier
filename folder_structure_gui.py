"""
Folder Structure Copier GUI (v4)

A faster rebuild of folder_structure_GUI / GUI2 / GUI3. Same job -- preview a
folder tree, filter it, and copy the structure (with or without file content)
to a destination -- with the slow parts redesigned. All scanning and copying
lives in fscopy_core (Qt-free, shared with fscopy_cli.py and the tests); this
file is only the window.

What this window adds on top of the engine:
  * Lazy tree previews that honour the same filters as the copy job, so a
    huge or noisy source folder costs one os.scandir per folder you open.
  * Drag and drop for source/destination.
  * Byte-accurate progress with live throughput and ETA.
  * Batched log output into a QPlainTextEdit (v1-v3 called
    QTextEdit.append() once per file, which was itself a bottleneck).
  * A Cancel button that stops in-flight work.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from collections import deque

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QFileDialog, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
    QSpinBox, QSplitter, QStyle, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
    QWidget,
)

from fscopy_core import (
    DEFAULT_EXCLUDES, DEFAULT_MAX_WORKERS, CopyOptions, ScanOptions,
    copy_tree, export_structure, file_passes, human_bytes, human_duration,
    is_excluded_dir, scan_tree,
)

CONFIG_FILE = "folder_copier_config.json"
MAX_LOG_HISTORY = 50_000  # keep memory flat on million-file jobs


# --------------------------------------------------------------------------
# Background workers
# --------------------------------------------------------------------------

class CopyThread(QThread):
    progress_update = pyqtSignal(object)        # Progress
    log_batch = pyqtSignal(list)                # list[str]
    scan_done = pyqtSignal(object)              # ScanResult
    finished_ok = pyqtSignal(object)            # CopyStats
    failed = pyqtSignal(str)

    def __init__(self, source, destination, scan_opts, copy_opts):
        super().__init__()
        self.source = source
        self.destination = destination
        self.scan_opts = scan_opts
        self.copy_opts = copy_opts
        self.cancel_event = threading.Event()

    def cancel(self):
        self.cancel_event.set()

    def run(self):
        try:
            stats = copy_tree(
                self.source, self.destination, self.scan_opts, self.copy_opts,
                on_progress=self.progress_update.emit,
                on_log=self.log_batch.emit,
                on_scan_done=self.scan_done.emit,
                cancel_event=self.cancel_event,
            )
            self.finished_ok.emit(stats)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


class ScanThread(QThread):
    done = pyqtSignal(object)   # ScanResult
    failed = pyqtSignal(str)

    def __init__(self, source, scan_opts, export_path=None):
        super().__init__()
        self.source = source
        self.scan_opts = scan_opts
        self.export_path = export_path

    def run(self):
        try:
            scan = scan_tree(self.source, self.scan_opts)
            if self.export_path:
                export_structure(self.source, self.export_path, scan=scan)
            self.done.emit(scan)
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


# --------------------------------------------------------------------------
# Lazy, filter-aware tree preview
# --------------------------------------------------------------------------

LAZY_PATH = Qt.ItemDataRole.UserRole
PLACEHOLDER = "…"  # a lone "…" child marks a folder that isn't populated yet


class LazyFolderTree(QTreeWidget):
    """A QTreeWidget that scans one directory level at a time.

    Only the root's immediate children are listed up front; each folder gets a
    placeholder child so the expand arrow shows, and the real contents are
    read with a single os.scandir the first time it is expanded. That is what
    makes previewing a huge tree instant instead of proportional to its size,
    and it applies the same filters as the copy job so the preview matches
    what would actually be copied.
    """

    def __init__(self, header, parent=None):
        super().__init__(parent)
        self.setHeaderLabels([header])
        self.itemExpanded.connect(self._on_expanded)
        self._dir_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon)
        self._file_icon = self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)
        self._root_path = ""
        self.scan_opts = ScanOptions()

    def set_filters(self, scan_opts):
        self.scan_opts = scan_opts

    def reload(self):
        self.load_root(self._root_path)

    def load_root(self, path):
        self.clear()
        self._root_path = path or ""
        if not path or not os.path.isdir(path):
            return
        root_item = QTreeWidgetItem([os.path.basename(os.path.normpath(path)) or path])
        root_item.setIcon(0, self._dir_icon)
        root_item.setData(0, LAZY_PATH, path)
        self.addTopLevelItem(root_item)
        self._populate(root_item, path)
        root_item.setExpanded(True)

    def _populate(self, item, path):
        try:
            entries = sorted(
                os.scandir(path),
                key=lambda e: (not self._safe_is_dir(e), e.name.lower()),
            )
        except OSError as e:
            item.addChild(QTreeWidgetItem([f"<error reading folder: {e}>"]))
            return

        opts = self.scan_opts
        for entry in entries:
            is_dir = self._safe_is_dir(entry)
            if is_dir:
                if is_excluded_dir(entry.name, opts.exclude_dirs):
                    continue
                child = QTreeWidgetItem([entry.name])
                child.setIcon(0, self._dir_icon)
                child.setData(0, LAZY_PATH, entry.path)
                child.addChild(QTreeWidgetItem([PLACEHOLDER]))
            else:
                if not file_passes(entry.name, opts.exclude_files, opts.extensions):
                    continue
                child = QTreeWidgetItem([entry.name])
                child.setIcon(0, self._file_icon)
            item.addChild(child)

    @staticmethod
    def _safe_is_dir(entry):
        try:
            return entry.is_dir(follow_symlinks=False)
        except OSError:
            return False

    def _on_expanded(self, item):
        path = item.data(0, LAZY_PATH)
        if path is None:
            return
        # Still holding just the placeholder? Then this is the first expand.
        if (item.childCount() == 1
                and item.child(0).text(0) == PLACEHOLDER
                and item.child(0).data(0, LAZY_PATH) is None):
            item.takeChildren()
            self.setUpdatesEnabled(False)
            try:
                self._populate(item, path)
            finally:
                self.setUpdatesEnabled(True)


# --------------------------------------------------------------------------
# Main window
# --------------------------------------------------------------------------

class FolderCopyApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Folder Structure Copier v4")
        self.resize(1150, 800)
        self.setAcceptDrops(True)
        self.source_folder = ""
        self.destination_folder = ""
        self.log_history = deque(maxlen=MAX_LOG_HISTORY)
        self.worker = None
        self.scan_worker = None
        self.init_ui()
        self.load_settings()

    # -- UI construction ---------------------------------------------------

    def init_ui(self):
        main_layout = QVBoxLayout()

        # --- 1. Folders ---
        folder_box = QGroupBox("1. Folder Selection  (or drag a folder onto the window)")
        folder_layout = QHBoxLayout()
        src_col = QVBoxLayout()
        self.btn_src = QPushButton("Select Source Folder")
        self.src_label = QLabel("Source: Not selected")
        self.btn_src.clicked.connect(self.select_source)
        src_col.addWidget(self.btn_src)
        src_col.addWidget(self.src_label)
        dest_col = QVBoxLayout()
        self.btn_dest = QPushButton("Select Destination Folder")
        self.dest_label = QLabel("Destination: Not selected")
        self.btn_dest.clicked.connect(self.select_destination)
        dest_col.addWidget(self.btn_dest)
        dest_col.addWidget(self.dest_label)
        folder_layout.addLayout(src_col)
        folder_layout.addLayout(dest_col)
        folder_box.setLayout(folder_layout)

        # --- 2. Filters ---
        filter_box = QGroupBox("2. Filters")
        filter_layout = QVBoxLayout()

        row_ext = QHBoxLayout()
        row_ext.addWidget(QLabel("Include extensions:"))
        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("e.g. .py,.txt   (blank = all files)")
        self.filter_input.editingFinished.connect(self.apply_filters_to_preview)
        row_ext.addWidget(self.filter_input)
        filter_layout.addLayout(row_ext)

        row_excl = QHBoxLayout()
        row_excl.addWidget(QLabel("Exclude folders:"))
        self.exclude_dirs_input = QLineEdit()
        self.exclude_dirs_input.setPlaceholderText("e.g. .git,node_modules,__pycache__")
        self.exclude_dirs_input.editingFinished.connect(self.apply_filters_to_preview)
        row_excl.addWidget(self.exclude_dirs_input)
        self.btn_default_excludes = QPushButton("Use defaults")
        self.btn_default_excludes.setToolTip(
            "Prune the usual noise: " + ", ".join(DEFAULT_EXCLUDES) +
            "\nPruned folders are never entered, so they cost nothing to scan."
        )
        self.btn_default_excludes.clicked.connect(self.set_default_excludes)
        row_excl.addWidget(self.btn_default_excludes)
        filter_layout.addLayout(row_excl)

        row_exclf = QHBoxLayout()
        row_exclf.addWidget(QLabel("Exclude files:"))
        self.exclude_files_input = QLineEdit()
        self.exclude_files_input.setPlaceholderText("glob patterns, e.g. *.tmp,~$*")
        self.exclude_files_input.editingFinished.connect(self.apply_filters_to_preview)
        row_exclf.addWidget(self.exclude_files_input)
        filter_layout.addLayout(row_exclf)

        row_sd = QHBoxLayout()
        self.size_filter_check = QCheckBox("Min file size (KB):")
        self.size_filter = QSpinBox()
        self.size_filter.setRange(0, 1_000_000)
        self.date_filter_check = QCheckBox("Modified within (days):")
        self.date_filter = QSpinBox()
        self.date_filter.setRange(0, 3650)
        row_sd.addWidget(self.size_filter_check)
        row_sd.addWidget(self.size_filter)
        row_sd.addWidget(self.date_filter_check)
        row_sd.addWidget(self.date_filter)
        row_sd.addStretch()
        filter_layout.addLayout(row_sd)
        filter_box.setLayout(filter_layout)

        # --- 3. Copy settings ---
        settings_box = QGroupBox("3. Copy Settings")
        settings_layout = QVBoxLayout()
        row1 = QHBoxLayout()
        self.extension_check = QCheckBox("Keep file extensions")
        self.extension_check.setChecked(True)
        self.copy_content_check = QCheckBox("Copy file contents")
        self.copy_content_check.setChecked(True)
        self.dry_run_check = QCheckBox("Dry run")
        self.dark_mode_check = QCheckBox("Dark mode")
        self.dark_mode_check.stateChanged.connect(self.toggle_dark_mode)
        for w in (self.extension_check, self.copy_content_check, self.dry_run_check, self.dark_mode_check):
            row1.addWidget(w)
        settings_layout.addLayout(row1)

        row2 = QHBoxLayout()
        self.skip_dup_check = QCheckBox("Skip existing duplicates (size + timestamp)")
        self.verify_hash_check = QCheckBox("Verify with hash (slower, exact)")
        row2.addWidget(self.skip_dup_check)
        row2.addWidget(self.verify_hash_check)
        row2.addStretch()
        settings_layout.addLayout(row2)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Parallel copy workers:"))
        self.workers_spin = QSpinBox()
        self.workers_spin.setRange(1, 64)
        self.workers_spin.setValue(DEFAULT_MAX_WORKERS)
        self.workers_spin.setToolTip("Lower for slow network shares, higher for many small local files.")
        row3.addWidget(self.workers_spin)
        row3.addStretch()
        self.btn_save_preset = QPushButton("Save Preset")
        self.btn_load_preset = QPushButton("Load Preset")
        self.btn_save_preset.clicked.connect(self.save_config_preset)
        self.btn_load_preset.clicked.connect(self.load_config_preset)
        row3.addWidget(self.btn_save_preset)
        row3.addWidget(self.btn_load_preset)
        settings_layout.addLayout(row3)
        settings_box.setLayout(settings_layout)

        # --- 4. Actions ---
        actions_box = QGroupBox("4. Actions")
        actions_layout = QVBoxLayout()
        btn_row = QHBoxLayout()
        self.btn_estimate = QPushButton("Estimate")
        self.btn_estimate.setToolTip("Scan the source with the current filters and report totals, without copying.")
        self.btn_refresh_preview = QPushButton("Refresh Preview")
        self.btn_start_copy = QPushButton("Start Copy")
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setEnabled(False)
        self.btn_estimate.clicked.connect(self.estimate)
        self.btn_refresh_preview.clicked.connect(self.refresh_previews)
        self.btn_start_copy.clicked.connect(self.start_copy)
        self.btn_cancel.clicked.connect(self.cancel_copy)
        for w in (self.btn_estimate, self.btn_refresh_preview, self.btn_start_copy, self.btn_cancel):
            btn_row.addWidget(w)
        actions_layout.addLayout(btn_row)

        self.progress = QProgressBar()
        self.status_label = QLabel("Ready.")
        actions_layout.addWidget(self.progress)
        actions_layout.addWidget(self.status_label)

        btn_row2 = QHBoxLayout()
        self.btn_save_log = QPushButton("Save Log")
        self.btn_export_txt = QPushButton("Export Structure (.txt)")
        self.btn_export_json = QPushButton("Export Structure (.json)")
        self.btn_save_log.clicked.connect(self.save_log)
        self.btn_export_txt.clicked.connect(lambda: self.export_structure("txt"))
        self.btn_export_json.clicked.connect(lambda: self.export_structure("json"))
        for w in (self.btn_save_log, self.btn_export_txt, self.btn_export_json):
            btn_row2.addWidget(w)
        actions_layout.addLayout(btn_row2)
        actions_box.setLayout(actions_layout)

        main_layout.addWidget(folder_box)
        main_layout.addWidget(filter_box)
        main_layout.addWidget(settings_box)
        main_layout.addWidget(actions_box)

        # --- Split views ---
        splitter = QSplitter(Qt.Orientation.Vertical)
        self.tree = LazyFolderTree("Source Folder Structure (filtered)")
        self.dest_tree = LazyFolderTree("Destination Folder Structure")
        self.log_output = QPlainTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.setMaximumBlockCount(20_000)
        splitter.addWidget(self.tree)
        splitter.addWidget(self.dest_tree)
        splitter.addWidget(self.log_output)
        splitter.setSizes([230, 230, 160])
        main_layout.addWidget(splitter)

        self.setLayout(main_layout)

    # -- Drag and drop -----------------------------------------------------

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = [u.toLocalFile() for u in event.mimeData().urls()]
        folders = [p for p in paths if os.path.isdir(p)]
        if not folders:
            return
        # First drop fills the empty slot; source first, then destination.
        for folder in folders:
            if not self.source_folder:
                self.set_source(folder)
            elif not self.destination_folder:
                self.set_destination(folder)
            else:
                self.set_source(folder)
                break
        event.acceptProposedAction()

    # -- Folder selection --------------------------------------------------

    def set_source(self, folder):
        self.source_folder = folder
        self.src_label.setText(f"Source: {folder}")
        self.tree.set_filters(self.current_scan_options())
        self.tree.load_root(folder)

    def set_destination(self, folder):
        self.destination_folder = folder
        self.dest_label.setText(f"Destination: {folder}")
        self.dest_tree.load_root(folder)

    def select_source(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Source Folder")
        if folder:
            self.set_source(folder)

    def select_destination(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Destination Folder")
        if folder:
            self.set_destination(folder)

    def refresh_previews(self):
        self.apply_filters_to_preview()
        if self.destination_folder:
            self.dest_tree.load_root(self.destination_folder)

    def apply_filters_to_preview(self):
        self.tree.set_filters(self.current_scan_options())
        if self.source_folder:
            self.tree.reload()

    def set_default_excludes(self):
        self.exclude_dirs_input.setText(",".join(DEFAULT_EXCLUDES))
        self.apply_filters_to_preview()

    # -- Options -----------------------------------------------------------

    def current_scan_options(self):
        return ScanOptions.from_text(
            ext_text=self.filter_input.text(),
            exclude_dir_text=self.exclude_dirs_input.text(),
            exclude_file_text=self.exclude_files_input.text(),
            min_size_kb=self.size_filter.value() if self.size_filter_check.isChecked() else 0,
            modified_within_days=self.date_filter.value() if self.date_filter_check.isChecked() else 0,
        )

    def current_copy_options(self):
        return CopyOptions(
            copy_content=self.copy_content_check.isChecked(),
            keep_ext=self.extension_check.isChecked(),
            dry_run=self.dry_run_check.isChecked(),
            skip_duplicates=self.skip_dup_check.isChecked(),
            verify_hash=self.verify_hash_check.isChecked(),
            max_workers=self.workers_spin.value(),
        )

    # -- Estimate ----------------------------------------------------------

    def estimate(self):
        if not self.source_folder:
            self.log("Please select a source folder first.")
            return
        if self.scan_worker is not None and self.scan_worker.isRunning():
            return
        self.status_label.setText("Scanning...")
        self.scan_worker = ScanThread(self.source_folder, self.current_scan_options())
        self.scan_worker.done.connect(self.on_estimate_done)
        self.scan_worker.failed.connect(lambda e: self.log(f"Scan failed: {e}"))
        self.scan_worker.start()

    def on_estimate_done(self, scan):
        msg = (f"{len(scan.files)} files, {human_bytes(scan.total_bytes)} across "
               f"{len(scan.dir_rels)} folders  ({scan.pruned_dirs} folders pruned, "
               f"{scan.filtered_out} files filtered out, scan took {scan.elapsed:.2f}s)")
        self.status_label.setText(msg)
        self.log("Estimate: " + msg)

    # -- Copy --------------------------------------------------------------

    def start_copy(self):
        if not self.source_folder or not self.destination_folder:
            self.log("Both source and destination folders must be selected.")
            return
        if self.worker is not None and self.worker.isRunning():
            self.log("A copy is already running.")
            return

        self.log_output.clear()
        self.log_history.clear()
        self.progress.setValue(0)
        self.status_label.setText("Scanning...")

        self.worker = CopyThread(
            self.source_folder, self.destination_folder,
            self.current_scan_options(), self.current_copy_options(),
        )
        self.worker.scan_done.connect(self.on_scan_done)
        self.worker.progress_update.connect(self.on_progress)
        self.worker.log_batch.connect(self.on_log_batch)
        self.worker.finished_ok.connect(self.on_copy_finished)
        self.worker.failed.connect(self.on_copy_failed)

        self.btn_start_copy.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.worker.start()

    def cancel_copy(self):
        if self.worker is not None:
            self.worker.cancel()
            self.status_label.setText("Cancelling...")

    def on_scan_done(self, scan):
        self.log(f"Scanned {len(scan.dir_rels)} folders, {len(scan.files)} files, "
                 f"{human_bytes(scan.total_bytes)} in {scan.elapsed:.2f}s "
                 f"({scan.pruned_dirs} folders pruned).")

    def on_progress(self, p):
        self.progress.setValue(p.pct)
        eta = f"  ETA {human_duration(p.eta_seconds)}" if p.eta_seconds else ""
        self.status_label.setText(
            f"{p.done_files}/{p.total_files} files  |  "
            f"{human_bytes(p.done_bytes)} / {human_bytes(p.total_bytes)}  |  "
            f"{human_bytes(p.bytes_per_sec)}/s{eta}"
        )

    def on_log_batch(self, lines):
        self.log_history.extend(lines)
        self.log_output.appendPlainText("\n".join(lines))

    def on_copy_finished(self, stats):
        self.btn_start_copy.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.progress.setValue(100 if not stats.cancelled else self.progress.value())
        rate = stats.bytes_copied / stats.elapsed if stats.elapsed else 0
        head = "Cancelled" if stats.cancelled else "Done"
        summary = (f"{head}: {stats.copied} copied ({human_bytes(stats.bytes_copied)}), "
                   f"{stats.skipped} skipped, {stats.errors} errors in "
                   f"{stats.elapsed:.2f}s ({human_bytes(rate)}/s)")
        self.status_label.setText(summary)
        self.log(summary)
        if self.destination_folder:
            self.dest_tree.load_root(self.destination_folder)

    def on_copy_failed(self, message):
        self.btn_start_copy.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.status_label.setText("Failed.")
        self.log(f"Failed: {message}")
        QMessageBox.warning(self, "Copy failed", message)

    # -- Export / logging --------------------------------------------------

    def export_structure(self, kind):
        if not self.source_folder:
            self.log("Please select a source folder first.")
            return
        filter_str = "Text Files (*.txt)" if kind == "txt" else "JSON Files (*.json)"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Structure As", f"folder_structure.{kind}", filter_str)
        if not path:
            return
        self.export_worker = ScanThread(self.source_folder, self.current_scan_options(), export_path=path)
        self.export_worker.done.connect(lambda _s: self.log(f"Folder structure exported to: {path}"))
        self.export_worker.failed.connect(lambda e: self.log(f"Failed to export structure: {e}"))
        self.export_worker.start()

    def log(self, message):
        self.log_history.append(message)
        self.log_output.appendPlainText(message)

    def save_log(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save Log", "copy_log.txt", "Text Files (*.txt)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(self.log_history))
            self.log(f"Log saved to {path}")

    # -- Presets / settings ------------------------------------------------

    def toggle_dark_mode(self, state):
        if state == Qt.CheckState.Checked.value:
            self.setStyleSheet(
                "QWidget { background-color: #2e2e2e; color: #eee; }"
                "QLineEdit, QPlainTextEdit, QTreeWidget, QSpinBox "
                "{ background-color: #3b3b3b; color: #eee; }"
            )
        else:
            self.setStyleSheet("")

    def _preset_dict(self):
        return {
            "filter": self.filter_input.text(),
            "exclude_dirs": self.exclude_dirs_input.text(),
            "exclude_files": self.exclude_files_input.text(),
            "keep_ext": self.extension_check.isChecked(),
            "copy_content": self.copy_content_check.isChecked(),
            "dry_run": self.dry_run_check.isChecked(),
            "dark_mode": self.dark_mode_check.isChecked(),
            "skip_duplicates": self.skip_dup_check.isChecked(),
            "verify_hash": self.verify_hash_check.isChecked(),
            "size_filter_enabled": self.size_filter_check.isChecked(),
            "size_filter_kb": self.size_filter.value(),
            "date_filter_enabled": self.date_filter_check.isChecked(),
            "date_filter_days": self.date_filter.value(),
            "max_workers": self.workers_spin.value(),
        }

    def _apply_preset(self, data):
        self.filter_input.setText(data.get("filter", ""))
        self.exclude_dirs_input.setText(data.get("exclude_dirs", ""))
        self.exclude_files_input.setText(data.get("exclude_files", ""))
        self.extension_check.setChecked(data.get("keep_ext", True))
        self.copy_content_check.setChecked(data.get("copy_content", True))
        self.dry_run_check.setChecked(data.get("dry_run", False))
        self.skip_dup_check.setChecked(data.get("skip_duplicates", False))
        self.verify_hash_check.setChecked(data.get("verify_hash", False))
        self.size_filter_check.setChecked(data.get("size_filter_enabled", False))
        self.size_filter.setValue(data.get("size_filter_kb", 0))
        self.date_filter_check.setChecked(data.get("date_filter_enabled", False))
        self.date_filter.setValue(data.get("date_filter_days", 0))
        self.workers_spin.setValue(data.get("max_workers", DEFAULT_MAX_WORKERS))
        dark = data.get("dark_mode", False)
        self.dark_mode_check.setChecked(dark)
        self.toggle_dark_mode(Qt.CheckState.Checked.value if dark else Qt.CheckState.Unchecked.value)
        self.tree.set_filters(self.current_scan_options())

    def save_config_preset(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save Preset As", "preset.json", "JSON Files (*.json)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._preset_dict(), f, indent=2)
            self.log(f"Preset saved to {path}")
        except Exception as e:  # noqa: BLE001
            self.log(f"Error saving preset: {e}")

    def load_config_preset(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load Preset", "", "JSON Files (*.json)")
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                self._apply_preset(json.load(f))
            self.apply_filters_to_preview()
            self.log(f"Preset loaded from {path}")
        except Exception as e:  # noqa: BLE001
            self.log(f"Error loading preset: {e}")

    def load_settings(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    self._apply_preset(json.load(f))
            except Exception:  # noqa: BLE001 - a bad config must never block startup
                pass

    def closeEvent(self, event):
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self._preset_dict(), f, indent=2)
        except Exception:  # noqa: BLE001
            pass
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(2000)
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = FolderCopyApp()
    window.show()
    sys.exit(app.exec())
