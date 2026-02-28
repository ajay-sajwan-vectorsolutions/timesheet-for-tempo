# Product Requirements Document: Tempo Timesheet Automation v4.0

**Status:** Draft | **Author:** Ajay Sajwan | **Date:** February 28, 2026
**Scope:** Full rewrite with modular architecture

---

## 1. Executive Summary

Tempo Timesheet Automation eliminates manual timesheet entry for a 200-person engineering
team at Vector Solutions. Developers get Jira worklogs auto-distributed across active
tickets; PO/Sales roles use manually configured activities. The system runs daily via
Task Scheduler (Windows) or cron (Mac), with a system tray app for interactive control.

**Why rewrite:** The current v3.9 is a 4,200-line single-file monolith built
incrementally over 4 weeks. It works, but it is fragile, hard to test without hacks,
and impossible for another developer to maintain. A clean modular rewrite will preserve
every feature while making the codebase sustainable.

---

## 2. Users and Roles

### 2.1 Developer (Primary)
- Has both Jira and Tempo API tokens
- Worklogs are created in Jira (Tempo auto-syncs via Jira-Tempo integration)
- Active tickets discovered automatically via JQL
- Hours distributed equally across active tickets with smart descriptions
- Overhead hours (daily standup, meetings) logged to dedicated OVERHEAD stories

### 2.2 Product Owner
- Has Tempo API token only (no Jira token)
- Uses manually configured activities (e.g., "Sprint Planning: 4h", "Backlog Grooming: 4h")
- Worklogs created directly in Tempo API

### 2.3 Sales
- Same as Product Owner but different activity set
- Worklogs created directly in Tempo API

### 2.4 Implicit Users
- **IT/Admin:** Deploys via installer, manages Task Scheduler
- **Team Leads:** May review submission status across team (future scope)

---

## 3. Functional Requirements

### 3.1 Daily Sync (Core Feature)

**FR-001: Schedule Guard**
- Before any API call, determine if today is a working day
- Priority chain (highest to lowest):
  1. Compensatory working days (override) -> WORK
  2. PTO days -> SKIP (but log overhead if configured)
  3. Weekends (Saturday/Sunday) -> SKIP (no overhead)
  4. Org holidays (from central JSON URL) -> SKIP (log overhead if configured)
  5. Country holidays (from `holidays` library) -> SKIP (log overhead if configured)
  6. Extra holidays (user-defined) -> SKIP (log overhead if configured)
  7. Default -> WORK
- Weekend skips silently; PTO/holiday skips log overhead hours to pto_story_key

**FR-002: Developer Auto-Distribution**
- Delete all existing non-overhead worklogs for target date (idempotent overwrite)
- Query active tickets: `assignee = currentUser() AND status IN ("IN DEVELOPMENT", "CODE REVIEW")`
- Calculate total seconds = daily_hours * 3600
- Subtract existing overhead hours (preserved, not deleted)
- If daily_overhead_hours configured (default 2h): log gap to overhead stories first
- Distribute remaining seconds equally across active tickets
- Integer division; remainder seconds assigned to last ticket (guarantees exact total)
- Generate smart description for each worklog (1-3 lines from ticket description + recent comments)

**FR-003: PO/Sales Manual Activities**
- Check existing Tempo entries for the date; skip if any exist
- Create Tempo worklogs directly for each configured manual_activity
- Use organization.default_issue_key as the issue key

**FR-004: Smart Descriptions**
- Line 1: First sentence of Jira ticket description (ADF -> plain text), max 120 chars
- Lines 2-3: First line of most recent comments (up to 2), max 120 chars each
- Fallback: ticket summary if no description available
- ADF (Atlassian Document Format) parser: recursive tree walk extracting text nodes

**FR-005: Date Override**
- `--date YYYY-MM-DD` allows syncing any past or future date
- Same schedule guard and distribution logic applies

---

### 3.2 Overhead Story Support

**FR-010: Five Overhead Cases**

