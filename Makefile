# ─── Anno 117 Layout Tool – build targets ────────────────────────────────────
#
# Requirements (install once):
#   pip install pyinstaller
#
# Usage:
#   make exe      – build dist/Anno117LayoutTool.exe  (Windows)
#   make clean    – remove build artefacts
# ─────────────────────────────────────────────────────────────────────────────

APP_NAME  := Anno 117 Layout Tool
MAIN      := main.py
ICON      := data/ui/app_icon.ico
DIST_DIR  := dist
BUILD_DIR := build

# PyInstaller --add-data separator: ';' on Windows (cmd / Git-Bash + GNU make),
# ':' on Linux / macOS.
ifeq ($(OS),Windows_NT)
    SEP := ;
else
    SEP := :
endif

.PHONY: exe clean

exe:
	pyinstaller \
		--onefile \
		--windowed \
		--name "$(APP_NAME)" \
		--icon "$(ICON)" \
		--version-file "file_version_info.txt" \
		--add-data "data$(SEP)data" \
		--distpath "$(DIST_DIR)" \
		--workpath "$(BUILD_DIR)" \
		--noconfirm \
		"$(MAIN)"

clean:
	rm -rf "$(BUILD_DIR)" "$(DIST_DIR)" "$(APP_NAME).spec"
