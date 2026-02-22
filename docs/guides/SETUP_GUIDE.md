# Tempo Automation - Setup Guide

Quick setup guide to get the Tempo timesheet automation running on your machine.

---

## What This Script Does

Runs daily at a scheduled time and:
1. Finds all Jira tickets assigned to you in **IN DEVELOPMENT** or **CODE REVIEW** status
2. Splits your 8-hour workday equally across those tickets
3. Logs the time directly in Jira (with a meaningful description pulled from the ticket)
4. Tempo auto-syncs from Jira -- no manual entry needed
5. Skips weekends, holidays, and PTO days automatically
6. Runs a weekly verification every Friday to catch any missed days
7. At the end of the month, verifies total hours and submits your timesheet for approval

---

## Prerequisites

- Windows 10/11 (Mac/Linux: Python code is cross-platform, but tray app is Windows-only)
- Python 3.7 or higher
- A Jira API token (developers)
- A Tempo API token (all users)

---

## Step 1: Install Python

If you already have Python installed, skip to Step 2.

1. Download Python from https://www.python.org/downloads/
2. Run the installer
3. **IMPORTANT: Check the box "Add Python to PATH"** at the bottom of the installer
4. Click "Install Now"
5. Verify it works -- open Command Prompt and run:
   ```cmd
   python --version
   ```
   You should see something like `Python 3.12.x` or higher.

---

## Step 2: Download the Script

