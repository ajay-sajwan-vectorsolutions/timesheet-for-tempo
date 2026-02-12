# Tempo Timesheet Automation - Claude Context File

**Project:** Tempo Timesheet Automation  
**Version:** 2.0  
**Status:** In Testing/Enhancement Phase  
**Last Updated:** February 12, 2026

---

## PROJECT OVERVIEW

### Purpose
Automate daily timesheet entry and monthly submission for a 200-person engineering team, saving $1.2M annually in lost productivity.

### The Problem
- Developers: 15-20 min/day copying Jira worklogs to Tempo manually
- Product Owners & Sales: Manual time tracking with no automation
- 38% late monthly submissions requiring manager follow-up
- Result: $1.2M/year in lost productivity across 200 people

### The Solution
Local Python script that:
- Auto-logs time in Jira by distributing daily hours across active tickets (developers)
- Overwrites previous worklogs on re-run (idempotent — always reflects current active tickets)
- Pre-fills timesheets from configuration (POs/Sales)
- Tempo auto-syncs from Jira worklogs (no direct Tempo writes for developers)
- Auto-submits monthly timesheets
- Runs via OS-level scheduling (Task Scheduler/cron)
- Zero hosting costs, zero organizational friction

---

## TECHNICAL ARCHITECTURE

### Environment
- **Jira Instance:** lmsportal.atlassian.net
- **Tempo:** Cloud version (Jira plugin)
- **Tempo API:** v4 (https://api.tempo.io/4/)
- **Jira API:** v3 REST
- **Python:** 3.7+
- **OS Support:** Windows, Mac, Linux

### Account ID Format
User's Tempo worker ID: `712020:66c372bc-e38f-414e-b5d3-fd8ff7513a44`
Format: `accountId:uuid` (retrieved from Tempo API)

### Project Structure
```
tempo-automation/
├── tempo_automation.py          # Main script (800+ lines)
├── config.json                  # User configuration (created by setup)
├── config_template.json         # Configuration template
├── requirements.txt             # Python dependencies (requests)
├── install.bat                  # Windows installer
├── install.sh                   # Mac/Linux installer
├── tempo_automation.log         # Runtime logs
├── README.md                    # User documentation
├── HANDOFF.md                   # Technical documentation
├── VERSION_2_RELEASE_NOTES.md   # v2.0 changes
├── QUICK_REFERENCE.md           # Command cheat sheet
└── examples/
    ├── developer_config.json
    ├── product_owner_config.json
    └── sales_config.json
```

---

## CODE ARCHITECTURE

### Main Components (tempo_automation.py)

**1. ConfigManager (Lines ~50-230)**
- Interactive setup wizard
- Configuration loading/saving
- Credential management
- First-time user experience

**2. JiraClient (Lines ~255-505)**
- Jira REST API v3 integration
- Fetches worklogs for date range (with worklog_id for deletion)
- Deletes worklogs by ID (for overwrite-on-rerun)
- Queries active issues (IN DEVELOPMENT / CODE REVIEW) via JQL
- Fetches issue details (description + comments) for smart worklog descriptions
- Extracts plain text from ADF (Atlassian Document Format) content
- Creates worklogs directly on Jira issues (multi-line ADF comment format)
- Basic auth (email + API token)

**3. TempoClient (Lines ~315-490)**
- Tempo API v4 integration
- Get user worklogs
- Create worklog entries
- Submit timesheets
- Get current period
- Bearer token authentication

**4. NotificationManager (Lines ~495-570)**
- Email notifications via SMTP
- Daily summary emails
- Submission confirmation emails
- HTML formatting

**5. TempoAutomation (Lines ~700-970)**
- Main orchestration engine
- Daily sync logic
- Monthly submission logic
- Auto-log Jira worklogs across active tickets (developers, default)
- Generates smart worklog descriptions from ticket content (description + comments)
- Overwrites existing worklogs on re-run (delete then create)
- Legacy Jira-to-Tempo sync kept but not called by default
- Manual activity sync (POs/Sales)

**6. CLI Interface (Lines ~725-755)**
- Command-line argument parsing
- Entry point (main function)
- Error handling

---

## API INTEGRATIONS

### Tempo API v4

**Base URL:** `https://api.tempo.io/4/`  
**Authentication:** Bearer token in header  
**Token Location:** config['tempo']['api_token']

**Endpoints Used:**
```
GET  /user
     → Returns: { accountId, displayName, email }
     → Purpose: Get current user's Tempo account ID
     → Called by: get_account_id() [Line 224]

GET  /worklogs/user/{accountId}?from={date}&to={date}
     → Returns: { results: [worklog objects] }
     → Purpose: Fetch user's worklogs for date range
     → Called by: get_user_worklogs() [Line 333]

POST /worklogs
     → Body: { issueKey, timeSpentSeconds, startDate, authorAccountId, description }
     → Returns: Created worklog object
     → Purpose: Create new timesheet entry
     → Called by: create_worklog() [Line 359]

GET  /timesheet-approvals/periods
     → Returns: { results: [period objects with dateFrom, dateTo, key] }
     → Purpose: Get configured timesheet periods
     → Called by: _get_current_period() [Line 457]

POST /timesheet-approvals/submit
     → Body: { worker: {accountId}, period: {key} }
     → Returns: Submission confirmation
     → Purpose: Submit timesheet for approval
     → Called by: submit_timesheet() [Line 389]
```

### Jira REST API v3

**Base URL:** `https://lmsportal.atlassian.net/rest/api/3/`  
**Authentication:** Basic auth (email + API token, base64 encoded)  
**Token Location:** config['jira']['api_token']

**Endpoints Used:**
```
GET /search/jql?jql={query}&fields=worklog,summary,key&maxResults=100
    → JQL: worklogAuthor = currentUser() AND worklogDate >= "YYYY-MM-DD"
    → Returns: { issues: [issue objects] }
    → Purpose: Find issues with worklogs by current user
    → Called by: get_my_worklogs()

GET /search/jql?jql={query}&fields=summary&maxResults=50
    → JQL: assignee = currentUser() AND status IN ("IN DEVELOPMENT", "CODE REVIEW")
    → Returns: { issues: [issue objects] }
    → Purpose: Find active tickets assigned to current user
    → Called by: get_my_active_issues()

GET /issue/{issueKey}/worklog
    → Returns: { worklogs: [worklog objects] }
    → Purpose: Get all worklogs for specific issue
    → Called by: get_my_worklogs()

POST /issue/{issueKey}/worklog
    → Body: { timeSpentSeconds, started (ISO datetime), comment (ADF format) }
    → Returns: Created worklog object
    → Purpose: Create worklog directly on Jira issue
    → Called by: JiraClient.create_worklog()

DELETE /issue/{issueKey}/worklog/{worklogId}
    → Returns: 204 No Content
    → Purpose: Delete an existing worklog (for overwrite-on-rerun)
    → Called by: delete_worklog()

GET /issue/{issueKey}?fields=summary,description,comment
    → Returns: { fields: { summary, description (ADF), comment: { comments: [...] } } }
    → Purpose: Fetch ticket description and comments for smart worklog descriptions
    → Called by: get_issue_details()
```

---

## CONFIGURATION

### config.json Structure

```json
{
  "user": {
    "email": "user@company.com",
    "name": "Full Name",
    "role": "developer|product_owner|sales"
  },
  "jira": {
    "url": "lmsportal.atlassian.net",
    "email": "user@company.com",
    "api_token": "jira_api_token"
  },
  "tempo": {
    "api_token": "tempo_api_token"
  },
  "organization": {
    "default_issue_key": "ADMIN-001"  // For non-Jira users
  },
  "schedule": {
    "daily_hours": 8,
    "daily_sync_time": "18:00",
    "monthly_submit_day": "last"
  },
  "notifications": {
    "email_enabled": true,
    "smtp_server": "smtp.gmail.com",
    "smtp_port": 587,
    "smtp_user": "user@gmail.com",
    "smtp_password": "app_password",
    "notification_email": "user@company.com"
  },
  "manual_activities": [
    {"activity": "Meetings", "hours": 3},
    {"activity": "Documentation", "hours": 5}
  ],
  "options": {
    "auto_submit": true,
    "require_confirmation": false,
    "sync_on_startup": false
  }
}
```

---

## USER ROLES & BEHAVIOR

### Developer Role
- **Has Jira access:** Yes
- **Workflow:**
  1. Script deletes any existing worklogs for the target date (overwrite behavior)
  2. Queries active tickets (status IN DEVELOPMENT / CODE REVIEW, assigned to user)
  3. Distributes daily_hours equally across active tickets
  4. Generates smart worklog descriptions from each ticket's content (description + recent comments)
  5. Creates Jira worklogs directly on each ticket with meaningful descriptions
  6. Tempo auto-syncs from Jira (no direct Tempo API writes needed)
  7. Sends daily summary email
- **Idempotent:** Re-running always overwrites — previous worklogs are deleted first
- **Configuration:** Requires both Jira and Tempo API tokens
- **Issue keys:** Uses actual Jira ticket keys (e.g., PROJ-1234)

### Product Owner Role
- **Has Jira access:** No (typically)
- **Workflow:**
  1. Script reads manual_activities from config
  2. Creates Tempo entries for configured activities
  3. Sends daily summary email
- **Configuration:** Only Tempo API token required
- **Issue keys:** Uses organization.default_issue_key (ask admin)

### Sales Role
- **Has Jira access:** No
- **Workflow:** Same as Product Owner
- **Configuration:** Only Tempo API token required
- **Issue keys:** Uses organization.default_issue_key

---

## KEY FUNCTIONS TO UNDERSTAND

### get_account_id() [Line 224-248]
**Purpose:** Retrieve user's Tempo account ID  
**API Call:** GET /user  
**Returns:** String in format "712020:uuid"  
**Critical:** This is called during config setup and worklog creation  
**Error Handling:** Falls back to email if API fails

### get_my_worklogs() [JiraClient]
**Purpose:** Fetch Jira worklogs for date range
**API Calls:** GET /search/jql (JQL), GET /issue/{key}/worklog
**Filters:** Only worklogs by current user in date range
**Returns:** List of worklog dicts with worklog_id, issue_key, time_spent_seconds, etc.

### delete_worklog() [JiraClient]
**Purpose:** Delete a worklog from a Jira issue
**API Call:** DELETE /issue/{issueKey}/worklog/{worklogId}
**Used by:** _auto_log_jira_worklogs() to clear previous entries before re-logging

### get_my_active_issues() [JiraClient]
**Purpose:** Find tickets assigned to current user with status IN DEVELOPMENT or CODE REVIEW
**API Call:** GET /search/jql with JQL: `assignee = currentUser() AND status IN ("IN DEVELOPMENT", "CODE REVIEW")`
**Returns:** List of dicts with issue_key and issue_summary

### get_issue_details() [JiraClient]
**Purpose:** Fetch a ticket's description and recent comments for generating worklog descriptions
**API Call:** GET /issue/{issueKey}?fields=summary,description,comment
**Returns:** Dict with summary, description_text (plain text extracted from ADF), recent_comments (last 3)
**Used by:** _generate_work_summary() to build meaningful worklog descriptions

### _extract_adf_text() [JiraClient, static]
**Purpose:** Recursively extract plain text from Jira's ADF (Atlassian Document Format) JSON
**Used by:** get_issue_details() to convert ADF descriptions and comments to plain text

### JiraClient.create_worklog()
**Purpose:** Create a worklog directly on a Jira issue
**API Call:** POST /issue/{issueKey}/worklog
**Parameters:** issue_key, time_spent_seconds, started (YYYY-MM-DD), comment
**Note:** Multi-line comments are rendered as separate ADF paragraphs for clean display in Jira

### TempoClient.create_worklog()
**Purpose:** Create new Tempo timesheet entry
**API Call:** POST /worklogs
**Parameters:** issue_key, time_seconds, start_date, description
**Note:** Used by legacy _sync_jira_worklogs() and manual activities only

### sync_daily() [TempoAutomation]
**Purpose:** Main daily sync orchestration
**Workflow:**
1. Determine user role
2. If developer: call _auto_log_jira_worklogs() (deletes old + creates new in Jira)
3. If PO/Sales: call _sync_manual_activities()
4. Calculate total hours
5. Send email notification
6. Log results

### _auto_log_jira_worklogs() [TempoAutomation]
**Purpose:** Auto-log time by distributing daily hours across active Jira tickets
**Workflow:**
1. Fetch existing worklogs for target date via get_my_worklogs()
2. Delete all existing worklogs (overwrite behavior)
3. Query active issues via get_my_active_issues()
4. Calculate hours_per_ticket = daily_hours / num_tickets
5. Generate smart description for each ticket via _generate_work_summary()
6. Create worklog on each ticket via JiraClient.create_worklog()
**Idempotent:** Safe to re-run — always deletes previous entries first

### _generate_work_summary() [TempoAutomation]
**Purpose:** Build a meaningful 1-3 line worklog description from a Jira ticket's content
**Logic:**
- Line 1: First sentence of the ticket description (falls back to summary if empty)
- Lines 2-3: First line of the most recent comments (reflects what was actually done)
- Each line truncated to 120 chars max
**Fallback:** If get_issue_details() fails, returns generic "Worked on {key}: {summary}"
**Example output:**
```
Implement pagination for the search results API endpoint
Fixed offset calculation for edge case with empty results
Added unit tests for boundary conditions
```

### submit_timesheet() [Line 680-710]
**Purpose:** Submit monthly timesheet  
**API Call:** POST /timesheet-approvals/submit  
**Timing:** Should run on last day of month at 11 PM

---

## COMMON TASKS & HOW TO DO THEM

### Debug Setup Issues
```python
# Check logs
cat tempo_automation.log

# Test account ID retrieval
# Add this temporarily in main():
config_mgr = ConfigManager()
account_id = config_mgr.get_account_id()
print(f"Account ID: {account_id}")
```

### Test API Calls Individually
```python
# Test Tempo user endpoint
import requests
token = "your_tempo_token"
response = requests.get(
    "https://api.tempo.io/4/user",
    headers={"Authorization": f"Bearer {token}"}
)
print(response.json())
```

### Add Better Error Messages
```python
# Pattern to follow
try:
    # API call here
    response.raise_for_status()
except requests.exceptions.HTTPError as e:
    if e.response.status_code == 401:
        logger.error("Authentication failed - check API token")
    elif e.response.status_code == 404:
        logger.error("Resource not found - check URL/endpoint")
    else:
        logger.error(f"HTTP error: {e}")
```

### Test Without Scheduling
```bash
# Test daily sync
python tempo_automation.py

# Test specific date
python tempo_automation.py --date 2026-02-01

# Test monthly submission (doesn't actually submit by default)
python tempo_automation.py --submit

# Re-run setup
python tempo_automation.py --setup
```

---

## DEBUGGING GUIDE

### Common Error Patterns

**1. "401 Unauthorized"**
- **Cause:** Invalid or expired API token
- **Check:** Token format, expiration, permissions
- **Fix:** Regenerate token and update config.json

**2. "Account ID not found"**
- **Cause:** get_account_id() failing
- **Check:** Line 224-248, Tempo API response
- **Fix:** Add logging to see actual API response

**3. "No worklogs found"**
- **Cause:** No Jira time logged, or JQL query issue
- **Check:** JQL query at line 260, date format
- **Fix:** Test JQL directly in Jira

**4. "Issue key not found"**
- **Cause:** Invalid default_issue_key for non-developers
- **Check:** config['organization']['default_issue_key']
- **Fix:** Ask Jira admin for correct key

**5. "Email sending failed"**
- **Cause:** SMTP credentials or server issue
- **Check:** Gmail requires App Password, not regular password
- **Fix:** Generate App Password at myaccount.google.com/apppasswords

### Logging Best Practices
```python
# Always log:
logger.info(f"Starting operation X with parameters: {params}")
logger.info(f"API call successful: {response.status_code}")
logger.error(f"Operation failed: {e}")
logger.error(f"API response: {response.text}")

# Don't log:
# - API tokens (security risk)
# - User passwords
# - Full config file
```

---

## ENHANCEMENT OPPORTUNITIES

### Priority 1 (High Value)
- [ ] Retry logic with exponential backoff for API calls
- [ ] Better duplicate detection (check by date + issue key)
- [ ] Validate API tokens on startup
- [ ] Add --dry-run flag for testing
- [ ] Token expiry warning (7 days before)

### Priority 2 (Medium Value)
- [ ] Offline queue (store operations, sync when online)
- [ ] Support for multiple Jira instances
- [ ] GUI setup wizard (instead of CLI)
- [ ] Desktop notifications (in addition to email)
- [ ] Weekly/monthly summary reports

### Priority 3 (Nice to Have)
- [ ] Integration with company calendar for holidays
- [ ] Slack notifications instead of email
- [ ] Web dashboard for monitoring
- [ ] Bulk operations (backfill multiple days)
- [ ] Custom field mapping

---

## CODING STANDARDS FOR THIS PROJECT

### Style Guidelines
- Follow PEP 8 Python style guide
- Use type hints for function parameters and returns
- Docstrings for all classes and public methods
- Max line length: 100 characters
- Use f-strings for string formatting

### Error Handling Pattern
```python
try:
    # Operation
    result = api_call()
    logger.info("Success message")
    return result
except SpecificException as e:
    logger.error(f"Specific error: {e}")
    # Fallback or raise
except Exception as e:
    logger.error(f"Unexpected error: {e}", exc_info=True)
    # Fallback or raise
```

### Configuration Access
```python
# Always use .get() with fallback
value = self.config.get('section', {}).get('key', 'default')

# Not:
value = self.config['section']['key']  # Can raise KeyError
```

### API Calls
```python
# Always set timeout
response = requests.get(url, headers=headers, timeout=30)

# Always check status
response.raise_for_status()

# Always log
logger.info(f"API call to {url}: {response.status_code}")
```

---

## TESTING CHECKLIST

### Unit Testing (Not yet implemented)
- [ ] ConfigManager.load_config()
- [ ] JiraClient.get_my_worklogs()
- [ ] TempoClient.create_worklog()
- [ ] NotificationManager.send_daily_summary()

### Integration Testing
- [ ] Full daily sync with real credentials
- [ ] Monthly submission (dry run)
- [ ] Email notifications
- [ ] Error scenarios (invalid token, network failure)

### User Acceptance Testing
- [ ] Developer role: Jira sync works
- [ ] Product Owner role: Manual activities work
- [ ] Sales role: Manual activities work
- [ ] Email notifications received
- [ ] Scheduled tasks run automatically

---

## DEPLOYMENT CONSIDERATIONS

### Prerequisites
- Python 3.7+ installed
- Tempo API token (all users)
- Jira API token (developers only)
- SMTP credentials (optional, for email)

### Rollout Plan
**Phase 1:** Self-testing (1 day)
**Phase 2:** Pilot (5 users, 1 week)
**Phase 3:** Frontend Team B (50 users, 1 week)
**Phase 4:** Full organization (200 users, 2 weeks)

### Support Plan
- Slack channel: #tempo-automation
- Documentation: README.md + FAQ
- Video tutorial: 5-minute installation guide
- Office hours: First week daily, then weekly

---

## SECURITY CONSIDERATIONS

### Credentials
- Stored locally in config.json (plain text currently)
- **TODO:** Encrypt using OS keychain
  - Windows: Windows Credential Manager
  - Mac: Keychain Access
  - Linux: Secret Service API

### API Tokens
- Tempo: Standard API token, can be revoked
- Jira: API token (not password), can be revoked
- Both should expire/rotate regularly

### Network
- All API calls over HTTPS
- No proxy configuration yet (TODO if needed)

### Data Privacy
- Logs contain: timestamps, operations, errors
- Logs do NOT contain: API tokens, passwords
- Logs may contain: issue keys, time amounts, user emails

---

## PERFORMANCE NOTES

### Current Performance
- Daily sync: ~5-10 seconds (depends on # of worklogs)
- Monthly submission: ~2-3 seconds
- Setup wizard: ~2 minutes (user input time)

### Optimization Opportunities
- Cache account ID (currently fetched every run)
- Batch API calls when possible
- Parallel processing for multiple days

### Resource Usage
- Memory: ~20-30 MB
- CPU: Minimal (mostly I/O bound)
- Network: ~10-50 KB per sync

---

## KNOWN LIMITATIONS

1. **Single Jira instance:** Only supports one Jira URL per user
2. **No offline mode:** Requires internet for API calls
3. **Manual period detection:** Simplified if API call fails
4. **No custom fields:** Doesn't map Jira custom fields to Tempo
5. **Windows Scheduler limitation:** Monthly task runs on days 28-31, checks if last day
6. **Equal distribution only:** Hours are split equally across active tickets (no weighting)

---

## WHEN TO CALL SPECIFIC FUNCTIONS

### Startup / Initialization
```python
ConfigManager() → loads or creates config
JiraClient(config) → initializes Jira connection
TempoClient(config) → initializes Tempo connection
```

### Daily Operations (Developer)
```python
sync_daily() → orchestrates entire sync
  ↓
_auto_log_jira_worklogs()
  ↓
get_my_worklogs() → find existing worklogs for target date
  ↓
delete_worklog() → remove each existing worklog (overwrite)
  ↓
get_my_active_issues() → find IN DEVELOPMENT / CODE REVIEW tickets
  ↓
_generate_work_summary() → for each ticket:
  ↓  get_issue_details() → fetch description + comments
  ↓  _extract_adf_text() → convert ADF to plain text
  ↓  build 1-3 line summary
  ↓
JiraClient.create_worklog() → log (daily_hours / num_tickets) on each with smart description
  ↓
send_daily_summary()
```

### Daily Operations (PO/Sales)
```python
sync_daily() → orchestrates entire sync
  ↓
_sync_manual_activities()
  ↓
TempoClient.create_worklog() (called multiple times)
  ↓
send_daily_summary()
```

### Legacy (kept but not called by default)
```python
_sync_jira_worklogs() → old flow that synced Jira worklogs to Tempo
```

### Monthly Operations
```python
submit_timesheet()
  ↓
_get_current_period()
  ↓
API call to submit
  ↓
send_submission_confirmation()
```

---

## IMPORTANT URLS & REFERENCES

### API Documentation
- Tempo API: https://apidocs.tempo.io/
- Jira REST API: https://developer.atlassian.com/cloud/jira/platform/rest/v3/

### Token Generation
- Tempo tokens: https://app.tempo.io/ → Settings → API Integration
- Jira tokens: https://id.atlassian.com/manage-profile/security/api-tokens
- Gmail App Passwords: https://myaccount.google.com/apppasswords

### Internal Resources
- Jira instance: https://lmsportal.atlassian.net/
- Tempo app: https://lmsportal.atlassian.net/plugins/servlet/ac/io.tempo.jira/tempo-app

---

## QUICK REFERENCE COMMANDS

```bash
# Setup (first time)
python tempo_automation.py --setup

# Daily sync
python tempo_automation.py

# Sync specific date
python tempo_automation.py --date 2026-02-15

# Submit monthly timesheet
python tempo_automation.py --submit

# View logs
cat tempo_automation.log           # Mac/Linux
type tempo_automation.log          # Windows

# Search logs for errors
grep ERROR tempo_automation.log    # Mac/Linux
findstr ERROR tempo_automation.log # Windows

# Check Python version
python --version

# Install dependencies
pip install -r requirements.txt

# Reinstall dependencies
pip install -r requirements.txt --upgrade --force-reinstall
```

---

## REMEMBER WHEN HELPING

1. **Always check logs first:** Most issues are visible in tempo_automation.log
2. **Verify API tokens:** Many errors are authentication-related
3. **Test incrementally:** Fix one thing, test, then move to next
4. **Keep user context:** Remember user is at lmsportal.atlassian.net
5. **Use fallbacks:** Code should degrade gracefully if APIs fail
6. **Log everything:** Better to have too much logging than too little
7. **Consider all roles:** Solutions should work for developers, POs, and sales
8. **Security first:** Never log API tokens or passwords
9. **User experience:** Error messages should be clear and actionable
10. **Document changes:** Update this file when making significant changes

---

## CONTACT & SUPPORT

- **Project Owner:** Ajay (Frontend Team Lead, Vector Solutions)
- **Team:** Frontend Team B (TargetSolutions Shield project)
- **Organization Size:** 200 people (150 dev, 30 PO, 20 sales)
- **Expected ROI:** $1.2M annual savings, 15 min/day per person

---

**This file provides complete context for all future Claude interactions. Refer to it when:**
- Starting a new debugging session
- Adding new features
- Reviewing code
- Answering questions about the project
- Onboarding new team members

*Last updated: February 12, 2026*
