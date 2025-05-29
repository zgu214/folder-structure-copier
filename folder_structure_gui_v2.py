import os
import sys
import json
import shutil
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QPushButton, QFileDialog, QLabel,
    QCheckBox, QProgressBar, QTextEdit, QTreeWidget, QTreeWidgetItem, QLineEdit,
    QSpinBox, QProgressDialog, QSplitter
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QPushButton, QFileDialog, QLabel,
    QCheckBox, QProgressBar, QTextEdit, QTreeWidget, QTreeWidgetItem, QLineEdit,
    QSpinBox, QProgressDialog, QSplitter, QGroupBox, QHBoxLayout
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
CONFIG_FILE = "folder_copier_config.json"

class FolderCopyWorker(QThread):
    progress = pyqtSignal(int)
    log = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(self, source, destination, keep_extensions=True, dry_run=False, copy_contents=False):
        super().__init__()
        self.source = source
        self.destination = destination
        self.keep_extensions = keep_extensions
        self.allowed_extensions = []
        self.dry_run = dry_run
        self.copy_contents = copy_contents

    def run(self):
        folder_structure = {}
        file_structure = {}

        for dirpath, dirnames, filenames in os.walk(self.source):
            folder_structure[dirpath] = dirnames
            file_structure[dirpath] = filenames

        total_files = sum(len(files) for files in file_structure.values())
        current = 0

        for parent_folder, subfolders in folder_structure.items():
            rel_path = os.path.relpath(parent_folder, self.source)
            new_parent = os.path.join(self.destination, rel_path)
            if not self.dry_run:
                os.makedirs(new_parent, exist_ok=True)
            self.log.emit(f"[DIR] {new_parent}")

            for subfolder in subfolders:
                new_sub = os.path.join(new_parent, subfolder)
                if not self.dry_run:
                    os.makedirs(new_sub, exist_ok=True)
                self.log.emit(f"[DIR] {new_sub}")

            for filename in file_structure[parent_folder]:
                if self.allowed_extensions:
                    if not any(filename.lower().endswith(ext) for ext in self.allowed_extensions):
                        continue

                source_file_path = os.path.join(parent_folder, filename)
                if not self.keep_extensions:
                    filename = os.path.splitext(filename)[0]

                dest_file_path = os.path.join(new_parent, filename)
                if not self.dry_run:
                    if self.copy_contents:
                        shutil.copy2(source_file_path, dest_file_path)
                    else:
                        open(dest_file_path, 'w').close()

                self.log.emit(f"[FILE] {dest_file_path}")
                current += 1
                self.progress.emit(int((current / total_files) * 100))

        self.finished.emit()

