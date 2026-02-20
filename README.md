# Tempo Timesheet Automation

**Automate your daily Tempo timesheet entry and monthly submission -- save 15+ minutes every day.**

Version 3.4 | Python 3.7+ | Windows (primary), Mac/Linux (untested)

---

## Overview

This automation script eliminates the manual burden of timesheet management:

- **For Developers:** Automatically distributes daily hours across your active Jira tickets and logs worklogs directly in Jira. Tempo auto-syncs.
- **For Product Owners & Sales:** Pre-fills timesheets based on configured activities via the Tempo API.
- **For Everyone:** Auto-submits timesheets at month-end, skips weekends/holidays/PTO, and catches missed days with weekly verification.

**Time saved:** 15-20 minutes per day per person
**Annual savings:** ~$1.2M across a 200-person team

---

## Features

### Core Automation
- **Daily sync** -- distributes hours equally across active Jira tickets (IN DEVELOPMENT / CODE REVIEW)
- **Smart descriptions** -- generates meaningful worklog comments from ticket description + recent comments
- **Idempotent** -- safe to re-run anytime; deletes previous entries then creates fresh ones
- **Monthly submission** -- verifies total hours and submits timesheet on the last day of each month
- **Weekly verification** -- Friday check catches missed days and backfills using historical ticket data
- **Overhead stories** -- automatically logs overhead hours on PTO days, holidays, and when no active tickets exist

### Schedule Management
- **Holiday detection** -- org holidays (auto-fetched from central URL) + national/state holidays (100+ countries)
- **PTO management** -- add/remove PTO dates via CLI or tray app; script skips those days
- **Override system** -- extra holidays (ad-hoc) and compensatory working days (weekend work)
- **Calendar view** -- visual month calendar showing working days, holidays, PTO
- **Interactive menu** -- guided schedule management via `--manage`

### System Tray App (Windows)
- **Persistent tray icon** -- company logo on color-coded background (green/orange/red)
- **Animated sync indicator** -- orange/red alternation during sync
- **One-click sync** -- double-click or right-click -> Sync Now
- **Add PTO from tray** -- dialog for entering PTO dates without opening a terminal
- **Smart exit** -- checks if hours are logged before closing; warns if not
- **Auto-start** -- registers itself on first run; starts automatically on Windows login
- **Toast notifications** -- Windows notification when it's time to log hours

### Developer Workflow
```
sync_daily()
  -> Delete existing worklogs for target date (overwrite)
  -> Query active Jira tickets (IN DEVELOPMENT / CODE REVIEW)
  -> Distribute daily_hours equally across tickets
  -> Generate smart description per ticket (from description + comments)
  -> Create Jira worklogs (Tempo auto-syncs)
```

### Scheduling Options
- **System tray app** (recommended) -- persistent icon, user confirmation before sync
- **Windows Task Scheduler** -- fully hands-off, runs via batch file wrappers
- Both can coexist safely (sync is idempotent)

---

## Prerequisites

- Python 3.7 or higher
- Tempo API token (all users)
- Jira API token (developers only)

---

## Quick Start

### Automated Install

```cmd
:: Right-click install.bat -> Run as Administrator
install.bat
```

The installer handles: dependency install, setup wizard, Task Scheduler, and optional tray app setup.

### Manual Install

```cmd
:: 1. Install dependencies
pip install -r requirements.txt

:: 2. Run setup wizard
python tempo_automation.py --setup

:: 3. Test it
python tempo_automation.py

:: 4. Start the tray app (recommended)
pythonw tray_app.py
```

See [SETUP_GUIDE.md](docs/guides/SETUP_GUIDE.md) for detailed step-by-step instructions.

---

## Usage

### Daily Operations

```cmd
:: Sync today's timesheet (default)
python tempo_automation.py

:: Sync a specific date
python tempo_automation.py --date 2026-02-15

:: Weekly verification & backfill (checks Mon-Fri)
python tempo_automation.py --verify-week

:: Submit monthly timesheet (only runs on last day of month)
python tempo_automation.py --submit
```

