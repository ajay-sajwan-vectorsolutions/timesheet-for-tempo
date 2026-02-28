# Feature Specification: Tempo Timesheet Automation v4.0

**Status:** Draft | **Date:** February 28, 2026

This document describes every user-facing feature in the system, organized by
what the user can do. Each feature includes who can use it, how to trigger it,
and what happens.

---

## 1. Daily Timesheet Logging

### 1.1 Auto-Log Daily Hours (Developer)
- **Who:** Developers (Jira + Tempo tokens)
- **Trigger:** `python tempo_automation.py` or tray menu "Sync Now"
- **What happens:**
  1. Checks if today is a working day (skips weekends, holidays, PTO)
  2. Finds all tickets assigned to the user in "IN DEVELOPMENT" or "CODE REVIEW" status
  3. Deletes any existing non-overhead worklogs for today (safe re-run)
  4. Logs configured overhead hours (default 2h) to overhead stories first
  5. Distributes remaining hours equally across active tickets
  6. Generates smart descriptions from ticket content (description + recent comments)
  7. Creates worklogs in Jira (Tempo auto-syncs)
- **Result:** Full daily hours logged across all active tickets with meaningful descriptions

### 1.2 Auto-Log Daily Hours (Product Owner / Sales)
- **Who:** Product Owners, Sales (Tempo token only)
- **Trigger:** Same as above
- **What happens:**
  1. Checks if today is a working day
  2. Checks if any entries already exist for today (skips if yes)
  3. Creates Tempo worklogs for each configured manual activity (e.g., "Sprint Planning: 4h", "Backlog Grooming: 4h")
- **Result:** Full daily hours logged to pre-configured activities

### 1.3 Sync a Specific Date
- **Who:** All roles
- **Trigger:** `python tempo_automation.py --date 2026-02-15`
- **What happens:** Same as daily sync but for the specified date instead of today
- **Use case:** Backfill a missed day, re-sync after fixing config

### 1.4 Smart Work Descriptions
- **Who:** Developers (automatic, no user action needed)
- **What happens:** Each worklog gets a 1-3 line description built from:
  - Line 1: First sentence of the Jira ticket description
  - Lines 2-3: First line of the most recent comments on the ticket
  - Truncated to 120 characters per line
- **Result:** Worklogs have context-rich descriptions instead of blank entries

---

## 2. Overhead Story Management

### 2.1 Configure Overhead Stories
- **Who:** Developers
- **Trigger:** `python tempo_automation.py --select-overhead` or tray menu "Configure > Select Overhead"
- **What happens:**
  1. Fetches overhead stories from Jira (project = OVERHEAD, status = "In Progress")
  2. Groups stories by PI (Program Increment) identifier
  3. User selects current PI stories and optionally planning PI stories
  4. User picks distribution mode: single story, equal split, or custom proportional
  5. User sets PTO story key, fallback issue key, daily overhead hours
  6. Saves to config
- **Result:** Overhead stories configured for automatic daily logging

### 2.2 View Overhead Configuration
- **Who:** Developers
- **Trigger:** `python tempo_automation.py --show-overhead` or review config.json
- **What happens:** Displays current overhead setup including PI identifier, stories, distribution mode, PTO story, daily overhead hours, and planning week dates

### 2.3 Daily Overhead Logging (Automatic)
- **Who:** Developers (no user action -- happens during daily sync)
- **What happens:** Every working day, configured overhead hours (default 2h) are logged to overhead stories before distributing remaining hours to active tickets
- **5 overhead scenarios:**
  - Normal day: 2h overhead + remaining to active tickets
  - No active tickets: all hours go to overhead stories
  - Manual overhead exists: preserved, remainder to active tickets
  - PTO / Holiday: full daily hours logged to PTO story
  - Planning week (after PI ends): hours logged to upcoming PI overhead stories

---

## 3. Schedule Management

### 3.1 Add PTO Days
- **Who:** All roles
- **Trigger:** `python tempo_automation.py --add-pto 2026-03-10,2026-03-11` or tray menu "Configure > Add PTO"
- **What happens:**
  - Validates date format (YYYY-MM-DD)
  - Rejects weekends (no need to mark weekends as PTO)
  - Adds to PTO list in config
  - Daily sync will skip these days (and log overhead for developers)
