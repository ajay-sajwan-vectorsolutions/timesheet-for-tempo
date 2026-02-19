# Tempo Automation - Setup Guide

Quick setup guide to get the Tempo timesheet automation running on your machine.

---

## What This Script Does

Runs daily at a scheduled time and:
1. Finds all Jira tickets assigned to you in **IN DEVELOPMENT** or **CODE REVIEW** status
2. Splits your 8-hour workday equally across those tickets
3. Logs the time directly in Jira (with a meaningful description pulled from the ticket)
4. Tempo auto-syncs from Jira — no manual entry needed
5. Skips weekends, holidays, and PTO days automatically
6. Runs a weekly verification every Friday to catch any missed days
7. At the end of the month, verifies total hours and submits your timesheet for approval
8. Sends a Teams/email notification if hours are short

---

## Prerequisites

- Windows 10/11 or Mac
- Python 3.7 or higher
- A Jira API token
- A Tempo API token

---

## Step 1: Install Python

If you already have Python installed, skip to Step 2.

1. Download Python from https://www.python.org/downloads/
2. Run the installer
3. **IMPORTANT: Check the box "Add Python to PATH"** at the bottom of the installer
4. Click "Install Now"
5. Verify it works — open Command Prompt and run:
   ```cmd
   python --version
   ```
   You should see something like `Python 3.14.x`

---

## Step 2: Download the Script

