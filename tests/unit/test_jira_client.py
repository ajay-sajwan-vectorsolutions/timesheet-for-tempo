"""
Unit tests for the JiraClient class in tempo_automation.py.

Coverage:
  - Constructor / __init__ (including get_myself_account_id call)
  - get_myself_account_id
  - get_my_worklogs (date filtering, author matching by email and accountId)
  - delete_worklog
  - get_my_active_issues
  - get_issues_in_status_on_date
  - get_issue_details (including ADF extraction)
  - get_overhead_stories (sprint as dict, list, None; PI from sprint vs summary)
  - _extract_adf_text (static method, multiple input shapes)
  - create_worklog (with / without comment, multi-line comment -> ADF paragraphs)
  - Error / HTTP 4xx / network-timeout paths for every public method

All HTTP calls are intercepted by the `responses` library so no real network
traffic is generated.
"""

import json

import pytest
import responses as responses_lib
import requests

from tempo_automation import JiraClient

# ---------------------------------------------------------------------------
# Constants used throughout the tests
# ---------------------------------------------------------------------------
BASE_URL = "https://test.atlassian.net"
MYSELF_URL = f"{BASE_URL}/rest/api/3/myself"
SEARCH_JQL_URL = f"{BASE_URL}/rest/api/3/search/jql"
ACCOUNT_ID = "712020:test-uuid-1234"


# ---------------------------------------------------------------------------
# Helper: register the /myself stub that __init__ calls automatically
# ---------------------------------------------------------------------------
def _register_myself(account_id: str = ACCOUNT_ID):
    """Add a GET /myself stub that returns the given accountId."""
    responses_lib.add(
        responses_lib.GET,
        MYSELF_URL,
        json={"accountId": account_id, "emailAddress": "dev@example.com"},
        status=200,
    )


# ---------------------------------------------------------------------------
# Helper: build a JiraClient inside an active responses mock context.
# Callers must already be inside @responses.activate or responses_lib.activate.
# ---------------------------------------------------------------------------
def _make_client(developer_config):
    """Register /myself and return a JiraClient instance."""
    _register_myself()
    return JiraClient(developer_config)


# ===========================================================================
# Construction / get_myself_account_id
# ===========================================================================

class TestInit:
    @responses_lib.activate
    def test_constructor_sets_base_url(self, developer_config):
        client = _make_client(developer_config)
        assert client.base_url == BASE_URL

    @responses_lib.activate
    def test_constructor_stores_email(self, developer_config):
        client = _make_client(developer_config)
        assert client.email == "dev@example.com"

    @responses_lib.activate
    def test_constructor_fetches_account_id(self, developer_config):
        client = _make_client(developer_config)
        assert client.account_id == ACCOUNT_ID

    @responses_lib.activate
    def test_constructor_account_id_empty_on_401(self, developer_config):
        """When /myself returns 401, account_id should fall back to ''."""
        responses_lib.add(responses_lib.GET, MYSELF_URL, status=401)
        client = JiraClient(developer_config)
        assert client.account_id == ""

    @responses_lib.activate
    def test_constructor_account_id_empty_on_network_error(self, developer_config):
        """Network-level exception -> account_id defaults to ''."""
        responses_lib.add(
            responses_lib.GET,
            MYSELF_URL,
            body=requests.exceptions.ConnectionError("unreachable"),
        )
        client = JiraClient(developer_config)
        assert client.account_id == ""

    @responses_lib.activate
    def test_constructor_account_id_empty_when_missing_from_response(
        self, developer_config
    ):
        """Response 200 but accountId key absent -> account_id == ''."""
        responses_lib.add(
            responses_lib.GET,
            MYSELF_URL,
            json={"displayName": "Test Developer"},
            status=200,
        )
        client = JiraClient(developer_config)
        assert client.account_id == ""

    @responses_lib.activate
    def test_session_uses_basic_auth(self, developer_config):
        client = _make_client(developer_config)
        assert client.session.auth == ("dev@example.com", "jira-test-token")