- **Tray shortcut:** Input dialog prompts for comma-separated dates

### 3.2 Remove PTO Days
- **Who:** All roles
- **Trigger:** `python tempo_automation.py --remove-pto 2026-03-10`
- **What happens:** Removes dates from PTO list, future syncs will treat them as working days

### 3.3 Add Extra Holidays
- **Who:** All roles
- **Trigger:** `python tempo_automation.py --add-holiday 2026-12-24`
- **What happens:** Adds user-defined holidays beyond the org/country holidays. Daily sync skips these days.

### 3.4 Remove Extra Holidays
- **Who:** All roles
- **Trigger:** `python tempo_automation.py --remove-holiday 2026-12-24`

### 3.5 Add Compensatory Working Days
- **Who:** All roles
- **Trigger:** `python tempo_automation.py --add-workday 2026-01-26`
- **What happens:** Marks a weekend or holiday as a working day (highest priority override). Daily sync will log hours on this day even if it falls on a weekend or holiday.
- **Use case:** Saturday make-up day, working holiday

### 3.6 Remove Compensatory Working Days
- **Who:** All roles
- **Trigger:** `python tempo_automation.py --remove-workday 2026-01-26`

### 3.7 View Monthly Calendar
- **Who:** All roles
- **Trigger:** `python tempo_automation.py --show-schedule` (current month) or `--show-schedule 2026-03` (specific month)
- **What happens:** Displays a Mon-Sun calendar grid with status labels:
  - W = Working day
  - H = Holiday (org or country)
  - PTO = Paid time off
  - CW = Compensatory working day
  - . = Weekend
  - Summary: total working days, PTO count, holiday count

### 3.8 Interactive Schedule Menu
- **Who:** All roles
- **Trigger:** `python tempo_automation.py --manage`
- **What happens:** 10-option interactive menu:
  1. Add PTO days
  2. Remove PTO days
  3. Add extra holidays
  4. Remove extra holidays
  5. Add compensatory working days
  6. Remove compensatory working days
  7. View current month calendar
  8. View specific month calendar
  9. List all PTO/holiday/working day dates
  10. Back/Exit

### 3.9 Organization Holidays (Automatic)
- **Who:** All roles (no user action)
- **What happens:** On every run, fetches the organization holiday calendar from a central URL. Includes country-specific and state-specific holidays (US, India-Pune/Hyderabad/Gandhinagar). Falls back to local file if URL unreachable.

### 3.10 Country Holidays (Automatic)
- **Who:** All roles (no user action)
- **What happens:** Uses the Python `holidays` library to detect 100+ country public holidays based on the user's configured country/state. Supplements the org holiday list.

### 3.11 Working Day Classification
- **Who:** All roles (no user action -- drives all schedule decisions)
- **Priority chain (highest wins):**
  1. Compensatory working day -> WORK (overrides everything)
  2. PTO -> SKIP
  3. Weekend -> SKIP
  4. Org holiday -> SKIP
  5. Country holiday -> SKIP
  6. Extra holiday -> SKIP
  7. Default -> WORK

---

## 4. Monthly Timesheet Submission

### 4.1 Submit Monthly Timesheet
- **Who:** All roles
- **Trigger:** `python tempo_automation.py --submit` or tray menu "Submit Timesheet"
- **What happens:**
  1. Checks if already submitted this month (skips if yes)
  2. Checks if in submission window (last 7 days of month)
  3. Runs per-day gap detection: fetches all worklogs for the month from Tempo
  4. Compares each working day against expected daily hours
  5. If shortfalls found: saves gap data, shows shortfall report, blocks submission
  6. If no shortfalls and last day of month: submits to Tempo API for approval
  7. If no shortfalls but not last day: reports clean status
- **Result:** Timesheet submitted for manager approval, or shortfall report generated

### 4.2 Early Timesheet Submission
- **Who:** All roles
- **Trigger:** Same as above, but triggered before last day of month
- **What happens:** If all remaining days in the month are non-working (PTO, holidays, weekends), submission proceeds early without waiting for the last day
- **Use case:** Taking PTO for the last week of the month

