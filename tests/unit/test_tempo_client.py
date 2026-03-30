"""
Unit tests for the TempoClient class in tempo_automation.py.

Coverage:
  - Constructor / __init__ (Bearer token, account_id parameter, email fallback)
  - get_user_worklogs (success, correct URL/params, HTTP error)
  - create_worklog (success, request body fields, HTTP error, network error)
  - submit_timesheet (success with explicit period key, body structure, error)
  - _get_current_period (matching period, no-match fallback, exception fallback)

All HTTP calls are intercepted by the `responses` library so no real network
traffic is generated.  `freezegun` is used to pin date.today() for the period
tests.
"""

import json

import requests
import responses as responses_lib
from freezegun import freeze_time

from tempo_automation import TempoClient

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
TEMPO_BASE_URL = "https://api.tempo.io/4"
WORKLOGS_URL = f"{TEMPO_BASE_URL}/worklogs"
PERIODS_URL = f"{TEMPO_BASE_URL}/periods"
SUBMIT_URL = f"{TEMPO_BASE_URL}/timesheet-approvals/submit"
WORK_ATTRIBUTES_URL = f"{TEMPO_BASE_URL}/work-attributes"
ACCOUNT_ID = "712020:test-uuid-1234"
USER_EMAIL = "dev@example.com"


# ---------------------------------------------------------------------------
# Helper: build a TempoClient without any HTTP calls (unlike JiraClient,
# __init__ is side-effect free for the network).
# ---------------------------------------------------------------------------
def _make_client(developer_config, account_id: str = ACCOUNT_ID):
    """Return a TempoClient using the shared developer_config fixture."""
    return TempoClient(developer_config, account_id=account_id)


# ===========================================================================
# Construction / __init__
# ===========================================================================


class TestInit:
    def test_bearer_token_set_in_session_headers(self, developer_config):
        """Session Authorization header must use the Bearer scheme."""
        client = _make_client(developer_config)
        auth_header = client.session.headers.get("Authorization")
        assert auth_header == "Bearer tempo-test-token"

    def test_account_id_taken_from_parameter(self, developer_config):
        """When account_id is provided explicitly it should be stored directly."""
        client = TempoClient(developer_config, account_id="explicit-account-id")
        assert client.account_id == "explicit-account-id"

    def test_account_id_falls_back_to_config_user_email(self, developer_config):
        """When account_id is omitted or empty, fall back to config user.email."""
        client = TempoClient(developer_config, account_id="")
        assert client.account_id == USER_EMAIL

    def test_content_type_header_set(self, developer_config):
        """Session must advertise JSON content type."""
        client = _make_client(developer_config)
        assert client.session.headers.get("Content-Type") == "application/json"

    def test_base_url_is_tempo_v4(self, developer_config):
        """base_url must point to the Tempo v4 API."""
        client = _make_client(developer_config)
        assert client.base_url == TEMPO_BASE_URL


# ===========================================================================
# get_user_worklogs
# ===========================================================================