# ===========================================================================
# get_my_worklogs
# ===========================================================================

class TestGetMyWorklogs:

    # ------------------------------------------------------------------
    # Convenience: builds a full JQL-search + worklog stub pair
    # ------------------------------------------------------------------
    def _register_search_and_worklogs(
        self, issue_key: str, worklog_response: dict
    ):
        """Register the JQL search stub (one issue) + its worklog stub."""
        responses_lib.add(
            responses_lib.GET,
            SEARCH_JQL_URL,
            json={
                "issues": [
                    {
                        "key": issue_key,
                        "fields": {"summary": "Test issue summary"},
                    }
                ]
            },
            status=200,
        )
        responses_lib.add(
            responses_lib.GET,
            f"{BASE_URL}/rest/api/3/issue/{issue_key}/worklog",
            json=worklog_response,
            status=200,
        )

    @responses_lib.activate
    def test_returns_worklog_matching_by_email(
        self, developer_config, jira_worklogs_response
    ):
        client = _make_client(developer_config)
        self._register_search_and_worklogs("PROJ-101", jira_worklogs_response)

        result = client.get_my_worklogs("2026-02-23", "2026-02-23")

        assert len(result) == 1
        assert result[0]["issue_key"] == "PROJ-101"
        assert result[0]["time_spent_seconds"] == 10800

    @responses_lib.activate
    def test_returns_worklog_matching_by_account_id_only(
        self, developer_config
    ):
        """Author has no emailAddress but accountId matches -> still included."""
        client = _make_client(developer_config)
        worklog_resp = {
            "worklogs": [
                {
                    "id": "20001",
                    "author": {
                        "accountId": ACCOUNT_ID,
                        # no emailAddress key
                    },
                    "timeSpentSeconds": 3600,
                    "started": "2026-02-23T09:00:00.000+0000",
                }
            ]
        }
        self._register_search_and_worklogs("PROJ-103", worklog_resp)

        result = client.get_my_worklogs("2026-02-23", "2026-02-23")
        assert len(result) == 1
        assert result[0]["worklog_id"] == "20001"

    @responses_lib.activate
    def test_excludes_worklog_from_different_author(self, developer_config):
        """Worklog by a different user should be filtered out."""
        client = _make_client(developer_config)
        worklog_resp = {
            "worklogs": [
                {
                    "id": "30001",
                    "author": {
                        "accountId": "999999:other-user",
                        "emailAddress": "other@example.com",
                    },
                    "timeSpentSeconds": 3600,
                    "started": "2026-02-23T09:00:00.000+0000",
                }
            ]
        }
        self._register_search_and_worklogs("PROJ-104", worklog_resp)

        result = client.get_my_worklogs("2026-02-23", "2026-02-23")
        assert result == []

    @responses_lib.activate
    def test_excludes_worklog_outside_date_range(self, developer_config):
        """Worklog on a date outside the query window must be dropped."""
        client = _make_client(developer_config)
        worklog_resp = {
            "worklogs": [
                {
                    "id": "40001",
                    "author": {
                        "accountId": ACCOUNT_ID,
                        "emailAddress": "dev@example.com",
                    },
                    "timeSpentSeconds": 3600,
                    "started": "2026-02-10T09:00:00.000+0000",  # outside range
                }
            ]
        }
        self._register_search_and_worklogs("PROJ-105", worklog_resp)

        result = client.get_my_worklogs("2026-02-23", "2026-02-23")
        assert result == []

    @responses_lib.activate
    def test_returns_empty_list_on_jql_error(self, developer_config):
        client = _make_client(developer_config)
        responses_lib.add(responses_lib.GET, SEARCH_JQL_URL, status=500)

        result = client.get_my_worklogs("2026-02-23", "2026-02-23")
        assert result == []

    @responses_lib.activate
    def test_returns_empty_list_when_no_issues(self, developer_config):
        client = _make_client(developer_config)
        responses_lib.add(
            responses_lib.GET,
            SEARCH_JQL_URL,
            json={"issues": []},
            status=200,
        )

        result = client.get_my_worklogs("2026-02-23", "2026-02-23")
        assert result == []

    @responses_lib.activate
    def test_worklog_dict_has_expected_keys(
        self, developer_config, jira_worklogs_response
    ):
        client = _make_client(developer_config)
        self._register_search_and_worklogs("PROJ-101", jira_worklogs_response)

        result = client.get_my_worklogs("2026-02-23", "2026-02-23")
        wl = result[0]
        for key in (
            "worklog_id",
            "issue_key",
            "issue_summary",
            "time_spent_seconds",
            "started",
            "comment",
        ):
            assert key in wl, f"Expected key '{key}' missing from worklog dict"