### 4.3 View Monthly Hours Report
- **Who:** All roles
- **Trigger:** `python tempo_automation.py --view-monthly` (current month) or `--view-monthly 2026-01` (specific month)
- **What happens:** Displays per-day table showing:
  - Date, day of week, hours logged, expected hours, gap (if any)
  - Summary: total working days, expected hours, actual hours
  - Saves shortfall file if gaps exist (enables tray "Fix" option)

### 4.4 Fix Monthly Shortfall (Interactive)
- **Who:** All roles
- **Trigger:** `python tempo_automation.py --fix-shortfall` or tray menu "Log and Reports > Fix Monthly Shortfall"
- **What happens:**
  1. Re-detects gaps from Tempo API (never trusts stale data)
  2. Shows numbered list of gap days with date, hours logged, expected, gap
  3. User selects which days to fix: A = all, comma-separated numbers, Q = quit
  4. For each selected day: runs daily sync to fill the gap
  5. Re-checks and updates shortfall file
- **Result:** Selected gap days get worklogs created, shortfall resolved

---

## 5. Weekly Verification

### 5.1 Verify and Backfill Current Week
- **Who:** All roles
- **Trigger:** `python tempo_automation.py --verify-week` (also runs via Friday scheduled task)
- **What happens:**
  1. Iterates Monday through Friday of current week
  2. Skips future dates
  3. For PTO/holidays: checks if overhead hours are logged, fills if missing
  4. For working days: checks actual hours vs expected
  5. If gap found: queries historical tickets active on that date, distributes gap hours
  6. Falls back to overhead stories if no historical tickets found
- **Result:** Any missed days in the current week are automatically backfilled

---

## 6. System Tray Application

### 6.1 Tray Icon with Status
- **Who:** All roles (Windows + Mac)
- **Trigger:** Launches on login (auto-registered) or manually via `pythonw tray_app.py`
- **What it shows:**
  - Green icon: idle / last sync successful
  - Orange icon: shortfall detected / sync in progress
  - Red icon: error occurred
  - Animated icon: sync or submission running (alternating colors)

### 6.2 Sync Now
- **Who:** All roles
- **Trigger:** Tray menu "Sync Now" or double-click tray icon
- **What happens:** Runs daily sync in background thread, shows toast notification with result, updates icon color

### 6.3 Scheduled Sync Notification
- **Who:** All roles (automatic)
- **What happens:** At configured sync time (default 18:00), tray app fires a notification reminding user to sync. User clicks "Sync Now" to execute.

### 6.4 Add PTO from Tray
- **Who:** All roles
- **Trigger:** Tray menu "Configure > Add PTO"
- **What happens:** Input dialog prompts for comma-separated dates, validates and adds PTO, shows result toast

### 6.5 Change Sync Time
- **Who:** All roles
- **Trigger:** Tray menu "Configure > Change Sync Time"
- **What happens:** Input dialog shows current time, user enters new HH:MM, validates format, updates config, reschedules timer immediately

### 6.6 Select Overhead from Tray
- **Who:** Developers
- **Trigger:** Tray menu "Configure > Select Overhead"
- **What happens:** Opens terminal with interactive overhead story selection

### 6.7 View Daily Log
- **Who:** All roles
- **Trigger:** Tray menu "Log and Reports > Daily Log"
- **What happens:** Opens daily-timesheet.log in text editor (Notepad on Windows, default app on Mac)

### 6.8 View Schedule from Tray
- **Who:** All roles
- **Trigger:** Tray menu "Log and Reports > Schedule"
- **What happens:** Opens terminal showing current month calendar

### 6.9 View Monthly Hours from Tray
- **Who:** All roles
- **Trigger:** Tray menu "Log and Reports > View Monthly Hours"
- **What happens:** Opens terminal showing per-day hours report

### 6.10 Fix Shortfall from Tray
- **Who:** All roles
- **Trigger:** Tray menu "Log and Reports > Fix Monthly Shortfall"
- **Visibility:** Only appears when monthly_shortfall.json exists (gaps detected)
- **What happens:** Opens terminal with interactive shortfall fix

