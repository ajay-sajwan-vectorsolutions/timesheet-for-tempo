# Implementation Plan v4: System Tray App & Task Scheduler Confirmation

**Status:** Fully Implemented
**Created:** February 18, 2026
**Last Updated:** February 18, 2026
**Implements:** System tray app (persistent icon + timer) and OK/Cancel confirmation dialog for Task Scheduler

---

## Summary of Features

| # | Feature | Priority | Status |
|---|---------|----------|--------|
| 1 | OK/Cancel confirmation dialog before Task Scheduler daily sync | Must-have | Done |
| 2 | System tray app with persistent icon and notification timer | Must-have | Done |
| 3 | Tray menu: Sync Now, Add PTO, View Log, View Schedule, Settings, Exit | Must-have | Done |
| 4 | Icon with status indicator: rounded-rect background (green/orange/red) | Must-have | Done |
| 5 | Auto-start on Windows login via HKCU registry key | Must-have | Done |
| 6 | Single-instance enforcement via Win32 named mutex | Must-have | Done |
| 7 | Toast notifications via winotify (timer alert, sync status) | Must-have | Done |
| 8 | Deferred import of TempoAutomation (graceful error if config missing) | Must-have | Done |
| 9 | Updated install.bat with optional tray app setup step | Must-have | Done |
| 10 | Updated run_daily.bat to use confirm_and_run.py | Must-have | Done |
| 11 | Add PTO from tray menu (VBScript InputBox dialog) | Must-have | Done |
| 12 | PTO validation: weekend rejection, format check, character restriction | Must-have | Done |
| 13 | pythonw.exe compatibility (sys.stdout=None fix in tempo_automation.py) | Must-have | Done |
| 14 | Sync output written to daily-timesheet.log from tray app | Must-have | Done |
| 15 | Smart exit: hours check + confirmation + one-time scheduled restart | Must-have | Done |
| 16 | Company favicon icon with animated sync indicator | Nice-to-have | Done |

---

## Problem Statement

The daily scheduler (`TempoAutomation-DailySync`) runs silently at 6 PM via Windows Task Scheduler. Users have no visibility or control:

- **No confirmation:** Sync fires without asking. If a user is still working on tickets or took a half-day, they may want to skip or delay.
- **No feedback:** No indication that sync ran, succeeded, or failed — users must check log files manually.
- **No quick access:** To run a manual sync or view schedule, users must open a terminal and type commands.

Two solutions address different user preferences:
1. **Task Scheduler users** get a simple OK/Cancel dialog before sync runs
2. **Tray app users** get a persistent icon with notification timer and full menu

Both approaches coexist. The sync is idempotent (deletes old worklogs then creates new), so even if both fire on the same day, no data corruption occurs.

---

## Architecture Overview

### System Tray App Flow

```
Windows Login
    |
    +--> Registry Run key: pythonw.exe tray_app.py
                |
                v
          TrayApp.__init__()
                |
                +--> _load_automation()       [deferred import of TempoAutomation]
                |       +--> TempoAutomation(config_path)
                |             +--> ConfigManager, ScheduleManager, JiraClient, etc.
                |
                +--> _schedule_next_sync()    [threading.Timer fires at daily_sync_time]
                |
                v
          pystray.Icon.run()               [Win32 message pump - blocks on main thread]
                |
       .--------+---------.
       |                   |
   Timer fires         User right-clicks
       |                   |
       v                   v
  _on_timer_fired()    pystray menu:
       |                - Sync Now (default, double-click)
       +--> toast()     - Add PTO
       +--> bg=orange   - View Log
       +--> rearm       - View Schedule
            timer       - Settings
                        - Exit (smart: checks hours first)
                             |
                   _on_sync_now()
                             |
                   _sync_running flag set?
                     Yes --> toast "already running"
                     No  --> threading.Thread(_run_sync)
                                   |
                             animated bg (orange<->red, 700ms)
                             TempoAutomation.sync_daily()
                                   |
                             success -> bg = green, toast OK
                             failure -> bg = red, toast error
                             5s later -> revert to idle state
```