# ===========================================================================
# delete_worklog
# ===========================================================================

class TestDeleteWorklog:

    @responses_lib.activate
    def test_returns_true_on_204(self, developer_config):
        client = _make_client(developer_config)
        responses_lib.add(
            responses_lib.DELETE,
            f"{BASE_URL}/rest/api/3/issue/PROJ-101/worklog/10001",
            status=204,
        )
        assert client.delete_worklog("PROJ-101", "10001") is True

    @responses_lib.activate
    def test_returns_false_on_404(self, developer_config):
        client = _make_client(developer_config)
        responses_lib.add(
            responses_lib.DELETE,
            f"{BASE_URL}/rest/api/3/issue/PROJ-101/worklog/99999",
            status=404,
        )
        assert client.delete_worklog("PROJ-101", "99999") is False

    @responses_lib.activate
    def test_returns_false_on_network_error(self, developer_config):
        client = _make_client(developer_config)
        responses_lib.add(
            responses_lib.DELETE,
            f"{BASE_URL}/rest/api/3/issue/PROJ-101/worklog/10001",
            body=requests.exceptions.ConnectionError("no route to host"),
        )
        assert client.delete_worklog("PROJ-101", "10001") is False


# ===========================================================================
# get_my_active_issues
# ===========================================================================

class TestGetMyActiveIssues:

    @responses_lib.activate
    def test_returns_list_of_issue_dicts(
        self, developer_config, jira_active_issues_response
    ):
        client = _make_client(developer_config)
        responses_lib.add(
            responses_lib.GET,
            SEARCH_JQL_URL,
            json=jira_active_issues_response,
            status=200,
        )

        result = client.get_my_active_issues()

        assert len(result) == 2
        assert result[0]["issue_key"] == "PROJ-101"
        assert result[0]["issue_summary"] == "Implement user authentication"
        assert result[1]["issue_key"] == "PROJ-102"

    @responses_lib.activate
    def test_returns_empty_list_when_no_tickets(self, developer_config):
        client = _make_client(developer_config)
        responses_lib.add(
            responses_lib.GET,
            SEARCH_JQL_URL,
            json={"issues": []},
            status=200,
        )
        assert client.get_my_active_issues() == []

    @responses_lib.activate
    def test_returns_empty_list_on_http_error(self, developer_config):
        client = _make_client(developer_config)
        responses_lib.add(
            responses_lib.GET, SEARCH_JQL_URL, status=401
        )
        assert client.get_my_active_issues() == []

    @responses_lib.activate
    def test_returns_empty_list_on_timeout(self, developer_config):
        client = _make_client(developer_config)
        responses_lib.add(
            responses_lib.GET,
            SEARCH_JQL_URL,
            body=requests.exceptions.Timeout("timed out"),
        )
        assert client.get_my_active_issues() == []


# ===========================================================================
# get_issues_in_status_on_date
# ===========================================================================

