# Windows Installer - Implementation Plan (PyInstaller + Inno Setup)

**Created:** February 21, 2026
**Branch:** feature/v3.5/windows-installer
**Status:** Planning

---

## Context

Currently, users must have Python 3.7+ pre-installed and run `install.bat` to set up Tempo Automation. This creates friction for non-technical team members (POs, Sales) and makes distribution across a 200-person org impractical. This feature creates a professional Windows installer (.exe) using PyInstaller to bundle Python + dependencies, and Inno Setup to create the installer with setup wizard, scheduled tasks, and uninstaller -- no Python installation required on the target machine.

---

## Architecture

```
Source Files (Python)
    |
    v  PyInstaller (onedir mode)
dist/tempo_automation/
    tempo_automation.exe    (console - CLI)
    tray_app.exe            (windowed - system tray)
    confirm_and_run.exe     (windowed - dialog)
    _internal/              (shared Python runtime + DLLs)
    assets/favicon.ico
    config_template.json
    org_holidays.json
    |
    v  Inno Setup
output/TempoAutomation-Setup-3.5.exe   (~30-40MB installer)
```

**Why onedir (not onefile):**
- Faster startup (1-2s vs 5-10s for onefile which extracts to temp every run)
- Fewer antivirus false positives
- Easier to debug (can inspect the dist/ folder)
- Inno Setup bundles the folder into a single installer .exe anyway
- All 3 executables share the `_internal/` runtime via MERGE

**Install location:** `{localappdata}\Tempo Automation` (C:\Users\<user>\AppData\Local\Tempo Automation)
- Writable without admin elevation
- Appropriate for per-user config/tokens
- Avoids Program Files write permission issues

---

## New Files to Create

| File | Purpose | Size |
|------|---------|------|
| `tempo_automation.spec` | PyInstaller spec -- 3 entry points, MERGE, data files | ~120 lines |
| `build/hook-holidays.py` | PyInstaller hook to collect all holidays country modules | ~3 lines |
| `installer/tempo_automation.iss` | Inno Setup script -- install, shortcuts, tasks, uninstall | ~180 lines |
| `build.bat` | Build automation (PyInstaller + Inno Setup compile) | ~30 lines |

## Existing Files to Modify

| File | Changes | Lines Changed |
|------|---------|---------------|
| `tempo_automation.py` | SCRIPT_DIR frozen check (L71-74) | ~4 lines |
| `tray_app.py` | SCRIPT_DIR + 7 frozen-mode conditionals (8 locations) | ~60 lines |
| `confirm_and_run.py` | SCRIPT_DIR + subprocess launch in frozen mode | ~10 lines |

---

## Code Changes Detail

### 1. Path Resolution (all 3 Python files)

Every file uses `SCRIPT_DIR = Path(__file__).parent` to find config.json, favicon.ico, etc. PyInstaller's frozen mode has different `__file__` behavior -- `sys.executable.parent` is the correct base directory in onedir mode.

**Pattern to add at each SCRIPT_DIR definition:**
```python
import sys
from pathlib import Path

if getattr(sys, 'frozen', False):
    SCRIPT_DIR = Path(sys.executable).parent
else:
    SCRIPT_DIR = Path(__file__).parent
```

**Files and locations:**
- `tempo_automation.py` line 71-74
- `tray_app.py` line 45
- `confirm_and_run.py` line 13

All downstream path references (CONFIG_FILE, LOG_FILE, ORG_HOLIDAYS_FILE, FAVICON_PATH, STOP_FILE) already derive from SCRIPT_DIR, so they inherit the fix automatically.

### 2. tray_app.py -- Subprocess Calls (L461, L479)

`_on_select_overhead()` and `_on_view_schedule()` currently launch `python.exe tempo_automation.py`. In frozen mode, launch `tempo_automation.exe` directly.

```python
def _on_select_overhead(self, icon=None, item=None):
    if getattr(sys, 'frozen', False):
        exe = str(SCRIPT_DIR / 'tempo_automation.exe')
        subprocess.Popen(
            ['cmd', '/k', exe, '--select-overhead'],
            cwd=str(SCRIPT_DIR),
            creationflags=subprocess.CREATE_NEW_CONSOLE
        )
    else:
        # existing python.exe logic unchanged
        python_dir = Path(sys.executable).parent
        python_exe = python_dir / "python.exe"
        script = SCRIPT_DIR / 'tempo_automation.py'
        subprocess.Popen(
            ['cmd', '/k', str(python_exe), str(script), '--select-overhead'],
            cwd=str(SCRIPT_DIR),
            creationflags=subprocess.CREATE_NEW_CONSOLE
        )
```

Same pattern for `_on_view_schedule()`.

### 3. tray_app.py -- Auto-Start Registration (L135, L796)

