"""
tool_manager.py – Locate or download the two external CLI tools required for
savegame importing:

  • RdaConsole.exe   (from RDAExplorer) – extracts files from .a8s RDA archives
  • FileDBReader.exe (from anno-mods)   – converts FileDB binary blobs to XML

Both are small framework-dependent .NET executables (~300 KB–1 MB download).
Running them requires .NET 6+ to be installed on the user's machine.
"""

import json
import os
import shutil
import subprocess
import threading
import tkinter as tk
import tkinter.ttk as ttk
import tkinter.filedialog as fd
import urllib.request
import webbrowser
import zipfile
from pathlib import Path
from typing import Optional

from config import SETTINGS_DIR, BG_MAIN, BG_SECTION, FG_MAIN, FG_GOLD

# ── Constants ──────────────────────────────────────────────────────────────────

# Tools are downloaded / cached here.
TOOL_DIR = Path(SETTINGS_DIR) / "tools"

DOTNET_URL = "https://dotnet.microsoft.com/en-us/download/dotnet/8.0"

# GitHub release info for each tool.
# 'exes' lists candidate filenames in priority order; the first one found in
# the extracted zip wins.
_TOOL_INFO = {
    'RdaConsole': {
        'label':   'RdaConsole (RDA Archive Extractor)',
        'setting': 'rda_console_path',
        'api':     'https://api.github.com/repos/lysanntranvouez/RDAExplorer/releases/latest',
        'asset':   None,          # None → use the first .zip asset in the release
        'exes':    ['RdaConsole.exe', 'RDAConsole.exe'],
        'subdir':  'RDAExplorer',
    },
    'FileDBReader': {
        'label':   'FileDBReader (FileDB Binary Decoder)',
        'setting': 'filedb_reader_path',
        'api':     'https://api.github.com/repos/anno-mods/FileDBReader/releases/latest',
        'asset':   'FileDBReader.zip',
        'exes':    ['FileDBReader.exe'],
        'subdir':  'FileDBReader',
    },
}

# Directories to scan in addition to PATH and the managed TOOL_DIR.
_EXTRA_SEARCH_DIRS = [
    Path('C:/tools'),
    Path('C:/tools/RDAExplorer'),
    Path('C:/tools/FileDBReader'),
    Path(os.environ.get('PROGRAMFILES', 'C:/Program Files')) / 'RDAExplorer',
    Path(os.environ.get('PROGRAMFILES(X86)', 'C:/Program Files (x86)')) / 'RDAExplorer',
]

# ── Internal helpers ───────────────────────────────────────────────────────────

def _find_exe(exe_names: list[str]) -> Optional[Path]:
    """Search TOOL_DIR, extra dirs, and PATH for any of the given exe names."""
    search_dirs = [TOOL_DIR / _TOOL_INFO['RdaConsole']['subdir'],
                   TOOL_DIR / _TOOL_INFO['FileDBReader']['subdir'],
                   TOOL_DIR,
                   *_EXTRA_SEARCH_DIRS]
    for name in exe_names:
        for d in search_dirs:
            p = d / name
            if p.is_file():
                return p
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def _check_dotnet() -> bool:
    """Return True if .NET 6+ runtime is detected."""
    # Fast path: check the standard install directory on Windows
    dotnet_base = Path(r'C:\Program Files\dotnet\shared\Microsoft.NETCore.App')
    if dotnet_base.is_dir():
        for entry in dotnet_base.iterdir():
            try:
                if int(entry.name.split('.')[0]) >= 6:
                    return True
            except (ValueError, IndexError):
                pass
    # Fallback: call dotnet CLI
    try:
        result = subprocess.run(
            ['dotnet', '--list-runtimes'],
            capture_output=True, timeout=8
        )
        output = result.stdout.decode('utf-8', errors='ignore')
        for line in output.splitlines():
            if line.startswith('Microsoft.NETCore.App'):
                try:
                    if int(line.split()[1].split('.')[0]) >= 6:
                        return True
                except (IndexError, ValueError):
                    pass
    except Exception:
        pass
    return False