class TestGetIssuesInStatusOnDate:

    @responses_lib.activate
    def test_returns_issues_for_historical_date(
        self, developer_config, jira_active_issues_response
    ):
        client = _make_client(developer_config)
        responses_lib.add(
            responses_lib.GET,
            SEARCH_JQL_URL,
            json=jira_active_issues_response,
            status=200,
        )

        result = client.get_issues_in_status_on_date("2026-02-15")

        assert len(result) == 2
        keys = [r["issue_key"] for r in result]
        assert "PROJ-101" in keys
        assert "PROJ-102" in keys

    @responses_lib.activate
    def test_jql_contains_was_on_clause(self, developer_config):
        """Verify the JQL sent to the API uses historical WAS ... ON syntax."""
        client = _make_client(developer_config)
        responses_lib.add(
            responses_lib.GET,
            SEARCH_JQL_URL,
            json={"issues": []},
            status=200,
        )

        client.get_issues_in_status_on_date("2026-02-15")

        # The request's params must contain the historical JQL pattern
        actual_params = responses_lib.calls[-1].request.url
        assert "WAS" in actual_params
        assert "2026-02-15" in actual_params

    @responses_lib.activate
    def test_returns_empty_list_on_error(self, developer_config):
        client = _make_client(developer_config)
        responses_lib.add(
            responses_lib.GET, SEARCH_JQL_URL, status=500
        )
        assert client.get_issues_in_status_on_date("2026-02-15") == []


# ===========================================================================
# get_issue_details
# ===========================================================================

class TestGetIssueDetails:

    @responses_lib.activate
    def test_returns_summary_and_description(
        self, developer_config, jira_issue_details_response
    ):
        client = _make_client(developer_config)
        responses_lib.add(
            responses_lib.GET,
            f"{BASE_URL}/rest/api/3/issue/PROJ-101",
            json=jira_issue_details_response,
            status=200,
        )

        result = client.get_issue_details("PROJ-101")

        assert result is not None
        assert result["summary"] == "Implement user authentication"
        # Both paragraphs should be present in the extracted text
        assert "JWT-based auth" in result["description_text"]
        assert "refresh tokens" in result["description_text"]

    @responses_lib.activate
    def test_returns_recent_comments(
        self, developer_config, jira_issue_details_response
    ):
        client = _make_client(developer_config)
        responses_lib.add(
            responses_lib.GET,
            f"{BASE_URL}/rest/api/3/issue/PROJ-101",
            json=jira_issue_details_response,
            status=200,
        )

        result = client.get_issue_details("PROJ-101")

        assert result is not None
        assert len(result["recent_comments"]) == 1
        assert "PR ready for review" in result["recent_comments"][0]

    @responses_lib.activate
    def test_returns_none_on_404(self, developer_config):
        client = _make_client(developer_config)
        responses_lib.add(
            responses_lib.GET,
            f"{BASE_URL}/rest/api/3/issue/MISSING-1",
            status=404,
        )
        assert client.get_issue_details("MISSING-1") is None

    @responses_lib.activate
    def test_returns_none_on_network_error(self, developer_config):
        client = _make_client(developer_config)
        responses_lib.add(
            responses_lib.GET,
            f"{BASE_URL}/rest/api/3/issue/PROJ-101",
            body=requests.exceptions.ConnectionError("no route"),
        )
        assert client.get_issue_details("PROJ-101") is None

    @responses_lib.activate
    def test_handles_missing_description(self, developer_config):
        """Issue with null description should not raise and returns '' for it."""
        client = _make_client(developer_config)
        responses_lib.add(
            responses_lib.GET,
            f"{BASE_URL}/rest/api/3/issue/PROJ-200",
            json={
                "key": "PROJ-200",
                "fields": {
                    "summary": "No description issue",
                    "description": None,
                    "comment": {"comments": []},
                },
            },
            status=200,
        )

        result = client.get_issue_details("PROJ-200")

        assert result is not None
        assert result["description_text"] == ""
        assert result["recent_comments"] == []

    @responses_lib.activate
    def test_only_last_3_comments_returned(self, developer_config):
        """If an issue has >3 comments, only the last 3 should be present."""
        comments = [
            {
                "body": {
                    "type": "doc",
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": f"Comment {i}"}],
                        }
                    ],
                }
            }
            for i in range(1, 6)  # 5 comments
        ]
        client = _make_client(developer_config)
        responses_lib.add(
            responses_lib.GET,
            f"{BASE_URL}/rest/api/3/issue/PROJ-300",
            json={
                "key": "PROJ-300",
                "fields": {
                    "summary": "Five comments",
                    "description": None,
                    "comment": {"comments": comments},
                },
            },
            status=200,
        )

        result = client.get_issue_details("PROJ-300")

        assert result is not None
        assert len(result["recent_comments"]) == 3
        # Should be the last 3 comments (3, 4, 5)
        assert "Comment 3" in result["recent_comments"][0]
        assert "Comment 5" in result["recent_comments"][2]


