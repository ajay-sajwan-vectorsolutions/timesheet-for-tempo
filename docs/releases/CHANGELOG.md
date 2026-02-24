# Changelog

All notable changes to the Tempo Timesheet Automation project are documented in this file.

---

## [3.9] - February 23, 2026

### Added
- **Early timesheet submission:** Bypasses the 7-day-before-month-end window when all remaining days in the month are non-working (PTO, holidays, weekends)
- Tray app Submit Timesheet menu item visible mid-month when eligible for early submission
- `_submit_visible()` safe fallback in tray app when `_automation` is None

### How It Works
- `submit_timesheet()` checks `count_working_days(tomorrow, last_day) == 0`
- If true, submission proceeds regardless of how many calendar days remain
- Tray app mirrors this logic for menu visibility

---

## [3.8.1] - February 23, 2026

### Fixed
- **cmd /k quoting:** Outer quotes fix for paths with spaces/hyphens in tray app terminal commands
- **Tray menu auto-refresh:** `update_menu()` called after sync, submit, shortfall, and terminal close
- **Quiet console:** User-facing CLI commands (schedule, PTO, overhead, monthly view, fix) suppress StreamHandler INFO logs

### Changed
- `--view-monthly` now saves `monthly_shortfall.json` when gaps are detected (enables tray menu integration)
- Welcome toast redesign: "Welcome, {name}!" title with time-of-day greeting heading; "Welcome back" for scheduler restarts

---

## [3.8] - February 23, 2026

### Added
- **Distribution zips:** `build_dist.bat` generates 3 zip types:
  - Windows Full (~40-50MB) -- embedded Python 3.12.8, no system Python needed
  - Windows Lite (~200KB) -- requires system Python 3.7+
  - Mac (~200KB) -- requires system python3
- Zip filenames include YYYYMMDD-HHMM timestamp
- `install.bat` auto-detects Python (embedded `python/python.exe` or system PATH)
- `run_daily.bat`, `run_weekly.bat`, `run_monthly.bat` regenerated with correct paths during install

### Technical Details
- Python 3.12.8 embedded distribution downloaded and cached in `build_tmp/`
- Dependencies installed to `lib/` via `pip --target`
- `python312._pth` modified to add `../lib` and `../` to sys.path

---

## [3.7] - February 22, 2026

### Changed
- **Tempo as source of truth:** 4 methods now read Tempo API as primary data source with Jira fallback:
  - `_detect_monthly_gaps()` -- monthly gap analysis
  - `_check_day_hours()` -- per-day hour verification
  - `_sync_pto_overhead()` -- PTO/overhead sync detection
  - `_exit_flow()` -- smart exit hours check
- Pattern: `max(jira_seconds, tempo_seconds)` protects against single API failure

### Fixed
- Monthly hours showing 16h instead of 128h (was reading only Jira, missing manual Tempo entries)
- PO/Sales roles now use Jira fallback correctly when Tempo data is unavailable

---

## [3.6] - February 22, 2026

### Added
- **Monthly shortfall detection:** Per-day gap analysis in `submit_timesheet()` blocks submission when hours are short
- **`--view-monthly`** CLI: Shows per-day hours report for any month with color-coded status
- **`--fix-shortfall`** CLI: Interactive fix for monthly gaps (select days, distribute hours)
- `monthly_shortfall.json` and `monthly_submitted.json` files for tray app integration
- Tray menu restructure: Configure and Log and Reports submenus
- Dynamic Submit Timesheet and Fix Shortfall menu items (appear/hide based on state)

---

## [3.5] - February 22, 2026

### Added
- **Cross-platform Mac support:**
  - `tray_app.py` -- osascript toasts and dialogs, LaunchAgent auto-start, fcntl single-instance lock
  - `install.sh` -- 7-step Mac installer (deps, setup, overhead, cron, tray app, BSD date compat)
  - `tempo_automation.py` -- Mac toast notifications via osascript
- Platform guards: `sys.platform == 'win32'` / `== 'darwin'`
- `winotify` marked Windows-only in requirements.txt (`; sys_platform == 'win32'`)
- Mac cron jobs: daily sync, weekly verify, monthly submit

---

## [3.4] - February 20, 2026

### Added
- **Overhead story support** with 5 cases:
  1. Default daily overhead (2h configurable via `daily_overhead_hours`)
  2. No active tickets -- all hours to overhead
  3. Manual overhead preserved (not overwritten)
  4. PTO and holidays -- log overhead hours instead of skipping
  5. Planning week -- uses upcoming PI's overhead stories