class TestGetUserWorklogs:
    @responses_lib.activate
    def test_returns_results_array_on_success(self, developer_config, tempo_worklogs_response):
        """A 200 response must return the list of worklog dicts."""
        client = _make_client(developer_config)
        responses_lib.add(
            responses_lib.GET,
            f"{TEMPO_BASE_URL}/worklogs/user/{ACCOUNT_ID}",
            json=tempo_worklogs_response,
            status=200,
        )

        result = client.get_user_worklogs("2026-02-01", "2026-02-28")

        assert len(result) == 1
        assert result[0]["tempoWorklogId"] == 5001

    @responses_lib.activate
    def test_url_includes_account_id(self, developer_config):
        """The request URL must embed the account_id path segment."""
        client = _make_client(developer_config)
        responses_lib.add(
            responses_lib.GET,
            f"{TEMPO_BASE_URL}/worklogs/user/{ACCOUNT_ID}",
            json={"results": []},
            status=200,
        )

        client.get_user_worklogs("2026-02-01", "2026-02-28")

        actual_url = responses_lib.calls[-1].request.url
        assert ACCOUNT_ID in actual_url

    @responses_lib.activate
    def test_query_params_include_from_and_to(self, developer_config):
        """The from and to date params must be passed in the query string."""
        client = _make_client(developer_config)
        responses_lib.add(
            responses_lib.GET,
            f"{TEMPO_BASE_URL}/worklogs/user/{ACCOUNT_ID}",
            json={"results": []},
            status=200,
        )

        client.get_user_worklogs("2026-02-01", "2026-02-28")

        actual_url = responses_lib.calls[-1].request.url
        assert "from=2026-02-01" in actual_url
        assert "to=2026-02-28" in actual_url

    @responses_lib.activate
    def test_returns_empty_list_on_http_error(self, developer_config):
        """Any HTTP error (e.g. 401) must return [] rather than raising."""
        client = _make_client(developer_config)
        responses_lib.add(
            responses_lib.GET,
            f"{TEMPO_BASE_URL}/worklogs/user/{ACCOUNT_ID}",
            status=401,
        )

        result = client.get_user_worklogs("2026-02-01", "2026-02-28")

        assert result == []

    @responses_lib.activate
    def test_returns_empty_list_on_network_error(self, developer_config):
        """A ConnectionError must be swallowed and return []."""
        client = _make_client(developer_config)
        responses_lib.add(
            responses_lib.GET,
            f"{TEMPO_BASE_URL}/worklogs/user/{ACCOUNT_ID}",
            body=requests.exceptions.ConnectionError("unreachable"),
        )

        result = client.get_user_worklogs("2026-02-01", "2026-02-28")

        assert result == []


# ===========================================================================
# create_worklog
# ===========================================================================


class TestCreateWorklog:
    @responses_lib.activate
    def test_returns_true_on_success(self, developer_config):
        """A 200/201 response must return True."""
        client = _make_client(developer_config)
        responses_lib.add(
            responses_lib.POST,
            WORKLOGS_URL,
            json={"tempoWorklogId": 9001},
            status=200,
        )

        result = client.create_worklog("PROJ-101", 28800, "2026-02-23", "Sprint work")

        assert result is True

    @responses_lib.activate
    def test_request_body_has_required_fields(self, developer_config):
        """POST body must include all mandatory Tempo worklog fields."""
        client = _make_client(developer_config)
        responses_lib.add(
            responses_lib.POST,
            WORKLOGS_URL,
            json={"tempoWorklogId": 9002},
            status=200,
        )

        client.create_worklog("TS-42", 14400, "2026-02-15", "Code review")

        sent_body = json.loads(responses_lib.calls[-1].request.body)
        assert sent_body["issueKey"] == "TS-42"
        assert sent_body["timeSpentSeconds"] == 14400
        assert sent_body["startDate"] == "2026-02-15"
        assert sent_body["startTime"] == "09:00:00"
        assert sent_body["authorAccountId"] == ACCOUNT_ID
        assert sent_body["description"] == "Code review"

    @responses_lib.activate
    def test_description_empty_string_when_not_provided(self, developer_config):
        """When description is omitted the body must contain an empty string."""
        client = _make_client(developer_config)
        responses_lib.add(
            responses_lib.POST,
            WORKLOGS_URL,
            json={"tempoWorklogId": 9003},
            status=200,
        )

        client.create_worklog("TS-1", 3600, "2026-02-20")

        sent_body = json.loads(responses_lib.calls[-1].request.body)
        assert sent_body["description"] == ""

    @responses_lib.activate
    def test_returns_false_on_http_error(self, developer_config):
        """A 4xx/5xx response must return False."""
        client = _make_client(developer_config)
        responses_lib.add(
            responses_lib.POST,
            WORKLOGS_URL,
            status=403,
        )

        result = client.create_worklog("PROJ-101", 3600, "2026-02-23")

        assert result is False

    @responses_lib.activate
    def test_returns_false_on_network_error(self, developer_config):
        """A ConnectionError must be caught and return False."""
        client = _make_client(developer_config)
        responses_lib.add(
            responses_lib.POST,
            WORKLOGS_URL,
            body=requests.exceptions.ConnectionError("connection refused"),
        )

        result = client.create_worklog("PROJ-101", 3600, "2026-02-23")

        assert result is False

    @responses_lib.activate
    def test_returns_false_on_timeout(self, developer_config):
        """A Timeout exception must be caught and return False."""
        client = _make_client(developer_config)
        responses_lib.add(
            responses_lib.POST,
            WORKLOGS_URL,
            body=requests.exceptions.Timeout("timed out"),
        )

        result = client.create_worklog("PROJ-101", 3600, "2026-02-23")

        assert result is False


