# E006: Post-Install Shortfall Detection and Backfill

**Date:** 2026-04-09
**Status:** Approved
**Enhancement:** E006 from enhancements.md

## Problem

When a user does a fresh installation (or re-installation) mid-month, there may be working days earlier in the month with no hours logged. The system has no way to detect or surface this during installation. Users only discover the shortfall later when they try to submit their monthly timesheet or manually run `--view-monthly`.

## Solution

Add a new **Step 8** to `install.bat` that runs after the optional test sync (Step 7). This step detects missing hours in the current month and offers to backfill them with a simple Y/N prompt.

## Design

### User Flow

```
Step 7: Optional test sync (existing)
            |
Step 8: Post-Install Shortfall Check (NEW)
            |
    python tempo_automation.py --post-install-check
            |
    +-- Query Tempo for current month worklogs
    +-- Run _detect_monthly_gaps() for current month
            |
        No gaps found:
            [OK] All hours are up to date for April 2026.
            |
        Gaps found:
            SHORTFALL DETECTED FOR APRIL 2026
            ============================================
            Date         Day        Logged  Expected  Gap
            2026-04-06   Monday     0.0h    8.0h      8.0h
            2026-04-08   Wednesday  0.0h    8.0h      8.0h
            ============================================
            Total: 16.0h missing across 2 days

            Would you like to sync hours for these days now? (Y/N):
                |
            Y --> backfill_range(first_gap, today)
                  Shows progress per day
                  [OK] Backfill complete: 2 days synced
                |
            N --> You can fix this later with:
                    python tempo_automation.py --fix-shortfall
```

### Changes Required

#### 1. tempo_automation.py -- New `post_install_check()` method

Add to `TempoAutomation` class (after `backfill_range`):

- Calls existing `_detect_monthly_gaps()` for the current month
- Prints a gap table (reuses formatting pattern from `view_monthly_hours`)
- Prompts Y/N via `input()`
- If Y: calls existing `backfill_range(from_date, to_date)` where `from_date` is the first gap day and `to_date` is today
- If no gaps: prints "[OK] All hours are up to date for {month} {year}." and returns

#### 2. tempo_automation.py -- New `--post-install-check` CLI argument

- Added to argparse in CLI class
- Calls `automation.post_install_check()`
- Exits after completion

#### 3. install.bat -- New Step 8

- After Step 7 (optional test sync), add Step 8
- Calls: `python tempo_automation.py --post-install-check`
- Runs regardless of whether the user did the test sync in Step 7
- Output is visible in the same terminal window

### Existing Code Reused (no new logic needed)

| Function | File | Purpose |
|----------|------|---------|
| `_detect_monthly_gaps()` | tempo_automation.py | Queries Tempo, identifies working days with missing hours |
| `backfill_range()` | tempo_automation.py | Iterates date range, calls `sync_daily()` per working day, skips weekends/holidays/PTO |
| `_pre_sync_health_check()` | tempo_automation.py | Validates Jira + Tempo API tokens before any sync |

### Edge Cases

| Scenario | Behavior |
|----------|----------|
| First day of month, no gaps | Prints "all up to date", exits cleanly |
| API tokens invalid | `_detect_monthly_gaps()` fails with existing error handling |
| User on PTO all month | PTO days skipped by gap detection, may find zero gaps |
| Re-installation same day | Backfill is idempotent (delete + recreate), safe to re-run |
| User declines backfill | Shows `--fix-shortfall` command for later use |
| No previous installation | Gap detection still works -- all working days will show as gaps |

### Scope

- **Date range:** Current month only (1st of month to today)
- **User interaction:** Simple Y/N prompt (sync all gaps or skip)
- **No interactive day picking** -- this is a quick post-install step, not --fix-shortfall
