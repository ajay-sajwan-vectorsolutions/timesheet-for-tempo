# Release Notes — v4.0.2

**Release Date:** April 30, 2026
**Branch:** `fix/weekend-overhead-logging-bug`
**Status:** Ready for distribution
**Tests:** 570 passing (71% coverage)

---

## What's in This Release

v4.0.2 is a bug-fix release with two targeted fixes and one visible improvement to the monthly hours report. No configuration changes are required — just replace the script files and you're done.

---

## Changes

### 1. Weekend Overhead Bug Fixed (High Priority)

**Who it affects:** Developers and QA engineers using the tray app.

Starting April 18, 2026, overhead worklogs were being logged on Saturdays and Sundays. This caused incorrect Tempo entries on affected weekends.

**What happened:** The schedule guard in `sync_daily()` compared the skip reason against the string `"Weekend"`, but `is_working_day()` returns `"Weekend (Saturday)"` or `"Weekend (Sunday)"`. The mismatch meant weekends passed the off-day check and entered the overhead logging branch instead of being skipped.

A separate change (commit `2e2f1fe`) removed the accidental weekend protection in the tray app's catchup backfill, activating the dormant bug when the laptop woke from sleep on a weekend.

**Fix:** The guard now uses `reason.startswith("Weekend")` — matching both variants. An explicit `is_working_day` check was also added to the tray app's `_catchup_backfill` to prevent it firing on weekends.

**Action required:** If you were affected (April 18, 19, 25, or 26), manually delete the incorrect weekend worklogs from Tempo for those dates.

---

### 2. QA Role Now Eligible for Overhead Logging

**Who it affects:** QA engineers.

The overhead logging branch previously checked `role == "developer"` while the rest of the codebase used `role in ("developer", "qa")`. QA engineers were being skipped on PTO days and holidays instead of having overhead hours logged.

**Fix:** QA is now included in all overhead eligibility checks, matching the behaviour for developers.

---

### 3. Monthly Report: Working / Non-working / Weekend Subtotals

**Who it affects:** Everyone using `--view-monthly` or the tray app monthly report.

The `--view-monthly` report now classifies every day into one of three types and shows a subtotal row for each:

```
  --------------------------------------------------
  Working (19)             152.0h   152.0h
  Non-working (1)            0.0h     8.0h
  Weekend (8)                0.0h       --
  --------------------------------------------------
  TOTAL                    152.0h   160.0h

  [!] Shortfall: 8.0h across 1 day(s)
```

Previously, a single TOTAL line was shown without breakdown. The new format makes it immediately clear whether a shortfall is from a missed working day, a PTO day with no overhead logged, or weekend hours (which are informational only and cannot cause a shortfall).

**PTO days and holidays** count as Non-working with an expected value of 8h (overhead should be logged). **Weekend days** show logged hours if any exist but have no expected value — weekend hours never contribute to shortfall calculation.

---

### 4. PTO Re-Sync Now Handles Already-Set Dates

**Who it affects:** Anyone who adds PTO via the tray app.

When adding PTO via the tray app's Add PTO dialog, dates that were already in your PTO list would silently exit with "No PTO Added" instead of offering to re-sync. This meant that if you added a date, removed it, then re-added it, the sync offer would not appear.

**Fix:** The tray app now captures which dates were already in the PTO list before the add call and still offers the sync prompt for those dates.

---

## Upgrade Instructions

This is a drop-in replacement. No configuration changes needed.

**Windows:**
1. Stop the tray app: right-click tray icon → Exit
2. Replace `tempo_automation.py` and `tray_app.py` in your install folder (`C:\tempo-timesheet\`)
3. Restart the tray app: double-click `tray_app.py` or it will restart automatically at next login

**Mac:**
1. Stop the tray app: `python3 tray_app.py --stop`
2. Replace `tempo_automation.py` and `tray_app.py`
3. Restart: `python3 tray_app.py &`

**Verify the update:**
```cmd
python tempo_automation.py --dry-run
```
You should see the version confirm and no errors.

---

## Data Cleanup (Weekend Overhead)

If worklogs were created on weekends between April 18-26, 2026, they must be deleted manually:

1. Open Tempo (app.tempo.io)
2. Go to your timesheet for the affected weeks
3. Delete any entries on April 18 (Saturday), 19 (Sunday), 25 (Saturday), 26 (Sunday)

The script will no longer create entries on those days going forward.

---

## What's Not Changed

- All CLI commands, flags, and config keys are identical to v4.0.1
- No setup wizard re-run needed
- No token regeneration needed
- All 528 v4.0.1 tests still pass; 42 new tests added for these fixes

---

## Version History

| Version | Date | Summary |
|---------|------|---------|
| v4.0.2 | April 30, 2026 | Weekend overhead fix, QA overhead, three-type day model, PTO re-sync fix |
| v4.0.1 | March 31, 2026 | Tempo API v4 submit endpoint, reviewer lookup, approval status |
| v4.0 | March 13, 2026 | Data safety, retry logic, health check, dry-run, backfill, 500 tests |
| v3.x | Feb–Mar 2026 | Tray app, Mac support, schedule management, overhead, monthly submit |

Full history: [CHANGELOG.md](CHANGELOG.md)