| Case | Trigger | Behavior |
|------|---------|----------|
| 0 - Default Daily | Every working day | Log `daily_overhead_hours` (default 2h) to PI overhead stories before distributing remainder |
| 1 - No Active Tickets | No IN DEVELOPMENT/CODE REVIEW tickets found | Log all remaining hours to overhead stories |
| 2 - Manual Overhead | Pre-existing OVERHEAD-* worklogs detected | Preserve them, distribute remainder to active tickets |
| 3 - PTO/Holiday | Non-weekend off day | Log full daily_hours to `pto_story_key` |
| 4 - Planning Week | 5 working days after PI end date | Log hours to upcoming PI's overhead stories |

**FR-011: Overhead Story Selection (Interactive)**
- Fetch overhead stories from Jira: `project = OVERHEAD AND status = "In Progress"`
- Group by PI identifier (parsed from sprint name or summary via regex `PI\.(\d{2})\.(\d+)\.([A-Z]{3})\.(\d{1,2})`)
- User selects current PI and optionally planning PI
- Choose distribution mode: single story, equal split, or custom proportional
- Configure PTO story key and fallback issue key
- Set daily_overhead_hours (default 2h)
- All saved to config.json `overhead` section

**FR-012: PI Calendar Derivation**
- PI end date parsed from identifier: PI.26.1.JAN.30 = January 30, 2026
- Planning week = next 5 working days after PI end (skipping weekends/holidays, 14-day safety limit)
- Next PI starts first working day after planning week

