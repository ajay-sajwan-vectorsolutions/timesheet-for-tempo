# Tempo Timesheet Automation - How It Works

A complete guide to the application logic, automated scheduling, and user workflows.

---

## Table of Contents

1. [Overview](#overview)
2. [Daily Sync - How Hours Get Logged](#daily-sync---how-hours-get-logged)
3. [Weekly Verification - Catching Missed Days](#weekly-verification---catching-missed-days)
4. [Monthly Submission - Timesheet Approval](#monthly-submission---timesheet-approval)
5. [Overhead Story Logic](#overhead-story-logic)
6. [Schedule Guard - Skipping Non-Working Days](#schedule-guard---skipping-non-working-days)
7. [Automated Scheduling](#automated-scheduling)
8. [System Tray App](#system-tray-app)
9. [User Workflows](#user-workflows)
10. [Notification System](#notification-system)
11. [API Integration](#api-integration)

---

## Overview

Tempo Timesheet Automation logs your daily work hours automatically by distributing them across your active Jira tickets. It runs on your local machine and syncs directly to Jira (Tempo auto-syncs from Jira). Three automated schedulers handle daily logging, weekly gap detection, and monthly timesheet submission.

**Key principle:** The system is **idempotent** -- re-running for the same date deletes previous entries and creates fresh ones, so it always reflects your current active tickets.

---

## Daily Sync - How Hours Get Logged

The daily sync is the core operation. Here's what happens step by step when it runs.

### For Developers

```
1. Check: Is today a working day?
   |
   +-- NO (weekend/holiday) --> Skip
   +-- NO (PTO) --> Log hours to PTO overhead story (if configured)
   +-- YES --> Continue
   |
2. Fetch existing worklogs for today from Jira
   |
3. Separate worklogs into:
   - Overhead worklogs (OVERHEAD-xxx tickets) --> KEEP these
   - Regular worklogs (all others) --> DELETE these
   |
4. Case 0: Default daily overhead
   - If daily_overhead_hours > 0 (default: 2h)
   - And current overhead < that amount
   - Log the gap to overhead stories
   |
5. Calculate remaining hours:
   remaining = daily_hours (8h) - overhead_hours
   |
   +-- remaining <= 0 --> Done (overhead covers full day)
   |
6. Check: Is this a PI planning week?
   +-- YES --> Log remaining hours to upcoming PI overhead stories --> Done
   |
7. Find active tickets (IN DEVELOPMENT / CODE REVIEW)
   |
   +-- No active tickets --> Log remaining to overhead stories --> Done
   |
8. Distribute remaining hours equally across active tickets:
   - seconds_per_ticket = remaining / num_tickets (integer division)
   - Last ticket gets any remainder (ensures exact total)
   |
9. For each ticket:
   - Generate smart description from ticket content
   - Create Jira worklog with allocated hours
   |
10. Print summary + send notification
```

### Hour Distribution Example

If you have 3 active tickets and 6 remaining hours (after 2h overhead):

| Ticket | Calculation | Hours |
|--------|-------------|-------|
| PROJ-101 | 21600s / 3 = 7200s | 2h 00m |
| PROJ-205 | 21600s / 3 = 7200s | 2h 00m |
| PROJ-318 | 7200s + 0s remainder | 2h 00m |
| **Total** | | **6h 00m** |

With overhead: 2h overhead + 6h tickets = **8h total** (exact, no rounding errors)

**Odd division example** (5 tickets, 6h remaining = 21600s):

| Ticket | Hours |
|--------|-------|
| Tickets 1-4 | 1h 12m each (4320s) |
| Ticket 5 | 1h 12m + 0s remainder (4320s) |
| **Total** | **6h 00m** |

The integer division + remainder approach ensures the total is always exact.

### Smart Worklog Descriptions

Each worklog gets a meaningful description built from the Jira ticket content:

```
Line 1: First sentence of ticket description (or summary if empty)
Line 2: First line of most recent comment
Line 3: First line of second most recent comment
```

**Example worklog description:**
```
Implement pagination for the search results API endpoint
Fixed offset calculation for edge case with empty results
Added unit tests for boundary conditions
```

If the ticket has no description or comments, fallback:
```
Worked on PROJ-101: Implement search pagination
```

### For Product Owners / Sales

POs and Sales don't have active Jira tickets. Instead, they use pre-configured activities:

```json
"manual_activities": [
  {"activity": "Stakeholder Meetings", "hours": 3},
  {"activity": "Planning & Documentation", "hours": 5}
]
```

The sync creates Tempo entries (not Jira worklogs) using the organization's default issue key. No ticket lookup or description generation is needed.

---

## Weekly Verification - Catching Missed Days

Runs every Friday at 4 PM to check the entire week for gaps.

### Algorithm

```
For each day Monday through Friday:
  |
  +-- Future date --> Skip
  |
  +-- Weekend --> Skip
  |
  +-- PTO/Holiday:
  |   - Check if overhead hours already logged
  |   - If gap > 0: Log PTO overhead hours
  |   - Status: [+] PTO (overhead logged)
  |
  +-- Working day:
      - Fetch existing worklogs
      - Calculate gap = daily_hours - existing_hours
      |
      +-- gap <= 0 --> [OK] Complete
      |
      +-- gap > 0 --> Backfill:
          1. Find tickets that WERE active on that date (historical JQL)
          2. Filter out tickets already logged
          3. If unlogged tickets found:
             - Distribute gap hours across them
             - Status: [+] Backfilled (X tickets)
          4. If NO unlogged tickets:
             - Log gap hours to overhead stories
             - Status: [+] Overhead fallback
```

### Historical JQL

The weekly verification uses Jira's `status WAS` syntax to find tickets that were active on past dates:

```
assignee = currentUser() AND (
  status WAS "IN DEVELOPMENT" ON "2026-02-18"
  OR status WAS "CODE REVIEW" ON "2026-02-18"
)
```

This means even tickets that have since been completed or moved to a different status will be found for backfilling.

### Weekly Summary Output

```
TEMPO WEEKLY VERIFICATION (started 2026-02-21 16:00:03)

Day        Date         Status                 Existing   Added
---------- ------------ ---------------------- ---------- ----------
Monday     2026-02-17   [OK] Complete           8.00h      0.00h
Tuesday    2026-02-18   [+] Backfilled          6.50h      1.50h
Wednesday  2026-02-19   [OK] Complete           8.00h      0.00h
Thursday   2026-02-20   [--] PTO                8.00h      0.00h
Friday     2026-02-21   [OK] Complete           8.00h      0.00h
---------- ------------ ---------------------- ---------- ----------
Working days: 4 (+ 1 PTO) | Expected: 40.00h | Actual: 40.00h
Status: [OK] ALL HOURS LOGGED
```

If there's a shortfall greater than 0.5h, a Windows toast notification is sent.

---

## Monthly Submission - Timesheet Approval

Runs on days 28-31 at 11 PM. The script checks if today is the actual last day of the month before submitting.

### Algorithm

```
1. Is today the last day of the month?
   +-- NO --> Skip (print message and exit)
   +-- YES --> Continue
   |
2. Count working days this month
   |
3. Calculate expected hours = working_days x daily_hours
   |
4. Fetch actual hours from Jira for entire month
   |
5. Compare:
   +-- Shortfall > 0.5h --> Send warning notification
   |
6. Submit timesheet to Tempo for approval
   |
7. Send confirmation notification
```

**Why days 28-31?** Windows Task Scheduler doesn't have a "last day of month" option. The task fires on all four days, but the script uses `calendar.monthrange()` to check if today is truly the last day. On months with 30 days, the script runs and skips on days 28-29, then submits on day 30.

### Monthly Hours Check Output

```
TEMPO MONTHLY TIMESHEET SUBMISSION (2026-02-28 23:00:01)

Monthly Hours Check:
  Working days: 20
  Expected: 160.0h (20 days x 8.0h)
  Actual:   158.5h
  [!] SHORTFALL: 1.5h missing

Submitting timesheet for period 2026-02...
[OK] Timesheet submitted for approval
```

---

## Overhead Story Logic

Overhead stories handle time that doesn't go to regular development tickets. There are 5 cases:

### Case 0: Default Daily Overhead (every working day)

- Config: `overhead.daily_overhead_hours` (default: 2h)
- Before distributing hours to active tickets, the system ensures a minimum number of hours are logged to overhead stories
- If you manually logged 1h to overhead but the default is 2h, the system logs 1h more
- If you manually logged 3h, no additional overhead is logged (3h > 2h)
- Set to 0 to disable this feature

### Case 1: No Active Tickets

- When no tickets are IN DEVELOPMENT or CODE REVIEW
- All remaining hours (after overhead) go to current PI overhead stories
- If overhead is not configured, hours are NOT logged (warning printed)

### Case 2: Manual Overhead Preserved

- The system only deletes non-overhead worklogs when re-running
- If you manually logged 2h to OVERHEAD-329, those hours are preserved
- Remaining hours are distributed across active tickets as usual
- Example: 2h manual overhead + 6h across 3 tickets = 8h total

### Case 3: PTO Day

- When `is_working_day()` returns PTO, the system logs full daily hours to the PTO overhead story
- Config: `overhead.pto_story_key`
- Idempotent: won't double-log if already logged for that PTO day

### Case 4: PI Planning Week

- The 5 working days after PI end date are "planning week"
- Hours go to the UPCOMING PI's overhead stories (not current PI)
- Config: `overhead.planning_pi.stories` and `overhead.planning_pi.distribution`
- If planning PI stories aren't configured, falls back to current PI with a warning

### Distribution Modes

| Mode | Behavior |
|------|----------|
| `single` | All hours on first story |
| `equal` | Hours split equally (remainder on last) |
| `custom` | Each story has user-assigned hours, proportionally scaled |

---

## Schedule Guard - Skipping Non-Working Days

Before any sync runs, the system checks if today is a working day. The checks are evaluated in priority order -- **first match wins**:

| Priority | Check | Result | Example |
|----------|-------|--------|---------|
| 1 (highest) | Is it in `working_days` list? | Working day | Compensatory Saturday |
| 2 | Is it in `pto_days` list? | Not working (PTO) | Vacation day |
| 3 | Is it a Saturday or Sunday? | Not working (Weekend) | Regular weekend |
| 4 | Is it in org holidays? | Not working (Holiday) | Company-wide holiday |
| 5 | Is it a country/state holiday? | Not working (Holiday) | National holiday |
| 6 | Is it in `extra_holidays` list? | Not working (Holiday) | Ad-hoc declared holiday |
| 7 (lowest) | Default | Working day | Normal weekday |

**Key point:** `working_days` has the highest priority. If the company declares a Saturday as a compensatory working day, it overrides the weekend check.

### Holiday Sources

1. **Org holidays:** Fetched from a central JSON file hosted on GitHub Pages. Contains company-wide holidays for all countries/offices.
2. **Country holidays:** Python `holidays` library. Supports US, India (with state-level: Maharashtra, Telangana, Gujarat).
3. **Extra holidays:** User-defined ad-hoc holidays via `--add-holiday` command.
4. **PTO days:** User-defined via `--add-pto` command or tray app "Add PTO" menu.

---

## Automated Scheduling

Three mechanisms keep the automation running without manual intervention.

### System Tray App (Recommended)

The tray app is a persistent background process that lives in your Windows system tray.

| Feature | Detail |
|---------|--------|
| Icon | Company favicon on colored background (green=idle, orange=pending, red=error) |
| Sync notification | Toast notification at configured time (default 6 PM) |
| Sync trigger | Click "Sync Now" or double-click tray icon |
| Auto-start | Registers itself to start on Windows login |
| Shutdown | Right-click > Exit (checks hours first) |

**How the timer works:**
1. On startup, calculates seconds until next `daily_sync_time`
2. Sets a `threading.Timer` for that delay
3. When timer fires: shows toast "Time to log hours!", sets icon to orange
4. User clicks "Sync Now" to trigger the sync
5. Timer re-arms for the next day

### Windows Task Scheduler

Three scheduled tasks run independently of the tray app:

| Task | Schedule | What It Does |
|------|----------|-------------|
| **TempoAutomation-DailySync** | Mon-Fri at 6:00 PM | Shows OK/Cancel dialog, then syncs today |
| **TempoAutomation-WeeklyVerify** | Fridays at 4:00 PM | Checks Mon-Fri for gaps, backfills |
| **TempoAutomation-MonthlySubmit** | Days 28-31 at 11:00 PM | Verifies hours + submits timesheet |

**Daily sync flow via Task Scheduler:**
```
Task Scheduler fires at 6 PM
  --> run_daily.bat
    --> confirm_and_run.py (shows OK/Cancel dialog)
      --> User clicks OK
        --> tempo_automation.py sync_daily()
```

**Both the tray app and Task Scheduler can coexist safely** because the sync is idempotent. If both fire for the same day, the second run simply overwrites what the first created.

### Manual Execution

You can always run commands directly:

```bash
python tempo_automation.py                       # Sync today
python tempo_automation.py --date 2026-02-20     # Sync specific date
python tempo_automation.py --verify-week          # Check this week
python tempo_automation.py --submit              # Submit monthly
```

---

## System Tray App

### Menu Options

| Menu Item | What It Does |
|-----------|-------------|
| **Sync Now** | Triggers daily sync immediately (background thread) |
| **Add PTO** | Opens dialog to enter PTO dates (validates format, rejects weekends) |
| **Select Overhead** | Opens console window for interactive overhead story selection |
| **View Log** | Opens daily-timesheet.log in Notepad |
| **View Schedule** | Opens console with current month calendar |
| **Settings** | Opens config.json in default editor |
| **Exit** | Checks if hours are logged, warns if not, offers to restart at sync time |

### Icon States

| Color | Meaning |
|-------|---------|
| Green | Idle / sync complete |
| Orange (blinking) | Sync in progress (alternates orange/red every 700ms) |
| Orange (solid) | Sync time reached, waiting for user action |
| Red | Error during last sync |

### Smart Exit

When you click Exit:
1. Checks if today is a working day
2. Fetches your logged hours from Jira
3. If hours < daily target (8h):
   - Shows warning: "You haven't logged 8h today (5.2h). Exit anyway?"
   - YES: Creates one-time scheduled task to restart tray at sync time
   - NO: Stays running
4. If hours are sufficient: exits cleanly

### Welcome Toast

On startup, shows a personalized greeting:
- Morning (before 12 PM): "Good Morning, Ajay!"
- Afternoon (12-5 PM): "Good Afternoon, Ajay!"
- Evening (after 5 PM): "Good Evening, Ajay!"

Body: "Tempo Automation is running. Your hours will be logged at 18:00 today."

### Single Instance

Uses a Windows mutex to prevent multiple instances. If you try to start a second instance, it exits silently.

### Remote Shutdown

The `--stop` flag creates a `_tray_stop.signal` file. A background thread in the running instance checks for this file every 1 second and shuts down when found. Used by the installer to stop the old instance before starting a fresh one.

---

## User Workflows

### First-Time Setup

```
1. Run install.bat (or installer .exe)
   |
2. Setup wizard asks for:
   - Your email
   - Jira API token
   - Tempo API token
   - Your role (developer / product owner / sales)
   - Your location (US / India cities)
   |
3. Configure overhead stories (interactive selection):
   - Fetches OVERHEAD stories from Jira
   - Groups by PI
   - You pick stories for current PI, PTO, and planning week
   - Set distribution mode and daily overhead hours
   |
4. Scheduled tasks created (3 tasks)
   |
5. Tray app starts (auto-starts on future logins)
   |
6. Optional test sync
```

### Typical Daily Workflow

```
Morning:
  - Windows starts --> tray app auto-starts
  - Welcome toast appears

During the day:
  - Work on your Jira tickets as normal
  - Move tickets to IN DEVELOPMENT or CODE REVIEW

At 6 PM:
  - Tray app shows toast: "Time to log hours!"
  - Task Scheduler shows OK/Cancel dialog
  - You click OK (or Sync Now from tray)
  - System logs 2h overhead + 6h across active tickets = 8h
  - Toast: "Sync complete - 8.0h logged"

End of day:
  - Close tray app if you want
  - Smart exit warns if hours aren't logged
```

### Managing PTO

**From tray app:**
1. Right-click tray icon > Add PTO
2. Enter dates: `2026-03-10,2026-03-11,2026-03-12`
3. Toast confirms: "Added 3 PTO day(s)"

**From command line:**
```bash
python tempo_automation.py --add-pto 2026-03-10,2026-03-11
python tempo_automation.py --remove-pto 2026-03-10
```

**What happens on PTO day:**
- Daily sync detects PTO via schedule guard
- Logs full 8h to PTO overhead story (if configured)
- Skips regular ticket distribution

### Viewing Your Schedule

**From tray app:** Right-click > View Schedule

**From command line:**
```bash
python tempo_automation.py --show-schedule         # Current month
python tempo_automation.py --show-schedule 2026-03  # Specific month
```

**Output:**
```
February 2026

  Mon   Tue   Wed   Thu   Fri   Sat   Sun
                                  1     2
    3     4     5     6     7     8     9
   10    11    12    13    14    15    16
   17   [18]  [19]   20    21    22    23
   24    25    26    27    28

Legend: [XX] = PTO, *XX* = Holiday, (XX) = Working Override
Working days: 20 | PTO: 2 | Holidays: 0
```

### Changing Overhead Stories (New PI)

When a new PI starts and new overhead stories are available:

```bash
python tempo_automation.py --select-overhead
```

Or from the tray app: Right-click > Select Overhead

The interactive wizard:
1. Fetches all OVERHEAD "In Progress" stories from Jira
2. Groups them by PI identifier
3. You select stories for current PI, PTO story, and planning PI
4. Choose distribution mode (single / equal / custom hours)
5. Set daily overhead hours (default: 2h)
6. Configuration saved to config.json

### Checking Configuration

```bash
python tempo_automation.py --show-overhead
```

Shows current overhead configuration, PI details, selected stories, and distribution mode.

---

## Notification System

### Windows Toast Notifications

Used throughout the application:
- Welcome toast on tray app startup
- "Time to log hours" at sync time
- "Sync complete" / "Sync failed" after sync
- "Overhead not configured" warning
- Weekly/monthly shortfall alerts

### Email Notifications

SMTP-based email with HTML formatting. Currently disabled -- Office 365 blocks Basic Auth (requires OAuth2 migration).

### Teams Notifications

Microsoft Teams Adaptive Card via incoming webhook. Code exists but the webhook call is commented out pending a Teams webhook URL from the admin.

---

## API Integration

### How Data Flows

```
Your active Jira tickets
    |
    v  (Jira REST API v3)
tempo_automation.py creates worklogs on Jira issues
    |
    v  (Automatic sync)
Tempo reads worklogs from Jira
    |
    v
Your Tempo timesheet is populated
    |
    v  (Monthly submission via Tempo API v4)
Timesheet submitted for manager approval
```

**Important:** For developers, the script writes to Jira only. Tempo automatically syncs from Jira. The script never writes directly to Tempo for developer worklogs.

### Jira API (REST v3)

| Operation | Endpoint | When Used |
|-----------|----------|-----------|
| Find worklogs | `GET /search/jql` + `GET /issue/{key}/worklog` | Before every sync (to delete old entries) |
| Delete worklog | `DELETE /issue/{key}/worklog/{id}` | Overwrite behavior (delete before create) |
| Find active tickets | `GET /search/jql` (status = IN DEV / CODE REVIEW) | Daily sync |
| Find historical tickets | `GET /search/jql` (status WAS on date) | Weekly backfill |
| Get ticket details | `GET /issue/{key}` (description + comments) | Smart description generation |
| Create worklog | `POST /issue/{key}/worklog` | Logging hours |

### Tempo API (v4)

| Operation | Endpoint | When Used |
|-----------|----------|-----------|
| Get account ID | `GET /user` | Setup wizard |
| Get worklogs | `GET /worklogs/user/{id}` | Cross-check with Jira (manual overhead detection) |
| Create worklog | `POST /worklogs` | PO/Sales manual activities only |
| Get period | `GET /timesheet-approvals/periods` | Monthly submission |
| Submit timesheet | `POST /timesheet-approvals/submit` | Monthly submission |

---

## Configuration Reference

### Key Config Fields

| Field | Default | Purpose |
|-------|---------|---------|
| `schedule.daily_hours` | 8 | Hours to log per working day |
| `schedule.daily_sync_time` | "18:00" | When tray app reminds to sync |
| `schedule.country_code` | "US" | Country for holiday detection |
| `schedule.pto_days` | [] | List of PTO dates |
| `overhead.daily_overhead_hours` | 2 | Hours logged to overhead every day |
| `overhead.current_pi.stories` | [] | Overhead stories for current PI |
| `overhead.pto_story_key` | "" | Story key for PTO day logging |

### CLI Commands

| Command | Purpose |
|---------|---------|
| `python tempo_automation.py` | Sync today's hours |
| `--date YYYY-MM-DD` | Sync a specific date |
| `--verify-week` | Check this week + backfill gaps |
| `--submit` | Submit monthly timesheet |
| `--setup` | Run setup wizard |
| `--select-overhead` | Choose overhead stories for current PI |
| `--show-overhead` | Display overhead configuration |
| `--show-schedule` | Show calendar for current month |
| `--show-schedule YYYY-MM` | Show calendar for specific month |
| `--manage` | Interactive schedule management menu |
| `--add-pto DATES` | Add PTO dates (comma-separated) |
| `--remove-pto DATE` | Remove a PTO date |
| `--add-holiday DATE` | Add an extra holiday |
| `--remove-holiday DATE` | Remove an extra holiday |
| `--add-workday DATE` | Add a compensatory working day |
| `--remove-workday DATE` | Remove a working day override |

---

*Last updated: February 22, 2026*
