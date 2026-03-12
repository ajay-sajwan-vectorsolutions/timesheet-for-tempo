# Implementation Plan: Auto-Fill Shortfalls on Monthly Submission

## Context

**Problem:** When `submit_timesheet()` runs on the last working day of the month, it detects gaps (including today if daily sync hasn't run yet) and **blocks submission**. The user must manually run `--fix-shortfall` or sync, then re-run `--submit`. This creates a race condition and a poor experience for automated monthly submission.

**Goal:** Make `submit_timesheet()` self-healing on the last working day (or early-eligible day). When gaps are found, **auto-fill them non-destructively** (preserve all existing entries, only add missing hours), then **re-verify**, then **submit** -- all in one flow.

**Key Constraint:** Never delete or modify existing worklogs. Only create new entries for the gap hours.

---

## Current Flow (what exists today)

```
submit_timesheet()
  1. Guard: already submitted?
  2. Check early submission eligibility
  3. Guard: in submission window?
  4. _detect_monthly_gaps() --> finds gaps
  5. If gaps: save shortfall file, BLOCK, return
  6. If no gaps + last day: submit via Tempo API
```

`fix_shortfall()` (separate method, interactive):
- Re-detects gaps
- Shows table, asks user to pick days (A/1,3,5/Q)
- Calls `sync_daily()` for each selected day (which DELETES + recreates)

`_backfill_day()` (used by verify_week):
- Finds historical issues for a past date
- Distributes gap hours across unlogged tickets
- Does NOT delete existing entries (additive only)

---

## Proposed New Flow

```
submit_timesheet()
  1. Guard: already submitted?
  2. Check early submission eligibility
  3. Guard: in submission window?
  4. _detect_monthly_gaps() --> finds gaps
  5. If gaps AND (is_last_day OR early_submit_eligible):
     a. Print gap summary
     b. AUTO-FILL: call _auto_fill_gaps(gap_data) for each gap day
     c. RE-VERIFY: call _detect_monthly_gaps() again
     d. If still gaps: save shortfall, BLOCK (some days couldn't be filled)
     e. If clean: submit via Tempo API
  6. If gaps but NOT last day: save shortfall, BLOCK (existing behavior)
  7. If no gaps + last day: submit (existing behavior)
```

---

## Files to Modify

| File | Changes |
|------|---------|
| `tempo_automation.py` | New `_auto_fill_gaps()` method, new `_fill_gap_day()` method, modify `submit_timesheet()` |
| `tray_app.py` | No changes needed (calls `submit_timesheet()` internally) |
| `CLAUDE.md` | Update version, current status, architecture notes |
| `MEMORY.md` | Update version history, critical patterns |

---

## Detailed Changes

### 1. New Method: `_fill_gap_day(target_date, gap_seconds)` (~60 lines)

**Location:** `tempo_automation.py`, in the "Monthly gap detection & shortfall fix" section (after `_detect_monthly_gaps`, ~line 3588)

**Purpose:** Fill gap hours for a single day WITHOUT touching existing entries. This is different from `sync_daily()` (which deletes+recreates) and `_backfill_day()` (which is used by verify_week).

**Logic:**

```
_fill_gap_day(target_date: str, gap_seconds: int) -> bool:
    1. Fetch existing worklogs for the day (Jira + Tempo)
       - Reuse pattern from _auto_log_jira_worklogs (lines 2328-2362)
       - Get jira_worklogs, separate overhead vs non-overhead
       - Get tempo_total for manual Tempo entries

    2. Build set of existing issue keys (to avoid duplicate entries)

    3. Calculate existing overhead hours
       - overhead_seconds = jira_overhead_seconds + tempo_only_seconds

    4. Check if overhead needs topping up (Case 0: daily_overhead_hours)
       - default_oh = config overhead.daily_overhead_hours (e.g. 2h)
       - If overhead_seconds < default_oh_seconds AND overhead configured:
         - oh_gap = default_oh_seconds - overhead_seconds
         - Reduce from gap_seconds: oh_gap = min(oh_gap, gap_seconds)
         - Call _log_overhead_hours(target_date, oh_gap)
         - gap_seconds -= oh_gap

    5. If gap_seconds <= 0: return True (overhead filled the gap)

    6. Check if today or past date:
       - If target_date == today: use get_my_active_issues()
       - If target_date < today: use get_issues_in_status_on_date(target_date)

    7. Filter out already-logged issue keys from the results

    8. If no available issues:
       - If overhead configured: log remaining gap to overhead stories
       - Else: print warning, return False

    9. Distribute gap_seconds across available issues
       - Integer division + remainder on last ticket
       - Call jira_client.create_worklog() for each
       - Generate smart descriptions via _generate_work_summary()

    10. Return True if all worklogs created successfully
```

**Key differences from existing methods:**
- vs `sync_daily()`: No deletion step. Only adds entries for the gap.
- vs `_backfill_day()`: Handles overhead Case 0 (daily overhead top-up). Uses today's active issues for today, historical JQL for past dates. Returns bool instead of dict.
- vs `_auto_log_jira_worklogs()`: No deletion of non-overhead worklogs. Only creates entries for gap_seconds, not full daily_hours.

### 2. New Method: `_auto_fill_gaps(gap_data)` (~40 lines)

**Location:** `tempo_automation.py`, right after `_fill_gap_day`

**Purpose:** Orchestrate filling all gap days from gap_data.

```
_auto_fill_gaps(gap_data: dict) -> int:
    """Auto-fill shortfall gaps. Returns count of days successfully filled."""
    filled = 0
    for gap in gap_data['gaps']:
        target_date = gap['date']
        gap_seconds = int(gap['gap'] * 3600)

        print(f"  Auto-filling {target_date} ({gap['day']}): "
              f"{gap['gap']:.1f}h gap...")

        try:
            success = self._fill_gap_day(target_date, gap_seconds)
            if success:
                filled += 1
                print(f"  [OK] {target_date} filled")
            else:
                print(f"  [!] {target_date}: could not fill gap")
        except Exception as e:
            print(f"  [FAIL] {target_date}: {e}")
            logger.error(f"Auto-fill failed for {target_date}: {e}",
                         exc_info=True)

    return filled
```

### 3. Modify: `submit_timesheet()` (~25 lines added)

**Location:** Lines 3407-3454 (the shortfall handling block)

**Current behavior (lines 3407-3454):**
```python
if gap_data['gaps']:
    # Print table, save shortfall, BLOCK, return
```

**New behavior:**
```python
if gap_data['gaps']:
    total_gap = sum(g['gap'] for g in gap_data['gaps'])
    # Print gap summary table (existing code, keep as-is)
    ...

    # NEW: Auto-fill on last day / early eligible
    if is_last_day or early_submit_eligible:
        print(f"\n  [->] Auto-filling {len(gap_data['gaps'])} "
              f"gap day(s) ({total_gap:.1f}h)...\n")
        filled = self._auto_fill_gaps(gap_data)

        # Re-verify after fill
        print(f"\n  Re-verifying hours after auto-fill...")
        gap_data_2 = self._detect_monthly_gaps(today.year, today.month)

        if gap_data_2['gaps']:
            # Still have gaps after auto-fill
            remaining_gap = sum(g['gap'] for g in gap_data_2['gaps'])
            print(f"  [!] {len(gap_data_2['gaps'])} gap(s) remain "
                  f"({remaining_gap:.1f}h) after auto-fill.\n")
            self._save_shortfall_data(gap_data_2)
            print("  [!] Timesheet NOT submitted due to remaining shortfall.")
            return

        # All gaps filled -- proceed to submit
        print("  [OK] All gaps filled successfully.\n")
        if SHORTFALL_FILE.exists():
            SHORTFALL_FILE.unlink()
        # Fall through to submission below
    else:
        # Not last day -- block as before (existing code)
        self._save_shortfall_data(gap_data)
        print("\n  [!] Timesheet NOT submitted due to shortfall.")
        print("      Fix gaps via tray menu or --fix-shortfall, "
              "then --submit again.")
        return

# --- No shortfall (or gaps were just auto-filled) ---
# Existing submission code continues from here (line ~3456)
```

**Structural change:** The `if gap_data['gaps']:` block no longer always returns. On last day/early eligible, it auto-fills and falls through to the submission code. The existing `if not is_last_day and not early_submit_eligible: return` guard (line 3464) becomes redundant for the auto-fill path since we already checked those conditions.

---

## Hour Distribution Logic (Critical)

The math for distributing gap hours must be precise:

```
Day: Feb 28, 2026
Expected: 8.0h
Already logged: 3.0h (e.g., 1h on PROJ-123, 2h on OVERHEAD-456)
Gap: 5.0h = 18000 seconds

Step 1: Check overhead top-up
  daily_overhead_hours = 2.0h
  Existing overhead = 2.0h (OVERHEAD-456)
  Overhead gap = 0h (already met)
  Remaining gap: 18000 seconds

Step 2: Find active issues (today) or historical issues (past)
  Active: [PROJ-789, PROJ-101, PROJ-202]
  Already logged: {PROJ-123} (from existing worklogs)
  Available: [PROJ-789, PROJ-101, PROJ-202] (PROJ-123 excluded)

Step 3: Distribute 18000s across 3 tickets
  per_ticket = 18000 // 3 = 6000s (1.67h)
  remainder  = 18000 - (6000 * 3) = 0s
  PROJ-789: 6000s, PROJ-101: 6000s, PROJ-202: 6000s

Total after fill: 3.0h + 5.0h = 8.0h [OK]
```

**Edge case -- overhead gap eats into remaining gap:**
```
Day: Feb 27, 2026
Expected: 8.0h, Already logged: 2.0h (all on PROJ-123, no overhead)
Gap: 6.0h = 21600 seconds

Step 1: Overhead top-up needed
  daily_overhead_hours = 2.0h, existing overhead = 0h
  oh_gap = min(7200, 21600) = 7200s
  _log_overhead_hours(date, 7200) -> creates 2h overhead
  gap_seconds = 21600 - 7200 = 14400s (4.0h)

Step 2: Find available issues, distribute 14400s
  ...

Total: 2.0h + 2.0h(OH) + 4.0h(tickets) = 8.0h [OK]
```

---

## Existing Code to Reuse

| What | Where | How |
|------|-------|-----|
| Fetch Jira worklogs + separate overhead | `_auto_log_jira_worklogs` lines 2328-2362 | Copy pattern (read-only fetch, no delete) |
| Fetch Tempo worklogs for total | `_auto_log_jira_worklogs` lines 2347-2352 | Same pattern |
| Overhead top-up (Case 0) | `_auto_log_jira_worklogs` lines 2399-2427 | Same logic |
| Get active issues (today) | `get_my_active_issues()` line 1395 | Direct call |
| Get historical issues (past) | `get_issues_in_status_on_date()` line 1431 | Direct call |
| Overhead fallback (no tickets) | `_auto_log_jira_worklogs` lines 2478-2498 | Same pattern |
| Log overhead hours | `_log_overhead_hours()` line 2692 | Direct call |
| Generate work summary | `_generate_work_summary()` line 2555 | Direct call |
| Create Jira worklog | `jira_client.create_worklog()` | Direct call |
| Integer division + remainder | `_auto_log_jira_worklogs` lines 2501-2505 | Same pattern |
| Planning week detection | `_is_planning_week()` line 2650 | Direct call |

---

## What Does NOT Change

- `sync_daily()` -- unchanged (still uses delete+recreate for normal daily sync)
- `fix_shortfall()` -- unchanged (still interactive, still calls `sync_daily()`)
- `_detect_monthly_gaps()` -- unchanged (still checks all days up to today)
- `_backfill_day()` -- unchanged (still used by `verify_week`)
- Tray app `_run_submit()` -- unchanged (calls `submit_timesheet()` which now auto-fills)
- `_submit_visible()` -- unchanged (visibility logic stays the same)

---

## Execution Flow Summary

### Scenario: Last day of month, daily sync hasn't run yet

```
run_monthly.bat -> tempo_automation.py --submit
  submit_timesheet():
    is_last_day = True
    _detect_monthly_gaps():
      Feb 1-27: all 8h [OK]
      Feb 28: 0h logged -> gap = 8.0h
    gaps found, is_last_day = True
    -> _auto_fill_gaps():
       _fill_gap_day("2026-02-28", 28800):
         Fetch existing: 0h
         Overhead top-up: 2h (daily_overhead_hours)
         Remaining gap: 6h
         get_my_active_issues(): [PROJ-1, PROJ-2, PROJ-3]
         Distribute 6h: 2h each
         Create 3 Jira worklogs
         return True
    -> _detect_monthly_gaps() (re-verify):
       Feb 28: 8h [OK]
       No gaps!
    -> Submit via Tempo API
    -> [OK] Timesheet submitted
```

### Scenario: Last working day, some partial entries exist

```
  gaps: Feb 25 (5h logged, 3h gap), Feb 28 (0h, 8h gap)
  -> _auto_fill_gaps():
     _fill_gap_day("2026-02-25", 10800):   # 3h gap
       Existing: 5h (2h OVERHEAD, 3h PROJ-99)
       Overhead OK (2h >= 2h)
       Historical issues for Feb 25: [PROJ-100, PROJ-101]
       Filter: PROJ-99 already logged -> [PROJ-100, PROJ-101]
       Distribute 3h: 1.5h each
       return True
     _fill_gap_day("2026-02-28", 28800):   # 8h gap
       Existing: 0h
       Overhead top-up: 2h
       Active issues: [PROJ-1, PROJ-2]
       Distribute 6h: 3h each
       return True
  -> Re-verify: all clean
  -> Submit
```

---

## Verification Plan

1. **Unit tests** for `_fill_gap_day()`:
   - Day with 0h logged -> fills full 8h (overhead + tickets)
   - Day with partial hours -> fills only the gap
   - Day with overhead missing -> tops up overhead first
   - Day with no active/historical tickets -> falls back to overhead
   - Day already at 8h -> returns True immediately (no-op)

2. **Unit tests** for `_auto_fill_gaps()`:
   - Multiple gap days -> fills all
   - Mix of fillable and unfillable days -> returns partial count
   - Empty gaps list -> returns 0

3. **Integration test** for `submit_timesheet()` with auto-fill:
   - Last day, gaps exist, auto-fill succeeds -> submits
   - Last day, gaps exist, auto-fill partial -> blocks with remaining gaps
   - Not last day, gaps exist -> blocks (no auto-fill attempted)
   - Early eligible, gaps exist, auto-fill succeeds -> submits

4. **Manual verification:**
   - Run `python tempo_automation.py --submit` on a test day
   - Verify existing worklogs are preserved (not deleted)
   - Verify gap hours are distributed correctly
   - Verify Tempo API shows correct totals after fill

---

## Version Bump

- Version: 3.9 -> 4.0 (significant behavioral change to submission flow)
- Update CLAUDE.md, MEMORY.md, README.md version references
- Add to CHANGELOG.md
