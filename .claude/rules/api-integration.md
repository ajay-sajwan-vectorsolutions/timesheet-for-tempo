# API Integration Reference

## Jira REST API v3

**Base URL:** `https://lmsportal.atlassian.net/rest/api/3/`
**Auth:** Basic auth (email + API token, base64 encoded)
**Config key:** `config['jira']['api_token']`

### Endpoints
| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/search/jql?jql={query}` | Search issues by JQL |
| GET | `/issue/{key}/worklog` | Get worklogs for issue |
| POST | `/issue/{key}/worklog` | Create worklog (ADF comment format) |
| DELETE | `/issue/{key}/worklog/{id}` | Delete worklog (for overwrite) |
| GET | `/issue/{key}?fields=summary,description,comment` | Get issue details |

### Key JQL Queries
- Active issues: `assignee = currentUser() AND status IN ("IN DEVELOPMENT", "CODE REVIEW")`
- Historical: `assignee = currentUser() AND status WAS "IN DEVELOPMENT" ON "YYYY-MM-DD"`
- Worklogs: `worklogAuthor = currentUser() AND worklogDate >= "YYYY-MM-DD"`

### ADF (Atlassian Document Format)
- Jira descriptions and comments use ADF JSON format
- `_extract_adf_text()` recursively extracts plain text from ADF
- Multi-line worklog comments rendered as separate ADF paragraphs

## Tempo API v4

**Base URL:** `https://api.tempo.io/4/`
**Auth:** Bearer token in header
**Config key:** `config['tempo']['api_token']`

### Endpoints
| Method | Endpoint | Purpose |
|--------|----------|---------|
| GET | `/user` | Get current user (accountId, displayName) |
| GET | `/worklogs/user/{accountId}?from=&to=` | Fetch user worklogs |
| POST | `/worklogs` | Create worklog (legacy/manual only) |
| GET | `/timesheet-approvals/periods` | Get timesheet periods |
| POST | `/timesheet-approvals/submit` | Submit timesheet for approval |

### Account ID
- Format: `712020:uuid` (e.g., `712020:66c372bc-e38f-414e-b5d3-fd8ff7513a44`)
- Retrieved from `GET /user` during setup

## Critical Patterns
- **No direct Tempo writes for developers** -- write to Jira, Tempo auto-syncs
- **Overwrite behavior:** delete all worklogs for date, then create new ones
- **Hour distribution:** integer division + remainder on last ticket = exact total
- **Smart descriptions:** built from ticket description + recent comments (1-3 lines)

## Token Generation URLs
- Tempo: https://app.tempo.io/ -> Settings -> API Integration
- Jira: https://id.atlassian.com/manage-profile/security/api-tokens