### Task Scheduler Confirmation Flow

```
Task Scheduler (6 PM weekdays)
    |
    +--> run_daily.bat
            |
            +--> pythonw.exe confirm_and_run.py
                    |
                    +--> MessageBoxW "Log hours today?" [OK] [Cancel]
                    |
                    OK --> import TempoAutomation, sync_daily()
                    Cancel --> exit(0)
```

### Icon Design

The tray icon uses the company's Vector Solutions favicon (`d:\Vector\logo\favicon.ico`) displayed on a colored rounded-rectangle background. The background color indicates status.

### Icon State Machine

```
Icon = Company favicon on colored rounded-rect background

              startup
                |
                v
    +------> GREEN bg <--------+
    |       "Tempo              |
    |        Automation"        | (5s after sync)
    |                           |
    |   timer fires             |
    |       |                   |
    |       v                   |
    |     ORANGE bg             |
    |   "Time to log            |
    |    hours!"                |
    |       |                   |
    |   user clicks             |
    |   "Sync Now"              |
    |       |                   |
    |       v                   |
    |     ANIMATED  ----------->+
    |   orange<->red       (on success)
    |   (700ms interval)
    |   "Syncing..."
    |       |
    |       | (on error)
    |       v
    |      RED bg
    |   "Error: ..."
    |       |
    +-------+  (5s later)

    RED bg (startup) = import error / missing config
```

### Background Colors
| State | Color | RGB |
|-------|-------|-----|
| Idle / synced | Light green | (186, 230, 126) |
| Pending / notification | Light orange | (252, 211, 119) |
| Syncing (animated) | Alternates orange <-> red every 700ms | orange + (252, 165, 165) |
| Error | Light red | (252, 165, 165) |

---

## New Files

### File 1: `tray_app.py` (~280 lines)

**Purpose:** Persistent system tray application that monitors and triggers daily timesheet sync.

**Dependencies:** `pystray`, `Pillow`, `winotify` (all pip-installable)

**Class: TrayApp**

| Method | Purpose |
|--------|---------|
| `__init__()` | Initialize flags, timer, icon refs, animation state |
| `_load_automation()` | Deferred import of TempoAutomation from tempo_automation.py |
| `_check_single_instance()` | Win32 mutex via `ctypes.windll.kernel32.CreateMutexW` |
| `_get_sync_time()` | Read `config.schedule.daily_sync_time` (default '18:00') |
| `_schedule_next_sync()` | Compute seconds until next sync time, create `threading.Timer` |
| `_on_timer_fired()` | Set pending flag, bg orange, show toast, re-arm for tomorrow |
| `_build_menu()` | Return `pystray.Menu` (Sync Now, Add PTO, View Log, View Schedule, Settings, Exit) |
| `_on_sync_now()` | Guard against duplicate sync, start `_run_sync` in daemon thread |
| `_run_sync()` | Start animated bg (orange<->red), call `sync_daily()`, update bg |
| `_on_add_pto()` | VBScript InputBox dialog to add PTO dates with validation |
| `_on_view_log()` | `subprocess.Popen(['notepad.exe', 'daily-timesheet.log'])` |
| `_on_view_schedule()` | Open cmd with `python tempo_automation.py --show-schedule` |
| `_on_settings()` | `os.startfile('config.json')` |
| `_on_exit()` | Start smart exit in separate thread (hours check + confirmation) |
| `_exit_flow()` | Check Jira hours, show MB_YESNO dialog, schedule restart if needed |
| `_schedule_restart()` | Create one-time `schtasks /SC ONCE` to relaunch at sync time |
| `_start_sync_animation()` | Start animated bg: alternates orange<->red every 700ms |
| `_stop_sync_animation()` | Stop the animation timer |
| `_set_icon_state(color, tooltip)` | Thread-safe icon + tooltip update (stops animation first) |
| `_show_toast(title, body)` | `winotify.Notification` (non-fatal on failure) |
| `run()` | Check deps, single instance, load automation, start pystray loop |

