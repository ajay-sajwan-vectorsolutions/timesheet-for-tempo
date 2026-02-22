# Mac Compatibility + Cross-Platform Zip Distribution

**Created:** February 22, 2026
**Branch:** feature/v3.5/mac-compatibility (or feature/v3.5/windows-installer)
**Status:** Planning

---

## Context

The application currently runs only on Windows. To distribute to Mac developers on the team (as a zip), we need to make `tray_app.py` cross-platform and update `install.sh` for Mac. The core script `tempo_automation.py` is already mostly cross-platform (DPAPI has a platform guard, toast notifications have a guard). The main problem is `tray_app.py` which has 9 Windows-specific code sections that will crash on Mac.

**Goal:** Distribute the current version as a zip to both Mac and Windows users. Mac users run `install.sh`, Windows users run `install.bat`.

---

## Scope

| File | Status | Work Needed |
|------|--------|-------------|
| `tempo_automation.py` | 95% cross-platform | Minor: improve toast fallback for Mac |
| `tray_app.py` | Windows-only | Major: 9 sections need platform conditionals |
| `confirm_and_run.py` | Windows-only | Skip on Mac (cron doesn't need a dialog) |
| `install.sh` | Exists but outdated | Update: add overhead step, weekly verify cron, tray app launch |
| `install.bat` | Windows-only | No changes needed |
| `requirements.txt` | Has `winotify` (Win-only) | Add Mac conditional or make optional |

---

## Windows-Specific Code Audit (tray_app.py)

| # | Location | Windows Code | Mac Replacement |
|---|----------|-------------|-----------------|
| 1 | L26 | `import ctypes` | Guard behind `sys.platform == 'win32'` |
| 2 | L50-52 | REG_KEY, MUTEX_NAME constants | Guard behind platform check |
| 3 | L191-203 | `CreateMutexW()` single instance | `fcntl.flock()` file lock |
| 4 | L373-457 | VBScript InputBox (`wscript.exe`) | `osascript` AppleScript dialog |
| 5 | L459-469 | `cmd /k` + `CREATE_NEW_CONSOLE` | `open -a Terminal` |
| 6 | L471-477 | `notepad.exe` | `open` (default app) |
| 7 | L479-489 | `cmd /k` + `CREATE_NEW_CONSOLE` | `open -a Terminal` |
| 8 | L491-500 | `os.startfile()` | `subprocess.Popen(['open', path])` |
| 9 | L541-553 | `MessageBoxW()` exit dialog | `osascript` dialog |
| 10 | L567-593 | `schtasks` restart scheduling | Skip on Mac (log reminder instead) |
| 11 | L634-651 | `winotify` toast | `osascript display notification` |
| 12 | L653-673 | `winreg` auto-start check | Check LaunchAgent plist exists |
| 13 | L716-719 | `schtasks /Delete` cleanup | Skip on Mac |
| 14 | L796-831 | `winreg` register/unregister | LaunchAgent plist create/delete |

---

## Code Changes Detail

### 1. Platform-Safe Imports (tray_app.py top)

```python
# Current (line 26):
import ctypes

# New:
if sys.platform == 'win32':
    import ctypes

# Current (lines 50-52):
REG_KEY = r'SOFTWARE\Microsoft\Windows\CurrentVersion\Run'
REG_VALUE = 'TempoTrayApp'
MUTEX_NAME = 'TempoTrayApp_SingleInstance_Mutex'

# New:
if sys.platform == 'win32':
    REG_KEY = r'SOFTWARE\Microsoft\Windows\CurrentVersion\Run'
    REG_VALUE = 'TempoTrayApp'
    MUTEX_NAME = 'TempoTrayApp_SingleInstance_Mutex'
```

### 2. Single Instance (L191-203)

```python
def _check_single_instance(self) -> bool:
    if sys.platform == 'win32':
        self._mutex = ctypes.windll.kernel32.CreateMutexW(None, True, MUTEX_NAME)
        return ctypes.windll.kernel32.GetLastError() != 183
    else:
        import fcntl
        self._lock_file = open(SCRIPT_DIR / '.tray_app.lock', 'w')
        try:
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except IOError:
            return False
```

### 3. Add PTO Dialog (L373-457)

```python
def _on_add_pto(self, icon=None, item=None):
    if sys.platform == 'win32':
        # existing VBScript InputBox approach (unchanged)
        ...
    else:
        # macOS: osascript dialog (no extra dependencies)
        script = (
            'set result to text returned of '
            '(display dialog "Enter PTO dates (YYYY-MM-DD, comma-separated):"'
            ' default answer "" with title "Tempo - Add PTO")'
        )
        proc = subprocess.run(
            ['osascript', '-e', script],
            capture_output=True, text=True, timeout=120
        )
        if proc.returncode == 0:
            raw = proc.stdout.strip()
            # same sanitize + add_pto logic as Windows path
```

### 4. Open Terminal for CLI Commands (L459-469, L479-489)

```python
def _open_terminal_command(self, args_list):
    """Open a terminal window running tempo_automation.py with given args."""
    script = str(SCRIPT_DIR / 'tempo_automation.py')
    if sys.platform == 'win32':
        python_exe = Path(sys.executable).parent / "python.exe"
        subprocess.Popen(
            ['cmd', '/k', str(python_exe), script] + args_list,
            cwd=str(SCRIPT_DIR),
            creationflags=subprocess.CREATE_NEW_CONSOLE
        )
    elif sys.platform == 'darwin':
        cmd_str = f'cd "{SCRIPT_DIR}" && python3 "{script}" {" ".join(args_list)}; echo "Press Enter to close..."; read'
        subprocess.Popen([
            'osascript', '-e',
            f'tell app "Terminal" to do script "{cmd_str}"'
        ])
    else:
        # Linux: try common terminal emulators
        for term in ['gnome-terminal', 'xterm', 'konsole']:
            if shutil.which(term):
                subprocess.Popen([term, '--', 'python3', script] + args_list)
                break
```

Then simplify the callers:
```python
def _on_select_overhead(self, icon=None, item=None):
    self._open_terminal_command(['--select-overhead'])

def _on_view_schedule(self, icon=None, item=None):
    self._open_terminal_command(['--show-schedule'])
```

### 5. View Log (L471-477)

```python
def _on_view_log(self, icon=None, item=None):
    if not LOG_FILE.exists():
        self._show_toast('No Log', 'Log file not found yet.')
        return
    if sys.platform == 'win32':
        subprocess.Popen(['notepad.exe', str(LOG_FILE)])
    elif sys.platform == 'darwin':
        subprocess.Popen(['open', str(LOG_FILE)])
    else:
        subprocess.Popen(['xdg-open', str(LOG_FILE)])
```

### 6. Settings (L491-500)

```python
def _on_settings(self, icon=None, item=None):
    if not CONFIG_FILE.exists():
        self._show_toast('No Config', 'config.json not found. Run setup first.')
        return
    if sys.platform == 'win32':
        os.startfile(str(CONFIG_FILE))
    elif sys.platform == 'darwin':
        subprocess.Popen(['open', str(CONFIG_FILE)])
    else:
        subprocess.Popen(['xdg-open', str(CONFIG_FILE)])
```

### 7. Exit Dialog (L541-553)

```python
if should_warn:
    if sys.platform == 'win32':
        flags = 0x04 | 0x30 | 0x40000 | 0x10000
        result = ctypes.windll.user32.MessageBoxW(0, msg, 'Tempo Automation', flags)
        user_wants_to_stay = (result != 6)
    else:
        osa_msg = msg.replace('"', '\\"').replace('\n', '\\n')
        script = (
            f'display dialog "{osa_msg}" '
            f'buttons {{"Exit Anyway", "Stay Running"}} '
            f'default button "Stay Running" '
            f'with title "Tempo Automation" '
            f'with icon caution'
        )
        proc = subprocess.run(['osascript', '-e', script], capture_output=True, text=True)
        user_wants_to_stay = ('Stay Running' in proc.stdout or proc.returncode != 0)

    if user_wants_to_stay:
        return
    if sys.platform == 'win32':
        self._schedule_restart()
```

### 8. Schedule Restart (L567-593)

```python
def _schedule_restart(self):
    if sys.platform != 'win32':
        tray_logger.info("Restart scheduling not available on this platform")
        return
    # existing schtasks logic (unchanged)
```

### 9. Toast Notifications (L634-651)

```python
def _show_toast(self, title, body):
    if sys.platform == 'win32' and WINOTIFY_OK:
        # existing winotify approach (unchanged)
        ...
    elif sys.platform == 'darwin':
        osa_body = body.replace('"', '\\"').replace('\n', ' ')
        osa_title = title.replace('"', '\\"')
        subprocess.Popen([
            'osascript', '-e',
            f'display notification "{osa_body}" with title "{osa_title}"'
        ])
    else:
        tray_logger.info(f"Notification: {title} - {body}")
```

### 10. Auto-Start Registration (L653-673, L796-831)

```python
LAUNCHAGENT_LABEL = 'com.tempo.trayapp'

def _get_plist_path():
    return Path.home() / 'Library' / 'LaunchAgents' / f'{LAUNCHAGENT_LABEL}.plist'

def register_autostart():
    if sys.platform == 'win32':
        # existing winreg approach (unchanged)
        ...
    elif sys.platform == 'darwin':
        plist_path = _get_plist_path()
        plist_path.parent.mkdir(parents=True, exist_ok=True)
        plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{LAUNCHAGENT_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{sys.executable}</string>
        <string>{SCRIPT_DIR / 'tray_app.py'}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
</dict>
</plist>"""
        plist_path.write_text(plist_content)
        subprocess.run(['launchctl', 'load', str(plist_path)], capture_output=True)
        print(f"[OK] Auto-start registered (LaunchAgent: {plist_path})")

def unregister_autostart():
    if sys.platform == 'win32':
        # existing winreg approach (unchanged)
        ...
    elif sys.platform == 'darwin':
        plist_path = _get_plist_path()
        if plist_path.exists():
            subprocess.run(['launchctl', 'unload', str(plist_path)], capture_output=True)
            plist_path.unlink()
            print("[OK] Auto-start removed")
        else:
            print("[OK] Auto-start was not registered")

def _ensure_autostart(self):
    if sys.platform == 'win32':
        # existing winreg check (unchanged)
        ...
    elif sys.platform == 'darwin':
        if not _get_plist_path().exists():
            tray_logger.info("Auto-start not found, registering...")
            register_autostart()
```

### 11. Cleanup on run() (L716-719)

```python
# Only cleanup Windows Task Scheduler restart task
if sys.platform == 'win32':
    subprocess.run(
        ['schtasks', '/Delete', '/TN', 'TempoTrayRestart', '/F'],
        capture_output=True
    )
```

---

## Changes to tempo_automation.py

### Toast Notification (send_windows_notification, ~L1842)

Add Mac fallback alongside existing Windows guard:

```python
def send_windows_notification(self, title, body):
    if sys.platform == 'win32':
        # existing winotify code (unchanged)
        ...
    elif sys.platform == 'darwin':
        try:
            osa_body = body.replace('"', '\\"')
            osa_title = title.replace('"', '\\"')
            subprocess.Popen([
                'osascript', '-e',
                f'display notification "{osa_body}" with title "{osa_title}"'
            ])
        except Exception as e:
            logger.error(f"Mac notification failed: {e}")
```

---

## Changes to requirements.txt

```
# HTTP requests
requests>=2.31.0

# Holiday detection
holidays>=0.40

# System tray app (cross-platform)
pystray>=0.19.0
Pillow>=10.0.0

# Windows toast notifications (Windows only, skipped on Mac/Linux)
winotify>=1.1.0; sys_platform == 'win32'
```

---

## install.sh Rewrite

Update from 5 steps to 7 steps to match install.bat:

```bash
#!/bin/bash
# Tempo Automation - Mac/Linux Installer (v3.5)

echo "[1/7] Checking Python installation..."
# python3 check (existing)

echo "[2/7] Installing dependencies..."
python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt
# winotify auto-skipped on Mac via platform marker

echo "[3/7] Running setup wizard..."
python3 tempo_automation.py --setup

echo "[4/7] Configuring overhead stories..."
read -p "Configure overhead stories now? (y/n, default: y): " SELECT_OH
if [ "$SELECT_OH" != "n" ] && [ "$SELECT_OH" != "N" ]; then
    python3 tempo_automation.py --select-overhead
fi

echo "[5/7] Setting up scheduled jobs..."
# Remove old Tempo cron entries
crontab -l 2>/dev/null | grep -v "tempo_automation.py" > /tmp/tempo_cron_$$.txt

# Daily sync (Mon-Fri at 6 PM)
echo "0 18 * * 1-5 cd $SCRIPT_DIR && python3 tempo_automation.py >> daily-timesheet.log 2>&1" >> /tmp/tempo_cron_$$.txt

# Weekly verify (Fridays at 4 PM)
echo "0 16 * * 5 cd $SCRIPT_DIR && python3 tempo_automation.py --verify-week >> daily-timesheet.log 2>&1" >> /tmp/tempo_cron_$$.txt

# Monthly submit (last day at 11 PM) -- macOS-compatible date check
if [ "$(uname)" = "Darwin" ]; then
    echo '0 23 28-31 * * [ $(date -v+1d +\%d) -eq 1 ] && cd '"$SCRIPT_DIR"' && python3 tempo_automation.py --submit >> daily-timesheet.log 2>&1' >> /tmp/tempo_cron_$$.txt
else
    echo '0 23 28-31 * * [ $(date -d tomorrow +\%d) -eq 1 ] && cd '"$SCRIPT_DIR"' && python3 tempo_automation.py --submit >> daily-timesheet.log 2>&1' >> /tmp/tempo_cron_$$.txt
fi

crontab /tmp/tempo_cron_$$.txt
rm /tmp/tempo_cron_$$.txt

echo "[6/7] Starting system tray app..."
python3 tray_app.py --register
nohup python3 "$SCRIPT_DIR/tray_app.py" > /dev/null 2>&1 &

echo "[7/7] Test sync (optional)..."
read -p "Run test? (y/n): " TEST_RUN
if [ "$TEST_RUN" = "y" ]; then
    python3 tempo_automation.py
fi

echo "[OK] Installation complete!"
# Print summary (same as install.bat)
```

### Key Mac Differences from install.bat
- `crontab` instead of `schtasks`
- `nohup python3 &` instead of VBScript silent launch
- `date -v+1d` (BSD) instead of `date -d tomorrow` (GNU) for monthly cron
- No `confirm_and_run.py` needed (cron runs directly)
- `python3` instead of `python` / `pythonw.exe`
- `winotify` auto-skipped via requirements.txt platform marker

---

## Implementation Order

### Phase 1: Platform-safe imports (non-breaking)
1. Guard `import ctypes` behind platform check
2. Guard Windows constants behind platform check
3. Update requirements.txt with platform marker

### Phase 2: tray_app.py cross-platform (12 changes)
4. `_check_single_instance()` -- add fcntl path
5. `_show_toast()` -- add osascript path
6. `_on_add_pto()` -- add osascript dialog
7. Extract `_open_terminal_command()` helper, refactor select_overhead + view_schedule
8. `_on_view_log()` -- add `open` command
9. `_on_settings()` -- add `open` command
10. `_exit_flow()` -- add osascript dialog
11. `_schedule_restart()` -- skip on non-Windows
12. `register_autostart()` / `unregister_autostart()` -- add LaunchAgent plist
13. `_ensure_autostart()` -- add plist check
14. `run()` -- guard schtasks cleanup

### Phase 3: tempo_automation.py toast
15. Add osascript notification path

### Phase 4: install.sh rewrite
16. Rewrite to 7 steps matching install.bat
17. ASCII-only output
18. BSD-compatible date command
19. Weekly verify cron job
20. Tray app launch + auto-start registration

### Phase 5: Test + zip
21. Verify no import errors on Mac (or simulate by checking all platform guards)
22. Create zip bundles for distribution

---

## Verification

```bash
# Windows (should still work exactly as before)
install.bat

# Mac
chmod +x install.sh
./install.sh

# Tray app on Mac
python3 tray_app.py
# Expected: tray icon, osascript welcome notification

# CLI on Mac
python3 tempo_automation.py --help
python3 tempo_automation.py --show-schedule
python3 tempo_automation.py --select-overhead
python3 tempo_automation.py --show-overhead

# Verify cron jobs
crontab -l
# Expected: 3 entries (daily Mon-Fri 6PM, weekly Fri 4PM, monthly last day 11PM)

# Verify auto-start registered
ls ~/Library/LaunchAgents/com.tempo.trayapp.plist

# Tray menu items
# Right-click tray > Add PTO (osascript dialog should appear)
# Right-click tray > View Schedule (Terminal window should open)
# Right-click tray > Settings (config.json opens in editor)
# Right-click tray > Exit (osascript warning if hours not logged)
```

---

## What Works Without Changes

- All Jira/Tempo API calls (requests library is cross-platform)
- Holiday detection (holidays library is cross-platform)
- Schedule management (pure Python logic)
- PTO/override management
- Weekly verification + backfill
- Monthly submission
- Smart worklog descriptions
- Configuration loading/saving
- CLI argument parsing
- All `--` commands (setup, manage, show-schedule, etc.)
- pystray (cross-platform system tray)
- PIL/Pillow (cross-platform image processing)

## What's Different on Mac

- Toast notifications: osascript instead of winotify
- Dialogs: osascript instead of VBScript/MessageBox
- Auto-start: LaunchAgent plist instead of Registry
- Single instance: fcntl file lock instead of Win32 mutex
- Terminal windows: `open -a Terminal` instead of `cmd /k`
- File opening: `open` command instead of `os.startfile()`
- Scheduling: cron instead of Task Scheduler
- Credential storage: plain text (DPAPI fallback, could use keyring later)
- No confirm_and_run.py (cron runs directly)
- No restart scheduling on exit (just logs reminder)

---

*Created: February 22, 2026*