### 6.11 Submit Timesheet from Tray
- **Who:** All roles
- **Trigger:** Tray menu "Submit Timesheet"
- **Visibility:** Only appears in last 7 days of month (or when early-eligible), hidden if shortfall exists or already submitted
- **What happens:** Runs submission in background, shows toast with result (success, shortfall, or failure)

### 6.12 Open Settings
- **Who:** All roles
- **Trigger:** Tray menu "Settings"
- **What happens:** Opens config.json in default text editor

### 6.13 Smart Exit
- **Who:** All roles
- **Trigger:** Tray menu "Exit"
- **What happens:**
  1. Checks if today is a working day
  2. If working day: checks how many hours are logged today via Tempo API
  3. If hours < expected: shows confirmation dialog "You have X/8 hours logged. Exit anyway?"
  4. If user says no: returns to tray
  5. If user says yes or non-working day: schedules tray restart (via Task Scheduler), exits

### 6.14 Welcome Toast
- **Who:** All roles (automatic on launch)
- **What happens:**
  - Normal start: "Welcome, {name}!" with time-of-day greeting and sync time info
  - Restart after crash: "Welcome back, {name}!" with recovery message

### 6.15 Tray Auto-Recovery
- **Who:** All roles (automatic)
- **What happens:** The daily Task Scheduler task checks if the tray app is running. If not, relaunches it with a recovery toast.

### 6.16 Stop Tray App
- **Who:** All roles
- **Trigger:** `python tray_app.py --stop`
- **What happens:** Signals running instance to shut down via file-based IPC

### 6.17 Register/Unregister Auto-Start
- **Who:** All roles
- **Trigger:** `python tray_app.py --register` / `--unregister`
- **What happens:**
  - Windows: adds/removes HKCU registry key for login auto-start
  - Mac: creates/removes LaunchAgent plist in ~/Library/LaunchAgents/

---

## 7. Notifications

### 7.1 Desktop Toast Notifications
- **Who:** All roles (automatic)
- **When:** After daily sync, submission, shortfall detection, errors
- **How:**
  - Windows: winotify (Action Center with company favicon), fallback to MessageBox
  - Mac: osascript `display notification` (native, no dependencies)

### 7.2 Email Notifications
- **Who:** All roles (opt-in, disabled by default)
- **When:** Daily summary, submission confirmation, shortfall alerts
- **How:** SMTP via Office 365 (TLS), HTML formatted body
- **Setup:** Enable during setup wizard or edit config.json

### 7.3 Microsoft Teams Notifications
- **Who:** All roles (not yet active)
- **When:** Daily summary, shortfall alerts
- **How:** Adaptive Card format via incoming webhook
- **Status:** Code exists but webhook URL not configured, call commented out

---

## 8. Setup and Configuration

### 8.1 First-Time Setup Wizard
- **Who:** New users
- **Trigger:** `python tempo_automation.py --setup` or first run with no config.json
- **What happens:**
  1. Prompts for email address (validated)
  2. Select role: Developer, Product Owner, or Sales
  3. Enter Tempo API token (verified against Tempo API, 3 retries)
  4. For developers: enter Jira API token (verified, auto-fetches display name)
  5. Set daily hours (default 8)
  6. Select location: US, India (Pune/Hyderabad/Gandhinagar), or custom country
  7. Configure email notifications (default: disabled)
  8. For PO/Sales: configure manual activities (activity name + hours)
  9. Encrypts sensitive tokens with DPAPI (Windows)
  10. Saves config.json
- **Result:** User is fully configured and ready to sync

### 8.2 Credential Security
- **Who:** All roles (automatic)
- **What happens:** API tokens and SMTP passwords are encrypted at rest using Windows DPAPI. Encrypted values stored as `ENC:<base64>` in config.json. Mac stores tokens as plain text (no DPAPI equivalent without Keychain integration).

### 8.3 Edit Configuration
- **Who:** All roles
- **Trigger:** Edit config.json directly or tray menu "Settings"
- **What can be changed:** Daily hours, sync time, PTO days, holidays, working days, notification settings, overhead configuration, manual activities