Get the following files from your team lead and place them all in one folder (e.g., `C:\tempo-automation\`):

```
tempo_automation.py
org_holidays.json
requirements.txt
run_daily.bat
run_monthly.bat
run_weekly.bat
```

---

## Step 3: Install Dependencies

Open Command Prompt, navigate to your folder, and run:

```cmd
pip install -r requirements.txt
```

This installs `requests` (for API calls) and `holidays` (for country-specific holiday detection).

---

## Step 4: Generate API Tokens

You need two tokens. Keep them safe — you'll enter them in the next step.

### Jira API Token
1. Go to https://id.atlassian.com/manage-profile/security/api-tokens
2. Click **"Create API token"**
3. Give it a label like "Tempo Automation"
4. Copy the token and save it somewhere

### Tempo API Token
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
- **Email:** Your work email
- **Full name:** Your name
- **Role:** Choose `1` (Developer)
- **Jira URL:** `lmsportal.atlassian.net`
- **Tempo API token:** Paste the Tempo token from Step 4
- **Jira API token:** Paste the Jira token from Step 4
- **Jira account email:** Your Jira login email
- **Daily hours:** `8` (or your standard work hours)
- **Country code:** `US` or `IN` (for holiday detection)
- **State/province:** Optional — for regional holidays (e.g., `KA` for Karnataka, `TX` for Texas)
- **MS Teams webhook URL:** Optional — for shortfall notifications (see Step 5b)
- **Email notifications:** `no` (unless you want email summaries)

This creates a `config.json` file in the same folder. **Do not share this file** — it contains your API tokens.

### Step 5b: Set Up MS Teams Webhook (Optional)

If you want to receive notifications in Teams when hours are short:

1. Open Microsoft Teams
2. Go to the channel where you want notifications (or create a personal channel)
3. Click the **...** menu on the channel -> **Connectors** (or **Workflows**)
4. Search for **"Incoming Webhook"**
5. Click **Configure**, give it a name like "Tempo Automation"
6. Copy the webhook URL
7. Paste it when the setup wizard asks, or add it later to `config.json`:
   ```json
   "notifications": {
       "teams_webhook_url": "https://outlook.office.com/webhook/..."
   }
   ```

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
TEMPO DAILY SYNC - 2026-02-17
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
[OK] SYNC COMPLETE
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

## Step 7: Set Up Automatic Scheduling

This makes the script run automatically so you never have to think about it.

### Find Your Python Path

Open Command Prompt and run:

```cmd
where python
```

Pick the path that looks like `C:\Users\YourName\AppData\Local\Programs\Python\...\python.exe` (NOT the WindowsApps one).

### Update the Batch Files

Open `run_daily.bat`, `run_monthly.bat`, and `run_weekly.bat` in a text editor (Notepad). Replace the Python path with YOUR path. Replace the script folder path with YOUR folder path.

### Create Scheduled Tasks

Open **Command Prompt as Administrator** and run:

**Daily sync (weekdays only at 6 PM):**
```cmd
schtasks /Create /TN "TempoAutomation-DailySync" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 18:00 /TR "C:\tempo-automation\run_daily.bat" /F
```

**Weekly verification (Friday at 4 PM):**
```cmd
schtasks /Create /TN "TempoAutomation-WeeklyVerify" /SC WEEKLY /D FRI /ST 16:00 /TR "C:\tempo-automation\run_weekly.bat" /F
```

**Monthly submission (last days of month at 11 PM):**
```cmd
schtasks /Create /TN "TempoAutomation-MonthlySubmit" /SC MONTHLY /D 28,29,30,31 /ST 23:00 /TR "C:\tempo-automation\run_monthly.bat" /F
```

Replace `C:\tempo-automation\` with whatever folder you used.

### Verify

```cmd
schtasks /Query /TN "TempoAutomation-DailySync"
schtasks /Query /TN "TempoAutomation-WeeklyVerify"
schtasks /Query /TN "TempoAutomation-MonthlySubmit"
```

That's it! The script will:
- Run Mon-Fri at 6:00 PM and log your time (skips holidays and PTO)
- Run every Friday at 4:00 PM to verify the week's hours and backfill gaps
- Run on the last day of each month at 11:00 PM to verify monthly hours and submit

---

## Step 8: Managing Your Schedule

### Adding PTO (Paid Time Off)

Before going on leave, add your PTO dates:

```cmd
:: Add multiple PTO days at once
python C:\tempo-automation\tempo_automation.py --add-pto 2026-03-10 2026-03-11 2026-03-12

:: Remove a PTO day (plans changed)
python C:\tempo-automation\tempo_automation.py --remove-pto 2026-03-12
```

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

## Useful Commands

```cmd
:: --- Core Operations ---
python C:\tempo-automation\tempo_automation.py                        :: Daily sync (today)
python C:\tempo-automation\tempo_automation.py --date 2026-02-15      :: Sync specific date
python C:\tempo-automation\tempo_automation.py --verify-week           :: Weekly verification
python C:\tempo-automation\tempo_automation.py --submit               :: Monthly submit

:: --- Schedule Management ---
python C:\tempo-automation\tempo_automation.py --add-pto 2026-03-10 2026-03-11
python C:\tempo-automation\tempo_automation.py --remove-pto 2026-03-10
python C:\tempo-automation\tempo_automation.py --add-holiday 2026-04-14
python C:\tempo-automation\tempo_automation.py --remove-holiday 2026-04-14
python C:\tempo-automation\tempo_automation.py --add-workday 2026-11-08
python C:\tempo-automation\tempo_automation.py --remove-workday 2026-11-08
python C:\tempo-automation\tempo_automation.py --show-schedule 2026-03
python C:\tempo-automation\tempo_automation.py --manage

:: --- Maintenance ---
python C:\tempo-automation\tempo_automation.py --setup                :: Re-run setup
type C:\tempo-automation\daily-timesheet.log                         :: View logs

:: --- Scheduler Management ---
schtasks /Change /TN "TempoAutomation-DailySync" /ST 17:30           :: Change time
schtasks /Run /TN "TempoAutomation-DailySync"                        :: Run now
schtasks /Delete /TN "TempoAutomation-DailySync" /F                  :: Remove task
schtasks /Delete /TN "TempoAutomation-WeeklyVerify" /F
schtasks /Delete /TN "TempoAutomation-MonthlySubmit" /F
```

---

## How Holidays Work

### Automatic (No Setup Needed)
- **Org holidays** are loaded from `org_holidays.json` (ships with the script, updated centrally)
- **National/state holidays** are detected automatically based on your `country_code` setting
- The script auto-fetches the latest org holiday list if a central URL is configured

### What You Manage
- **PTO days** — add before going on leave (`--add-pto`)
- **Extra holidays** — when HR declares an ad-hoc holiday (`--add-holiday`)
- **Compensatory working days** — when asked to work a weekend (`--add-workday`)

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
Re-run the script — it automatically deletes previous entries for that day and creates fresh ones.

### "401 Unauthorized" error
Your API token is invalid or expired. Generate a new one and update `config.json`.

### Script logged time on a weekend or holiday
Run the script again for that date after the fix. The overwrite behavior will handle cleanup. Or delete the worklogs manually in Jira/Tempo.

### Scheduled task opens Python but does nothing
Make sure the Python path in the batch files is correct. Run `where python` to check.

### Teams notification not working
- Verify the webhook URL is correct in `config.json`
- Test by visiting the URL in a browser (should return an error page, not 404)
- Check if the webhook connector is still active in Teams

### Need help?
Contact your team lead or check the logs:
- `daily-timesheet.log` — execution output
- `tempo_automation.log` — detailed runtime logs

---

**IMPORTANT: Never share your `config.json` file — it contains your personal API tokens.**