`_find_pythonw()` and `register_autostart()` build a `pythonw.exe tray_app.py` command. In frozen mode, just use `tray_app.exe` (it's already windowed/no console).

```python
def _find_pythonw() -> str:
    if getattr(sys, 'frozen', False):
        return sys.executable  # tray_app.exe is already windowed
    # existing pythonw detection unchanged
    python_dir = Path(sys.executable).parent
    pythonw = python_dir / "pythonw.exe"
    if pythonw.exists():
        return str(pythonw)
    return sys.executable
```

```python
def register_autostart():
    import winreg
    if getattr(sys, 'frozen', False):
        command = f'"{sys.executable}"'
    else:
        pythonw = _find_pythonw()
        tray_script = str(SCRIPT_DIR / 'tray_app.py')
        command = f'"{pythonw}" "{tray_script}"'
    # registry write unchanged
```

### 4. tray_app.py -- Schedule Restart (L567)

`_schedule_restart()` creates a schtasks one-time task. Must use `tray_app.exe` path in frozen mode.

```python
def _schedule_restart(self):
    if getattr(sys, 'frozen', False):
        tr_cmd = f'"{sys.executable}"'
    else:
        pythonw = _find_pythonw()
        tray_script = str(SCRIPT_DIR / 'tray_app.py')
        tr_cmd = f'"{pythonw}" "{tray_script}"'
    # schtasks command uses tr_cmd
```

### 5. tray_app.py -- Sync Import (L163-189)

`_load_automation()` does `from tempo_automation import TempoAutomation` in-process. In frozen MERGE mode, this import should work if `tempo_automation` is listed as a hidden import in the tray_app Analysis. This is the preferred approach (faster, better error reporting).

**Fallback:** If MERGE doesn't share the module, refactor `_run_sync()` to launch `tempo_automation.exe` as subprocess in frozen mode.

### 6. confirm_and_run.py -- Sync Call (L40-43)

Currently imports and calls `TempoAutomation.sync_daily()` in-process. In frozen mode, launch `tempo_automation.exe` as subprocess (cleaner -- console output displays properly since confirm_and_run.exe is windowed).

```python
if getattr(sys, 'frozen', False):
    exe = str(SCRIPT_DIR / 'tempo_automation.exe')
    subprocess.run([exe], cwd=str(SCRIPT_DIR))
else:
    sys.path.insert(0, str(SCRIPT_DIR))
    from tempo_automation import TempoAutomation, CONFIG_FILE
    automation = TempoAutomation(CONFIG_FILE)
    automation.sync_daily()
```

---

## PyInstaller Spec File (tempo_automation.spec)

```python
# tempo_automation.spec
block_cipher = None

# --- Analysis for each entry point ---

a_main = Analysis(
    ['tempo_automation.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('assets/favicon.ico', 'assets'),
        ('config_template.json', '.'),
        ('org_holidays.json', '.'),
    ],
    hiddenimports=[
        'requests.adapters',
        'requests.packages.urllib3',
    ],
    hookspath=['build'],
    # ...
)

a_tray = Analysis(
    ['tray_app.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('assets/favicon.ico', 'assets'),
    ],
    hiddenimports=[
        'pystray._win32',
        'PIL.Image',
        'PIL.ImageDraw',
        'PIL.ImageFont',
        'winotify',
        'tempo_automation',  # for in-process import
    ],
    hookspath=['build'],
    # ...
)

a_confirm = Analysis(
    ['confirm_and_run.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    # ...
)

# MERGE to share common modules (deduplicates Python runtime)
MERGE(
    (a_main, 'tempo_automation', 'tempo_automation'),
    (a_tray, 'tray_app', 'tray_app'),
    (a_confirm, 'confirm_and_run', 'confirm_and_run'),
)

# --- PYZ + EXE for each ---

pyz_main = PYZ(a_main.pure)
exe_main = EXE(pyz_main, a_main.scripts, [],
    name='tempo_automation',
    console=True,              # CLI app needs console
    icon='assets/favicon.ico')

pyz_tray = PYZ(a_tray.pure)
exe_tray = EXE(pyz_tray, a_tray.scripts, [],
    name='tray_app',
    console=False,             # System tray -- no console
    icon='assets/favicon.ico')

pyz_confirm = PYZ(a_confirm.pure)
exe_confirm = EXE(pyz_confirm, a_confirm.scripts, [],
    name='confirm_and_run',
    console=False,             # Dialog only -- no console
    icon='assets/favicon.ico')

# --- COLLECT into single directory ---

coll = COLLECT(
    exe_main, a_main.binaries, a_main.datas,
    exe_tray, a_tray.binaries, a_tray.datas,
    exe_confirm, a_confirm.binaries, a_confirm.datas,
    name='tempo_automation',
)
```

### PyInstaller Hook (build/hook-holidays.py)

```python
from PyInstaller.utils.hooks import collect_all
datas, binaries, hiddenimports = collect_all('holidays')
```

The `holidays` library dynamically imports country modules (100+ countries). This hook ensures they're all bundled. Adds ~5MB to the output.

---

## Inno Setup Script (installer/tempo_automation.iss)

### Setup Configuration
```
AppName=Tempo Automation
AppVersion=3.5
DefaultDirName={localappdata}\Tempo Automation
PrivilegesRequired=lowest
Compression=lzma2/ultra
```

### Install Tasks (user-selectable)
- [x] Create desktop shortcut
- [x] Create scheduled tasks (daily sync, weekly verify, monthly submit)
- [x] Start tray app automatically on Windows login

### Files
- `dist\tempo_automation\*` -> `{app}` (recursesubdirs)
- `config_template.json` -> `{app}` (always overwrite, it's a template)
- config.json: `Flags: onlyifdoesntexist` (NEVER overwrite user config on upgrade)

### Start Menu Shortcuts
- **Tempo Automation** -> `tray_app.exe` (main entry for most users)
- **Tempo Schedule Manager** -> `tempo_automation.exe --manage`
- **Tempo Setup Wizard** -> `tempo_automation.exe --setup`
- **Uninstall Tempo Automation** -> uninstaller

### Post-Install Actions
1. If no config.json exists (fresh install): run `tempo_automation.exe --setup`
2. Create scheduled tasks via schtasks (using `{app}\tempo_automation.exe` paths)
3. Offer to start `tray_app.exe`

### Scheduled Tasks (created via Pascal script)
```
DailySync:    Mon-Fri 6 PM  -> tempo_automation.exe --logfile "...\daily-timesheet.log"
WeeklyVerify: Fri 4 PM      -> tempo_automation.exe --verify-week --logfile "..."
MonthlySubmit: Days 28-31 11 PM -> tempo_automation.exe --submit --logfile "..."
```

### Auto-Start Registry
```
HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\Run\TempoTrayApp = "{app}\tray_app.exe"
```

### Uninstall Actions
1. Stop tray app: `tray_app.exe --stop`
2. Remove auto-start: `tray_app.exe --unregister`
3. Delete scheduled tasks: `schtasks /Delete /TN "TempoAutomation-*" /F`
4. Remove files (log files, signal files, temp files)

### Upgrade Behavior
- config.json: preserved (never overwritten)
- Executables + _internal: always replaced with new version
- config_template.json, org_holidays.json: always replaced
- Scheduled tasks: recreated with new paths
- Auto-start registry: updated with new path

---

## Build Script (build.bat)

```batch
@echo off
echo Building Tempo Automation Installer...

REM Step 1: Install build dependencies
pip install pyinstaller

REM Step 2: Run PyInstaller
pyinstaller tempo_automation.spec --clean --noconfirm

REM Step 3: Verify output
if not exist "dist\tempo_automation\tempo_automation.exe" (
    echo [FAIL] PyInstaller build failed
    exit /b 1
)

REM Step 4: Run Inno Setup compiler
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" installer\tempo_automation.iss

echo [OK] Installer: output\TempoAutomation-Setup-3.5.exe
```

---

## Implementation Order

### Phase 1: Python Code Changes (frozen-mode compatibility)

| Step | File | Location | Change |
|------|------|----------|--------|
| 1.1 | `tempo_automation.py` | L71-74 | SCRIPT_DIR frozen check |
| 1.2 | `tray_app.py` | L45 | SCRIPT_DIR frozen check |
| 1.3 | `confirm_and_run.py` | L13 | SCRIPT_DIR frozen check |
| 1.4 | `tray_app.py` | L135-142 | `_find_pythonw()` frozen mode |
| 1.5 | `tray_app.py` | L796-801 | `register_autostart()` frozen mode |
| 1.6 | `tray_app.py` | L567-579 | `_schedule_restart()` frozen mode |
| 1.7 | `tray_app.py` | L461-468 | `_on_select_overhead()` frozen mode |
| 1.8 | `tray_app.py` | L479-489 | `_on_view_schedule()` frozen mode |
| 1.9 | `confirm_and_run.py` | L40-43 | Subprocess launch in frozen mode |
| 1.10 | `tray_app.py` | L163-189 | `_load_automation()` import handling |

### Phase 2: PyInstaller Build Setup

| Step | Action |
|------|--------|
| 2.1 | Create `build/hook-holidays.py` |
| 2.2 | Create `tempo_automation.spec` |
| 2.3 | Create `build.bat` |
| 2.4 | Run PyInstaller build, fix any missing imports/data |

### Phase 3: Test PyInstaller Output

| Step | Test | Expected |
|------|------|----------|
| 3.1 | `tempo_automation.exe --help` | CLI help displays |
| 3.2 | `tempo_automation.exe --setup` | Setup wizard runs |
| 3.3 | `tempo_automation.exe --show-schedule` | Calendar displays |
| 3.4 | `tray_app.exe` | Tray icon appears, welcome toast |
| 3.5 | `confirm_and_run.exe` | OK/Cancel dialog appears |
| 3.6 | Tray > Sync Now | Sync completes |
| 3.7 | Tray > View Schedule | Console window with calendar |
| 3.8 | Tray > Select Overhead | Console window with selection |
| 3.9 | `tray_app.exe --register` / `--stop` | Auto-start + shutdown work |

### Phase 4: Inno Setup

| Step | Action |
|------|--------|
| 4.1 | Create `installer/tempo_automation.iss` |
| 4.2 | Compile with ISCC.exe |
| 4.3 | Test fresh install (no Python on machine) |
| 4.4 | Test upgrade install (verify config.json preserved) |
| 4.5 | Test uninstall (verify cleanup) |

### Phase 5: Polish

| Step | Action |
|------|--------|
| 5.1 | Update README.md with installer instructions |
| 5.2 | Update SETUP_GUIDE.md for exe-based workflow |
| 5.3 | Add version info resource to exes |
| 5.4 | End-to-end test on clean machine without Python |

---

## Potential Blockers

| Risk | Impact | Mitigation |
|------|--------|-----------|
| **Python 3.14 not supported by PyInstaller** | Build fails entirely | Check first. Use `pip install pyinstaller --pre` for dev version, or build with Python 3.12/3.13 (exe bundles its own runtime) |
| **Antivirus false positives** | Users can't run installer/exes | Use onedir mode (less flagged), submit to Microsoft Defender for whitelisting, document for users |
| **holidays dynamic imports missed** | Country holidays fail at runtime | `build/hook-holidays.py` with `collect_all('holidays')` catches all (~5MB added) |
| **MERGE cross-exe imports fail** | tray_app can't import TempoAutomation | Refactor `_run_sync()` to use subprocess calls to `tempo_automation.exe` |
| **Inno Setup not installed on build machine** | Can't compile installer | Build prerequisite -- free download from jrsoftware.org |
| **Large bundle size** | Slow download for 200 users | lzma2 compression typically achieves ~30-40MB. Acceptable for one-time download |

---

## Edge Cases

1. **Config file write permissions**: Solved by installing to `{localappdata}` (always writable)
2. **Upgrade preserves config**: Inno Setup `onlyifdoesntexist` flag on config.json
3. **Multiple Python versions on machine**: Irrelevant -- bundled exe uses its own runtime
4. **holidays library version pinning**: Pin in requirements.txt to match bundled version
5. **winotify AppId in frozen mode**: Usually works. If toasts fail, set AppUserModelId explicitly
6. **pystray._win32 backend**: Must be in hiddenimports (PyInstaller misses it via static analysis)
7. **VBScript temp files in tray**: Created in SCRIPT_DIR, writable since `{localappdata}`
8. **DualWriter log path**: Task Scheduler uses absolute paths generated by Inno Setup
9. **org_holidays.json auto-refresh**: Script overwrites in SCRIPT_DIR, works since `{localappdata}`

---

## Verification

```bash
# 1. Build everything
build.bat

# 2. Test CLI exe directly
dist\tempo_automation\tempo_automation.exe --help
dist\tempo_automation\tempo_automation.exe --show-schedule

# 3. Test tray exe directly
dist\tempo_automation\tray_app.exe

# 4. Test dialog exe directly
dist\tempo_automation\confirm_and_run.exe

# 5. Test installer (fresh install)
output\TempoAutomation-Setup-3.5.exe
#   -> Setup wizard should run
#   -> Tray app should start
#   -> Scheduled tasks should be created
#   -> Start menu shortcuts should exist

# 6. Test installer (upgrade)
#   -> Edit config.json (add a comment)
#   -> Re-run installer
#   -> Verify config.json NOT overwritten

# 7. Test uninstall
#   -> Control Panel > Uninstall > Tempo Automation
#   -> Verify: tasks deleted, registry cleaned, tray stopped, files removed

# 8. Test on clean machine WITHOUT Python installed
#   -> Copy installer to machine
#   -> Run installer
#   -> Verify complete workflow: setup wizard, tray app, sync, schedule
```

---

## Prerequisites (Build Machine Only)

- Python 3.12+ (or 3.14 if PyInstaller supports it)
- PyInstaller: `pip install pyinstaller`
- Inno Setup 6: download from https://jrsoftware.org/isdl.php
- All project dependencies: `pip install -r requirements.txt`

End users need NOTHING pre-installed -- that's the whole point.

---

*Created: February 21, 2026*
*Branch: feature/v3.5/windows-installer*