---

## 9. Installation and Scheduling

### 9.1 Windows Installation
- **Who:** Windows users
- **Trigger:** Run `install.bat` as Administrator
- **What happens (7 steps):**
  1. Detects Python (embedded or system PATH)
  2. Installs pip dependencies
  3. Runs setup wizard
  4. Optional: configures overhead stories
  5. Creates 3 Windows Task Scheduler tasks:
     - Daily sync: Mon-Fri at 18:00 with OK/Cancel confirmation dialog
     - Weekly verify: Fridays at 16:00
     - Monthly submit: Last day of month at 23:00
  6. Sets up tray app: registers auto-start, launches detached
  7. Optional: runs test sync

### 9.2 Mac Installation
- **Who:** Mac users
- **Trigger:** Run `bash install.sh`
- **What happens (7 steps):**
  1. Checks Python 3
  2. Installs pip dependencies
  3. Runs setup wizard
  4. Optional: configures overhead stories
  5. Creates 3 cron jobs: daily, weekly verify, monthly submit
  6. Sets up tray app: registers LaunchAgent, launches with nohup
  7. Optional: runs test sync

### 9.3 Daily Confirmation Dialog
- **Who:** Windows users (automatic via Task Scheduler)
- **Trigger:** Task Scheduler fires at configured time (default 18:00)
- **What happens:**
  1. Checks if tray app is running, relaunches if not
  2. Shows OK/Cancel dialog: "It is time to log your daily hours"
  3. OK: runs daily sync
  4. Cancel: exits silently

---

## 10. Distribution and Deployment

### 10.1 Build Distribution Zips
- **Who:** Admin/deployer
- **Trigger:** Run `build_dist.bat`
- **3 distribution types:**
  - **Windows Full:** All files + embedded Python 3.12.8 + dependencies (~40-50MB). No Python installation required.
  - **Windows Lite:** All files, no Python (~200KB). Requires Python on system PATH.
  - **Mac/Linux:** Python files + install.sh (~200KB). Requires Python 3.

### 10.2 Embedded Python
- **Who:** Windows Full distribution users
- **What happens:** Python 3.12.8 is bundled in a `python/` subdirectory with all dependencies pre-installed in `lib/`. No system Python needed. The installer auto-detects and uses embedded Python.

---

## 11. Logging and Troubleshooting

### 11.1 Runtime Log
- **File:** `tempo_automation.log`
- **Contains:** Internal debug/info/error messages from all operations
- **Access:** Direct file access or check after errors

### 11.2 Execution Log
- **File:** `daily-timesheet.log`
- **Contains:** Output from each sync/submit run (timestamped sections)
- **Access:** Tray menu "Log and Reports > Daily Log" or direct file access

### 11.3 Common Issues
- **401 error:** Expired or invalid API token -- regenerate and update config
- **"No active tickets":** No tickets in "IN DEVELOPMENT" or "CODE REVIEW" status
- **Tray icon missing:** Old instance holding mutex -- run `python tray_app.py --stop` first
- **Hours mismatch:** Manual Tempo entries not visible in Jira -- system uses max(Jira, Tempo) to compensate

---

## Feature Matrix by Role

| Feature | Developer | Product Owner | Sales |
|---------|:---------:|:------------:|:-----:|
| Auto-distribute across Jira tickets | Y | - | - |
| Smart descriptions from ticket content | Y | - | - |
| Manual activity logging | - | Y | Y |
| Overhead story management | Y | - | - |
| PTO / Holiday management | Y | Y | Y |
| Schedule calendar view | Y | Y | Y |
| Monthly submission | Y | Y | Y |
| Weekly verify and backfill | Y | Y | Y |
| Shortfall detection and fix | Y | Y | Y |
| System tray app | Y | Y | Y |
| Desktop notifications | Y | Y | Y |
| Email notifications (opt-in) | Y | Y | Y |
| DPAPI credential encryption | Y (Win) | Y (Win) | Y (Win) |
| Worklogs written to | Jira | Tempo | Tempo |
