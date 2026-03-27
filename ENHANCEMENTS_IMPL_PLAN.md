# Enhancement Implementation Plan: E001 (QA Role) + E002 (Dry-Run Submission)

## Context

Two enhancements from `enhancements.md`:

- **E001**: Add QA role support — QA users should only log hours against tickets in `Testing` or `User Acceptance Testing` status, same assignment-guard logic as Developer.
- **E002**: Monthly submission dry-run — `--dry-run --submit` should preview the submission without hitting the Tempo API.

---

## E001: QA Role Active Ticket Filtering

### Problem
- `get_my_active_issues()` (line 1971) hardcodes statuses `IN DEVELOPMENT, CODE REVIEW`
- `get_issues_in_status_on_date()` (line 2007) hardcodes the same statuses
- `JiraClient` is only initialized for `developer` role (line 2737–2738)
- `sync_daily()` only calls `_auto_log_jira_worklogs()` for `developer` (line 2982)
- Config validation (line 363) only allows `developer`, `product_owner`, `sales`

### Changes

**1. `JiraClient.get_my_active_issues()` — add `statuses` parameter**
```python
# Before
def get_my_active_issues(self) -> List[Dict]:
    jql = 'assignee = currentUser() AND status IN ("IN DEVELOPMENT", "CODE REVIEW")'

# After
DEVELOPER_STATUSES = ["IN DEVELOPMENT", "CODE REVIEW"]
QA_STATUSES = ["Testing", "User Acceptance Testing"]

def get_my_active_issues(self, statuses: List[str] = None) -> List[Dict]:
    if statuses is None:
        statuses = DEVELOPER_STATUSES
    quoted = ', '.join(f'"{s}"' for s in statuses)
    jql = f'assignee = currentUser() AND status IN ({quoted})'
```

**2. `JiraClient.get_issues_in_status_on_date()` — add `statuses` parameter**
```python
# After: same pattern — build WAS clauses from statuses list
```

**3. `ConfigManager._validate_config()` (line 363) — allow 'qa' role**
```python
allowed_roles = ('developer', 'qa', 'product_owner', 'sales')
```

**4. `TempoAutomation.__init__()` (line 2737) — init JiraClient for QA too**
```python
if self.config.get('user', {}).get('role') in ('developer', 'qa'):
    self.jira_client = JiraClient(self.config)
```

**5. `TempoAutomation.sync_daily()` (line 2982) — QA uses same auto-log path**
```python
if self.config.get('user', {}).get('role') in ('developer', 'qa'):
    worklogs_created = self._auto_log_jira_worklogs(target_date)
else:
    worklogs_created = self._sync_manual_activities(target_date)
```

**6. `TempoAutomation._auto_log_jira_worklogs()` — pass role-specific statuses**

Add a helper method:
```python
def _get_active_statuses(self) -> List[str]:
    role = self.config.get('user', {}).get('role', 'developer')
    if role == 'qa':
        return ["Testing", "User Acceptance Testing"]
    return ["IN DEVELOPMENT", "CODE REVIEW"]
```

Then in `_auto_log_jira_worklogs()`, replace:
```python
active_issues = self.jira_client.get_my_active_issues()
```
with:
```python
active_issues = self.jira_client.get_my_active_issues(
    statuses=self._get_active_statuses()
)
```

Do the same for `get_issues_in_status_on_date()` calls in `backfill_range()` / `verify_week()`.

**7. Setup wizard — add 'qa' as selectable role**

Find the role selection in `ConfigManager` setup wizard and add `qa` as option with label `QA` alongside `developer`.

**8. No-active-tickets message — update to be role-aware**
```python
# Before
logger.warning("No active issues found (IN DEVELOPMENT / CODE REVIEW)")
# After
status_label = " / ".join(self._get_active_statuses())
logger.warning(f"No active issues found ({status_label})")
```

---

## E002: Dry-Run for Monthly Submission

### Problem
`submit_timesheet()` (line 4344) has zero `self.dry_run` checks. When `--dry-run --submit` is passed, the actual Tempo API call `self.tempo_client.submit_timesheet(period)` fires anyway.

### Change — one guard in `submit_timesheet()`

At the point where submission fires (~line 4494, after shortfall check passes):

```python
# After
if self.dry_run:
    print(f"[DRY RUN] Would submit timesheet for {period}")
    print("[DRY RUN] Gap detection ran above -- no API call made.")
    return

print(f"Submitting timesheet for {period}...")
success = self.tempo_client.submit_timesheet(period)
...
```

This lets the user run `--dry-run --submit` to:
1. See the full gap detection output (all monthly hours, any shortfalls)
2. Confirm the submission *would* proceed
3. Without touching the Tempo API

---

## Critical Files

| File | Changes |
|------|---------|
| `tempo_automation.py` | All code changes (JiraClient ~1971, ~2007; ConfigManager ~363; TempoAutomation ~2737, ~2982, ~3090+, ~4494) |

No new files needed.

---

## Verification

### E001
```bash
# 1. Add 'qa' role to a test config, run daily sync in dry-run
python tempo_automation.py --date 2026-03-26 --dry-run
# Expect: JQL uses "Testing" / "User Acceptance Testing" in log

# 2. Keep developer config, run daily sync
python tempo_automation.py --dry-run
# Expect: JQL still uses "IN DEVELOPMENT" / "CODE REVIEW" (no regression)
```

Run existing test suite — no regressions:
```bash
pytest tests/ -v --tb=short
```

### E002
```bash
# Dry-run submission — should show gap analysis, print [DRY RUN] line, exit without API call
python tempo_automation.py --dry-run --submit

# Without dry-run (existing behavior unchanged)
python tempo_automation.py --submit
```
