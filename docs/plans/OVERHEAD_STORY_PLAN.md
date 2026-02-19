# Overhead Story Support - Implementation Plan (v3.4)

**Created:** February 19, 2026
**Branch:** feature/v3.4/overhead-story-support
**Status:** Planning

---

## Context

Currently, when the daily sync finds no active Jira tickets, it logs nothing and returns empty. PTO days and PI planning weeks are skipped entirely. Users must manually log overhead hours in these scenarios. This feature adds automatic overhead story logging for 4 cases, saving developers from manual Tempo/Jira entry on days without active tickets, PTO days, and PI planning weeks.

---

## 4 Cases to Implement

| Case | Trigger | Behavior |
|------|---------|----------|
| 1 | No active tickets (IN DEV / CODE REVIEW) | Log full remaining hours to overhead stories |
| 2 | User manually logged overhead hours | Preserve manual overhead, distribute only remaining hours to active tickets |
| 3 | PTO day | Log 8h to PTO overhead story (instead of skipping) |
| 4 | PI Planning week | Log 8h to **upcoming PI's** overhead stories (not current PI) |

---

## Files to Modify

| File | Changes |
|------|---------|
| `tempo_automation.py` | New JiraClient method, 8 new TempoAutomation methods, modify `_auto_log_jira_worklogs`, `sync_daily`, `verify_week`, `_backfill_day`, CLI args |
| `tray_app.py` | New "Select Overhead" menu item, toast reminder, config reload |
| `config_template.json` | Add `overhead` section |

---

## Config Schema (new `overhead` section in config.json)

```json
{
  "overhead": {
    "current_pi": {
      "pi_identifier": "PI.26.2.APR.17",
      "pi_end_date": "2026-04-17",
      "stories": [
        {"issue_key": "OVERHEAD-329", "summary": "Plan and Management - TRAINING", "hours": 8}
      ],
      "distribution": "single"
    },
    "pto_story_key": "OVERHEAD-329",
    "planning_pi": {
      "pi_identifier": "PI.26.3.JUL.17",
      "stories": [
        {"issue_key": "OVERHEAD-350", "summary": "Plan and Management - TRAINING", "hours": 4},
        {"issue_key": "OVERHEAD-351", "summary": "Plan and Management - MEETINGS", "hours": 4}
      ],
      "distribution": "custom"
    },
    "fallback_issue_key": "OVERHEAD-001",
    "project_prefix": "OVERHEAD-",
    "_last_pi_check": "2026-02-19"
  }
}
```

**Distribution modes:**
- `"single"` -- one story gets all hours
- `"equal"` -- hours split equally across stories
- `"custom"` -- each story has user-assigned `hours` field

---

## New Methods

### JiraClient (after line ~1304)

**`get_overhead_stories() -> List[Dict]`**
- JQL: `project = OVERHEAD AND status = "In Progress"`
- Fields: `summary, sprint`
- Returns: `[{issue_key, issue_summary, pi_identifier}]`
- Extracts PI from sprint field name via regex `PI\.(\d{2})\.(\d+)\.([A-Z]{3})\.(\d{1,2})`
- Fallback: if sprint field unavailable, returns stories without pi_identifier

### TempoAutomation (after `_generate_work_summary`, ~line 2041)

**`_get_overhead_config() -> Dict`**
- Returns `self.config.get('overhead', {})`

**`_is_overhead_configured() -> bool`**
- True if `overhead.current_pi.stories` has at least one entry

**`_parse_pi_end_date(pi_identifier: str) -> Optional[str]`**
- Parses `PI.26.2.APR.17` to `"2026-04-17"` (YYYY-MM-DD)
- Returns None if parse fails

**`_is_planning_week(target_date: str) -> bool`**
- Gets `pi_end_date` from `overhead.current_pi`
- Counts 5 working days forward from PI end (using `is_working_day()` to skip weekends/holidays)
- Returns True if target_date falls within that 5-day window
- Safety: max 14 calendar days scan to avoid infinite loop

**`_log_overhead_hours(target_date, total_seconds, stories=None, distribution=None) -> List[Dict]`**
- Core method: creates Jira worklogs on overhead stories
- Supports 3 distribution modes:
  - `single`: all hours on first story
  - `equal`: integer division + remainder on last
  - `custom`: uses story `hours` field, proportionally scales to match total_seconds