**Module-level functions:**

| Function | Purpose |
|----------|---------|
| `_load_favicon(size)` | Load and cache the company favicon.ico, resize to fit |
| `_make_icon(color)` | Generate 64x64 PIL Image: rounded-rect bg + company logo |
| `_find_pythonw()` | Find `pythonw.exe` alongside `sys.executable` |
| `register_autostart()` | Write to `HKCU\...\Run` registry key |
| `unregister_autostart()` | Delete registry value |
| `main()` | argparse: `--register`, `--unregister`, or run app |

**Integration points with existing code:**
- `from tempo_automation import TempoAutomation, CONFIG_FILE` (deferred in `_load_automation`)
- `self._automation.sync_daily()` — the main sync call
- `config['schedule']['daily_sync_time']` — timer target time
- Logs to `tempo_automation.log` via its own named logger (`tray_app`)

---

### File 2: `confirm_and_run.py` (~35 lines)

**Purpose:** OK/Cancel dialog wrapper for Task Scheduler users who don't use the tray app.

**Dependencies:** None (only `ctypes` from stdlib)

| Function | Purpose |
|----------|---------|
| `ask_user(title, msg)` | `ctypes.windll.user32.MessageBoxW` with `MB_OKCANCEL \| MB_ICONQUESTION` |
| `main()` | Show dialog, if OK import TempoAutomation and call `sync_daily()` |

**Dialog text:**
```
Title: "Tempo Automation"
Body:  "It is time to log your daily hours.

        Click OK to sync now, or Cancel to skip today."

Buttons: [OK] [Cancel]
```

**Behavior:**
- OK clicked: imports `TempoAutomation`, calls `sync_daily()` (which has built-in schedule guard)
- Cancel clicked: prints `[SKIP] User cancelled daily sync`, exits with code 0
- Uses `pythonw.exe` (no console window when triggered by Task Scheduler)

---

## Files Modified

### File 3: `requirements.txt`

**Change:** Add two new dependencies after `winotify`:

```diff
 # Windows toast notifications (Action Center)
 winotify>=1.1.0

+# System tray app
+pystray>=0.19.0
+Pillow>=10.0.0

 # Note: All other dependencies (json, logging, smtplib, etc.)
```

---

### File 4: `run_daily.bat`

**Change:** Replace direct `python.exe tempo_automation.py` call with `pythonw.exe confirm_and_run.py`.

**Before:**
```bat
"C:\...\python.exe" "D:\...\tempo_automation.py" --logfile "D:\...\daily-timesheet.log"
```

**After:**
```bat
"C:\...\pythonw.exe" "D:\...\confirm_and_run.py"
```

**Why `pythonw.exe`:** No console window flashes when Task Scheduler triggers the bat file. The dialog is GUI-only.

**Log header lines** (echo statements at top of bat file) remain unchanged — they still append run timestamps to `daily-timesheet.log`.

---

### File 5: `install.bat`

**Change:** Add Step 5 (optional tray app setup) between Task Scheduler setup and test run. Step count updated from 5 to 6.

**New Step 5:**
```bat
echo [5/6] System Tray App (optional)
echo The tray app shows a notification at your configured sync time
echo and lives in your system tray for quick access.
set /p TRAY_SETUP="Set up tray app? (y/n): "

if /i "%TRAY_SETUP%"=="y" (
    python -m pip install pystray Pillow --quiet
    python tray_app.py --register
    start "" pythonw.exe tray_app.py
    echo [OK] Tray app is running
    echo NOTE: If using tray app only, disable Task Scheduler daily task:
    echo   schtasks /Change /TN "TempoAutomation-DailySync" /DISABLE
)
```

**Completion message updated** to include tray app commands and uninstall instructions.

---

### File 6: `CLAUDE.md`

