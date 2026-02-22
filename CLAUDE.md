# Tempo Timesheet Automation

**Version:** 3.6 | **Status:** Production | **Updated:** February 22, 2026
**Owner:** Ajay Sajwan (ajay.sajwan-ctr@vectorsolutions.com, developer role)

---

## Rules & Standards

@.claude/rules/coding-standards.md
@.claude/rules/api-integration.md
@.claude/rules/auto-update.md

---

## Project Overview

Automates daily timesheet entry and monthly submission for a 200-person engineering team.
Developers get Jira worklogs auto-distributed across active tickets; Tempo syncs from Jira.

- **Main script:** `tempo_automation.py` (4,224 lines, 8 classes)
- **Tray app:** `tray_app.py` (~1,306 lines, cross-platform: Windows + Mac)
- **Installers:** `install.bat` (Windows), `install.sh` (Mac) -- 7 steps each
- **Python:** 3.7+ (Ajay: Python 3.14)
- **Jira:** lmsportal.atlassian.net (REST v3, Basic auth)
- **Tempo:** api.tempo.io/4 (Bearer token)

### How It Works (Developer)
1. Deletes existing worklogs for target date (idempotent overwrite)
2. Queries active tickets (IN DEVELOPMENT / CODE REVIEW)
3. Distributes daily_hours equally (integer division, remainder on last ticket)
4. Generates smart descriptions from ticket content (description + recent comments)
5. Creates Jira worklogs directly -- Tempo auto-syncs
6. Schedule guard: skips weekends, holidays, PTO before any API calls

### Roles
- **Developer:** Jira + Tempo tokens, auto-distributes across active tickets
- **Product Owner / Sales:** Tempo token only, uses manual_activities from config

---

## Architecture (tempo_automation.py)

| Class | Lines | Purpose |
|-------|-------|---------|
| DualWriter | 47 | stdout + file dual output (--logfile) |
| CredentialManager | 91-199 | DPAPI encrypt/decrypt for Windows |
| ConfigManager | 206-475 | Config, setup wizard, location picker, get_account_id() |
| ScheduleManager | 477-1120 | Holidays, PTO, overrides, is_working_day(), calendar |
| JiraClient | ~1125 | Worklogs CRUD, active issues, historical JQL, ADF, get_myself_account_id(), account_id attr |
| TempoClient | ~1509 | Worklogs, submit timesheet, get period (takes account_id parameter) |
| NotificationManager | ~1587 | SMTP email, Teams webhook, desktop toast (Win + Mac) |
| TempoAutomation | ~1918 | Orchestration, sync, verify_week, backfill, overhead, monthly gap detection, shortfall fix |
| CLI | ~3970-4220 | argparse with 18 arguments (added --view-monthly, --fix-shortfall) |

### Key Patterns
- **Day priority:** working_days > pto > weekend > org_holidays > country_holidays > extra_holidays
- **Setup wizard --setup:** uses `ConfigManager.__new__` to skip load_config() (avoids double wizard)
- **Jira URL + holidays URL:** hardcoded in setup wizard (org-wide defaults)
- **Tray app callbacks:** must return quickly -- heavy work in daemon threads

---

## Project Structure

```
v2/
├── CLAUDE.md                       # This file (project context)
├── README.md                       # User documentation
├── tempo_automation.py             # Main script (4,224 lines)
├── tray_app.py                     # System tray app (~1,306 lines, cross-platform)
├── confirm_and_run.py              # OK/Cancel dialog for Task Scheduler
├── install.bat / install.sh        # Installers
├── run_daily.bat / run_weekly.bat / run_monthly.bat  # Task Scheduler wrappers
├── config.json                     # User config (gitignored)
├── config_template.json            # Config template
├── org_holidays.json               # Org holidays (auto-fetched)
├── requirements.txt                # Python deps
├── assets/favicon.ico              # Company icon for tray app
├── examples/                       # Example configs (developer, PO, sales)
├── .claude/
│   ├── rules/                      # Modular coding rules (auto-loaded)
│   │   ├── coding-standards.md
│   │   ├── api-integration.md
│   │   └── auto-update.md
│   └── skills/                     # Custom slash commands
│       ├── deploy-scheduler/       # /deploy-scheduler
│       ├── debug-sync/             # /debug-sync
│       ├── review-code/            # /review-code
│       ├── test-apis/              # /test-apis
│       └── update-docs/            # /update-docs
├── docs/
│   ├── guides/                     # User-facing docs
│   │   ├── SETUP_GUIDE.md
│   │   ├── QUICK_REFERENCE.md
│   │   └── HANDOFF.md
│   ├── plans/                      # Implementation plans (historical)
│   │   ├── IMPLEMENTATION_PLAN_V3.md
│   │   ├── IMPLEMENTATION_PLAN_V4.md
│   │   └── WEEKLY_VERIFY_PLAN.md
│   ├── releases/                   # Release notes
│   │   ├── VERSION_2_RELEASE_NOTES.md
│   │   └── FUTURE_ENHANCEMENTS.md
│   └── business/                   # Executive docs
└── archive/                        # Deprecated files
```