# ===========================================================================
# get_overhead_stories
# ===========================================================================

class TestGetOverheadStories:

    @responses_lib.activate
    def test_returns_stories_with_pi_from_sprint_dict(
        self, developer_config, jira_overhead_stories_response
    ):
        client = _make_client(developer_config)
        responses_lib.add(
            responses_lib.GET,
            SEARCH_JQL_URL,
            json=jira_overhead_stories_response,
            status=200,
        )

        result = client.get_overhead_stories()

        assert len(result) == 2
        # OVERHEAD-10 has sprint dict with PI in name
        oh10 = next(r for r in result if r["issue_key"] == "OVERHEAD-10")
        assert oh10["pi_identifier"] == "PI.26.1.JAN.30"

    @responses_lib.activate
    def test_extracts_pi_from_summary_when_sprint_is_none(
        self, developer_config, jira_overhead_stories_response
    ):
        """OVERHEAD-11 has sprint=None, PI must be extracted from summary."""
        client = _make_client(developer_config)
        responses_lib.add(
            responses_lib.GET,
            SEARCH_JQL_URL,
            json=jira_overhead_stories_response,
            status=200,
        )

        result = client.get_overhead_stories()

        oh11 = next(r for r in result if r["issue_key"] == "OVERHEAD-11")
        assert oh11["pi_identifier"] == "PI.26.1.JAN.30"

    @responses_lib.activate
    def test_extracts_pi_from_sprint_as_list(self, developer_config):
        """Sprint field as list -> use last element's name for PI extraction."""
        client = _make_client(developer_config)
        responses_lib.add(
            responses_lib.GET,
            SEARCH_JQL_URL,
            json={
                "issues": [
                    {
                        "key": "OVERHEAD-20",
                        "fields": {
                            "summary": "No PI here",
                            "sprint": [
                                {"name": "Old Sprint"},
                                {"name": "PI.26.2.APR.17 Sprint 1"},
                            ],
                        },
                    }
                ]
            },
            status=200,
        )

        result = client.get_overhead_stories()

        assert len(result) == 1
        assert result[0]["pi_identifier"] == "PI.26.2.APR.17"

    @responses_lib.activate
    def test_pi_identifier_empty_when_no_pattern_found(self, developer_config):
        """Story with no PI pattern anywhere -> pi_identifier is ''."""
        client = _make_client(developer_config)
        responses_lib.add(
            responses_lib.GET,
            SEARCH_JQL_URL,
            json={
                "issues": [
                    {
                        "key": "OVERHEAD-30",
                        "fields": {
                            "summary": "General overhead, no PI",
                            "sprint": None,
                        },
                    }
                ]
            },
            status=200,
        )

        result = client.get_overhead_stories()

        assert result[0]["pi_identifier"] == ""

    @responses_lib.activate
    def test_returns_empty_list_on_http_error(self, developer_config):
        client = _make_client(developer_config)
        responses_lib.add(
            responses_lib.GET, SEARCH_JQL_URL, status=403
        )
        assert client.get_overhead_stories() == []