**Changes:**
- Project structure: added `tray_app.py`, `confirm_and_run.py`, updated descriptions for `requirements.txt`, `install.bat`, `run_daily.bat`
- What's Working: added tray app and confirmation dialog items
- Version History: added v3.1 entry
- Quick Reference: added tray app commands section
- Task Scheduler section: updated DailySync description, added tray app subsection

---

### File 7: `MEMORY.md`

**Changes:**
- Version updated to 3.1
- Architecture: added tray app and confirm dialog to scheduling description
- Added v3.1 features section
- Dependencies list updated

---

## Key Design Decisions

| # | Decision | Rationale |
|---|----------|-----------|
| 1 | `threading.Timer` instead of `schedule` library | No polling loop needed, no extra dependency. Timer fires once at wall-clock time, then re-arms for next day. Works cleanly with pystray's Win32 message pump. |
| 2 | Toast is informational only (no action buttons) | winotify action buttons require COM callbacks + AppUserModelId registration. The tray menu is the more reliable confirmation surface. Toast serves as the alert; tray icon is the action. |
| 3 | Deferred import of `TempoAutomation` | `tempo_automation.py` has module-level side effects (logging setup, stdout redirect). Importing inside `_load_automation()` lets the tray icon appear first. If import fails, icon shows red with error tooltip instead of crashing invisibly. |
| 4 | HKCU Registry key for auto-start | Uses stdlib `winreg` — no admin needed, no `pywin32` dependency. Startup folder approach would need `win32com.shell` (heavy). |
| 5 | Separate `confirm_and_run.py` file | Task Scheduler users get the confirmation dialog without needing pystray/Pillow installed. Zero extra dependencies (ctypes only). |
| 6 | Named mutex for single instance | Prevents duplicate tray apps on login race conditions or accidental double-click. Uses `ctypes.windll.kernel32.CreateMutexW`. |
| 7 | `pythonw.exe` for both tray app and confirm_and_run | No console window. `pythonw.exe` ships with every Python installation alongside `python.exe`. |
| 8 | Company favicon on colored rounded-rect bg | Uses `d:\Vector\logo\favicon.ico` as-is (original colors). Pillow draws a rounded-rectangle background in the status color (green/orange/red) and overlays the logo. Cached favicon avoids repeated disk reads. Animated orange<->red alternation during sync (700ms interval via threading.Timer). Falls back to "T" glyph if favicon not found. |
| 9 | `sync_daily()` called with no arguments | Defaults to today. Already has built-in schedule guard (is_working_day check). No need to duplicate that logic in the tray app or confirm dialog. |
| 10 | Both approaches coexist | Sync is idempotent. If tray app AND Task Scheduler both fire, the second run simply deletes and re-creates the same worklogs. No data corruption. Users choose which to use. |

---

## Potential Pitfalls & Mitigations

| Pitfall | Mitigation |
|---------|-----------|
| `pystray.Icon.run()` must be on the main thread | Called from `main()` directly — all background work in daemon threads |
| DST time change could mis-fire timer | Timer uses wall-clock delay via `datetime.now()`. Re-arm after each fire recalculates fresh. DST shifts at 2 AM; timer fires at 6 PM — no overlap |
| Two instances running (tray + Task Scheduler) | Sync is idempotent. Document that user can disable one. No data corruption. |
| `tempo_automation.py` import overwrites root logger | Tray app uses its own named logger (`tray_app`). Acceptable side effect. |
| `sync_daily()` print() goes nowhere in pythonw.exe | No stdout in pythonw.exe. Output goes to `tempo_automation.log` via logging module. DualWriter not activated (no `--logfile`). |
| User has no config.json | `_load_automation()` catches FileNotFoundError, sets error message. Icon shows red. User sees "Run setup first" in tooltip and toast. |
| Pillow not installed | `PYSTRAY_OK` flag is False, `run()` prints error message and exits |
| Font not found for icon "T" | `_make_icon()` catches OSError on `truetype("arial.ttf")`, falls back to `ImageFont.load_default()` |
| Explorer.exe restart (crash/kill) | Tray icon disappears but process keeps running. User can double-click tray_app.py again — mutex blocks duplicate, but original instance continues. Minor UX issue, acceptable. |