def _fetch_download_url(api_url: str, preferred_asset: Optional[str]) -> Optional[str]:
    """Query the GitHub releases API and return the download URL for the asset."""
    req = urllib.request.Request(
        api_url,
        headers={'User-Agent': 'Anno117LayoutTool/1.0', 'Accept': 'application/json'}
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    assets = data.get('assets', [])
    if preferred_asset:
        for a in assets:
            if a['name'] == preferred_asset:
                return a['browser_download_url']
    # Fallback: first zip asset
    for a in assets:
        if a['name'].endswith('.zip'):
            return a['browser_download_url']
    return None


def _download_and_extract(url: str, dest_dir: Path,
                           progress_cb=None) -> Path:
    """
    Download a zip from *url* into a temp file, extract to *dest_dir*, and
    return the directory.  *progress_cb(downloaded_bytes, total_bytes)* is
    called periodically if supplied.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    zip_path = dest_dir / '_download.zip'

    req = urllib.request.Request(
        url, headers={'User-Agent': 'Anno117LayoutTool/1.0'}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        total = int(resp.headers.get('Content-Length', 0))
        downloaded = 0
        chunk = 65536
        with open(zip_path, 'wb') as f:
            while True:
                block = resp.read(chunk)
                if not block:
                    break
                f.write(block)
                downloaded += len(block)
                if progress_cb:
                    progress_cb(downloaded, total)

    with zipfile.ZipFile(zip_path, 'r') as zf:
        zf.extractall(dest_dir)
    zip_path.unlink(missing_ok=True)
    return dest_dir


# ── Public API ─────────────────────────────────────────────────────────────────

def get_tool_paths(settings: dict) -> dict[str, Optional[Path]]:
    """
    Return {'RdaConsole': Path|None, 'FileDBReader': Path|None}.
    Uses cached settings first, then searches known locations.
    Updates *settings* in-place whenever a new path is found (caller must save).
    """
    result = {}
    for key, info in _TOOL_INFO.items():
        cached = settings.get(info['setting'])
        if cached and Path(cached).is_file():
            result[key] = Path(cached)
            continue
        found = _find_exe(info['exes'])
        if found:
            settings[info['setting']] = str(found)
        result[key] = found
    return result


def ensure_tools(parent: tk.Misc, settings: dict) -> Optional[dict[str, Path]]:
    """
    Guarantee both tools are available.  Opens the setup dialog if any are
    missing.  Returns a {key: Path} dict when both are ready, or None if the
    user dismissed without completing setup.
    """
    paths = get_tool_paths(settings)
    if all(paths.values()):
        return paths

    dlg = _ToolSetupDialog(parent, settings, paths)
    parent.wait_window(dlg)

    paths = get_tool_paths(settings)
    return paths if all(paths.values()) else None


# ── Setup dialog ───────────────────────────────────────────────────────────────

class _ToolSetupDialog(tk.Toplevel):
    """Modal dialog shown when one or both tools are missing."""

    def __init__(self, parent: tk.Misc, settings: dict,
                 initial_paths: dict[str, Optional[Path]]):
        super().__init__(parent)
        self._settings = settings
        self._paths = dict(initial_paths)      # mutable local copy
        self._dotnet_ok = _check_dotnet()
        self._thread: Optional[threading.Thread] = None
        self._cancel = threading.Event()

        self.title("Anno Tools Setup")
        self.resizable(False, False)
        self.configure(bg=BG_MAIN)
        self.grab_set()
        self._build_ui()
        self._refresh_ui()
        self.transient(parent)
        self.after(100, self._centre)

    # ── Layout ────────────────────────────────────────────────────────────────

    def _build_ui(self):
        pad = dict(padx=14, pady=6)
        lbl_style = dict(bg=BG_MAIN, fg=FG_MAIN, font=('Segoe UI', 10))
        hdr_style = dict(bg=BG_MAIN, fg=FG_GOLD,  font=('Segoe UI', 11, 'bold'))

        tk.Label(self, text="External Tools Required", **hdr_style).pack(
            anchor='w', padx=14, pady=(14, 4))
        tk.Label(
            self,
            text="Savegame importing uses two small command-line tools.\n"
                 "They will be downloaded and stored automatically.",
            **lbl_style, justify='left'
        ).pack(anchor='w', **pad)

        # .NET warning banner (only shown when .NET 6+ is not detected)
        if not self._dotnet_ok:
            dotnet_frame = tk.Frame(self, bg='#3a1a00', relief='flat')
            tk.Label(
                dotnet_frame,
                text="⚠  .NET 6+ runtime not detected.\n"
                     "   The tools require it to run.  Download it once from Microsoft.",
                bg='#3a1a00', fg='#ffcc66', font=('Segoe UI', 9),
                justify='left'
            ).pack(side='left', padx=10, pady=6)
            tk.Button(
                dotnet_frame,
                text="Get .NET 8",
                command=lambda: webbrowser.open(DOTNET_URL),
                bg=BG_SECTION, fg=FG_GOLD, relief='flat',
                font=('Segoe UI', 9, 'bold'), padx=6, cursor='hand2'
            ).pack(side='right', padx=10, pady=6)
            dotnet_frame.pack(fill='x', padx=14, pady=4)

        # Per-tool rows
        tool_frame = tk.Frame(self, bg=BG_SECTION, padx=12, pady=8)
        tool_frame.pack(fill='x', padx=14, pady=4)

        self._rows: dict[str, dict] = {}
        for i, (key, info) in enumerate(_TOOL_INFO.items()):
            r = {}
            row = tk.Frame(tool_frame, bg=BG_SECTION)
            row.grid(row=i, column=0, sticky='ew', pady=3)
            tool_frame.columnconfigure(0, weight=1)

            r['status'] = tk.Label(row, text='', bg=BG_SECTION,
                                   font=('Segoe UI', 10), width=2)
            r['status'].pack(side='left')
            tk.Label(row, text=info['label'], bg=BG_SECTION, fg=FG_MAIN,
                     font=('Segoe UI', 10)).pack(side='left', padx=(4, 12))
            r['path_lbl'] = tk.Label(row, text='', bg=BG_SECTION,
                                     fg='#8899aa', font=('Segoe UI', 8),
                                     anchor='w')
            r['path_lbl'].pack(side='left', fill='x', expand=True)
            r['locate_btn'] = tk.Button(
                row, text='Locate…',
                command=lambda k=key: self._locate_tool(k),
                bg=BG_SECTION, fg=FG_MAIN, relief='flat',
                font=('Segoe UI', 9), padx=6
            )
            r['locate_btn'].pack(side='right')
            self._rows[key] = r

        # Progress area
        prog_frame = tk.Frame(self, bg=BG_MAIN)
        prog_frame.pack(fill='x', padx=14, pady=4)

        self._progress = ttk.Progressbar(prog_frame, length=400, mode='determinate')
        self._progress.pack(fill='x')
        self._prog_label = tk.Label(prog_frame, text='', bg=BG_MAIN,
                                    fg='#8899aa', font=('Segoe UI', 9))
        self._prog_label.pack(anchor='w')

        # Bottom buttons
        btn_frame = tk.Frame(self, bg=BG_MAIN)
        btn_frame.pack(fill='x', padx=14, pady=(6, 14))

        self._dl_btn = tk.Button(
            btn_frame, text='Download Both Automatically',
            command=self._start_download,
            bg='#1a3a6a', fg=FG_GOLD, relief='flat',
            font=('Segoe UI', 10, 'bold'), padx=10, pady=4
        )
        self._dl_btn.pack(side='left')

        self._cancel_btn = tk.Button(
            btn_frame, text='Cancel Download',
            command=self._cancel_download,
            bg=BG_SECTION, fg=FG_MAIN, relief='flat',
            font=('Segoe UI', 9), padx=8, pady=4,
            state='disabled'
        )
        self._cancel_btn.pack(side='left', padx=8)

        self._ok_btn = tk.Button(
            btn_frame, text='Continue',
            command=self.destroy,
            bg=BG_SECTION, fg=FG_GOLD, relief='flat',
            font=('Segoe UI', 10, 'bold'), padx=10, pady=4
        )
        self._ok_btn.pack(side='right')

    # ── UI refresh ────────────────────────────────────────────────────────────

    def _refresh_ui(self):
        all_found = True
        for key, row in self._rows.items():
            p = self._paths.get(key)
            if p:
                row['status'].config(text='✓', fg='#44cc66')
                row['path_lbl'].config(text=str(p))
            else:
                row['status'].config(text='✗', fg='#cc4444')
                row['path_lbl'].config(text='not found')
                all_found = False

        self._ok_btn.config(state='normal' if all_found else 'disabled')
        missing = [k for k, p in self._paths.items() if not p]
        if missing:
            self._dl_btn.config(state='normal')
        else:
            self._dl_btn.config(state='disabled')

    # ── Manual locate ─────────────────────────────────────────────────────────

    def _locate_tool(self, key: str):
        info = _TOOL_INFO[key]
        primary_exe = info['exes'][0]
        path = fd.askopenfilename(
            parent=self,
            title=f"Locate {primary_exe}",
            filetypes=[('Executable', '*.exe'), ('All files', '*.*')],
            initialdir='C:/',
        )
        if path and os.path.isfile(path):
            p = Path(path)
            self._paths[key] = p
            self._settings[info['setting']] = str(p)
            self._refresh_ui()

    # ── Download ──────────────────────────────────────────────────────────────

    def _start_download(self):
        self._dl_btn.config(state='disabled')
        self._cancel_btn.config(state='normal')
        self._cancel.clear()
        self._thread = threading.Thread(
            target=self._download_worker, daemon=True
        )
        self._thread.start()

    def _cancel_download(self):
        self._cancel.set()
        self._cancel_btn.config(state='disabled')
        self._prog_label.config(text='Download cancelled.')

    def _download_worker(self):
        """Runs on a background thread; uses after() to update UI thread-safely."""
        missing = [k for k, p in self._paths.items() if not p]
        total_tools = len(missing)
        for i, key in enumerate(missing):
            if self._cancel.is_set():
                break
            info = _TOOL_INFO[key]
            self._set_progress_label(
                f'[{i+1}/{total_tools}]  Fetching release info for {info["exes"][0]}…'
            )
            try:
                url = _fetch_download_url(info['api'], info.get('asset'))
                if not url:
                    self._set_progress_label(
                        f'Error: no release asset found for {key}.')
                    continue

                dest = TOOL_DIR / info['subdir']
                self._set_progress_label(
                    f'[{i+1}/{total_tools}]  Downloading {info["exes"][0]}…'
                )

                def _prog(dl, tot, _key=key, _i=i, _total=total_tools):
                    if tot > 0:
                        pct = dl / tot * 100
                        self.after(0, lambda p=pct: self._progress.config(value=p))
                    kb = dl // 1024
                    self.after(0, lambda k=kb: self._prog_label.config(
                        text=f'[{_i+1}/{_total}]  Downloading… {k} KB'
                    ))

                _download_and_extract(url, dest, progress_cb=_prog)

                # Locate the exe in the extracted folder
                found = _find_exe_in_dir(dest, info['exes'])
                if found:
                    self._paths[key] = found
                    self._settings[info['setting']] = str(found)
                    self.after(0, self._refresh_ui)
                else:
                    self._set_progress_label(
                        f'Warning: {info["exes"][0]} not found after extraction.\n'
                        f'Please use "Locate…" to point to it manually.'
                    )
            except Exception as exc:
                self._set_progress_label(f'Error downloading {key}: {exc}')

        if not self._cancel.is_set():
            self.after(0, lambda: self._progress.config(value=100))
            all_ok = all(self._paths.values())
            msg = 'Done!' if all_ok else 'Download complete. Some tools still missing.'
            self.after(0, lambda: self._prog_label.config(text=msg))
        self.after(0, lambda: self._cancel_btn.config(state='disabled'))

    def _set_progress_label(self, text: str):
        self.after(0, lambda: self._prog_label.config(text=text))

    # ── Util ──────────────────────────────────────────────────────────────────

    def _centre(self):
        self.update_idletasks()
        pw = self.master.winfo_rootx() + self.master.winfo_width() // 2
        ph = self.master.winfo_rooty() + self.master.winfo_height() // 2
        w, h = self.winfo_width(), self.winfo_height()
        self.geometry(f'+{pw - w//2}+{ph - h//2}')


# ── Helper (module-level so _download_worker can call it after extraction) ────

def _find_exe_in_dir(root: Path, exe_names: list[str]) -> Optional[Path]:
    """Recursively search *root* for the first matching exe name."""
    for name in exe_names:
        for match in root.rglob(name):
            if match.is_file():
                return match
    return None
