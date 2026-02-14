# Tempo Automation - Setup Guide

Quick setup guide to get the Tempo timesheet automation running on your machine.

---

## What This Script Does

Runs daily at a scheduled time and:
1. Finds all Jira tickets assigned to you in **IN DEVELOPMENT** or **CODE REVIEW** status
2. Splits your 8-hour workday equally across those tickets
3. Logs the time directly in Jira (with a meaningful description pulled from the ticket)
4. Tempo auto-syncs from Jira — no manual entry needed
5. At the end of the month, automatically submits your timesheet for approval

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
requirements.txt
run_daily.bat
run_monthly.bat
```

---

## Step 3: Install Dependencies

Open Command Prompt, navigate to your folder, and run:

```cmd
pip install requests
```

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
- **Email notifications:** `no` (unless you want email summaries)

This creates a `config.json` file in the same folder. **Do not share this file** — it contains your API tokens.

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
TEMPO DAILY SYNC - 2026-02-12
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

Verify in Jira that the worklogs appear on your tickets.

---

## Step 7: Set Up Automatic Scheduling (Optional)

This makes the script run every day automatically so you never have to think about it.

### Find Your Python Path

Open Command Prompt and run:

```cmd
where python
```

Pick the path that looks like `C:\Users\YourName\AppData\Local\Programs\Python\...\python.exe` (NOT the WindowsApps one).

### Update the Batch Files

Open `run_daily.bat` in a text editor (Notepad) and replace the Python path on line 5 with YOUR path. Do the same for `run_monthly.bat`.

### Create Scheduled Tasks

Open **Command Prompt as Administrator** and run:

```cmd
schtasks /Create /TN "TempoAutomation-DailySync" /SC DAILY /ST 18:00 /TR "C:\tempo-automation\run_daily.bat" /F
```

```cmd
schtasks /Create /TN "TempoAutomation-MonthlySubmit" /SC MONTHLY /D 28,29,30,31 /ST 23:00 /TR "C:\tempo-automation\run_monthly.bat" /F
```

Replace `C:\tempo-automation\` with whatever folder you used.

### Verify

```cmd
schtasks /Query /TN "TempoAutomation-DailySync"
schtasks /Query /TN "TempoAutomation-MonthlySubmit"
```

That's it! The script will:
- Run daily at 6:00 PM and log your time
- Run on the last day of each month at 11:00 PM and submit your timesheet

---

## Useful Commands

```cmd
:: Run manually
python C:\tempo-automation\tempo_automation.py

:: Sync a specific date
python C:\tempo-automation\tempo_automation.py --date 2026-02-11

:: Check the execution log
type C:\tempo-automation\daily-timesheet.log

:: Re-run setup wizard
python C:\tempo-automation\tempo_automation.py --setup

:: Change daily run time (e.g., 5:30 PM)
schtasks /Change /TN "TempoAutomation-DailySync" /ST 17:30

:: Remove scheduled tasks
schtasks /Delete /TN "TempoAutomation-DailySync" /F
schtasks /Delete /TN "TempoAutomation-MonthlySubmit" /F
```

---

## Troubleshooting

### "No active tickets found"
You don't have any tickets assigned to you with status **IN DEVELOPMENT** or **CODE REVIEW**. Check your Jira board.

### Script runs but logs wrong hours
Re-run the script — it automatically deletes previous entries for that day and creates fresh ones.

### "401 Unauthorized" error
Your API token is invalid or expired. Generate a new one and update `config.json`.

### Scheduled task opens Python but does nothing
Make sure the Python path in `run_daily.bat` is correct. Run `where python` to check.

### Need help?
Contact your team lead or check the logs:
- `daily-timesheet.log` — execution output
- `tempo_automation.log` — detailed runtime logs

---

**IMPORTANT: Never share your `config.json` file — it contains your personal API tokens.**