---

## Dependencies Summary

| Package | Version | Purpose | Size | New? |
|---------|---------|---------|------|------|
| pystray | >=0.19.0 | System tray icon + menu | ~50 KB | Yes |
| Pillow | >=10.0.0 | Icon image generation (64x64 circles) | ~4 MB | Yes |
| winotify | >=1.1.0 | Toast notifications (Action Center) | ~15 KB | No (existing) |
| requests | >=2.31.0 | HTTP API calls | - | No (existing) |
| holidays | >=0.40 | Holiday detection | - | No (existing) |

**New total:** 5 pip packages (up from 3). Pillow is the largest at ~4 MB.

**stdlib dependencies used:** `ctypes` (MessageBox, mutex), `winreg` (auto-start), `threading` (Timer, Thread, Event), `subprocess` (view log/schedule), `argparse`, `logging`.

---

## Implementation Order

| Step | File | Action | Verification |
|------|------|--------|--------------|
| 1 | `requirements.txt` | Add pystray, Pillow | `pip install pystray Pillow` succeeds |
| 2 | `tray_app.py` | Create full implementation | `python tray_app.py` shows green icon in tray |
| 3 | `confirm_and_run.py` | Create dialog wrapper | `python confirm_and_run.py` shows OK/Cancel dialog |
| 4 | `run_daily.bat` | Update to use `pythonw.exe confirm_and_run.py` | Manual trigger shows dialog |
| 5 | `install.bat` | Add tray app setup step (Step 5/6) | Run install, choose Y, verify tray appears |
| 6 | `CLAUDE.md` | Update project structure, features, quick reference | Review docs |
| 7 | `MEMORY.md` | Update version, architecture, features | Review docs |

---

## Verification Checklist

### Tray App
- [ ] Tray icon appears on `python tray_app.py` (green "T" circle)
- [ ] Right-click shows full menu (7 items with 2 separators)
- [ ] "Run Now" triggers sync — icon turns yellow then green
- [ ] Double-click triggers "Confirm and Run Now" (default menu item)
- [ ] Second "Run Now" while running shows "already running" toast
- [ ] Timer fires at configured `daily_sync_time`, shows toast notification
- [ ] After timer, icon turns yellow with "Time to log hours!" tooltip
- [ ] "View Log" opens notepad with `daily-timesheet.log`
- [ ] "View Schedule" opens cmd window with calendar output
- [ ] "Settings" opens `config.json` in default editor
- [ ] "Exit" removes tray icon cleanly, process terminates
- [ ] `--register` adds `TempoTrayApp` to HKCU registry Run key
- [ ] `--unregister` removes registry entry
- [ ] After Windows login, tray icon auto-starts (if registered)
- [ ] Second instance of `tray_app.py` exits immediately (mutex guard)
- [ ] Error state: remove `config.json`, start tray — icon shows red, tooltip shows error

### Confirmation Dialog
- [ ] `python confirm_and_run.py` shows Windows dialog with OK/Cancel
- [ ] OK button triggers `sync_daily()`
- [ ] Cancel button exits with code 0 (no sync)
- [ ] `pythonw.exe confirm_and_run.py` shows dialog without console window

### Task Scheduler Integration
- [ ] `run_daily.bat` calls `pythonw.exe confirm_and_run.py`
- [ ] Manual trigger of Task Scheduler shows dialog
- [ ] Log header lines still written to `daily-timesheet.log`

### Install
- [ ] `install.bat` shows tray app question at Step 5
- [ ] Choosing "y" installs deps, registers auto-start, starts tray app
- [ ] Choosing "n" skips tray setup
- [ ] Completion message includes tray app commands

---

## User Guide

### Choosing Between Tray App and Task Scheduler

