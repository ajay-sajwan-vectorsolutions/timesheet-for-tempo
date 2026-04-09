# E008 - Mac Support Implementation Plan

**Created:** April 9, 2026
**Status:** IMPLEMENTED (pending Mac testing on feature/E008-mac-support branch)
**Enhancement:** E008 (enhancements.md)

---

## Context

The Tempo Timesheet Automation app works well on Windows but has incomplete Mac support. The codebase already has **partial** Mac handling (keyring credentials, osascript notifications, LaunchAgent auto-start, AppleScript dialogs, fcntl locking), but key gaps remain: no shell wrapper scripts for cron jobs, install.sh lacks feature parity with install.bat, tray_app.py has hardcoded `python3` and no restart/scheduler-update logic for Mac, and the Mac distribution zip is missing files.

The user reported they couldn't see the tray app on Mac during last test. All changes must not break Windows.

---

## Phase 1 — `feature/for-mac/shell-wrappers` (New files only, zero risk to Windows)

### Create run_daily.sh, run_weekly.sh, run_monthly.sh

Mirror the `.bat` wrappers. Each script:
- Resolves `SCRIPT_DIR` from its own location
- Uses `_get_month.py` for log rotation (`YYYY-MM` suffix)
- Has a `PYTHON_EXE` variable placeholder (filled by install.sh during generation)
- Logs timestamp header to the log file

| Script | Calls | Mirrors |
|--------|-------|---------|
| `run_daily.sh` | `confirm_and_run.py` | `run_daily.bat` (line 8) |
| `run_weekly.sh` | `tempo_automation.py --verify-week --logfile` | `run_weekly.bat` |
| `run_monthly.sh` | `tempo_automation.py --submit --logfile` | `run_monthly.bat` |

**Reference:** `run_daily.bat` (10 lines) — uses PowerShell for date, calls `confirm_and_run.py`

### Files to create
- `run_daily.sh`, `run_weekly.sh`, `run_monthly.sh`

---

## Phase 2 — `feature/for-mac/install-sh-parity` (Depends on Phase 1)

### Bring install.sh (307 lines) closer to install.bat (578 lines)

**2a. Better Python detection** (install.sh currently just checks `python3`)
- Check `python3`, then `python` (verify it's 3.x)
- Check Homebrew paths: `/opt/homebrew/bin/python3`, `/usr/local/bin/python3`
- Store full resolved path in `PYTHON_PATH`

**2b. Multi-method upgrade detection** (install.bat has 5 methods, install.sh has 1)
- Method 1: LaunchAgent plist (already exists, line 34-47)
- Method 2: Parse `crontab -l` for `tempo_automation.py`, extract directory
- Method 3: Check running processes via `pgrep -af tray_app.py`
- Method 4: Scan well-known paths: `~/tempo-timesheet`, `~/Desktop/tempo-timesheet`

**2c. Config migration**
- Before stopping old tray: copy `config.json` from old install to `/tmp/_tempo_migrated_config.json`
- After file copy: restore from temp file or from `~/.config/TempoAutomation/config.json`

**2d. Generate wrapper scripts with detected Python path**
- Generate `run_daily.sh`, `run_weekly.sh`, `run_monthly.sh` with `PYTHON_EXE` baked in
- Generate `_get_month.py` helper
- `chmod +x *.sh`

**2e. Update cron jobs to use wrapper scripts**
- Change from `python3 tempo_automation.py >> log 2>&1` to `"$INSTALL_DIR/run_daily.sh"`
- This gives cron jobs log rotation automatically

**2f. Post-install shortfall check** (install.bat step 8/8)
- Add final step: `python3 tempo_automation.py --post-install-check`

**2g. Clean up old installation traces**
- Remove old LaunchAgent plist, old cron entries, old lock/signal files

### Files to modify
- `install.sh`

---

## Phase 3 — `fix/for-mac/tray-app-fixes` (Independent, but do after Phase 2)

### 3a. Fix hardcoded `python3` in terminal launch
- **tray_app.py line 1236**: Replace `python3` → `sys.executable`
- **tray_app.py line 1245** (Linux fallback): Same fix

### 3b. Implement crontab time update on Mac
- **tray_app.py lines 989-1015**: `_update_task_scheduler_time()` is no-op on Mac
- Add Mac branch: parse `crontab -l`, find daily sync line, update minute/hour fields, write back via `crontab -`

### 3c. Implement restart strategy for Mac
- **tray_app.py lines 1472-1484**: Currently just logs a message on Mac
- Change LaunchAgent `KeepAlive` from `<false/>` to `SuccessfulExit: false` — macOS auto-restarts on crash but not on clean exit (sys.exit(0))
- Update `register_autostart()` (line 1821) accordingly

### 3d. Reconcile crontab on tray startup
- **tray_app.py line 1009-1018**: `_reconcile_task_scheduler` returns immediately on non-Windows
- Add Mac logic: compare config sync time with crontab entry, update if mismatched

### Files to modify
- `tray_app.py`

---

## Phase 4 — `feature/for-mac/build-dist-update` (Depends on Phase 1)

### Update Mac distribution zip contents
- **build_dist.bat `:build_mac` section (lines 228-252)**:
  - Add `run_daily.sh`, `run_weekly.sh`, `run_monthly.sh` to Mac staging
  - Add `confirm_and_run.py` (it's already cross-platform — uses fcntl on Mac)
  - Update comment at line 241

### Files to modify
- `build_dist.bat`

---

## Branch Strategy & Merge Order

| # | Branch | Base | Key Files | 
|---|--------|------|-----------|
| 1 | `feature/for-mac/shell-wrappers` | `main` | New: `run_daily.sh`, `run_weekly.sh`, `run_monthly.sh` |
| 2 | `feature/for-mac/install-sh-parity` | `main` (after #1 merged) | `install.sh` |
| 3 | `fix/for-mac/tray-app-fixes` | `main` (after #2 merged) | `tray_app.py` |
| 4 | `feature/for-mac/build-dist-update` | `main` (after #1 merged) | `build_dist.bat` |

---

## Verification

### Per-phase
- `pytest tests/ -v --tb=short` — all 528 tests must pass after each phase
- Manual Windows regression check: install.bat, tray menu, .bat wrappers unchanged

### End-to-end (after all phases merged)
1. Build Mac zip via `build_dist.bat` → verify all .sh scripts + confirm_and_run.py included
2. Fresh install on Mac from zip → verify all 8 install steps complete
3. Upgrade install on Mac (simulate old install) → verify config migrates
4. Tray app on Mac: change sync time → verify crontab updates
5. Tray app on Mac: exit → verify KeepAlive behavior (no restart on clean exit, restart on crash)
6. Run `run_daily.sh` manually → verify log rotation works
7. Full fresh install on Windows → verify no regressions

### Known limitation
- No Mac hardware available for live testing; verification will rely on code review of platform branches and testing on user's Mac
