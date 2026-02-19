---
name: debug-sync
description: Systematically troubleshoot Tempo daily sync failures. Checks logs, identifies error patterns, validates APIs, and verifies schedule configuration.
user-invocable: true
disable-model-invocation: false
---

# Debug Tempo Sync

Systematic troubleshooting for daily sync failures.

## Step 1: Check Logs
Read both log files to identify the error:
- `tempo_automation.log` -- internal runtime logs (via logging module)
- `daily-timesheet.log` -- execution output (appended by bat files + --logfile)
Search for ERROR, FAIL, or Traceback patterns in both files.

## Step 2: Identify Error Type

### "401 Unauthorized"
**Cause:** Invalid or expired API token
**Fix:** Regenerate token at Jira/Tempo token pages, re-run `--setup`

### "No active tickets"
**Cause:** No Jira tickets with status IN DEVELOPMENT or CODE REVIEW assigned to user
**Fix:** Check Jira board, verify tickets are assigned and in correct status

### "UnicodeEncodeError"
**Cause:** Unicode character in print() output (Windows cp1252)
**Fix:** Replace with ASCII: checkmark->[OK], cross->[FAIL], warning->[!]

### "is_working_day returned False"
**Cause:** Script correctly skipped a non-working day
**Check:** Run `python tempo_automation.py --show-schedule`

## Step 3: Test Manually
```bash
python tempo_automation.py --logfile test.log
python tempo_automation.py --date 2026-02-15 --logfile test.log
python tempo_automation.py --show-schedule
```

## Step 4: Verify Config
Read config.json and check: jira.url, jira.email, api tokens, schedule.daily_hours, user.role

## Step 5: Check Tray App
If using tray app: is icon visible? Right-click -> View Log. If frozen: `python tray_app.py --stop` then restart.