| Feature | Task Scheduler (confirm_and_run.py) | Tray App (tray_app.py) |
|---------|--------------------------------------|------------------------|
| Confirmation before sync | Yes (OK/Cancel dialog) | Yes (toast + menu click) |
| Persistent icon | No | Yes (green/yellow/red) |
| Quick access to sync/log/schedule | No | Yes (right-click menu) |
| Dependencies | None (stdlib only) | pystray + Pillow |
| Setup | Already configured | Optional (`--register`) |
| Best for | Users who prefer minimal background apps | Users who want visibility |

**Both can run simultaneously** — sync is idempotent. To use only the tray app, disable the Task Scheduler daily task:
```cmd
schtasks /Change /TN "TempoAutomation-DailySync" /DISABLE
```

### Tray App Commands
```bash
pythonw tray_app.py                # Run tray app (no console)
python tray_app.py --register      # Auto-start on Windows login
python tray_app.py --unregister    # Remove auto-start
```

### Registry Entry Details
- **Key:** `HKEY_CURRENT_USER\SOFTWARE\Microsoft\Windows\CurrentVersion\Run`
- **Value name:** `TempoTrayApp`
- **Value data:** `"C:\...\pythonw.exe" "D:\...\tray_app.py"`
- **Admin required:** No (HKCU is per-user)

---

## Code Metrics

| File | Lines | New/Modified |
|------|-------|-------------|
| `tray_app.py` | ~530 | New |
| `confirm_and_run.py` | ~35 | New |
| `requirements.txt` | +3 lines | Modified |
| `run_daily.bat` | 1 line changed | Modified |
| `install.bat` | +20 lines | Modified |
| `CLAUDE.md` | ~10 lines changed | Modified |
| `MEMORY.md` | ~8 lines changed | Modified |

**Total new code:** ~580 lines across 2 new Python files.

---

---

## Feature 15: Smart Exit with Hours Check & Auto-Restart

**Status:** Done

### Problem

When a user clicks **Exit** on the tray app:
- The process terminates completely
- The tray icon disappears
- If the user hasn't logged hours yet, there's no reminder until next Windows login
- If exit happens at 5:30 PM and sync time is 6:00 PM, the user misses that day entirely

### Proposed Flow

```
User clicks "Exit"
    |
    v
Is today a working day? (is_working_day check)
    |
    +--> NO (weekend / holiday / PTO)
    |       |
    |       v
    |     Exit cleanly. No hours expected today.
    |
    +--> YES
            |
            v
          Check Jira worklogs for today
          (JiraClient.get_my_worklogs for today's date)
            |
            +--> API call succeeds
            |       |
            |       +--> Hours >= daily_hours (8h)
            |       |       |
            |       |       v
            |       |     Exit cleanly. User's work is done.
            |       |
            |       +--> Hours < daily_hours
            |               |
            |               v
            |             Show confirmation dialog (see below)
            |
            +--> API call fails (network error, timeout)
                    |
                    v
                  Show confirmation dialog (assume not logged)
```

### Confirmation Dialog

Uses `ctypes.windll.user32.MessageBoxW` (same as confirm_and_run.py):

```
+--------------------------------------------+
|  Tempo Automation                      [X] |
|--------------------------------------------|
|  (?) You haven't logged hours for today    |
|      (0.0h / 8.0h).                       |
|                                            |
|      The app will remind you at 18:00.     |
|                                            |
|      [Exit Anyway]       [Stay Running]    |
+--------------------------------------------+
```

- **Stay Running** → cancel exit, tray icon stays, timer keeps running
- **Exit Anyway** → exit the app, BUT schedule a one-time restart (see below)

### One-Time Scheduled Restart

When user chooses "Exit Anyway" with hours not logged:

```python
def _schedule_restart(self):
    """Create a one-time Task Scheduler task to relaunch the tray app."""
    pythonw = _find_pythonw()
    tray_script = str(SCRIPT_DIR / 'tray_app.py')
    sync_time = self._get_sync_time()  # e.g., "18:00"

    # schtasks /Create /TN "TempoTrayRestart"
    #   /SC ONCE /ST 18:00
    #   /TR "\"path\to\pythonw.exe\" \"path\to\tray_app.py\""
    #   /F
    cmd = [
        'schtasks', '/Create',
        '/TN', 'TempoTrayRestart',
        '/SC', 'ONCE',
        '/ST', sync_time,
        '/TR', f'"{pythonw}" "{tray_script}"',
        '/F'
    ]
    subprocess.run(cmd, capture_output=True)
```