### Schedule Management

```cmd
:: Add PTO days (comma-separated)
python tempo_automation.py --add-pto 2026-03-10,2026-03-11,2026-03-12

:: Remove a PTO day
python tempo_automation.py --remove-pto 2026-03-12

:: Add an extra holiday (org-declared)
python tempo_automation.py --add-holiday 2026-04-14

:: Add a compensatory working day (weekend override)
python tempo_automation.py --add-workday 2026-11-08

:: View schedule calendar
python tempo_automation.py --show-schedule          :: current month
python tempo_automation.py --show-schedule 2026-03  :: specific month

:: Interactive menu
python tempo_automation.py --manage
```

### Tray App

```cmd
:: Run the tray app (no console window)
pythonw tray_app.py

:: Register/remove auto-start on login (auto-registers on first run)
python tray_app.py --register
python tray_app.py --unregister

:: Stop a running tray app instance
python tray_app.py --stop
```

### Overhead Stories

```cmd
:: Select overhead stories for current PI
python tempo_automation.py --select-overhead

:: View current overhead configuration
python tempo_automation.py --show-overhead
```

### Other

```cmd
:: Re-run setup wizard
python tempo_automation.py --setup

:: Dual output (console + log file)
python tempo_automation.py --logfile daily-timesheet.log
```

---

## Configuration

The setup wizard creates `config.json` with your settings. Key sections:

```json
{
  "user": {
    "email": "your.email@company.com",
    "name": "Your Name",
    "role": "developer"
  },
  "jira": {
    "url": "lmsportal.atlassian.net",
    "email": "your.email@company.com",
    "api_token": "ENC:... (encrypted)"
  },
  "tempo": {
    "api_token": "ENC:... (encrypted)"
  },
  "organization": {
    "holidays_url": "https://ajay-sajwan-vectorsolutions.github.io/local-assets/org_holidays.json"
  },
  "schedule": {
    "daily_hours": 8,
    "daily_sync_time": "18:00",
    "monthly_submit_day": "last",
    "country_code": "US",
    "state": "",
    "pto_days": [],
    "extra_holidays": [],
    "working_days": []
  },
  "overhead": {
    "stories": ["OVERHEAD-329", "OVERHEAD-330"],
    "pi_name": "PI 2026.1",
    "selected_date": "2026-02-20"
  },
  "notifications": {
    "email_enabled": false,
    "teams_webhook_url": "",
    "notify_on_shortfall": true
  }
}
```

**Do not share `config.json`** -- it contains your API tokens.

API tokens are encrypted using Windows DPAPI (tied to your Windows user account). On non-Windows systems, tokens are stored as plain text.

### Getting API Tokens

**Tempo API Token:**
1. Go to https://app.tempo.io/
2. Settings -> API Integration -> New Token
3. Copy the token

**Jira API Token (developers):**
1. Go to https://id.atlassian.com/manage-profile/security/api-tokens
2. Create API token -> Copy

### Role Configurations

| Role | Jira Token | Tempo Token | How Hours Are Logged |
|------|-----------|-------------|---------------------|
| Developer | Required | Required | Auto-distributed across active Jira tickets |
| Product Owner | Not needed | Required | From `manual_activities` config |
| Sales | Not needed | Required | From `manual_activities` config |

---

## How It Works

### Daily Sync (Developer)
1. Check if today is a working day (skip weekends, holidays, PTO)
2. Delete any existing worklogs for today (idempotent overwrite)
3. Query Jira for tickets assigned to you in IN DEVELOPMENT or CODE REVIEW
4. Divide `daily_hours` equally across tickets (integer division, remainder on last ticket)
5. For each ticket, generate a 1-3 line description from its content
6. Create Jira worklogs -- Tempo auto-syncs from Jira

### Overhead Story Logging
When no active tickets exist, hours are logged to configured overhead stories. PTO days and holidays also log overhead hours instead of skipping.

