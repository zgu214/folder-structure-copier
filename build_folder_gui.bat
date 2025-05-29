@echo off
echo Building Folder Structure Copier using PyInstaller...
pyinstaller --noconsole --onefile --windowed folder_structure_gui.py --name FolderStructureCopier
echo Build complete! Check the 'dist' folder.
pause