# ===========================================================================
# submit_timesheet
# ===========================================================================


class TestSubmitTimesheet:
    @responses_lib.activate
    def test_returns_true_and_body_has_worker_and_period(
        self, developer_config, tempo_periods_response
    ):
        """Successful submit must return True and send the correct JSON body."""
        client = _make_client(developer_config)
        # The submit call uses an explicit period key so no periods lookup needed.
        responses_lib.add(
            responses_lib.POST,
            SUBMIT_URL,
            json={"message": "Submitted"},
            status=200,
        )

        result = client.submit_timesheet(period_key="2026-02")

        assert result is True
        sent_body = json.loads(responses_lib.calls[-1].request.body)
        assert sent_body["worker"]["accountId"] == ACCOUNT_ID
        assert sent_body["period"]["key"] == "2026-02"

    @responses_lib.activate
    def test_returns_false_on_http_error(self, developer_config):
        """A 4xx response on the submit endpoint must return False."""
        client = _make_client(developer_config)
        responses_lib.add(
            responses_lib.POST,
            SUBMIT_URL,
            status=400,
        )

        result = client.submit_timesheet(period_key="2026-02")

        assert result is False

    @responses_lib.activate
    @freeze_time("2026-02-22")
    def test_auto_detects_period_when_not_provided(self, developer_config, tempo_periods_response):
        """When period_key is None the client must call _get_current_period first."""
        client = _make_client(developer_config)
        # Register the periods lookup
        responses_lib.add(
            responses_lib.GET,
            PERIODS_URL,
            json=tempo_periods_response,
            status=200,
        )
        # Register the submit call
        responses_lib.add(
            responses_lib.POST,
            SUBMIT_URL,
            json={"message": "Submitted"},
            status=200,
        )

        result = client.submit_timesheet()

        assert result is True
        # The submit call must use the period found from the periods API
        sent_body = json.loads(responses_lib.calls[-1].request.body)
        assert sent_body["period"]["key"] == "2026-02"


# ===========================================================================
# _get_current_period
# ===========================================================================