### Weekly Verification (Friday)
1. Check each day Mon-Fri for sufficient logged hours
2. For any day with missing hours, query historical tickets using `status WAS`
3. Backfill missing days with worklogs distributed across those tickets
4. Send notification if hours are still short after backfill

### Monthly Submission (Last Day)
1. Verify total monthly hours meet expectation
2. Submit timesheet for approval via Tempo API
3. Send confirmation notification

### Holiday Priority
When dates conflict, this priority applies:
1. Compensatory working day (always wins)
2. PTO
3. Weekend
4. Org holiday
5. National/state holiday
6. Extra holiday
7. Default (working day)

---

## Troubleshooting

### Common Issues

| Problem | Cause | Fix |
|---------|-------|-----|
| "No active tickets found" | No tickets in IN DEVELOPMENT / CODE REVIEW | Check your Jira board |
| "401 Unauthorized" | Expired or invalid API token | Regenerate token, re-run `--setup` |
| Script logs wrong hours | Stale data from previous run | Re-run -- it overwrites automatically |
| Tray app not starting on reboot | Auto-start not registered | Run tray app once (`pythonw tray_app.py`) -- it auto-registers |
| Tray icon shows red | Config or import error | Check `tempo_automation.log` |

### Logs

- `daily-timesheet.log` -- what the script did (execution output)
- `tempo_automation.log` -- detailed runtime logs (API calls, errors, timestamps)

```cmd
:: View execution log
type daily-timesheet.log

:: Search for errors
findstr ERROR daily-timesheet.log
```

---

## Uninstall

```cmd
:: Stop running tray app and remove auto-start
python tray_app.py --stop
python tray_app.py --unregister

:: Remove scheduled tasks (if using Task Scheduler)
schtasks /Delete /TN "TempoAutomation-DailySync" /F
schtasks /Delete /TN "TempoAutomation-WeeklyVerify" /F
schtasks /Delete /TN "TempoAutomation-MonthlySubmit" /F

:: Then delete the installation folder
```

---

## Project Structure

```
tempo_automation.py          # Main script (3,724 lines, 8 classes)
tray_app.py                  # System tray app (~831 lines)
confirm_and_run.py           # OK/Cancel dialog for Task Scheduler
config.json                  # User config (gitignored, contains tokens)
config_template.json         # Config template for new users
org_holidays.json            # Organization holiday definitions
requirements.txt             # Python dependencies
install.bat                  # Windows installer
run_daily.bat                # Task Scheduler wrapper (daily)
run_weekly.bat               # Task Scheduler wrapper (weekly)
run_monthly.bat              # Task Scheduler wrapper (monthly)
assets/favicon.ico           # Company icon for tray app
examples/                    # Example configs for each role
```

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 3.4 | Feb 20, 2026 | Overhead story support -- automatic logging for PTO, holidays, no-ticket days, and PI planning weeks. New CLI: --select-overhead, --show-overhead |
| 3.2 | Feb 19, 2026 | Hardcoded Jira/holidays URL, fixed double setup wizard, install.bat rewrite (ASCII, weekday-only, WeeklyVerify, tray default, countdown close), --stop flag, welcome toast, auto-register autostart |
| 3.1 | Feb 18, 2026 | System tray app, animated sync indicator, smart exit, Add PTO from tray, OK/Cancel dialog, auto-start |
| 3.0 | Feb 17, 2026 | Schedule management, holiday detection (org + country/state), PTO/override system, weekly verification & backfill, monthly hours check, calendar view, 12 new CLI commands |
| 2.0 | Feb 12, 2026 | ASCII-only output, DualWriter, batch wrappers, active daily use |
| 1.0 | Feb 3, 2026 | Initial release with daily sync and monthly submission |

---

## Support

- Check the logs first: `daily-timesheet.log` and `tempo_automation.log`
- Slack channel: #tempo-automation
- Detailed setup instructions: [SETUP_GUIDE.md](SETUP_GUIDE.md)

---

Developed by Vector Solutions Engineering Team