- Falls back to `fallback_issue_key` if no stories provided
- Returns list of created worklog dicts

**`_warn_overhead_not_configured()`**
- Prints warning + sends Windows toast notification
- Message: "Run --select-overhead to set overhead stories for this PI"

**`_save_config()`**
- Writes `self.config` to config.json (JSON indent=2)

**`select_overhead_stories() -> bool`** (public, interactive CLI)
1. Query `get_overhead_stories()` from Jira
2. Group stories by PI identifier
3. Display grouped list with numbered items
4. **Current PI selection**: user picks stories + distribution mode + hours (if custom)
5. **PTO story selection**: user picks one story from current PI selection
6. **Planning PI selection**: if stories from a different/newer PI exist, user picks for planning week
7. Prompt for fallback issue key
8. Save all to `config['overhead']`
9. Show summary

**`_check_overhead_pi_current() -> bool`**
- Compares stored PI identifier against live Jira overhead stories
- Caches check daily (`_last_pi_check` field)
- Returns True if still current

---

## Modifications to Existing Methods

### `_auto_log_jira_worklogs()` (line 1930) -- Cases 1, 2, 4

**Current flow:** Delete ALL worklogs -> get active issues -> distribute daily hours

**New flow:**
1. Fetch existing worklogs
2. **Separate overhead vs non-overhead** (by `issue_key.startswith('OVERHEAD-')`)
3. **Only delete non-overhead worklogs** (preserves Case 2 manual overhead)
4. Calculate `overhead_seconds` from preserved overhead worklogs
5. `remaining_seconds = daily_hours_seconds - overhead_seconds`
6. If `remaining_seconds <= 0` -> done (overhead covers daily target)
7. **Check planning week** (Case 4) -> log remaining to planning_pi stories, return
8. Get active issues
9. **If no active issues** (Case 1) -> log remaining to current_pi stories, return
10. **Normal flow**: distribute remaining_seconds across active tickets

### `sync_daily()` (line 1837) -- Case 3

**Current:** Schedule guard returns early for ALL non-working days including PTO

**New:** After `is_working_day()` returns `(False, "PTO")`:
1. Check if overhead is configured and role is developer
2. If yes: log daily_hours to `pto_story_key` (with idempotency check -- skip if already logged)
3. Send daily summary notification
4. If overhead not configured: skip with warning + toast

All other non-working day reasons (weekend, holiday) continue to skip as before.

### `verify_week()` (line 2166) -- Cases 1, 3

**Current:** Skips non-working days entirely

**New:** When `is_working_day()` returns `(False, "PTO")`:
1. Check existing hours for that day
2. If gap > 0: log PTO overhead hours
3. Track in weekly summary as `[+] PTO (overhead logged)`

For working days with gaps, `_backfill_day()` handles the overhead fallback.

### `_backfill_day()` (line 2341) -- Case 1 fallback

**Current:** Returns `method='none'` when no historical stories found

**New:** After "no unlogged stories found":
1. Check if overhead is configured
2. If yes: log gap hours to overhead stories
3. Set `method='overhead'` in result

### `TempoAutomation.__init__()` (line ~1835)

Add after `check_year_end_warning()`:
- If developer role and overhead not configured -> print info
- If overhead configured but PI stale (daily check) -> print warning + toast

---

## CLI Arguments (2 new)

| Argument | Purpose |
|----------|---------|
| `--select-overhead` | Interactive overhead story selection for current PI |
| `--show-overhead` | Display current overhead configuration |

Both require full TempoAutomation init (they need JiraClient).

---

## Tray App Changes

1. **New menu item**: "Select Overhead" between "Add PTO" and "View Log"
   - Opens cmd window running `tempo_automation.py --select-overhead`
   - Same pattern as "View Schedule" (opens console for interactive CLI)

2. **Toast reminder**: In `_on_timer_fired()`, before sync notification:
   - If `overhead.current_pi.stories` is empty -> toast "Overhead stories not configured"
   - Reload config from disk first (`_reload_config()` method)

3. **New method**: `_reload_config()` -- re-reads config.json for fresh overhead state

---

## Planning Week -- Upcoming PI Logic

Key requirement: **planning week logs to UPCOMING PI's overhead stories, not current PI**.