class FolderCopyApp(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Folder Structure Copier")
        self.setMinimumWidth(700)
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
        self.btn_export_structure.clicked.connect(self.save_folder_structure)
        self.progress = QProgressBar()
        self.btn_preview.clicked.connect(self.preview_source)
        self.btn_start_copy.clicked.connect(self.start_copy)
        self.btn_save_log.clicked.connect(self.save_log)
        actions_layout.addWidget(self.btn_preview)
        actions_layout.addWidget(self.btn_start_copy)
        actions_layout.addWidget(self.progress)
        actions_layout.addWidget(self.btn_save_log)
        actions_box.setLayout(actions_layout)
        actions_layout.addWidget(self.btn_save_log)
        actions_layout.addWidget(self.btn_export_structure)

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
            self, "Save Structure As", "folder_structure.txt", "Text Files (*.txt)"
        )
        if not path:
            return
    
        with open(path, "w", encoding="utf-8") as f:
            for root, dirs, files in os.walk(self.source_folder):
                level = root.replace(self.source_folder, '').count(os.sep)
                indent = '    ' * level
                f.write(f"{indent}📁 {os.path.basename(root)}/\n")
                subindent = '    ' * (level + 1)
                for file in files:
                    f.write(f"{subindent}📄 {file}\n")
    
        self.log(f"Folder structure exported to: {path}")

    def start_copy(self):
        if not self.source_folder or not self.destination_folder:
            self.log("Please select both source and destination folders.")
            return
        self.log_output.clear()
        self.log_history.clear()
        self.progress.setValue(0)

        extensions = [e.strip().lower() for e in self.filter_input.text().split(',') if e.strip()]

        self.worker = FolderCopyWorker(
            source=self.source_folder,
            destination=self.destination_folder,
            keep_extensions=self.extension_check.isChecked(),
            dry_run=self.dry_run_check.isChecked(),
            copy_contents=self.copy_content_check.isChecked()
        )
        self.worker.allowed_extensions = extensions
        self.worker.progress.connect(self.progress.setValue)
        self.worker.log.connect(self.log)
        self.worker.finished.connect(lambda: self.log("Copy complete."))
        self.worker.start()

    def save_settings(self):
        data = {
            "source_folder": self.source_folder,
            "destination_folder": self.destination_folder,
            "extensions": self.filter_input.text().strip(),
            "keep_extensions": self.extension_check.isChecked(),
            "preview_depth": self.preview_depth_input.value(),
            "skip_preview": self.skip_preview_check.isChecked(),
            "dry_run": self.dry_run_check.isChecked(),
            "copy_contents": self.copy_content_check.isChecked()
        }
        with open(CONFIG_FILE, 'w') as f:
            json.dump(data, f, indent=2)

    def load_settings(self):
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r') as f:
                try:
                    data = json.load(f)
                    self.source_folder = data.get("source_folder", "")
                    self.destination_folder = data.get("destination_folder", "")
                    self.filter_input.setText(data.get("extensions", ""))
                    self.extension_check.setChecked(data.get("keep_extensions", True))
                    self.copy_content_check.setChecked(data.get("copy_contents", False))
                    self.preview_depth_input.setValue(data.get("preview_depth", 3))
                    self.skip_preview_check.setChecked(data.get("skip_preview", False))
                    self.dry_run_check.setChecked(data.get("dry_run", False))
                    if self.source_folder:
                        self.src_label.setText(f"Source Folder: {self.source_folder}")
                    if self.destination_folder:
                        self.dest_label.setText(f"Destination Folder: {self.destination_folder}")
                except Exception as e:
                    self.log(f"Error loading settings: {e}")

    def log(self, message):
        self.log_history.append(message)
        self.log_output.append(message)

    def save_log(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save Log", "copy_log.txt", "Text Files (*.txt)")
        if path:
            with open(path, "w") as f:
                f.write("".join(self.log_history))
            self.log(f"Log saved to {path}")

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

    def preview_source(self):
        if not self.source_folder:
            self.log("Please select a source folder.")
            return
        self.tree.clear()
        root_item = QTreeWidgetItem([os.path.basename(self.source_folder)])
        self.tree.addTopLevelItem(root_item)
        self.add_preview_items(root_item, self.source_folder, self.preview_depth_input.value())
        self.tree.expandAll()

    def add_preview_items(self, parent, path, depth):
        if depth <= 0:
            return
        try:
            for item in os.listdir(path):
                full_path = os.path.join(path, item)
                child = QTreeWidgetItem([item])
                parent.addChild(child)
                if os.path.isdir(full_path):
                    self.add_preview_items(child, full_path, depth - 1)
        except Exception as e:
            parent.addChild(QTreeWidgetItem([f"[Access Denied: {e}]"]))

    def toggle_dark_mode(self):
        if self.dark_mode_check.isChecked():
            self.setStyleSheet("""
                QWidget { background-color: #2b2b2b; color: #f0f0f0; }
                QPushButton { background-color: #3c3f41; color: white; }
                QLineEdit, QTreeWidget, QTextEdit, QSpinBox { background-color: #3c3f41; color: white; }
            """)
        else:
            self.setStyleSheet("")

        
if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = FolderCopyApp()
    window.show()
    sys.exit(app.exec())