**What happens at sync time:**
1. Task Scheduler fires `TempoTrayRestart` at 18:00
2. Tray app starts, loads automation, timer fires immediately (since 18:00 has arrived)
3. User sees toast notification + yellow icon
4. The one-time task auto-expires (SC ONCE only fires once)

**Self-cleanup:** On next successful tray app startup, delete any leftover restart task:
```python
# In TrayApp.run(), after icon is ready:
subprocess.run(
    ['schtasks', '/Delete', '/TN', 'TempoTrayRestart', '/F'],
    capture_output=True  # ignore error if task doesn't exist
)
```

### Updated `_on_exit()` Method

```python
def _on_exit(self, icon=None, item=None):
    """Smart exit: check hours before closing."""
    should_warn = False
    hours_logged = 0.0
    daily_hours = 8.0

    # Skip check on non-working days
    today = date.today().strftime('%Y-%m-%d')
    if self._automation:
        is_working, reason = self._automation.schedule_mgr.is_working_day(today)
        if is_working:
            # Check if hours are logged in Jira
            daily_hours = self._automation.schedule_mgr.daily_hours
            try:
                worklogs = self._automation.jira_client.get_my_worklogs(today, today)
                hours_logged = sum(w['time_spent_seconds'] for w in worklogs) / 3600
                if hours_logged < daily_hours:
                    should_warn = True
            except Exception:
                should_warn = True  # Assume not logged if API fails

    if should_warn:
        # MB_YESNO | MB_ICONWARNING, Yes=6, No=7
        msg = (
            f"You haven't logged hours for today "
            f"({hours_logged:.1f}h / {daily_hours:.1f}h).\n\n"
            f"The app will remind you at {self._get_sync_time()}.\n\n"
            f"Exit anyway?"
        )
        result = ctypes.windll.user32.MessageBoxW(
            0, msg, 'Tempo Automation',
            0x04 | 0x30  # MB_YESNO | MB_ICONWARNING
        )
        if result != 6:  # Not "Yes"
            return  # Stay running

        # User chose "Exit Anyway" -- schedule restart at sync time
        self._schedule_restart()

    # Proceed with exit
    tray_logger.info("Tray app exiting")
    if self._timer:
        self._timer.cancel()
    if self._icon:
        self._icon.stop()
```

### Design Decisions

| # | Decision | Rationale |
|---|----------|-----------|
| 1 | Check Jira worklogs, not local state | Sync might have been done via CLI or Task Scheduler. Only Jira knows the truth. |
| 2 | Threshold = daily_hours (8h) from config | Respects user's configured hours. Partial hours still trigger warning. |
| 3 | Skip check on non-working days | If `is_working_day()` returns False (weekend/holiday/PTO), exit immediately — no hours expected. |
| 4 | One-time `schtasks /SC ONCE` for restart | Fires once at sync time, then auto-expires. No persistent background process. No admin rights needed. |
| 5 | User can always "Exit Anyway" | Remind, not prevent. User stays in control. |
| 6 | Default to warning on API failure | If Jira API check fails (network error, timeout), assume hours not logged. Better to warn unnecessarily than to miss. |
| 7 | Self-cleanup of restart task on next startup | `schtasks /Delete /TN TempoTrayRestart /F` on startup. Prevents stale tasks from accumulating. |

### Alternatives Considered (Rejected)

| Approach | Why Rejected |
|----------|-------------|
| Wrapper process that monitors and restarts | Extra process running permanently, complex, overkill |
| Hide icon instead of exit | Confusing UX — user thinks they exited but process still running |
| Windows Service | Requires admin install, heavy for a simple tray app |
| Just restart on next login | Doesn't help same-day — user exits at 5:30 PM, sync is at 6:00 PM |
| Block exit entirely | Bad UX — user should always be able to close the app |