class TestGetCurrentPeriod:
    @responses_lib.activate
    @freeze_time("2026-02-22")
    def test_returns_key_of_matching_period(self, developer_config, tempo_periods_response):
        """Period whose dateFrom <= today <= dateTo must be returned."""
        client = _make_client(developer_config)
        responses_lib.add(
            responses_lib.GET,
            PERIODS_URL,
            json=tempo_periods_response,
            status=200,
        )

        result = client._get_current_period()

        assert result == "2026-02"

    @responses_lib.activate
    @freeze_time("2026-03-15")
    def test_returns_fallback_format_when_no_period_matches(self, developer_config):
        """When today falls outside all periods, return 'YYYY-MM' fallback."""
        client = _make_client(developer_config)
        # Return a period that does NOT contain March 15
        responses_lib.add(
            responses_lib.GET,
            PERIODS_URL,
            json={
                "results": [
                    {
                        "key": "2026-02",
                        "dateFrom": "2026-02-01",
                        "dateTo": "2026-02-28",
                        "status": "OPEN",
                    }
                ]
            },
            status=200,
        )

        result = client._get_current_period()

        # Fallback must be "2026-03" since today is frozen to March 2026
        assert result == "2026-03"

    @responses_lib.activate
    @freeze_time("2026-04-05")
    def test_returns_fallback_format_on_http_exception(self, developer_config):
        """Any HTTP error must be caught and the YYYY-MM fallback returned."""
        client = _make_client(developer_config)
        responses_lib.add(
            responses_lib.GET,
            PERIODS_URL,
            status=500,
        )

        result = client._get_current_period()

        assert result == "2026-04"

    @responses_lib.activate
    @freeze_time("2026-05-10")
    def test_returns_fallback_format_on_network_error(self, developer_config):
        """A ConnectionError must be caught and the YYYY-MM fallback returned."""
        client = _make_client(developer_config)
        responses_lib.add(
            responses_lib.GET,
            PERIODS_URL,
            body=requests.exceptions.ConnectionError("no route to host"),
        )

        result = client._get_current_period()

        assert result == "2026-05"

    @responses_lib.activate
    @freeze_time("2026-02-22")
    def test_returns_first_matching_period_when_multiple_exist(self, developer_config):
        """If multiple periods span today, the first match is returned."""
        client = _make_client(developer_config)
        responses_lib.add(
            responses_lib.GET,
            PERIODS_URL,
            json={
                "results": [
                    {
                        "key": "2026-02",
                        "dateFrom": "2026-02-01",
                        "dateTo": "2026-02-28",
                        "status": "OPEN",
                    },
                    {
                        "key": "2026-Q1",
                        "dateFrom": "2026-01-01",
                        "dateTo": "2026-03-31",
                        "status": "OPEN",
                    },
                ]
            },
            status=200,
        )

        result = client._get_current_period()

        # First period in the list that contains today wins
        assert result == "2026-02"

    @responses_lib.activate
    @freeze_time("2026-02-22")
    def test_returns_fallback_when_results_empty(self, developer_config):
        """Empty results list (no periods configured) returns YYYY-MM fallback."""
        client = _make_client(developer_config)
        responses_lib.add(
            responses_lib.GET,
            PERIODS_URL,
            json={"results": []},
            status=200,
        )

        result = client._get_current_period()

        assert result == "2026-02"


# ===========================================================================
# Retry Logic
# ===========================================================================