# ===========================================================================
# _extract_adf_text (static method)
# ===========================================================================

class TestExtractAdfText:
    """Tests for the static ADF text extraction helper."""

    def test_simple_text_node(self):
        adf = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "Hello world"}],
                }
            ],
        }
        assert JiraClient._extract_adf_text(adf) == "Hello world"

    def test_multiple_paragraphs(self):
        adf = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "First."}],
                },
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "Second."}],
                },
            ],
        }
        result = JiraClient._extract_adf_text(adf)
        assert "First." in result
        assert "Second." in result

    def test_deeply_nested_content(self):
        """Text buried several levels deep must still be found."""
        adf = {
            "type": "doc",
            "content": [
                {
                    "type": "bulletList",
                    "content": [
                        {
                            "type": "listItem",
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [
                                        {"type": "text", "text": "Nested item"}
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ],
        }
        assert JiraClient._extract_adf_text(adf) == "Nested item"

    def test_returns_empty_string_for_none(self):
        assert JiraClient._extract_adf_text(None) == ""

    def test_returns_empty_string_for_empty_dict(self):
        assert JiraClient._extract_adf_text({}) == ""

    def test_returns_empty_string_for_non_dict(self):
        # Strings, lists, integers should all return ''
        assert JiraClient._extract_adf_text("plain string") == ""
        assert JiraClient._extract_adf_text(42) == ""
        assert JiraClient._extract_adf_text([]) == ""

    def test_mixed_node_types_ignored_gracefully(self):
        """Non-text node types (e.g. hardBreak) should not raise."""
        adf = {
            "type": "doc",
            "content": [
                {"type": "hardBreak"},
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "After break"}],
                },
            ],
        }
        result = JiraClient._extract_adf_text(adf)
        assert "After break" in result

    def test_multiple_inline_text_nodes(self):
        """Paragraph with bold + plain text inline nodes."""
        adf = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": "Bold "},
                        {"type": "text", "text": "and plain"},
                    ],
                }
            ],
        }
        result = JiraClient._extract_adf_text(adf)
        assert "Bold" in result
        assert "and plain" in result

    def test_content_with_no_text_nodes(self):
        """Doc that contains only structural nodes and no text -> ''."""
        adf = {
            "type": "doc",
            "content": [{"type": "rule"}],  # horizontal rule, no text
        }
        assert JiraClient._extract_adf_text(adf) == ""


# ===========================================================================
# create_worklog
# ===========================================================================