---

## Quick Commands

```bash
# Core
python tempo_automation.py                          # Daily sync (today)
python tempo_automation.py --date 2026-02-15        # Sync specific date
python tempo_automation.py --verify-week             # Weekly verify & backfill
python tempo_automation.py --submit                  # Monthly submit
python tempo_automation.py --setup                   # Setup wizard

# Schedule
python tempo_automation.py --add-pto 2026-03-10,2026-03-11
python tempo_automation.py --remove-pto 2026-03-10
python tempo_automation.py --show-schedule           # Current month calendar
python tempo_automation.py --manage                  # Interactive menu

# Overhead
python tempo_automation.py --select-overhead         # Interactive overhead story selection
python tempo_automation.py --show-overhead            # Display current overhead config

# Monthly
python tempo_automation.py --view-monthly            # Show current month hours per day
python tempo_automation.py --view-monthly 2026-01    # Show specific month hours
python tempo_automation.py --fix-shortfall           # Interactive fix for monthly gaps

# Tray App
pythonw tray_app.py                                  # Run (no console)
python tray_app.py --stop                            # Stop running instance
python tray_app.py --register / --unregister         # Auto-start control
```

---

## Current Status

**Working:** Daily sync, idempotent overwrite, smart descriptions, schedule guard (weekends/holidays/PTO), weekly verify, monthly submit with per-day gap detection (blocks submission on shortfall), --view-monthly hours report, --fix-shortfall interactive fix, tray app with favicon (cross-platform: Windows + Mac), tray menu submenus (Configure, Log and Reports), dynamic Submit Timesheet/Fix Shortfall menu items, install.bat + install.sh (7 steps each), DPAPI encryption (plain text fallback on Mac), --stop flag, welcome toast, auto-register autostart (Win: registry, Mac: LaunchAgent), overhead story support (5 cases), hybrid Jira+Tempo overhead detection, holiday overhead logging, email notifications default disabled, Mac toast via osascript, Mac dialogs via AppleScript, Mac single instance via fcntl file lock, Mac cron (daily + weekly verify + monthly submit with BSD date compat)

**TODO:**
- [ ] Test --verify-week with live data
- [ ] Test monthly submission (end of month)
- [ ] Test PO/Sales roles
- [ ] Teams webhook: uncomment call (line ~2447) + add webhook URL
- [ ] Test tray app on actual Mac hardware
- [ ] PyInstaller .exe, unit tests, --dry-run, retry logic

### Version History
| Version | Date | Changes |
|---------|------|---------|
| v1.0 | Feb 3 | Initial development |
| v2.0 | Feb 3 | Account ID, issue key, period API fixes |
| v3.0 | Feb 17 | ScheduleManager, holidays, PTO, weekly verify, Teams |
| v3.1 | Feb 18 | Tray app, favicon, smart exit, confirm dialog |
| v3.2 | Feb 19 | Hardcoded URLs, install.bat rewrite, --stop, welcome toast |
| v3.3 | Feb 19 | Doc reorganization, .claude/rules, .claude/skills |
| v3.6 | Feb 22 | Monthly shortfall detection: per-day gap analysis, blocks submission on shortfall, --view-monthly/--fix-shortfall CLI, interactive fix with day selection, tray menu restructure (Configure/Log and Reports submenus), dynamic Submit Timesheet and Fix Shortfall menu items |
| v3.5 | Feb 22 | Cross-platform Mac support: tray_app.py (osascript dialogs/toasts, LaunchAgent autostart, fcntl mutex), install.sh rewrite (7 steps, overhead, weekly verify cron, BSD date), Mac toast in tempo_automation.py, winotify platform marker |
| v3.4 | Feb 20 | Overhead story support: 5 cases (default daily 2h overhead, no active tickets, manual overhead, PTO/holidays, planning week), --select-overhead/--show-overhead CLI, hybrid Jira+Tempo detection, JiraClient account_id, TempoClient account_id parameter, holidays log overhead same as PTO, email default disabled, configurable daily_overhead_hours |

---

## Key URLs

- Jira: https://lmsportal.atlassian.net/
- Tempo API docs: https://apidocs.tempo.io/
- Jira API docs: https://developer.atlassian.com/cloud/jira/platform/rest/v3/
- Jira tokens: https://id.atlassian.com/manage-profile/security/api-tokens
- Tempo tokens: https://app.tempo.io/ -> Settings -> API Integration

---

## Logs & Debugging

- `tempo_automation.log` -- internal runtime logs
- `daily-timesheet.log` -- execution output
- 401 = expired/invalid API token
- "No active tickets" = no IN DEVELOPMENT / CODE REVIEW tickets assigned
- UnicodeEncodeError = Unicode in print() -- replace with ASCII

*See /debug-sync skill for systematic troubleshooting.*