class TestRetryLogic:
    """Tests for HTTP retry configuration on TempoClient and JiraClient."""

    def test_tempo_retry_on_429(self, developer_config):
        """TempoClient session adapter must include 429 in status_forcelist."""
        client = _make_client(developer_config)
        adapter = client.session.get_adapter("https://")
        assert 429 in adapter.max_retries.status_forcelist

    def test_tempo_retry_on_503(self, developer_config):
        """TempoClient session adapter must include 503 in status_forcelist."""
        client = _make_client(developer_config)
        adapter = client.session.get_adapter("https://")
        assert 503 in adapter.max_retries.status_forcelist

    def test_tempo_retry_total_is_3(self, developer_config):
        """TempoClient retry adapter must allow up to 3 total retries."""
        client = _make_client(developer_config)
        adapter = client.session.get_adapter("https://")
        assert adapter.max_retries.total == 3

    def test_tempo_retry_includes_502_and_504(self, developer_config):
        """TempoClient retry config must include 502 and 504."""
        client = _make_client(developer_config)
        adapter = client.session.get_adapter("https://")
        assert 502 in adapter.max_retries.status_forcelist
        assert 504 in adapter.max_retries.status_forcelist

    def test_tempo_retry_backoff_factor(self, developer_config):
        """TempoClient retry config should use exponential backoff."""
        client = _make_client(developer_config)
        adapter = client.session.get_adapter("https://")
        assert adapter.max_retries.backoff_factor >= 1

    @responses_lib.activate
    def test_jira_retry_on_502(self, developer_config):
        """JiraClient session adapter must include 502 in status_forcelist."""
        from tempo_automation import JiraClient

        # Register the /myself endpoint that JiraClient.__init__ calls
        responses_lib.add(
            responses_lib.GET,
            f"https://{developer_config['jira']['url']}/rest/api/3/myself",
            json={"accountId": ACCOUNT_ID, "emailAddress": "dev@example.com"},
            status=200,
        )
        jira = JiraClient(developer_config)
        adapter = jira.session.get_adapter("https://")
        assert 502 in adapter.max_retries.status_forcelist

    @responses_lib.activate
    def test_jira_retry_on_504(self, developer_config):
        """JiraClient session adapter must include 504 in status_forcelist."""
        from tempo_automation import JiraClient

        responses_lib.add(
            responses_lib.GET,
            f"https://{developer_config['jira']['url']}/rest/api/3/myself",
            json={"accountId": ACCOUNT_ID, "emailAddress": "dev@example.com"},
            status=200,
        )
        jira = JiraClient(developer_config)
        adapter = jira.session.get_adapter("https://")
        assert 504 in adapter.max_retries.status_forcelist

    @responses_lib.activate
    def test_jira_retry_total_is_3(self, developer_config):
        """JiraClient retry adapter must allow up to 3 total retries."""
        from tempo_automation import JiraClient

        responses_lib.add(
            responses_lib.GET,
            f"https://{developer_config['jira']['url']}/rest/api/3/myself",
            json={"accountId": ACCOUNT_ID, "emailAddress": "dev@example.com"},
            status=200,
        )
        jira = JiraClient(developer_config)
        adapter = jira.session.get_adapter("https://")
        assert adapter.max_retries.total == 3

    def test_tempo_retry_respects_retry_after(self, developer_config):
        """TempoClient retry config must respect Retry-After header."""
        client = _make_client(developer_config)
        adapter = client.session.get_adapter("https://")
        assert adapter.max_retries.respect_retry_after_header is True


# ===========================================================================
# check_forge_status
# ===========================================================================


class TestCheckForgeStatus:
    """Tests for TempoClient.check_forge_status()."""

    @responses_lib.activate
    def test_detects_connect_platform(self, developer_config):
        """When no Forge headers present, platform should be 'connect'."""
        responses_lib.add(
            responses_lib.GET,
            WORK_ATTRIBUTES_URL,
            json={"results": []},
            status=200,
            headers={"Server": "nginx"},
        )
        client = _make_client(developer_config)
        result = client.check_forge_status()

        assert result["platform"] == "connect"
        assert result["healthy"] is True
        assert result["latency_ms"] >= 0

    @responses_lib.activate
    def test_detects_forge_platform(self, developer_config):
        """When Forge headers are present, platform should be 'forge'."""
        responses_lib.add(
            responses_lib.GET,
            WORK_ATTRIBUTES_URL,
            json={"results": []},
            status=200,
            headers={"X-Forge-App": "tempo-timesheets", "Server": "forge"},
        )
        client = _make_client(developer_config)
        result = client.check_forge_status()

        assert result["platform"] == "forge"
        assert result["healthy"] is True
        assert "X-Forge-App" in result["headers"]

    @responses_lib.activate
    def test_unhealthy_on_http_error(self, developer_config):
        """On HTTP error, healthy should be False and platform unknown."""
        responses_lib.add(
            responses_lib.GET,
            WORK_ATTRIBUTES_URL,
            json={"error": "Unauthorized"},
            status=401,
        )
        client = _make_client(developer_config)
        result = client.check_forge_status()

        assert result["healthy"] is False
        assert result["platform"] == "unknown"

    @responses_lib.activate
    def test_unhealthy_on_network_error(self, developer_config):
        """On network error, healthy should be False."""
        responses_lib.add(
            responses_lib.GET,
            WORK_ATTRIBUTES_URL,
            body=requests.ConnectionError("unreachable"),
        )
        client = _make_client(developer_config)
        result = client.check_forge_status()

        assert result["healthy"] is False
        assert result["platform"] == "unknown"

    @responses_lib.activate
    def test_captures_server_header(self, developer_config):
        """Server and Via headers should be captured in result."""
        responses_lib.add(
            responses_lib.GET,
            WORK_ATTRIBUTES_URL,
            json={"results": []},
            status=200,
            headers={"Server": "nginx", "Via": "1.1 cloudfront.net"},
        )
        client = _make_client(developer_config)
        result = client.check_forge_status()

        assert "Server" in result["headers"]
        assert "Via" in result["headers"]


