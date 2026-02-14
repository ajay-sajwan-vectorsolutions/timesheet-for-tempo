# Plan: Weekly Verification/Backfill + Calendar Fallback

**Status:** Approved — ready for implementation
**Created:** February 13, 2026
**Scheduled for:** Next coding session

---

## Context

The daily auto-sync at 6 PM works well, but if the script fails or the PC is off, days get missed. We need a **weekly safety net** that runs every Friday at 4 PM, checks Mon-Fri for gaps, and backfills them. Additionally, when a developer has no Jira stories on a given day, we fall back to their Outlook calendar to log meeting time, with remainder on OVERHEAD-329.

**Key constraint:** Fill gaps only — never delete existing worklogs, never exceed 8h/day.

---

## Deduplication Algorithm (per day)

```
1. Fetch existing worklogs for the day
2. existing_seconds = sum of all existing worklog durations
3. IF existing_seconds >= 8h --> day is complete, skip
4. gap_seconds = (8h * 3600) - existing_seconds
5. already_logged_keys = set of issue_keys from existing worklogs
6. Find stories that were IN DEVELOPMENT/CODE REVIEW ON that specific date (historical JQL)
7. Filter out stories already in already_logged_keys
8. IF unlogged stories found --> distribute gap_seconds across them (same integer-division pattern as daily)
9. ELSE IF calendar configured --> read Outlook meetings, log each as worklog on OVERHEAD-329
   --> if meeting total < gap --> log remainder on OVERHEAD-329 as "General overhead"
10. ELSE --> log full gap on OVERHEAD-329
```

**Stories always take priority over calendar.** No partial story + calendar mix on the same day.

---

## Files to Modify

| File | Change |
|---|---|
| `tempo_automation.py` | Add JiraClient method, CalendarClient class, TempoAutomation weekly methods, CLI arg |
| `config_template.json` | Add `calendar` config section |
| `requirements.txt` | Add `msal>=1.24.0` (optional, for calendar) |
| `run_weekly.bat` | **New file** -- Task Scheduler wrapper |
| `examples/developer_config.json` | Add calendar config example |

---

## Implementation Steps

### Step 1: Add `JiraClient.get_issues_in_status_on_date()` (after line 412)

New method using historical JQL:

```python
def get_issues_in_status_on_date(self, target_date: str) -> List[Dict]:
    """
    Fetch issues assigned to current user that were IN DEVELOPMENT or CODE REVIEW
    on a specific past date using historical JQL (status WAS ... ON ...).
    """
```

JQL query:
```
assignee = currentUser() AND (
    status WAS "IN DEVELOPMENT" ON "2026-02-10"
    OR status WAS "CODE REVIEW" ON "2026-02-10"
)
```

Returns same format as `get_my_active_issues()`: list of `{issue_key, issue_summary}`.

### Step 2: Add `CalendarClient` class (insert between TempoClient and NotificationManager, ~line 690)

```python
class CalendarClient:
    __init__(config)              # reads calendar section, checks enabled
    is_configured() -> bool       # True if tenant_id/client_id/client_secret present
    _get_access_token() -> str    # MSAL client credentials flow (import msal inside method)
    get_events_for_day(date) -> List[Dict]  # Microsoft Graph /users/{email}/calendarView
```

Event filtering:
- Skip cancelled events, all-day events, events marked "free"
- Return: `{subject, duration_seconds, start, end}` per event

Auth: Client credentials flow (app-level, no browser needed for Task Scheduler). Requires Azure AD app registration with `Calendars.Read` application permission.

`msal` import is inside `_get_access_token()` -- script doesn't crash if msal isn't installed; it just skips calendar gracefully.

### Step 3: Add `TempoAutomation` weekly methods (after line 1074)

**`verify_week()`** -- main orchestration:
- Calculates Monday of the current week
- Loops Mon-Fri, skips future dates
- For each day: calls `_backfill_day()`
- Prints a weekly summary table with day-by-day status