**FR-013: PI Freshness Check**
- On daily sync, verify stored PI is still valid against Jira
- Check cached daily (don't hit Jira API on every run)
- Warn if stored PI doesn't match Jira data

---

### 3.3 Monthly Submission

**FR-020: Submission Window**
- Runs in last 7 days of month (day >= last_day - 6)
- OR earlier when all remaining days in month are non-working (early submission)
- Guard: skip if already submitted (check monthly_submitted.json marker)

**FR-021: Per-Day Gap Detection**
- Fetch all worklogs for the month from Tempo API (source of truth)
- Fallback to Jira API if Tempo unavailable
- Use `max(jira_seconds, tempo_seconds)` to catch manual entries
- Compare each working day against daily_hours
- Gap threshold: 0.5h (under this is ignored)
- Return: list of shortfall days with date, day name, logged hours, expected hours, gap

**FR-022: Submission Flow**
- If shortfalls found:
  - Save gap data to monthly_shortfall.json (for tray app)
  - Send desktop notification
  - DO NOT submit; print instructions for --fix-shortfall
- If no shortfalls and last day (or early eligible):
  - Submit via Tempo API: POST /timesheet-approvals/submit
  - Save monthly_submitted.json marker with period and timestamp
  - Send confirmation notification
  - Clean up stale shortfall file
- If no shortfalls but not last day:
  - Report clean status, note auto-submission date

**FR-023: Shortfall Fix (Interactive)**
- Re-detect gaps from Tempo API (never trust stale file)
- Display numbered list of gap days
- User selects: A=all, comma-separated numbers, Q=quit
- For each selected day: run sync_daily(date) to backfill
- Re-check and update/remove shortfall file

**FR-024: Monthly Hours Report**
- `--view-monthly [YYYY-MM]` displays per-day hours table
- Shows: date, day, hours logged, expected, gap (if any)
- Summary: total working days, expected hours, actual hours
- Saves shortfall file if gaps detected (enables tray Fix option)

---

### 3.4 Weekly Verification

**FR-030: Verify and Backfill Current Week**
- Iterate Monday through Friday of current week
- Skip future dates
- For PTO/holidays: check/log overhead hours if missing
- For working days: check actual hours vs expected
- If gap found: backfill using historical ticket queries
  - `assignee = currentUser() AND status WAS "IN DEVELOPMENT" ON "{date}"`
  - Distribute gap hours across found tickets (excluding already-logged ones)
  - Fallback to overhead stories if no unlogged historical tickets

---

### 3.5 Schedule Management

**FR-040: PTO Management**
- Add PTO dates: validate YYYY-MM-DD format, reject weekends
- Remove PTO dates
- Store as sorted list in config.json `schedule.pto_days`

**FR-041: Extra Holiday Management**
- Add/remove user-defined holidays (same validation as PTO)
- Store in config.json `schedule.extra_holidays`

**FR-042: Compensatory Working Days**
- Add/remove dates that override holidays/weekends as working days
- Store in config.json `schedule.working_days`
- Highest priority in is_working_day() chain

**FR-043: Calendar Display**
- Show month calendar with Mon-Sun layout
- Status labels: W=Working, H=Holiday, PTO, CW=CompWorkday, .=Weekend
- Summary: working days count, PTO count, holiday count

**FR-044: Interactive Schedule Menu**
- 10 options: add/remove PTO, add/remove holidays, add/remove working days,
  view calendar, list all dates, back
- Input validation on all date entries

**FR-045: Organization Holidays**
- Primary source: remote URL (fetched on every run, cached locally)
- Fallback: local org_holidays.json file
- Structure: country -> year -> common holidays + state-specific holidays
- Location picker in setup wizard: US, India (Pune/Hyderabad/Gandhinagar), custom

**FR-046: Country Holiday Library**
- Uses Python `holidays` library for 100+ countries
- Falls back gracefully if library not installed
- Supplements org holidays (org holidays take precedence by position in priority chain)

---

### 3.6 Configuration and Setup

**FR-050: First-Time Setup Wizard**
- Interactive prompts: email, role, tokens, daily hours, location, notifications
- Token verification with 3 retry attempts:
  - Tempo: GET /work-attributes
  - Jira: GET /myself (also fetches display name)
- Account ID fetched from Tempo: GET /user
- Hardcoded Jira URL: lmsportal.atlassian.net
- Email notifications default disabled
- Saves encrypted tokens (DPAPI on Windows, plain text on Mac)

**FR-051: Credential Encryption**
- Windows: DPAPI (CryptProtectData/CryptUnprotectData) via ctypes
- Encrypted format: `ENC:<base64>`
- Tied to Windows user account + machine
- Graceful fallback: plain text on non-Windows or DPAPI failure
- Applied to: Jira token, Tempo token, SMTP password

**FR-052: Configuration File**
- JSON format at `config.json` (gitignored)
- All access via .get() with defaults (never direct key access)
- Template provided: config_template.json
- Example configs in examples/ directory

---

### 3.7 System Tray Application

**FR-060: Tray Icon**
- 64x64 generated icon: rounded rectangle background + company favicon overlay
- Color states: green (idle/success), orange (shortfall/syncing), red (error)
- Sync animation: alternating orange/red every 700ms during operations
- Fallback: "T" letter if favicon.ico missing

**FR-061: Tray Menu Structure**
```
{Name} | Vector Solutions           [non-clickable header]
---
Sync Now (Auto Sync @HH:MM)        [default/double-click action]
---
Configure >
    Add PTO                         [input dialog]
    Select Overhead                 [opens terminal]
    Change Sync Time                [input dialog, HH:MM validation]
Log and Reports >
    Daily Log                       [opens in text editor]
    Schedule                        [opens terminal --show-schedule]
    View Monthly Hours              [opens terminal --view-monthly]
    Fix Monthly Shortfall           [DYNAMIC: visible when shortfall file exists]
---
Submit Timesheet                    [DYNAMIC: visible in submission window, hidden if shortfall/submitted]
Settings                            [opens config.json in default editor]
Exit                                [smart exit with hours check]
```

**FR-062: Daily Sync from Tray**
- Creates fresh TempoAutomation instance (picks up config changes)
- Redirects stdout to daily-timesheet.log
- Calls sync_daily()
- Updates icon color based on result
- Shows toast notification with summary
- Refreshes menu (dynamic items may change)

**FR-063: Scheduled Sync Notification**
- Timer fires at configured daily_sync_time (default 18:00)
- Sets pending_confirmation flag (icon may indicate pending)
- Does NOT auto-sync; user clicks "Sync Now" to execute

**FR-064: Smart Exit**
- Checks if today is a working day
- If working day: queries Tempo (primary) + Jira (fallback) for today's hours
- If hours < daily_hours: shows confirmation dialog with logged/expected hours
- If user declines: returns to tray
- If user confirms or non-working day: schedules tray restart task, stops icon

**FR-065: Dynamic Menu Items**
- "Fix Monthly Shortfall": visible only when monthly_shortfall.json exists
- "Submit Timesheet": visible when in submission window AND no shortfall AND not submitted
- Menu refreshed via update_menu() after every sync, submit, shortfall fix, and terminal close

**FR-066: Add PTO from Tray**
- Input dialog: VBScript InputBox (Windows) / AppleScript display dialog (Mac)
- Accepts comma-separated dates
- Validates and adds via ScheduleManager
- Shows result toast (added/skipped counts)

**FR-067: Change Sync Time**
- Input dialog with current time pre-filled
- Validates HH:MM format (0-23 hours, 0-59 minutes)
- Updates config.json
- Reschedules timer immediately

**FR-068: Welcome Toast**
- Normal start: "Welcome, {name}!" title, time-of-day greeting, sync time info
- Quiet restart (--quiet): "Welcome back, {name}!" with recovery message
- 2-second delay after icon appears (ensures icon is visible first)

**FR-069: Tray Recovery**
- confirm_and_run.py (Task Scheduler daily) checks if tray is running
- If not running: launches tray_app.py --quiet (detached, no console)
- Ensures tray survives crashes and logoffs

---

### 3.8 Notifications

**FR-070: Desktop Toast Notifications**
- Windows: winotify library (Action Center integration)
  - Custom app_id per toast type (e.g., "Welcome, {name}!" vs "Tempo Automation")
  - Company favicon as icon
  - Falls back to Win32 MessageBoxW if winotify unavailable
- Mac: osascript `display notification` (no external dependencies)

**FR-071: Email Notifications**
- SMTP via Office 365 (smtp.office365.com:587, STARTTLS)
- HTML formatted body
- Types: daily summary, submission confirmation, shortfall alert
- Default: disabled (opt-in during setup)
- SMTP password encrypted with DPAPI

**FR-072: Microsoft Teams Notifications**
- Adaptive Card format via incoming webhook
- Types: daily summary, shortfall alert
- Currently disabled (webhook URL not configured, code exists but commented out)
- Future: migrate to Graph API when webhook deprecation happens

---

### 3.9 Installation and Distribution

**FR-080: Windows Installer (install.bat)**
7-step flow (requires Administrator for schtasks):
1. Detect Python: embedded (python\python.exe) > system PATH > error
2. Install dependencies: skip if lib/ exists, otherwise pip install
3. Run setup wizard (--setup)
4. Optional overhead story configuration
5. Generate run_*.bat wrappers + create 3 Task Scheduler tasks:
   - Daily: Mon-Fri at configurable time, with OK/Cancel confirmation dialog
   - Weekly verify: Fridays at 16:00
   - Monthly submit: Last day of month at 23:00
6. Tray app: stop existing, register auto-start (registry), launch detached
7. Optional test sync

**FR-081: Mac Installer (install.sh)**
7-step flow:
1. Check Python 3
2. Install dependencies (pip, --user fallback)
3. Setup wizard
4. Optional overhead configuration
5. Cron jobs: daily (Mon-Fri 18:00), weekly verify (Fri 16:00), monthly submit (last day via BSD date)
6. Tray: stop existing, register LaunchAgent, launch with nohup
7. Optional test sync

**FR-082: Distribution Builds (build_dist.bat)**
3 zip types with YYYYMMDD-HHMM timestamp:
- **Windows Full:** All files + embedded Python 3.12.8 + pip-installed dependencies in lib/
- **Windows Lite:** All files, requires system Python
- **Mac/Linux:** Python files + install.sh only (no .bat, no confirm_and_run.py)

**FR-083: Confirmation Dialog (confirm_and_run.py)**
- Launched by Task Scheduler daily task
- Ensures tray app is running (restarts with --quiet if not)
- Shows OK/Cancel MessageBox: "It is time to log your daily hours"
- OK: imports and runs sync_daily()
- Cancel: exits silently

---

### 3.10 Logging and Debugging

**FR-090: Dual Logging**
- `tempo_automation.log`: internal runtime logs (logger.info/error/warning)
- `daily-timesheet.log`: execution output (stdout capture via DualWriter or tray redirect)

**FR-091: Log Hygiene**
- Never log API tokens, passwords, or full config
- Log: issue keys, time amounts, user emails, API status codes
- Log all API calls with URL and status code

**FR-092: Quiet Console Mode**
- User-facing commands (schedule, PTO, overhead, monthly view) suppress StreamHandler
- Prevents init messages (logger setup, config load) from cluttering interactive output
- File logging unaffected

---

## 4. Non-Functional Requirements

### 4.1 Performance
- **NFR-001:** Daily sync completes within 60 seconds for typical developer (5-10 active tickets)
- **NFR-002:** Tray icon appears within 2 seconds of launch (deferred automation import)
- **NFR-003:** Monthly gap detection (single Tempo API call for all worklogs, no per-day calls)

### 4.2 Reliability
- **NFR-010:** All API calls use 30-second timeout
- **NFR-011:** Idempotent overwrite: re-running sync for the same date produces identical results
- **NFR-012:** Graceful degradation: Tempo API failure falls back to Jira; missing holiday library still works
- **NFR-013:** No data loss on crash: worklogs are only deleted immediately before recreation

### 4.3 Security
- **NFR-020:** API tokens encrypted at rest (DPAPI on Windows)
- **NFR-021:** All API calls over HTTPS
- **NFR-022:** config.json gitignored (never committed)
- **NFR-023:** No credentials in log files

### 4.4 Compatibility
- **NFR-030:** Python 3.7+ (embedded distribution uses 3.12.8)
- **NFR-031:** Windows 10/11 (primary), macOS (secondary)
- **NFR-032:** ASCII-only stdout output (Windows cp1252 safe for file redirect)
- **NFR-033:** pythonw.exe compatibility (sys.stdout may be None)

### 4.5 Testability
- **NFR-040:** All business logic testable without live API calls
- **NFR-041:** No singleton patterns or module-level side effects that prevent test isolation
- **NFR-042:** Target 85%+ code coverage

### 4.6 Maintainability
- **NFR-050:** No file exceeds 500 lines
- **NFR-051:** Each class in its own module
- **NFR-052:** Clear dependency graph: no circular imports
- **NFR-053:** Type hints on all public methods
- **NFR-054:** Docstrings on all classes and public methods

---

## 5. API Integrations

### 5.1 Jira REST API v3
- **Base URL:** https://lmsportal.atlassian.net/rest/api/3/
- **Auth:** Basic auth (email + API token, base64)
- **Endpoints used:**
  - GET /myself -- account ID and display name
  - GET /search/jql -- issue search (active, historical, worklogs)
  - GET /issue/{key}/worklog -- fetch worklogs for issue
  - POST /issue/{key}/worklog -- create worklog (ADF comment format)
  - DELETE /issue/{key}/worklog/{id} -- delete worklog
  - GET /issue/{key}?fields=summary,description,comment -- ticket details
- **Jira Agile API:** GET /rest/agile/1.0/board/{boardId}/sprint -- PI sprint data

### 5.2 Tempo REST API v4
- **Base URL:** https://api.tempo.io/4/
- **Auth:** Bearer token in Authorization header
- **Endpoints used:**
  - GET /user -- current user (accountId, displayName)
  - GET /worklogs/user/{accountId}?from=&to= -- fetch worklogs
  - POST /worklogs -- create worklog (PO/Sales only)
  - GET /timesheet-approvals/periods -- list periods
  - POST /timesheet-approvals/submit -- submit timesheet
  - GET /work-attributes -- token verification

### 5.3 Key Integration Rules
- Developers write to Jira; Tempo auto-syncs (one-way: Jira -> Tempo)
- PO/Sales write directly to Tempo
- Manual Tempo entries are NOT visible via Jira API
- Use max(jira_seconds, tempo_seconds) pattern to catch manual entries
- Account ID format: `712020:uuid` (not email)

---

## 6. Configuration Schema

```
config.json
├── user
│   ├── email          (string, required)
│   ├── name           (string, required)
│   └── role           (enum: developer | product_owner | sales)
├── jira
│   ├── url            (string, default: "lmsportal.atlassian.net")
│   ├── email          (string, required)
│   └── api_token      (string, encrypted, developers only)
├── tempo
│   └── api_token      (string, encrypted, required)
├── organization
│   ├── default_issue_key   (string, default: "GENERAL-001")
│   └── holidays_url        (string, central org holidays URL)
├── schedule
│   ├── daily_hours         (float, default: 8)
│   ├── daily_sync_time     (string, default: "18:00")
│   ├── country_code        (string, default: "US")
│   ├── state               (string, optional)
│   ├── pto_days            (list[string], YYYY-MM-DD)
│   ├── extra_holidays      (list[string])
│   └── working_days        (list[string])
├── notifications
│   ├── email_enabled       (bool, default: false)
│   ├── smtp_server         (string, default: "smtp.office365.com")
│   ├── smtp_port           (int, default: 587)
│   ├── smtp_user           (string)
│   ├── smtp_password       (string, encrypted)
│   ├── notification_email  (string)
│   ├── teams_webhook_url   (string)
│   └── notify_on_shortfall (bool, default: true)
├── manual_activities       (list[{activity, hours}], PO/Sales only)
├── overhead
│   ├── current_pi
│   │   ├── pi_identifier   (string, e.g., "PI.26.1.JAN.30")
│   │   ├── pi_end_date     (string, YYYY-MM-DD)
│   │   ├── stories         (list[{issue_key, summary, hours?}])
│   │   └── distribution    (enum: single | equal | custom)
│   ├── planning_pi         (same structure as current_pi)
│   ├── pto_story_key       (string)
│   ├── daily_overhead_hours (float, default: 2)
│   ├── fallback_issue_key  (string)
│   ├── project_prefix      (string, default: "OVERHEAD-")
│   └── _last_pi_check      (string, internal)
└── options
    ├── auto_submit          (bool, default: true)
    ├── require_confirmation (bool, default: false)
    └── sync_on_startup      (bool, default: false)
```

---

## 7. State Files

| File | Format | Purpose | Lifecycle |
|------|--------|---------|-----------|
| config.json | JSON | User configuration | Persistent, gitignored |
| org_holidays.json | JSON | Organization holiday calendar | Auto-fetched, committed |
| tempo_automation.log | Text | Internal runtime logs | Persistent, gitignored |
| daily-timesheet.log | Text | Execution output | Persistent, gitignored |
| monthly_shortfall.json | JSON | Current month gap data | Created on gaps, deleted on submit |
| monthly_submitted.json | JSON | Submission marker (period + timestamp) | Created on submit, checked monthly |
| _tray_stop.signal | Empty | IPC signal to stop tray app | Transient |
| .tray_app.lock | Lock | Mac single-instance lock | Transient |

---

## 8. CLI Interface

```
tempo_automation.py [options]

Daily Sync:
  (no args)                    Sync today
  --date YYYY-MM-DD            Sync specific date

Schedule Management:
  --show-schedule [YYYY-MM]    Show month calendar
  --manage                     Interactive schedule menu
  --add-pto DATES              Add PTO (comma-separated)
  --remove-pto DATES           Remove PTO
  --add-holiday DATES          Add extra holidays
  --remove-holiday DATES       Remove extra holidays
  --add-workday DATES          Add compensatory working days
  --remove-workday DATES       Remove compensatory working days

Overhead:
  --select-overhead            Interactive overhead story selection
  --show-overhead              Display current overhead config

Monthly:
  --submit                     Submit monthly timesheet
  --view-monthly [YYYY-MM]     Show per-day hours report
  --fix-shortfall              Interactive gap fix

Setup:
  --setup                      Run setup wizard
  --logfile PATH               Also write output to file
```

---

## 9. End-to-End Test Requirements

The existing v3.9 "integration" tests mock all collaborators and only verify orchestration
call order. They are effectively unit tests with larger scope. True E2E tests must exercise
the full stack: config load -> object construction -> HTTP (mocked at network boundary via
`responses` library) -> business logic -> file I/O -> result verification.

### 9.1 E2E Test Scenarios (Required)

**E2E-001: Developer Daily Sync -- Full Stack**
- Load real config from file (developer role)
- Construct all real objects (ConfigManager, ScheduleManager, JiraClient, TempoClient, etc.)
- Mock HTTP at the network boundary (via `responses`): /myself, /search/jql, /issue/*/worklog, /worklogs/user/*
- Freeze date to a working day
- Run sync_daily()
- Verify: correct DELETE calls for old worklogs, correct POST calls for new worklogs (issue keys, seconds, ADF comments), correct Tempo reads for overhead detection
- Verify: daily-timesheet.log written (if --logfile used)
- Verify: notification methods called with correct summary

**E2E-002: Developer Daily Sync -- PTO Day with Overhead**
- Same full-stack setup as E2E-001
- Freeze date to a PTO day
- Verify: no active issue queries, overhead logged to pto_story_key via Jira, correct hours

**E2E-003: PO/Sales Daily Sync -- Full Stack**
- Load PO config, construct real objects (no JiraClient)
- Mock Tempo HTTP
- Verify: create_worklog called on TempoClient for each manual_activity, correct hours

**E2E-004: Monthly Submission -- Happy Path**
- Full stack with mocked HTTP
- Freeze to last day of month
- Mock Tempo worklogs returning full hours for every working day
- Run submit_timesheet()
- Verify: POST /timesheet-approvals/submit called with correct accountId and period
- Verify: monthly_submitted.json marker file created with correct period
- Verify: no monthly_shortfall.json exists

**E2E-005: Monthly Submission -- Gaps Block Submission**
- Full stack, freeze to last day
- Mock Tempo worklogs with 2 days showing < daily_hours
- Run submit_timesheet()
- Verify: POST /timesheet-approvals/submit NOT called
- Verify: monthly_shortfall.json created with correct gap data (dates, amounts)
- Verify: notification sent with shortfall details

**E2E-006: Monthly Submission -- Early Submission**
- Full stack, freeze to day 25 of month
- Configure PTO for days 26-31 (all remaining days are non-working)
- Mock full hours for all past working days
- Run submit_timesheet()
- Verify: submission proceeds despite not being last day
- Verify: marker file created

**E2E-007: Shortfall Fix Flow**
- Full stack, freeze to last day
- Create monthly_shortfall.json with 2 gap days
- Mock user input selecting "A" (all days)
- Mock HTTP for sync_daily on each gap day
- Run fix_shortfall()
- Verify: sync_daily called for each gap day
- Verify: shortfall file removed after all gaps filled

**E2E-008: Weekly Verify and Backfill**
- Full stack, freeze to Friday
- Mock Mon-Thu as having full hours, Wed as having 6h (2h gap)
- Run verify_week()
- Verify: _backfill_day called for Wednesday with 2h gap
- Verify: historical JQL query for Wednesday's date
- Verify: worklogs created for gap hours

**E2E-009: Marker File Lifecycle**
- Run submit with gaps -> verify shortfall file created
- Run fix_shortfall for all gaps -> verify shortfall file deleted
- Run submit again -> verify submission proceeds, submitted marker created
- Run submit third time -> verify "already submitted" early return

**E2E-010: Config Change Propagation**
- Full stack setup
- Run sync_daily (success)
- Modify config: add PTO for today
- Construct fresh TempoAutomation (simulates tray app re-create)
- Run sync_daily again
- Verify: second run skips (PTO) instead of syncing

**E2E-011: View Monthly Hours Report**
- Full stack, freeze to mid-month
- Mock Tempo worklogs with some gaps
- Run view_monthly_hours()
- Verify: printed output contains correct per-day table
- Verify: shortfall file created when gaps exist
- Verify: shortfall file NOT created when no gaps

**E2E-012: Overhead Story Selection**
- Full stack with mocked Jira (overhead stories endpoint)
- Mock user input for PI selection, distribution mode, PTO story
- Run select_overhead_stories()
- Verify: config.json updated with correct overhead section
- Verify: subsequent sync_daily uses the configured overhead

**E2E-013: max(jira, tempo) Pattern**
- Full stack, freeze to a working day
- Mock Jira returning 4h of worklogs, Tempo returning 6h (manual entry)
- Run _check_day_hours() or _detect_monthly_gaps()
- Verify: detected hours = 6h (max), not 4h

**E2E-014: Gap Threshold (0.5h)**
- Full stack
- Mock a working day with 7.6h logged (gap = 0.4h, under threshold)
- Run _detect_monthly_gaps()
- Verify: day is NOT listed as a gap

**E2E-015: Partial API Failure**
- Full stack with 3 active tickets
- Mock first 2 create_worklog calls as success, third as 500 error
- Run sync_daily()
- Verify: error logged, appropriate notification sent
- Verify: first 2 worklogs were created (not rolled back)

### 9.2 Tray App E2E Scenarios

**E2E-020: Tray Sync Now**
- Construct TrayApp with real TempoAutomation (mocked HTTP)
- Call _run_sync()
- Verify: daily-timesheet.log updated
- Verify: icon color set to green on success
- Verify: toast shown with summary
- Verify: update_menu() called

**E2E-021: Tray Submit -- Success**
- Construct TrayApp, freeze to last day, mock full hours
- Call _run_submit()
- Verify: monthly_submitted.json created
- Verify: icon green, toast "Submitted"
- Verify: update_menu() called (Submit item should hide)

**E2E-022: Tray Submit -- Failure Shows Error**
- Construct TrayApp, freeze to last day
- Mock Tempo submit API to return 403
- Call _run_submit()
- Verify: icon red, toast "Submission Failed"
- Verify: no marker file created

**E2E-023: Tray Smart Exit**
- Construct TrayApp, mock today as working day
- Mock Tempo worklogs showing 4h (< 8h daily_hours)
- Call _exit_flow() with confirmation dialog returning "stay"
- Verify: tray does NOT stop
- Call _exit_flow() with confirmation dialog returning "exit"
- Verify: tray stops, restart scheduled

**E2E-024: Tray Dynamic Menu Visibility**
- Create shortfall file -> verify _shortfall_visible returns True
- Delete shortfall file -> verify _shortfall_visible returns False
- Freeze to day 20 -> verify _submit_visible returns False
- Freeze to day 28 -> verify _submit_visible returns True
- Create submitted marker -> verify _submit_visible returns False

**E2E-025: confirm_and_run.py**
- Mock mutex/fcntl to indicate tray NOT running
- Run confirm_and_run main flow
- Verify: tray_app.py launched with --quiet
- Mock confirmation dialog -> OK
- Verify: sync_daily() called

### 9.3 Cross-Platform E2E Scenarios

**E2E-030: Windows Platform Adapter**
- Test registry autostart registration/unregistration
- Test VBScript input dialog creation and result parsing
- Test Win32 confirm dialog
- Test cmd /k terminal launch with paths containing spaces

**E2E-031: Mac Platform Adapter**
- Test LaunchAgent plist creation/removal
- Test AppleScript dialog creation
- Test Terminal.app launch via osascript
- Test fcntl file lock acquisition/release

---

## 10. Success Criteria

| Metric | Target |
|--------|--------|
| Feature parity with v3.9 | 100% -- every feature listed above works identically |
| All 385 existing tests pass | Green on full ported test suite |
| New E2E tests | ~25-30 full-stack scenarios all pass (Section 9) |
| Test coverage | 85%+ (unit + integration + E2E combined) |
| E2E coverage gaps closed | All 15 core E2E + 6 tray E2E + 2 platform E2E scenarios green |
| Max file size | 500 lines per module |
| No circular imports | Verified by import graph |
| Setup wizard | Works identically to v3.9 |
| Backward-compatible config | v3.9 config.json loads without migration |
| Cross-platform | Windows + Mac parity maintained |
| No regressions | Bug discovered in v3.9 (wrong accountId in submit) is fixed |

---

## 10. Out of Scope (v4.0)

These are documented future enhancements, NOT part of this rewrite:
- PyInstaller .exe packaging
- --dry-run mode
- Retry logic with exponential backoff
- Teams webhook via Graph API
- Multi-team dashboard
- Electron/web UI replacement for tray app
- Database backend (currently file-based state)