# ===========================================================================
# _forge_error_hint
# ===========================================================================


class TestForgeErrorHint:
    """Tests for TempoClient._forge_error_hint()."""

    def test_hint_on_403(self):
        """403 should mention token regeneration."""
        resp = requests.models.Response()
        resp.status_code = 403
        exc = requests.exceptions.HTTPError(response=resp)
        hint = TempoClient._forge_error_hint(exc)
        assert "403" in hint
        assert "regenerated" in hint

    def test_hint_on_404(self):
        """404 should mention Forge migration."""
        resp = requests.models.Response()
        resp.status_code = 404
        exc = requests.exceptions.HTTPError(response=resp)
        hint = TempoClient._forge_error_hint(exc)
        assert "404" in hint
        assert "help.tempo.io" in hint

    def test_hint_on_502(self):
        """502 should mention Forge migration."""
        resp = requests.models.Response()
        resp.status_code = 502
        exc = requests.exceptions.HTTPError(response=resp)
        hint = TempoClient._forge_error_hint(exc)
        assert "502" in hint

    def test_hint_on_connection_error(self):
        """ConnectionError should mention firewall."""
        exc = requests.exceptions.ConnectionError("refused")
        hint = TempoClient._forge_error_hint(exc)
        assert "firewall" in hint

    def test_hint_on_timeout(self):
        """Timeout should mention firewall."""
        exc = requests.exceptions.Timeout("timed out")
        hint = TempoClient._forge_error_hint(exc)
        assert "firewall" in hint

    def test_no_hint_on_200_range_error(self):
        """Non-Forge status codes should return empty hint."""
        resp = requests.models.Response()
        resp.status_code = 500
        exc = requests.exceptions.HTTPError(response=resp)
        hint = TempoClient._forge_error_hint(exc)
        assert hint == ""

    def test_no_hint_on_generic_exception(self):
        """Generic RequestException without response returns empty hint."""
        exc = requests.exceptions.RequestException("generic")
        hint = TempoClient._forge_error_hint(exc)
        assert hint == ""


# ===========================================================================
# get_timesheet_periods
# ===========================================================================


class TestGetTimesheetPeriods:
    """Tests for TempoClient.get_timesheet_periods()."""

    @responses_lib.activate
    def test_returns_single_period_as_list(self, developer_config):
        """API returning a single dict should be wrapped in a list."""
        url = f"{TEMPO_BASE_URL}/timesheet-approvals/user/{ACCOUNT_ID}"
        responses_lib.add(
            responses_lib.GET,
            url,
            json={
                "period": {"from": "2026-03-01", "to": "2026-03-31"},
                "status": {"key": "OPEN"},
                "requiredSeconds": 633600,
            },
            status=200,
        )
        client = _make_client(developer_config)
        periods = client.get_timesheet_periods("2026-03-01", "2026-03-31")

        assert len(periods) == 1
        assert periods[0]["status"] == {"key": "OPEN"}

    @responses_lib.activate
    def test_returns_empty_on_error(self, developer_config):
        """HTTP error should return empty list."""
        url = f"{TEMPO_BASE_URL}/timesheet-approvals/user/{ACCOUNT_ID}"
        responses_lib.add(
            responses_lib.GET,
            url,
            json={"error": "not found"},
            status=404,
        )
        client = _make_client(developer_config)
        periods = client.get_timesheet_periods("2026-03-01", "2026-03-31")

        assert periods == []