Get the following files from your team lead and place them all in one folder (e.g., `C:\tempo-automation\`):

```
tempo_automation.py          # Main automation script
tray_app.py                  # System tray app (Windows)
confirm_and_run.py           # OK/Cancel dialog for Task Scheduler
org_holidays.json            # Organization holiday definitions
requirements.txt             # Python dependencies
config_template.json         # Configuration template
run_daily.bat                # Batch wrapper for daily sync
run_weekly.bat               # Batch wrapper for weekly verification
run_monthly.bat              # Batch wrapper for monthly submission
install.bat                  # Automated installer (recommended)
assets/
  favicon.ico                # Company icon for tray app
```

**Quick install option:** Instead of Steps 3-7, you can right-click `install.bat` and select **"Run as Administrator"**. It handles everything automatically.

---

## Step 3: Install Dependencies

Open Command Prompt, navigate to your folder, and run:

```cmd
pip install -r requirements.txt
```

This installs:
- `requests` -- HTTP API calls
- `holidays` -- country/state holiday detection (US, India, 100+ countries)
- `pystray` -- system tray icon
- `Pillow` -- image processing for tray icon
- `winotify` -- Windows toast notifications

---

## Step 4: Generate API Tokens

You need two tokens. Keep them safe -- you'll enter them in the next step.

### Jira API Token (developers only)
1. Go to https://id.atlassian.com/manage-profile/security/api-tokens
2. Click **"Create API token"**
3. Give it a label like "Tempo Automation"
4. Copy the token and save it somewhere

### Tempo API Token (everyone)
1. Go to https://app.tempo.io/
2. Go to **Settings -> API Integration**
3. Click **"New Token"**
4. Give it a name like "Automation"
5. Copy the token and save it somewhere

---

## Step 5: Run Setup Wizard

Open Command Prompt and run:

```cmd
python C:\tempo-automation\tempo_automation.py --setup
```

The wizard will ask you:

| Prompt | What to enter |
|--------|--------------|
| **Email** | Your work email |
| **Full name** | Your name |
| **Role** | Choose `1` (Developer), `2` (Product Owner), or `3` (Sales) |
| **Jira URL** | Auto-configured: `lmsportal.atlassian.net` (not prompted) |
| **Tempo API token** | Paste the Tempo token from Step 4 |
| **Jira API token** | Paste the Jira token from Step 4 (developers only) |
| **Jira account email** | Your Jira login email (developers only) |
| **Daily hours** | `8` (or your standard work hours) |
| **Country/city** | Select your country and city for holiday detection |
| **Holidays URL** | Auto-configured (not prompted) |
| **Email notifications** | `yes` or `no` -- defaults to `no` (if yes, enter your email/app password) |

This creates a `config.json` file in the same folder. **Do not share this file** -- it contains your API tokens (encrypted with Windows DPAPI).

---

## Step 6: Test It

Make sure you have at least one Jira ticket assigned to you with status **IN DEVELOPMENT** or **CODE REVIEW**.

Run:

```cmd
python C:\tempo-automation\tempo_automation.py
```

You should see output like:

```
============================================================
TEMPO DAILY SYNC - 2026-02-19 (started 2026-02-19 18:00:05)
============================================================

Found 3 active ticket(s):
  - TS-101: Implement search feature
  - TS-102: Fix login validation
  - TS-103: Update API endpoint

8h / 3 tickets = 2.67h each

  [OK] Logged 2.67h on TS-101
    Description: Implement search feature for the dashboard...
  [OK] Logged 2.67h on TS-102
    Description: Fix login validation for edge cases...
  [OK] Logged 2.66h on TS-103
    Description: Update API endpoint response format...

============================================================
[OK] SYNC COMPLETE (18:00:12)
============================================================
Total entries: 3
Total hours: 8.00 / 8
Status: [OK] Complete
```

If you run on a weekend or holiday, you'll see:
```
[SKIP] 2026-02-14 is not a working day: Weekend (Saturday)
       Use --add-workday to override if this day should be worked.
```

Verify in Jira that the worklogs appear on your tickets.

---

## Post-Install: Select Overhead Stories

After initial setup, select your overhead stories for the current PI:

```cmd
python C:\tempo-automation\tempo_automation.py --select-overhead
```

This configures which overhead stories receive hours on PTO days, holidays,
and days with no active tickets. Re-run at the start of each new PI.

To view your current overhead configuration at any time:

```cmd
python C:\tempo-automation\tempo_automation.py --show-overhead
```

---

## Step 7: Set Up Automatic Scheduling

Choose **one** of these two options. The tray app is recommended for most users.

### Option A: System Tray App (Recommended)

The tray app sits in your system tray, shows a notification at your configured sync time, and lets you confirm before syncing. It auto-starts on Windows login.

**Start the tray app:**
```cmd
pythonw C:\tempo-automation\tray_app.py
```

That's it. The tray app will:
- Auto-register itself to start on every Windows login
- Show a toast notification at 6:00 PM (configurable in `config.json`)
- Let you sync, add PTO, view logs, and view your schedule from the tray menu
- Warn you if you try to exit without logging hours

**Tray icon colors:**
- Green = idle, ready
- Orange = time to log hours (notification pending)
- Orange/red animated = sync in progress
- Red = error (check logs)

**Tray menu options:**
- **Sync Now** -- run daily sync immediately (also activates on double-click)
- **Add PTO** -- enter PTO dates via a dialog
- **View Log** -- open the log file in Notepad
- **View Schedule** -- show the month calendar
- **Settings** -- open config.json for editing
- **Exit** -- checks hours before closing, warns if not logged

**Note:** The tray app auto-registers itself for auto-start on first run. You don't need to run `--register` manually.

**Manual tray app commands:**
```cmd
python tray_app.py --register      # Manually register auto-start
python tray_app.py --unregister    # Remove auto-start
python tray_app.py --stop          # Stop a running tray app instance
```

### Option B: Windows Task Scheduler

If you prefer a fully hands-off approach without a tray icon, use Task Scheduler.

**Find your Python path:**
```cmd
where python
```
Pick the path like `C:\Users\YourName\AppData\Local\Programs\Python\...\python.exe` (NOT the WindowsApps one).

**Update the batch files:**
Open `run_daily.bat`, `run_weekly.bat`, and `run_monthly.bat` in a text editor. Replace the Python path and script folder path with YOUR paths.

**Create scheduled tasks** (open Command Prompt as Administrator):

Daily sync (weekdays only at 6 PM):
```cmd
schtasks /Create /TN "TempoAutomation-DailySync" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 18:00 /TR "C:\tempo-automation\run_daily.bat" /F
```

Weekly verification (Friday at 4 PM):
```cmd
schtasks /Create /TN "TempoAutomation-WeeklyVerify" /SC WEEKLY /D FRI /ST 16:00 /TR "C:\tempo-automation\run_weekly.bat" /F
```

Monthly submission (last days of month at 11 PM):
```cmd
schtasks /Create /TN "TempoAutomation-MonthlySubmit" /SC MONTHLY /D 28,29,30,31 /ST 23:00 /TR "C:\tempo-automation\run_monthly.bat" /F
```

Replace `C:\tempo-automation\` with your actual folder.

**Verify:**
```cmd
schtasks /Query /TN "TempoAutomation-DailySync"
schtasks /Query /TN "TempoAutomation-WeeklyVerify"
schtasks /Query /TN "TempoAutomation-MonthlySubmit"
```

**Note:** Both options can coexist safely -- the sync is idempotent (re-running overwrites previous entries).

---

## Step 8: Managing Your Schedule

### Adding PTO (Paid Time Off)

Before going on leave, add your PTO dates:

```cmd
:: Add multiple PTO days at once
python C:\tempo-automation\tempo_automation.py --add-pto 2026-03-10,2026-03-11,2026-03-12

:: Remove a PTO day (plans changed)
python C:\tempo-automation\tempo_automation.py --remove-pto 2026-03-12
```

Or use the tray app: right-click the tray icon -> **Add PTO**.

### Adding Extra Holidays (Org-Declared)

When HR announces an extra holiday (e.g., election day):

```cmd
python C:\tempo-automation\tempo_automation.py --add-holiday 2026-04-14
```

### Adding Compensatory Working Days

When the org asks you to work on a weekend to compensate:

```cmd
python C:\tempo-automation\tempo_automation.py --add-workday 2026-11-08
```

### Interactive Menu

For a guided experience, use the interactive menu:

```cmd
python C:\tempo-automation\tempo_automation.py --manage
```

This opens a menu where you can add/remove PTO, holidays, and working days step by step.

### Viewing Your Schedule

See your working/non-working days for any month:

```cmd
:: Current month
python C:\tempo-automation\tempo_automation.py --show-schedule

:: Specific month
python C:\tempo-automation\tempo_automation.py --show-schedule 2026-03
```

Output:
```
March 2026
=========================================
Mon  Tue  Wed  Thu  Fri  | Sat  Sun
 2    3    4    5    6   |  7    8
 W    W    W    W    W   |  .    .
 9   10   11   12   13  | 14   15
 W   PTO  PTO  PTO  W   |  .    .
16   17   18   19   20  | 21   22
 W    W    W    W    W   |  .    .
23   24   25   26   27  | 28   29
 W    W    W    H    W   |  .    .
30   31
 W    W

Legend: W=Working  H=Holiday  PTO=PTO  CW=Comp. Working  .=Weekend

Summary:
  Working days: 19  |  Expected hours: 152.0h
  Holidays: 1 (Holi - Mar 26)
  PTO days: 3 (Mar 10-12)
```

---

## All Commands Reference

```cmd
:: --- Core Operations ---
python tempo_automation.py                        :: Daily sync (today)
python tempo_automation.py --date 2026-02-15      :: Sync specific date
python tempo_automation.py --verify-week           :: Verify & backfill this week
python tempo_automation.py --submit               :: Monthly submit
python tempo_automation.py --setup                :: Re-run setup wizard

:: --- Overhead Stories ---
python tempo_automation.py --select-overhead              :: Select overhead stories for current PI
python tempo_automation.py --show-overhead                :: View current overhead configuration

:: --- Monthly Hours & Shortfall ---
python tempo_automation.py --view-monthly                 :: View current month hours per day
python tempo_automation.py --view-monthly 2026-01         :: View specific month hours
python tempo_automation.py --fix-shortfall                :: Interactive fix for monthly gaps

:: --- Schedule Management ---
python tempo_automation.py --add-pto 2026-03-10,2026-03-11
python tempo_automation.py --remove-pto 2026-03-10
python tempo_automation.py --add-holiday 2026-04-14
python tempo_automation.py --remove-holiday 2026-04-14
python tempo_automation.py --add-workday 2026-11-08
python tempo_automation.py --remove-workday 2026-11-08
python tempo_automation.py --show-schedule 2026-03
python tempo_automation.py --manage

:: --- Logging ---
python tempo_automation.py --logfile daily-timesheet.log   :: Dual output

:: --- Maintenance ---
type daily-timesheet.log                         :: View execution log
type tempo_automation.log                        :: View runtime log

:: --- Task Scheduler Management ---
schtasks /Change /TN "TempoAutomation-DailySync" /ST 17:30  :: Change time
schtasks /Run /TN "TempoAutomation-DailySync"               :: Run now
schtasks /Delete /TN "TempoAutomation-DailySync" /F         :: Remove task

:: --- Tray App ---
pythonw tray_app.py                :: Run tray app (no console)
python tray_app.py --register      :: Register auto-start (auto on first run)
python tray_app.py --unregister    :: Remove auto-start
python tray_app.py --stop          :: Stop running tray app
```

---

## How Holidays Work

### Automatic (No Setup Needed)
- **Org holidays** are loaded from `org_holidays.json` (ships with the script, auto-fetched from central URL)
- **National/state holidays** are detected automatically based on your `country_code` and `state` settings
- The script auto-fetches the latest org holiday list on every run (if a central URL is configured)

### What You Manage
- **PTO days** -- add before going on leave (`--add-pto`)
- **Extra holidays** -- when HR declares an ad-hoc holiday (`--add-holiday`)
- **Compensatory working days** -- when asked to work a weekend (`--add-workday`)

### Priority Rules
When dates conflict, this priority order applies:
1. **Compensatory working day** -- always wins (you work even if it's a weekend/holiday)
2. **PTO** -- you're on leave
3. **Weekend** -- Saturday/Sunday
4. **Org holiday** -- from org_holidays.json
5. **National holiday** -- from holidays library
6. **Extra holiday** -- your personal additions
7. **Default** -- it's a working day

### Annual Holiday Refresh
- Your admin updates the central `org_holidays.json` file in December each year
- The script auto-fetches the new version on your next run
- If you're on long PTO at year start, it updates automatically when you return
- In December, the script will warn you if next year's holidays aren't loaded yet

---

## Troubleshooting

### "No active tickets found"
You don't have any tickets assigned to you with status **IN DEVELOPMENT** or **CODE REVIEW**. Check your Jira board.

### Script runs but logs wrong hours
Re-run the script -- it automatically deletes previous entries for that day and creates fresh ones. Safe to re-run anytime.

### "401 Unauthorized" error
Your API token is invalid or expired. Generate a new one and update `config.json`, or re-run `--setup`.

### Tray app icon not appearing after reboot
Start it manually with `pythonw tray_app.py` -- it auto-registers for auto-start on first run. Or run `python tray_app.py --register` explicitly.

### Scheduled task opens but does nothing
Make sure the Python path in the batch files is correct. Run `where python` to check.

### Need help?
Contact your team lead or check the logs:
- `daily-timesheet.log` -- execution output (what the script did)
- `tempo_automation.log` -- detailed runtime logs (API calls, errors)

---

## Uninstall

To remove the automation completely:

```cmd
:: Remove tray app auto-start
python tray_app.py --unregister

:: Remove scheduled tasks (if using Task Scheduler)
schtasks /Delete /TN "TempoAutomation-DailySync" /F
schtasks /Delete /TN "TempoAutomation-WeeklyVerify" /F
schtasks /Delete /TN "TempoAutomation-MonthlySubmit" /F

:: Then delete the installation folder
```

---

**IMPORTANT: Never share your `config.json` file -- it contains your personal API tokens.**