### Pitfalls & Mitigations

| Pitfall | Mitigation |
|---------|-----------|
| Jira API call on exit could be slow (2-5s) | Show "Checking hours..." tooltip on icon during check. Set timeout=10 on the API call. |
| User is offline (no network) | API call fails, default to showing warning. |
| schtasks may fail (permissions, policy) | `capture_output=True`, log error, proceed with exit anyway. User still has auto-start on next login. |
| Multiple "Exit Anyway" creates duplicate tasks | `/F` flag forces overwrite of existing task with same name. |
| Restart task fires but user already restarted manually | Mutex blocks the duplicate instance. No harm. |

### Files Changed

| File | Change |
|------|--------|
| `tray_app.py` | Modify `_on_exit()`, add `_schedule_restart()`, add cleanup in `run()` |

### Verification Checklist

- [ ] Exit with hours logged (>= 8h) → exits immediately, no dialog
- [ ] Exit with hours NOT logged (< 8h) → shows confirmation dialog
- [ ] "Stay Running" → tray icon stays, app continues normally
- [ ] "Exit Anyway" → creates TempoTrayRestart scheduled task, then exits
- [ ] Restart task fires at sync time → tray app relaunches
- [ ] Next tray app startup cleans up TempoTrayRestart task
- [ ] Exit on weekend → exits immediately, no dialog
- [ ] Exit on holiday/PTO → exits immediately, no dialog
- [ ] Exit with no network → shows confirmation (assumes not logged)
- [ ] Second "Exit Anyway" overwrites previous restart task (no duplicates)

---

---

## Feature 16: Company Favicon Icon with Animated Sync Indicator

**Status:** Done

### Problem

The original icon (a letter "T" or Font Awesome glyph) didn't represent the company brand. Users wanted the Vector Solutions logo in the system tray.

### Evolution

The icon went through several iterations:
1. **White "T" on colored circle** — functional but generic
2. **Font Awesome fa-clock glyph** — better visually, but required font file
3. **Company favicon colorized** — logo recognizable but lost original colors
4. **Company favicon + colored status dot** — original logo preserved but dot was too small
5. **Company favicon on colored rounded-rect background** (final) — brand-consistent, clear status

### Final Design

```
+------------------+
|  rounded-rect bg |   Background color = status:
|   (status color) |     green  = idle/synced
|                  |     orange = pending notification
|    [favicon]     |     animated orange<->red = syncing
|    (original     |     red    = error
|     colors)      |
|                  |
+------------------+
```

- 64x64 RGBA icon with 12px corner radius
- Favicon from `d:\Vector\logo\favicon.ico` (48x48, centered with 8px padding)
- Favicon cached in memory after first load (`_favicon_cache`)
- Falls back to blue "T" on colored bg if favicon not found

### Sync Animation

During sync, the background alternates between orange and red every 700ms:
- `_start_sync_animation()`: starts a `threading.Timer` loop (700ms)
- Each tick swaps the bg color and updates the pystray icon
- `_stop_sync_animation()`: cancels the timer loop
- `_set_icon_state()` always stops animation before setting a static color

### Key Implementation Details

```python
BG_COLORS = {
    'green': (186, 230, 126),   # idle
    'orange': (252, 211, 119),  # pending / syncing frame 1
    'red': (252, 165, 165),     # error / syncing frame 2
}

def _make_icon(color='green') -> Image:
    # 1. Draw rounded rectangle in status color
    # 2. Load & cache favicon (48x48)
    # 3. Paste favicon centered on the background
```

### Files Changed

| File | Change |
|------|--------|
| `tray_app.py` | Replaced `_make_icon()`, added `_load_favicon()`, `_start_sync_animation()`, `_stop_sync_animation()`, updated color constants |

---

*All 16 features implemented February 18, 2026.*