- **`--select-overhead`** CLI: Interactive overhead story selection for current PI
- **`--show-overhead`** CLI: Display current overhead configuration
- Hybrid Jira+Tempo overhead detection (Jira for issue keys, Tempo for total hours)
- `JiraClient.account_id` attribute and `get_myself_account_id()` method
- `TempoClient` methods now accept `account_id` parameter
- PI identifier parsing from sprint field (regex: `PI\.(\d{2})\.(\d+)\.([A-Z]{3})\.(\d{1,2})`)

### Changed
- Holiday and PTO days now log overhead hours (previously skipped entirely)
- Email notifications default to disabled in setup wizard

---

## [3.3] - February 19, 2026

### Changed
- **Documentation reorganization:**
  - `.claude/rules/` -- modular coding rules (coding-standards, api-integration, auto-update)
  - `.claude/skills/` -- 5 slash commands (deploy-scheduler, debug-sync, review-code, test-apis, update-docs)
  - `docs/guides/` -- SETUP_GUIDE, QUICK_REFERENCE, HANDOFF
  - `docs/plans/` -- implementation plans (historical)
  - `docs/releases/` -- release notes, future enhancements
  - `docs/business/` -- executive docs, business case, one-pager
- CLAUDE.md slimmed to ~145 lines with @imports to rule files

---

## [3.2] - February 19, 2026

### Added
- **`--stop` flag:** Stop running tray app instance from command line
- **Welcome toast:** Greeting notification when tray app starts
- Auto-register autostart on first tray app run (Windows registry, Mac LaunchAgent)
- Tray auto-restart via daily scheduler (`confirm_and_run.py` checks mutex, `--quiet` flag for recovery toast)

### Fixed
- Double setup wizard bug: `ConfigManager.__new__` now skips `load_config()` when `--setup` is used
- `install.bat` rewrite: ASCII-only output, weekday-only daily sync, WeeklyVerify task, tray default, countdown close

### Changed
- Jira URL and holidays URL hardcoded in setup wizard (org-wide defaults, not prompted)

---

## [3.1] - February 18, 2026

### Added
- **System tray app** (`tray_app.py`):
  - Persistent tray icon with company favicon on color-coded background
  - Animated sync indicator (orange/red alternation)
  - One-click Sync Now (double-click or right-click menu)
  - Add PTO from tray dialog
  - Smart exit with hours verification
  - Auto-start on login (Windows registry)
  - Toast notifications via winotify
- **`confirm_and_run.py`:** OK/Cancel dialog for Task Scheduler wrappers

---

## [3.0] - February 17, 2026

### Added
- **ScheduleManager class** (~640 lines): Complete schedule management system
  - Org holidays auto-fetch from central URL (`org_holidays.json`)
  - Country/state holiday detection via `holidays` library (100+ countries)
  - PTO management: `--add-pto`, `--remove-pto`
  - Override system: `--add-holiday`, `--remove-holiday`, `--add-workday`, `--remove-workday`
  - Calendar view: `--show-schedule` with month calendar
  - Interactive menu: `--manage`
  - Day priority: working_days > pto > weekend > org_holidays > country_holidays > extra_holidays
- **Weekly verification:** `--verify-week` checks Mon-Fri, backfills using historical JQL (`status WAS`)
- **Monthly submission:** `--submit` verifies total hours and submits via Tempo API
- **NotificationManager class:** SMTP email + Teams webhook + desktop toast (Windows)
- 12 new CLI arguments for schedule management

---

## [2.0] - February 12, 2026

### Added
- **DualWriter class:** Simultaneous stdout + file output (`--logfile` flag)
- Batch file wrappers: `run_daily.bat`, `run_weekly.bat`, `run_monthly.bat`
- ASCII-only output enforcement (Windows cp1252 compatibility)

### Changed
- All print() output converted to ASCII-safe characters
- Error messages use `[OK]`/`[FAIL]`/`[!]` instead of Unicode symbols

---

## [1.0] - February 3, 2026

### Added
- **Initial release** with core functionality:
  - Daily timesheet sync for developers
  - Jira REST API v3 integration (Basic auth)
  - Tempo API v4 integration (Bearer token)
  - Smart hour distribution across active tickets (integer division, remainder on last)
  - Smart worklog descriptions from ticket content (description + recent comments)
  - Idempotent overwrite (delete then create)
  - Monthly timesheet submission via Tempo API
  - ConfigManager with setup wizard
  - CredentialManager with DPAPI encryption (Windows)
  - CLI with argparse