During `--select-overhead`:
1. Query all OVERHEAD "In Progress" stories from Jira
2. Group by PI identifier extracted from sprint names
3. Show as sections: "Current PI (PI.26.2.APR.17)" and "Upcoming PI (PI.26.3.JUL.17)"
4. User selects separately for current PI and planning PI
5. If only one PI found, warn that planning week stories may not be available yet

During planning week detection in `_auto_log_jira_worklogs()`:
- Use `overhead.planning_pi.stories` (not `current_pi.stories`)
- Use `overhead.planning_pi.distribution` mode
- If `planning_pi` not configured, fall back to `current_pi` stories with warning

---

## Fallback Mechanisms

| Scenario | Fallback |
|----------|----------|
| Jira OVERHEAD project unreadable (permissions/network) | Use `fallback_issue_key` from config |
| `fallback_issue_key` not configured | Print warning, skip overhead logging, don't fail sync |
| PI identifier unparseable from sprint | Planning week detection disabled, other cases still work |
| Overhead not configured at all | Cases 1/3/4 degrade to current behavior (skip/empty), daily toast reminder |
| Planning PI stories not yet available | Warn during selection, fall back to current PI stories during planning week |
| Multiple PIs detected in overhead stories | Group and show both, let user choose |

---

## Implementation Order

**Phase 1: Foundation (non-breaking)**
1. All new methods in JiraClient and TempoAutomation
2. CLI args: `--select-overhead`, `--show-overhead`
3. Config template update
4. Test: selection flow works, config saved correctly

**Phase 2: Case 2 -- Preserve Manual Overhead**
5. Modify `_auto_log_jira_worklogs()`: separate overhead/non-overhead, only delete non-overhead
6. Deduct preserved overhead hours from daily total
7. Test: manually log 2h overhead -> sync -> 6h across active tickets

**Phase 3: Case 1 -- No Active Tickets Fallback**
8. Overhead fallback in `_auto_log_jira_worklogs()` when no active issues
9. Overhead fallback in `_backfill_day()` when no historical stories
10. Test: remove active tickets -> sync -> overhead logged

**Phase 4: Case 3 -- PTO Overhead Logging**
11. Modify `sync_daily()` schedule guard for PTO
12. Modify `verify_week()` for PTO days
13. Test: add PTO -> sync for PTO date -> overhead logged

**Phase 5: Case 4 -- Planning Week**
14. `_is_planning_week()` check in `_auto_log_jira_worklogs()`
15. Upcoming PI story selection and usage
16. Test: set past pi_end_date -> sync -> planning overhead logged

**Phase 6: Tray App Integration**
17. "Select Overhead" menu item + handler
18. Toast reminder for missing config
19. Config reload method

---

## Verification

```bash
# 1. Select overhead stories (interactive)
python tempo_automation.py --select-overhead

# 2. Show saved config
python tempo_automation.py --show-overhead

# 3. Case 1: No active tickets
python tempo_automation.py --date 2026-02-20

# 4. Case 2: Manual overhead preservation
#    (log 2h manually to OVERHEAD-329 in Jira first)
python tempo_automation.py --date 2026-02-20
#    Expected: OVERHEAD-329 2h preserved, remaining 6h split across active tickets

# 5. Case 3: PTO day
python tempo_automation.py --add-pto 2026-02-21
python tempo_automation.py --date 2026-02-21
#    Expected: 8h logged to pto_story_key

# 6. Case 4: Planning week
#    (set pi_end_date to a recent past date in config)
python tempo_automation.py --date 2026-02-20
#    Expected: 8h logged to planning_pi stories

# 7. Weekly verify with mixed days
python tempo_automation.py --verify-week

# 8. Idempotency: run sync twice, verify no duplicates
python tempo_automation.py --date 2026-02-20
python tempo_automation.py --date 2026-02-20
```

---

## Edge Cases

1. **Sprint field name variation**: Jira Cloud sprint field may be `sprint` or `customfield_10020`. Try both, add `overhead.sprint_field` config escape hatch.
2. **Overhead hours exceeding daily hours**: If manual overhead is 10h but target is 8h, print info and skip ticket logging.
3. **PTO day re-run**: Second run detects existing overhead hours, skips (no double-log).
4. **Planning week overlapping with PTO**: PTO takes priority (checked first in `sync_daily()`).
5. **Config migration**: No `overhead` section in existing configs -- all code uses `.get('overhead', {})`.