class TestCreateWorklog:

    @responses_lib.activate
    def test_returns_worklog_id_on_success(self, developer_config):
        client = _make_client(developer_config)
        responses_lib.add(
            responses_lib.POST,
            f"{BASE_URL}/rest/api/3/issue/PROJ-101/worklog",
            json={"id": "50001"},
            status=201,
        )

        result = client.create_worklog("PROJ-101", 28800, "2026-02-23")
        assert result == "50001"

    @responses_lib.activate
    def test_started_formatted_as_iso_datetime(self, developer_config):
        """Payload must contain started in the full ISO 8601 format."""
        client = _make_client(developer_config)
        responses_lib.add(
            responses_lib.POST,
            f"{BASE_URL}/rest/api/3/issue/PROJ-101/worklog",
            json={"id": "50002"},
            status=201,
        )

        client.create_worklog("PROJ-101", 3600, "2026-02-23")

        sent_body = json.loads(responses_lib.calls[-1].request.body)
        assert sent_body["started"] == "2026-02-23T09:00:00.000+0000"

    @responses_lib.activate
    def test_payload_contains_time_spent_seconds(self, developer_config):
        client = _make_client(developer_config)
        responses_lib.add(
            responses_lib.POST,
            f"{BASE_URL}/rest/api/3/issue/PROJ-101/worklog",
            json={"id": "50003"},
            status=201,
        )

        client.create_worklog("PROJ-101", 14400, "2026-02-23")

        sent_body = json.loads(responses_lib.calls[-1].request.body)
        assert sent_body["timeSpentSeconds"] == 14400

    @responses_lib.activate
    def test_single_line_comment_becomes_one_adf_paragraph(
        self, developer_config
    ):
        client = _make_client(developer_config)
        responses_lib.add(
            responses_lib.POST,
            f"{BASE_URL}/rest/api/3/issue/PROJ-101/worklog",
            json={"id": "50004"},
            status=201,
        )

        client.create_worklog("PROJ-101", 3600, "2026-02-23", comment="Fixed bug")

        sent_body = json.loads(responses_lib.calls[-1].request.body)
        assert "comment" in sent_body
        comment = sent_body["comment"]
        assert comment["type"] == "doc"
        assert comment["version"] == 1
        assert len(comment["content"]) == 1
        assert comment["content"][0]["type"] == "paragraph"
        assert comment["content"][0]["content"][0]["text"] == "Fixed bug"

    @responses_lib.activate
    def test_multiline_comment_becomes_multiple_adf_paragraphs(
        self, developer_config
    ):
        client = _make_client(developer_config)
        responses_lib.add(
            responses_lib.POST,
            f"{BASE_URL}/rest/api/3/issue/PROJ-101/worklog",
            json={"id": "50005"},
            status=201,
        )

        multiline = "Line one\nLine two\nLine three"
        client.create_worklog("PROJ-101", 3600, "2026-02-23", comment=multiline)

        sent_body = json.loads(responses_lib.calls[-1].request.body)
        paragraphs = sent_body["comment"]["content"]
        assert len(paragraphs) == 3
        texts = [p["content"][0]["text"] for p in paragraphs]
        assert texts == ["Line one", "Line two", "Line three"]

    @responses_lib.activate
    def test_no_comment_field_in_payload_when_comment_empty(
        self, developer_config
    ):
        client = _make_client(developer_config)
        responses_lib.add(
            responses_lib.POST,
            f"{BASE_URL}/rest/api/3/issue/PROJ-101/worklog",
            json={"id": "50006"},
            status=201,
        )

        client.create_worklog("PROJ-101", 3600, "2026-02-23", comment="")

        sent_body = json.loads(responses_lib.calls[-1].request.body)
        assert "comment" not in sent_body

    @responses_lib.activate
    def test_returns_none_on_http_error(self, developer_config):
        client = _make_client(developer_config)
        responses_lib.add(
            responses_lib.POST,
            f"{BASE_URL}/rest/api/3/issue/PROJ-101/worklog",
            status=403,
        )

        assert client.create_worklog("PROJ-101", 3600, "2026-02-23") is None

    @responses_lib.activate
    def test_returns_none_on_network_error(self, developer_config):
        client = _make_client(developer_config)
        responses_lib.add(
            responses_lib.POST,
            f"{BASE_URL}/rest/api/3/issue/PROJ-101/worklog",
            body=requests.exceptions.Timeout("request timed out"),
        )

        assert client.create_worklog("PROJ-101", 3600, "2026-02-23") is None

    @responses_lib.activate
    def test_blank_comment_lines_are_skipped(self, developer_config):
        """Empty lines in a multi-line comment must not produce empty paragraphs."""
        client = _make_client(developer_config)
        responses_lib.add(
            responses_lib.POST,
            f"{BASE_URL}/rest/api/3/issue/PROJ-101/worklog",
            json={"id": "50007"},
            status=201,
        )

        comment_with_blanks = "First line\n\n\nThird line"
        client.create_worklog(
            "PROJ-101", 3600, "2026-02-23", comment=comment_with_blanks
        )

        sent_body = json.loads(responses_lib.calls[-1].request.body)
        paragraphs = sent_body["comment"]["content"]
        # Only 2 non-empty lines -> 2 paragraphs
        assert len(paragraphs) == 2
        assert paragraphs[0]["content"][0]["text"] == "First line"
        assert paragraphs[1]["content"][0]["text"] == "Third line"
