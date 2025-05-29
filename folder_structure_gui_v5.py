import os
import sys
import json
import shutil
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QPushButton, QFileDialog, QLabel,
    QCheckBox, QProgressBar, QTextEdit, QTreeWidget, QTreeWidgetItem, QLineEdit,
    QSpinBox, QProgressDialog, QSplitter, QGroupBox, QHBoxLayout
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal

CONFIG_FILE = "folder_copier_config.json"

class FolderCopyWorker(QThread):
    progress_update = pyqtSignal(int)
    log_message = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, source, destination, copy_content=True, extensions=None, keep_ext=True, dry_run=False):
        super().__init__()
        self.source = source
        self.destination = destination
        self.copy_content = copy_content
        self.extensions = extensions or []
        self.keep_ext = keep_ext
        self.dry_run = dry_run


    def run(self):
        total = 0
        for _, _, files in os.walk(self.source):
            if self.extensions:
                total += sum(1 for f in files if any(f.endswith(ext) for ext in self.extensions))
            else:    
                total += len(files)
        #total = sum(len(files) for _, _, files in os.walk(self.source))
        count = 0
        for root, dirs, files in os.walk(self.source):
            rel_path = os.path.relpath(root, self.source)
            dest_dir = os.path.join(self.destination, rel_path)
            if not self.dry_run:
                os.makedirs(dest_dir, exist_ok=True)

            for file in files:
                if self.extensions:
                    if not any(file.endswith(ext) for ext in self.extensions):
                        continue

                src_file = os.path.join(root, file)
                dest_file = os.path.join(dest_dir, file if self.keep_ext else os.path.splitext(file)[0])

                try:
                    if self.dry_run:
                        self.log_message.emit(f"Would create: {dest_file}")
                    elif self.copy_content:
                        shutil.copy2(src_file, dest_file)
                        self.log_message.emit(f"Copied: {src_file} -> {dest_file}")
                    else:
                        open(dest_file, 'w').close()
                        self.log_message.emit(f"Created empty file: {dest_file}")
                except Exception as e:
                    self.log_message.emit(f"Error copying {src_file}: {e}")

                count += 1
                self.progress_update.emit(int((count / total) * 100))

        self.finished.emit()

class FolderCopyApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Folder Structure Copier")
        self.resize(1000, 700)
        self.source_folder = ""
        self.destination_folder = ""
        self.log_history = []
        self.init_ui()
        self.load_settings()

    def init_ui(self):
        main_layout = QVBoxLayout()

        # --- Folder Selection ---
        folder_box = QGroupBox("1. Folder Selection")
        folder_box.setStyleSheet("QGroupBox { font-weight: bold; color: #2a4b8d; }")
        folder_layout = QVBoxLayout()
        self.src_label = QLabel("Source Folder: Not selected")
        self.src_label.setStyleSheet("font-weight: bold; color: #555;")
        self.dest_label = QLabel("Destination Folder: Not selected")
        self.dest_label.setStyleSheet("font-weight: bold; color: #555;")
        self.btn_src = QPushButton("Select Source Folder")
        self.btn_dest = QPushButton("Select Destination Folder")
        self.btn_src.clicked.connect(self.select_source)
        self.btn_dest.clicked.connect(self.select_destination)
        folder_layout.addWidget(self.btn_src)
        folder_layout.addWidget(self.src_label)
        folder_layout.addWidget(self.btn_dest)
        folder_layout.addWidget(self.dest_label)
        folder_box.setLayout(folder_layout)

        # --- Preview Settings ---
        preview_box = QGroupBox("2. Preview Options")
        preview_box.setStyleSheet("QGroupBox { font-weight: bold; color: #2a4b8d; }")
        preview_layout = QVBoxLayout()
        self.preview_depth_input = QSpinBox()
        self.preview_depth_input.setMinimum(1)
        self.preview_depth_input.setMaximum(20)
        self.preview_depth_input.setValue(3)
        self.skip_preview_check = QCheckBox("Skip folder structure preview")
        preview_layout.addWidget(QLabel("Preview Depth:"))
        preview_layout.addWidget(self.preview_depth_input)
        preview_layout.addWidget(self.skip_preview_check)
        self.btn_preview_filtered = QPushButton("Preview Filtered Now")
        self.btn_preview_filtered.setToolTip("Update the source preview based on extension filter.")
        self.btn_preview_filtered.clicked.connect(self.preview_filtered_source)
        preview_layout.addWidget(self.btn_preview_filtered)

        preview_box.setLayout(preview_layout)

        # --- Copy Settings ---
        settings_box = QGroupBox("3. Copy Settings")
        settings_box.setStyleSheet("QGroupBox { font-weight: bold; color: #2a4b8d; }")
        settings_layout = QVBoxLayout()
        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("Include only files with extensions (e.g. .py,.txt)")
        self.extension_check = QCheckBox("Keep file extensions")
        self.copy_content_check = QCheckBox("Copy file contents")
        self.dry_run_check = QCheckBox("Dry Run (no actual files created)")
        self.dark_mode_check = QCheckBox("Enable Dark Mode")
        self.dark_mode_check.stateChanged.connect(self.toggle_dark_mode)
        #self.dark_mode_check.stateChanged.connect(self.toggle_dark_mode)
        
        self.btn_save_preset = QPushButton("Save Preset")
        self.btn_load_preset = QPushButton("Load Preset")
        self.btn_save_preset.clicked.connect(self.save_config_preset)
        self.btn_load_preset.clicked.connect(self.load_config_preset)
        settings_layout.addWidget(self.btn_save_preset)
        settings_layout.addWidget(self.btn_load_preset)

        self.extension_check.setToolTip("If unchecked, file extensions (e.g. .txt) will be removed from copied files.")
        self.copy_content_check.setToolTip("If checked, the content of each file will be copied. Otherwise, empty files will be created.")
        self.dry_run_check.setToolTip("Simulates the copy operation without creating any files or folders. Use this to preview what would happen.")
        self.dark_mode_check.setToolTip("Toggles dark mode for the user interface.")

        settings_layout.addWidget(QLabel("File Extension Filter:"))
        settings_layout.addWidget(self.filter_input)
        settings_layout.addWidget(self.extension_check)
        settings_layout.addWidget(self.copy_content_check)
        settings_layout.addWidget(self.dry_run_check)
        settings_layout.addWidget(self.dark_mode_check)
        settings_box.setLayout(settings_layout)

        # --- Action Buttons ---
        actions_box = QGroupBox("4. Actions")
        actions_box.setStyleSheet("QGroupBox { font-weight: bold; color: #2a4b8d; }")
        actions_layout = QVBoxLayout()
        self.btn_preview = QPushButton("Preview Source Now")
        self.btn_start_copy = QPushButton("Start Copy Anyway")
        self.btn_save_log = QPushButton("Save Log")
        self.btn_export_structure = QPushButton("Export Folder Structure")
        self.progress = QProgressBar()
        self.btn_preview.clicked.connect(self.preview_source)
        self.btn_start_copy.clicked.connect(self.start_copy)
        self.btn_save_log.clicked.connect(self.save_log)
        self.btn_export_structure.clicked.connect(self.save_folder_structure)
        actions_layout.addWidget(self.btn_preview)
        actions_layout.addWidget(self.btn_start_copy)
        actions_layout.addWidget(self.progress)
        actions_layout.addWidget(self.btn_save_log)
        actions_layout.addWidget(self.btn_export_structure)
        actions_box.setLayout(actions_layout)

        # --- Layout All Groups ---
        main_layout.addWidget(folder_box)
        main_layout.addWidget(preview_box)
        main_layout.addWidget(settings_box)
        main_layout.addWidget(actions_box)

        # --- Split Views ---
        splitter = QSplitter(Qt.Orientation.Vertical)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Source Folder Structure"])
        self.dest_tree = QTreeWidget()
        self.dest_tree.setHeaderLabels(["Destination Folder Structure"])
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        splitter.addWidget(self.tree)
        splitter.addWidget(self.dest_tree)
        splitter.addWidget(self.log_output)
        splitter.setSizes([150, 150, 100])

        main_layout.addWidget(splitter)
        self.setLayout(main_layout)

    def save_folder_structure(self):
        if not self.source_folder:
            self.log("Please select a source folder first.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Structure As", "folder_structure.txt", "Text Files (*.txt);;JSON Files (*.json)"
)
        if not path:
            return

        structure = []
        for root, dirs, files in os.walk(self.source_folder):
            rel_root = os.path.relpath(root, self.source_folder)
            structure.append({
                "path": rel_root,
                "files": files
            })

        try:
            if path.endswith(".json"):
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(structure, f, indent=2)
            else:
                with open(path, "w", encoding="utf-8") as f:
                    def write_tree(structure):
                        tree = {}
                        for entry in structure:
                            parts = entry["path"].split(os.sep) if entry["path"] != "." else []
                            current = tree
                            for part in parts:
                                current = current.setdefault(part, {})
                            current["_files"] = entry["files"]
            
                        def render(current, prefix=""):
                            keys = [k for k in current if k != "_files"]
                            for i, key in enumerate(keys):
                                is_last = (i == len(keys) - 1)
                                branch = "└── " if is_last else "├── "
                                sub_prefix = "    " if is_last else "│   "
                                f.write(f"{prefix}{branch}📁 {key}\n")
                                if "_files" in current[key]:
                                    for file in current[key]["_files"]:
                                        f.write(f"{prefix}{sub_prefix}📄 {file}\n")
                                render(current[key], prefix + sub_prefix)
            
                        base_name = os.path.basename(os.path.normpath(self.source_folder))
                        f.write(f"📁 {base_name}\n")
                        render(tree)
                    write_tree(structure)    
            self.log(f"Folder structure exported to: {path}")
        except Exception as e:
            self.log(f"Failed to export structure: {e}")

    def start_copy(self):
        if not self.source_folder or not self.destination_folder:
            self.log("Both source and destination folders must be selected.")
            return

        extensions = [ext.strip() for ext in self.filter_input.text().split(',') if ext.strip()]
        self.worker = FolderCopyWorker(
            self.source_folder,
            self.destination_folder,
            copy_content=self.copy_content_check.isChecked(),
            extensions=extensions,
            keep_ext=self.extension_check.isChecked(),
            dry_run=self.dry_run_check.isChecked()
        )
        self.worker.progress_update.connect(self.progress.setValue)
        self.worker.log_message.connect(self.log)
        #self.worker.finished.connect(lambda: self.log("Copy process finished."))
        self.worker.finished.connect(lambda: (self.log("Copy process finished."), self.preview_destination()))

        self.worker.start()

    def select_source(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Source Folder")
        if folder:
            self.source_folder = folder
            self.src_label.setText(f"Source Folder: {folder}")

    def select_destination(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Destination Folder")
        if folder:
            self.destination_folder = folder
            self.dest_label.setText(f"Destination Folder: {folder}")

    def log(self, message):
        self.log_history.append(message)
        self.log_output.append(message)

    def toggle_dark_mode(self, state):
        if state == Qt.CheckState.Checked.value:
            self.setStyleSheet("QWidget { background-color: #2e2e2e; color: white; }")
        else:
            self.setStyleSheet("")

    def preview_source(self):
        if not self.source_folder:
            self.log("Please select a source folder first.")
            return
        self.tree.clear()
        depth = self.preview_depth_input.value()

        def add_items(parent_item, path, current_depth):
            if current_depth > depth:
                return
            try:
                for name in os.listdir(path):
                    full_path = os.path.join(path, name)
                    item = QTreeWidgetItem([name])
                    parent_item.addChild(item)
                    if os.path.isdir(full_path):
                        add_items(item, full_path, current_depth + 1)
            except Exception as e:
                self.log(f"Error reading directory {path}: {e}")

        folder_name = os.path.basename(os.path.normpath(self.source_folder)) or self.source_folder
        root_item = QTreeWidgetItem([folder_name])
        self.tree.addTopLevelItem(root_item)
        add_items(root_item, self.source_folder, 1)

    def preview_destination(self):
        if not self.destination_folder or not os.path.exists(self.destination_folder):
            self.log("Please select a valid destination folder first.")
            return
    
        self.dest_tree.clear()
        depth = self.preview_depth_input.value()
    
        def add_items(parent_item, path, current_depth):
            if current_depth > depth:
                return
            try:
                for name in os.listdir(path):
                    full_path = os.path.join(path, name)
                    item = QTreeWidgetItem([name])
                    parent_item.addChild(item)
                    if os.path.isdir(full_path):
                        add_items(item, full_path, current_depth + 1)
            except Exception as e:
                self.log(f"Error reading directory {path}: {e}")
    
        folder_name = os.path.basename(os.path.normpath(self.destination_folder)) or self.destination_folder
        root_item = QTreeWidgetItem([folder_name])
        self.dest_tree.addTopLevelItem(root_item)
        add_items(root_item, self.destination_folder, 1)

    def preview_filtered_source(self):
        if not self.source_folder:
            self.log("Please select a source folder first.")
            return
    
        self.tree.clear()
        depth = self.preview_depth_input.value()
        filter_text = self.filter_input.text()
        extensions = [ext.strip() for ext in filter_text.split(',') if ext.strip()]
    
        def add_items(parent_item, path, current_depth):
            if current_depth > depth:
                return
            try:
                for name in sorted(os.listdir(path)):
                    full_path = os.path.join(path, name)
                    if os.path.isdir(full_path):
                        dir_item = QTreeWidgetItem([f"📁 {name}"])
                        parent_item.addChild(dir_item)
                        add_items(dir_item, full_path, current_depth + 1)
                    elif not extensions or any(name.endswith(ext) for ext in extensions):
                        file_item = QTreeWidgetItem([f"📄 {name}"])
                        parent_item.addChild(file_item)
            except Exception as e:
                self.log(f"Error reading directory {path}: {e}")
    
        folder_name = os.path.basename(os.path.normpath(self.source_folder)) or self.source_folder
        root_item = QTreeWidgetItem([f"📁 {folder_name}"])
        self.tree.addTopLevelItem(root_item)
        add_items(root_item, self.source_folder, 1)
        
    def save_config_preset(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save Preset As", "preset.json", "JSON Files (*.json)")
        if not path:
            return
        data = {
            "filter": self.filter_input.text(),
            "keep_ext": self.extension_check.isChecked(),
            "copy_content": self.copy_content_check.isChecked(),
            "dry_run": self.dry_run_check.isChecked(),
            "dark_mode": self.dark_mode_check.isChecked(),
            "preview_depth": self.preview_depth_input.value()
        }
        try:
            with open(path, "w") as f:
                json.dump(data, f, indent=2)
            self.log(f"Preset saved to {path}")
        except Exception as e:
            self.log(f"Error saving preset: {e}")
    
    def load_config_preset(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load Preset", "", "JSON Files (*.json)")
        if not path:
            return
        try:
            with open(path, "r") as f:
                data = json.load(f)
            self.filter_input.setText(data.get("filter", ""))
            self.extension_check.setChecked(data.get("keep_ext", True))
            self.copy_content_check.setChecked(data.get("copy_content", True))
            self.dry_run_check.setChecked(data.get("dry_run", False))
            self.dark_mode_check.setChecked(data.get("dark_mode", False))
            self.preview_depth_input.setValue(data.get("preview_depth", 3))
            self.toggle_dark_mode(Qt.CheckState.Checked.value if data.get("dark_mode", False) else Qt.CheckState.Unchecked.value)
            self.log(f"Preset loaded from {path}")
        except Exception as e:
            self.log(f"Error loading preset: {e}")
            

    def save_log(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save Log", "copy_log.txt", "Text Files (*.txt)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(self.log_history))
            self.log(f"Log saved to {path}")

    def load_settings(self):
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
                self.filter_input.setText(data.get("filter", ""))
                self.extension_check.setChecked(data.get("keep_ext", True))
                self.copy_content_check.setChecked(data.get("copy_content", True))
                self.dry_run_check.setChecked(data.get("dry_run", False))
                self.dark_mode_check.setChecked(data.get("dark_mode", False))

    def closeEvent(self, event):
        data = {
            "filter": self.filter_input.text(),
            "keep_ext": self.extension_check.isChecked(),
            "copy_content": self.copy_content_check.isChecked(),
            "dry_run": self.dry_run_check.isChecked(),
            "dark_mode": self.dark_mode_check.isChecked(),
        }
        with open(CONFIG_FILE, "w") as f:
            json.dump(data, f, indent=2)
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = FolderCopyApp()
    window.show()
    sys.exit(app.exec())