**`_backfill_day(target_date)`** -- single-day logic:
- Implements the deduplication algorithm above
- Returns `(status_string, created_worklogs_count, hours_added)`

**`_distribute_and_create_worklogs(issues, gap_seconds, target_date)`** -- hour distribution:
- Same integer-division + remainder pattern as existing `_auto_log_jira_worklogs`
- Uses `_generate_work_summary()` for smart descriptions

**`_backfill_from_calendar(target_date, gap_seconds, already_logged_keys)`** -- calendar fallback:
- Reads meetings via `CalendarClient.get_events_for_day()`
- Logs each meeting on OVERHEAD-329 with "Meeting: {title}" as comment
- Caps total at `gap_seconds`
- Logs remainder on OVERHEAD-329 as "General overhead"

**`_log_on_overhead(target_date, gap_seconds)`** -- last resort:
- Logs full gap on OVERHEAD-329 when no stories and no calendar

### Step 4: Update `TempoAutomation.__init__()` (line ~809)

Add after `self.notifier = NotificationManager(self.config)`:
```python
self.calendar_client = CalendarClient(self.config)
```

### Step 5: CLI changes in `main()` (lines 1094, 1119)

Add argument:
```python
parser.add_argument('--verify-week', action='store_true',
                   help='Verify and backfill current week (Mon-Fri)')
```

Add handler (between submit and daily sync):
```python
elif args.verify_week:
    automation.verify_week()
```

### Step 6: Config changes

Add to `config_template.json` and `examples/developer_config.json`:
```json
"calendar": {
    "enabled": false,
    "tenant_id": "",
    "client_id": "",
    "client_secret": "",
    "user_email": "",
    "overhead_issue_key": "OVERHEAD-329"
}
```

### Step 7: Update `requirements.txt`

Add:
```
# Microsoft Graph API authentication (optional - for calendar integration)
# Install if using calendar fallback: pip install msal
# msal>=1.24.0
```

Keep it commented out so `pip install -r requirements.txt` doesn't fail for users who don't need calendar.

### Step 8: Create `run_weekly.bat`

Following exact pattern of `run_daily.bat`:
```bat
@echo off
echo ============================================ >> "D:\working\AI-Tempo-automation\v2\daily-timesheet.log"
echo Run: %date% %time% (Weekly Verify) >> "D:\working\AI-Tempo-automation\v2\daily-timesheet.log"
echo ============================================ >> "D:\working\AI-Tempo-automation\v2\daily-timesheet.log"
"C:\Users\asajwan.DESKTOP-TN8HNF1\AppData\Local\Programs\Python\Python314\python.exe" "D:\working\AI-Tempo-automation\v2\tempo_automation.py" --verify-week --logfile "D:\working\AI-Tempo-automation\v2\daily-timesheet.log"
```

Task Scheduler command (run as Admin):
```cmd
schtasks /Create /TN "TempoAutomation-WeeklyVerify" /SC WEEKLY /D FRI /ST 16:00 /TR "D:\working\AI-Tempo-automation\v2\run_weekly.bat" /F
```

---

## Expected Console Output

