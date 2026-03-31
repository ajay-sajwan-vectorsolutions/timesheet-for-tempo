"""
Shared test fixtures for Tempo Timesheet Automation.

Provides config builders, API response factories, and mock helpers
used across all test modules.
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Path helpers -- ensure the project root is importable
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Config builders
# ---------------------------------------------------------------------------


@pytest.fixture
def developer_config():
    """Valid developer role configuration."""
    return {
        "user": {
            "email": "dev@example.com",
            "name": "Test Developer",
            "role": "developer",
        },
        "jira": {
            "url": "test.atlassian.net",
            "email": "dev@example.com",
            "api_token": "jira-test-token",
        },
        "tempo": {"api_token": "tempo-test-token"},
        "organization": {
            "default_issue_key": "DEFAULT-1",
            "holidays_url": "",
        },
        "schedule": {
            "daily_hours": 8.0,
            "daily_sync_time": "18:00",
            "monthly_submit_day": "last",
            "country_code": "US",
            "state": "",
            "pto_days": [],
            "extra_holidays": [],
            "working_days": [],
        },
        "notifications": {
            "email_enabled": False,
            "smtp_server": "smtp.office365.com",
            "smtp_port": 587,
            "smtp_user": "dev@example.com",
            "smtp_password": "",
            "notification_email": "dev@example.com",
            "teams_webhook_url": "",
            "notify_on_shortfall": True,
        },
        "overhead": {
            "current_pi": {
                "pi_identifier": "PI.26.1.JAN.30",
                "pi_end_date": "2026-01-30",
                "stories": [
                    {
                        "issue_key": "OVERHEAD-10",
                        "summary": "Scrum Ceremonies",
                        "hours": 2,
                    }
                ],
                "distribution": "single",
            },
            "pto_story_key": "OVERHEAD-2",
            "planning_pi": {},
            "daily_overhead_hours": 2,
            "fallback_issue_key": "DEFAULT-1",
            "project_prefix": "OVERHEAD-",
        },
        "manual_activities": [],
        "options": {
            "auto_submit": True,
            "require_confirmation": False,
            "sync_on_startup": False,
        },
    }


@pytest.fixture
def po_config(developer_config):
    """Product Owner role config (Tempo only, manual activities)."""
    config = dict(developer_config)
    config["user"] = {
        "email": "po@example.com",
        "name": "Test PO",
        "role": "product_owner",
    }
    config["jira"] = {
        "url": "test.atlassian.net",
        "email": "po@example.com",
        "api_token": "",
    }
    config["manual_activities"] = [
        {"activity": "Stakeholder Meetings", "hours": 3},
        {"activity": "Backlog Refinement", "hours": 2},
        {"activity": "Sprint Planning", "hours": 3},
    ]
    return config


@pytest.fixture
def config_file(tmp_path, developer_config):
    """Write developer config to temp file, return Path."""
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(developer_config, indent=2), encoding="utf-8")
    return config_path


@pytest.fixture
def org_holidays_data():
    """Org holidays fixture data (parsed JSON)."""
    path = FIXTURES_DIR / "org_holidays.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# API response factories
# ---------------------------------------------------------------------------


@pytest.fixture
def jira_myself_response():
    """GET /rest/api/3/myself response."""
    return {
        "accountId": "712020:test-uuid-1234",
        "emailAddress": "dev@example.com",
        "displayName": "Test Developer",
    }


@pytest.fixture
def jira_active_issues_response():
    """GET /rest/api/3/search/jql (active issues) response."""
    return {
        "issues": [
            {
                "key": "PROJ-101",
                "fields": {
                    "summary": "Implement user authentication",
                    "status": {"name": "IN DEVELOPMENT"},
                },
            },
            {
                "key": "PROJ-102",
                "fields": {
                    "summary": "Add search functionality",
                    "status": {"name": "CODE REVIEW"},
                },
            },
        ]
    }


@pytest.fixture
def jira_worklogs_response():
    """GET /rest/api/3/issue/{key}/worklog response."""
    return {
        "worklogs": [
            {
                "id": "10001",
                "author": {
                    "accountId": "712020:test-uuid-1234",
                    "emailAddress": "dev@example.com",
                },
                "timeSpentSeconds": 10800,
                "started": "2026-02-23T09:00:00.000+0000",
                "comment": {
                    "type": "doc",
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": "Worked on auth module"}],
                        }
                    ],
                },
            }
        ]
    }


@pytest.fixture
def jira_issue_details_response():
    """GET /rest/api/3/issue/{key} response with ADF."""
    return {
        "key": "PROJ-101",
        "fields": {
            "summary": "Implement user authentication",
            "description": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [
                            {
                                "type": "text",
                                "text": "Add JWT-based auth to the API layer.",
                            }
                        ],
                    },
                    {
                        "type": "paragraph",
                        "content": [
                            {
                                "type": "text",
                                "text": "Must support refresh tokens.",
                            }
                        ],
                    },
                ],
            },
            "comment": {
                "comments": [
                    {
                        "body": {
                            "type": "doc",
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [
                                        {
                                            "type": "text",
                                            "text": "PR ready for review",
                                        }
                                    ],
                                }
                            ],
                        }
                    }
                ]
            },
        },
    }


@pytest.fixture
def jira_overhead_stories_response():
    """GET /rest/api/3/search/jql (OVERHEAD project) response."""
    return {
        "issues": [
            {
                "key": "OVERHEAD-10",
                "fields": {
                    "summary": "PI.26.1.JAN.30 - Scrum Ceremonies",
                    "sprint": {"name": "PI.26.1.JAN.30 Sprint 5"},
                },
            },
            {
                "key": "OVERHEAD-11",
                "fields": {
                    "summary": "PI.26.1.JAN.30 - Team Meetings",
                    "sprint": None,
                },
            },
        ]
    }


@pytest.fixture
def tempo_worklogs_response():
    """GET /worklogs/user/{id} response."""
    return {
        "results": [
            {
                "tempoWorklogId": 5001,
                "issue": {"id": 10101},
                "timeSpentSeconds": 10800,
                "startDate": "2026-02-23",
                "description": "Worked on auth module",
                "author": {"accountId": "712020:test-uuid-1234"},
            }
        ]
    }


# ---------------------------------------------------------------------------
# Module-level patching helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def patch_org_holidays_file(tmp_path, org_holidays_data):
    """Write org_holidays.json to tmp_path and patch ORG_HOLIDAYS_FILE."""
    hol_path = tmp_path / "org_holidays.json"
    hol_path.write_text(json.dumps(org_holidays_data, indent=2), encoding="utf-8")
    with patch("tempo_automation.ORG_HOLIDAYS_FILE", hol_path):
        yield hol_path


@pytest.fixture
def patch_config_file(tmp_path):
    """Patch CONFIG_FILE to point to tmp_path."""
    cfg_path = tmp_path / "config.json"
    with patch("tempo_automation.CONFIG_FILE", cfg_path):
        yield cfg_path
