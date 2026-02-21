# Overhead Story Feature - Attention List

Items that need verification, testing, or refinement at some point.
Check off items as they are addressed.

---

## Fallback & Error Handling

- [ ] **Fallback issue key**: Test that `_log_overhead_hours()` actually uses `fallback_issue_key` when `current_pi.stories` is empty and Jira API fails
- [ ] **Jira API failure during sync**: Simulate API timeout/401 to verify fallback kicks in gracefully (no crash, logs warning)
- [ ] **Overhead not configured warning**: Verify the toast notification fires correctly on Windows (not just the print message)

## PI Detection & Parsing

- [ ] **Sprint field is empty**: The OVERHEAD project returns no sprint data -- PI is parsed from issue summary instead. Investigate if a custom field ID (e.g., `customfield_10020`) would return sprint info, or if summary parsing is the permanent approach
- [ ] **Stories without PI identifier**: 3 overhead stories (OVERHEAD-323, 321, 320) have no PI pattern in their summary. Decide if these should be filtered out or shown in a separate "No PI" group (currently shown separately)
- [ ] **PI transition**: When a new PI starts and old overhead stories are closed/moved out of "In Progress", verify the tool detects the PI change and prompts re-selection
- [ ] **_check_overhead_pi_current() daily cache**: Verify the `_last_pi_check` date stamp works correctly and only makes one API call per day

## Planning Week

- [ ] **Planning week stories not testable yet**: Only PI.26.2 exists -- planning week selection requires a second PI's stories to be present in Jira. Test when PI.26.3 overhead stories are created
- [ ] **Planning week date calculation**: Verify the 5-working-day count correctly skips weekends and holidays (e.g., if PI ends on a Thursday, planning week should be Fri + next Mon-Thu)
- [ ] **Planning week overlapping with PTO**: If a PTO day falls within planning week, PTO takes priority (checked first in sync_daily). Verify this precedence works

## Distribution Modes

- [ ] **Custom hours distribution**: Test the interactive hour-assignment UX with live data (assigning specific hours per story, verifying remainder calculation)
- [ ] **Custom hours proportional scaling**: When custom hours are configured but the actual total_seconds differs (e.g., 6h remaining after manual overhead), verify proportional scaling produces correct results
- [ ] **Single story distribution**: Verify that when only 1 story is selected, all hours go to that one story without errors

## Sync Behavior

- [ ] **PTO re-run idempotency**: Run `--date` on a PTO day twice -- second run should say "PTO hours already logged" and not create duplicates
- [ ] **Manual overhead exceeding daily hours**: Test case where user manually logs 10h to OVERHEAD but daily target is 8h. Should print info message and not try to log negative hours
- [ ] **Backfill with overhead fallback**: Run `--verify-week` on a week with gaps where no historical stories exist. Verify overhead fallback in `_backfill_day()` works
- [ ] **Mixed day: manual overhead + active tickets**: Test the core Case 2 flow end-to-end with real data (manual 2h overhead + active tickets = 6h distributed)

## Default Daily Overhead (Case 0)

- [ ] **Normal day, 0h manual overhead**: Verify 2h logged to overhead + 6h across active tickets
- [ ] **Manual overhead < default**: If user manually logged 1h, verify 1h more auto-logged to reach 2h default, then 6h to active tickets
- [ ] **Manual overhead >= default**: If user manually logged 3h, verify no additional overhead logged, 5h to active tickets
- [ ] **No active tickets + default overhead**: Verify 2h default + 6h remaining all go to overhead = 8h total
- [ ] **daily_overhead_hours = 0**: Verify feature is disabled, behaves like pre-Case-0 logic
- [ ] **Idempotency**: Run sync twice on same day -- second run should not double the default overhead
- [ ] **select_overhead prompt**: Verify `--select-overhead` asks for daily_overhead_hours value

## Tray App

- [ ] **"Select Overhead" menu item**: Test that clicking it opens a cmd window with the interactive selection flow
- [ ] **Toast reminder timing**: Verify the overhead-not-configured toast fires at `daily_sync_time` (before the regular sync toast)
- [ ] **Config reload after selection**: After running `--select-overhead` from tray menu, verify the tray app picks up the new config on next timer fire

## Config & Migration

- [ ] **Existing users without overhead section**: Verify that users upgrading from v3.3 (no `overhead` in config.json) get graceful behavior -- info message, no crashes
- [ ] **Config template**: Verify `config_template.json` overhead section matches the actual structure written by `--select-overhead`

## Documentation

- [x] **Update CLAUDE.md**: Add overhead methods to class map, update CLI command list, update current status
- [x] **Update MEMORY.md**: Add overhead feature notes
- [x] **Update SETUP_GUIDE.md**: Add `--select-overhead` to the post-install steps
- [x] **Update README.md**: Mention overhead story support in features

---

*Created: February 19, 2026*
*Branch: feature/v3.4/overhead-story-support*