```
============================================================
TEMPO WEEKLY VERIFICATION & BACKFILL
Week of February 10, 2026
============================================================

--- Monday (2026-02-10) ---
  Existing worklogs (8.00h):
    - TS-36389: 2.00h
    - TS-36344: 2.00h
    - TS-36320: 2.00h
    - TS-36308: 2.00h
  [OK] Complete (8.00h / 8h)

--- Tuesday (2026-02-11) ---
  No existing worklogs
  [!] Gap: 8.00h needed (have 0.00h / 8h)
  Found 3 unlogged story(ies) for 2026-02-11:
    - TS-36389: Implement search feature
    - TS-36344: Fix login validation
    - TS-36320: Update API endpoint
  [OK] Logged 2.67h on TS-36389
  [OK] Logged 2.67h on TS-36344
  [OK] Logged 2.66h on TS-36320

--- Wednesday (2026-02-12) ---
  Existing worklogs (4.00h):
    - TS-36389: 4.00h
  [!] Gap: 4.00h needed (have 4.00h / 8h)
  Found 1 unlogged story(ies) for 2026-02-12:
    - TS-36344: Fix login validation
  [OK] Logged 4.00h on TS-36344

--- Thursday (2026-02-13) ---
  No existing worklogs
  [!] Gap: 8.00h needed (have 0.00h / 8h)
  No unlogged stories found for 2026-02-13
  Trying calendar fallback...
  [OK] Logged 1.00h on OVERHEAD-329 (Meeting: Team Standup)
  [OK] Logged 2.00h on OVERHEAD-329 (Meeting: Sprint Planning)
  [OK] Logged 5.00h on OVERHEAD-329 (Remaining overhead)

--- Friday (2026-02-14) ---
  [SKIP] Future date

============================================================
WEEKLY SUMMARY
============================================================
Day          Date         Status                  Existing    Added
------------------------------------------------------------
Monday       2026-02-10   [OK] Complete              8.00h    0.00h
Tuesday      2026-02-11   [+] Backfilled (stories)   0.00h    8.00h
Wednesday    2026-02-12   [+] Backfilled (stories)   4.00h    4.00h
Thursday     2026-02-13   [+] Backfilled (calendar)  0.00h    8.00h
Friday       2026-02-14   [--] Future                0.00h    0.00h
------------------------------------------------------------
Total worklogs created: 7
Total hours backfilled: 20.00h
============================================================
```

---

## Error Handling

| Scenario | Behavior |
|---|---|
| Historical JQL fails | Returns `[]`, falls through to calendar/overhead |
| Calendar token fails | `get_events_for_day()` returns `[]`, falls to overhead |
| `msal` not installed | ImportError caught, logged, calendar skipped |
| Calendar not configured | `is_configured()` returns False, logged, falls to overhead |
| No stories AND no calendar | Full gap logged on OVERHEAD-329 |
| `create_worklog` fails for one ticket | That ticket skipped, continues to next |
| Day already at 8h+ | Marked complete, no action |
| Future date in the week | Skipped with `[SKIP] Future date` |

---

## Calendar Setup Requirements (for later)

The user will need to:
1. Register an app in Azure AD (portal.azure.com)
2. Grant `Calendars.Read` application permission + admin consent
3. Get tenant_id, client_id, client_secret
4. Add to config.json `calendar` section
5. `pip install msal`

This can be done after the core weekly verify is working -- calendar is a graceful fallback.

---

## Verification Plan

1. **Test `--verify-week` with current week** -- should show existing worklogs and detect complete days
2. **Test the JQL directly in Jira** -- `status WAS "IN DEVELOPMENT" ON "2026-02-10" AND assignee = currentUser()`
3. **Test with calendar disabled** -- should fall to OVERHEAD-329 cleanly
4. **Test with calendar enabled** (after Azure AD setup) -- should read meetings and log them
5. **Check daily-timesheet.log** -- verify dual output works with `--logfile`
6. **Verify 8h cap** -- create 6h of manual worklogs, run verify, confirm only 2h backfilled
7. **Verify dedup** -- create worklogs on TS-100, run verify, confirm TS-100 not double-logged

---

## Key Code References (current line numbers in tempo_automation.py)

| What | Lines | Notes |
|---|---|---|
| `JiraClient.get_my_active_issues()` | 378-412 | Pattern to follow for new historical method |
| `JiraClient.get_my_worklogs()` | 295-353 | Used to check existing worklogs per day |
| `_auto_log_jira_worklogs()` | 890-954 | Integer-division hour distribution pattern to reuse |
| `_generate_work_summary()` | 956-1000 | Reuse for smart descriptions on backfilled stories |
| `TempoAutomation.__init__()` | 800-809 | Where to add CalendarClient initialization |
| `TempoClient` class ends | ~689 | Where to insert CalendarClient class |
| CLI argparse | 1082-1102 | Where to add --verify-week argument |
| main() handler | 1119-1124 | Where to add verify_week handler |

---

*To implement: read this file, then follow Steps 1-8 in order.*
