"""
Unit tests for the NotificationManager and DualWriter classes
in tempo_automation.py.

Coverage:
  - DualWriter: write to console + file, flush, close, append, empty string
  - NotificationManager.__init__: enabled/disabled from config
  - send_daily_summary: skip when disabled, email content, complete/incomplete
  - send_submission_confirmation: skip when disabled, period in subject
  - _send_email: SMTP connection, starttls, login, headers, error handling
  - send_teams_notification: skip when no URL, Adaptive Card, facts, errors
  - send_windows_notification: winotify, MessageBox fallback, Mac osascript
  - send_shortfall_email: skip when disabled, facts table, no facts

All SMTP calls are intercepted with unittest.mock.  HTTP calls for the
Teams webhook are intercepted by the ``responses`` library (imported as
``responses_lib``).
"""

import io
import smtplib
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import responses as responses_lib

# ---------------------------------------------------------------------------
# Ensure project root is importable
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tempo_automation import DualWriter, NotificationManager  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _enabled_config(base_config: dict) -> dict:
    """Return a copy of the config with email_enabled set to True."""
    import copy

    cfg = copy.deepcopy(base_config)
    cfg["notifications"]["email_enabled"] = True
    cfg["notifications"]["smtp_password"] = "plain-password"
    return cfg


def _sample_worklogs() -> list:
    """Return a small list of worklog dicts for send_daily_summary tests."""
    return [
        {
            "issue_key": "PROJ-101",
            "time_spent_seconds": 10800,
            "issue_summary": "Implement auth module",
        },
        {
            "issue_key": "PROJ-102",
            "time_spent_seconds": 7200,
            "issue_summary": "Fix search bug",
        },
    ]


# ===========================================================================
# DualWriter
# ===========================================================================


class TestDualWriter:
    """Tests for the DualWriter class that tees output to console + logfile."""

    def test_write_sends_to_both_console_and_file(self, tmp_path):
        """write() must push the same text to the console stream and file."""
        console = io.StringIO()
        logfile_path = str(tmp_path / "output.log")
        writer = DualWriter(console, logfile_path)

        writer.write("hello world")
        writer.close()

        assert console.getvalue() == "hello world"
        assert Path(logfile_path).read_text(encoding="utf-8") == "hello world"

    def test_flush_flushes_both(self, tmp_path):
        """flush() must call flush on both the console and the logfile."""
        console = MagicMock()
        logfile_path = str(tmp_path / "flush_test.log")
        writer = DualWriter(console, logfile_path)

        writer.write("data")
        writer.flush()

        console.flush.assert_called()
        # The logfile is auto-flushed on every write(); verify it is readable
        assert Path(logfile_path).read_text(encoding="utf-8") == "data"
        writer.close()

    def test_close_closes_logfile(self, tmp_path):
        """close() must close the underlying log file handle."""
        console = io.StringIO()
        logfile_path = str(tmp_path / "close_test.log")
        writer = DualWriter(console, logfile_path)

        writer.write("before close")
        writer.close()

        # After close, the file handle should be closed
        assert writer.logfile.closed

    def test_appends_to_existing_file(self, tmp_path):
        """DualWriter opens in append mode -- existing content is preserved."""
        logfile_path = tmp_path / "append.log"
        logfile_path.write_text("existing\n", encoding="utf-8")

        console = io.StringIO()
        writer = DualWriter(console, str(logfile_path))
        writer.write("new line")
        writer.close()

        content = logfile_path.read_text(encoding="utf-8")
        assert content == "existing\nnew line"

    def test_multiple_writes_accumulate(self, tmp_path):
        """Several write() calls must all appear in order in the file."""
        console = io.StringIO()
        logfile_path = str(tmp_path / "multi.log")
        writer = DualWriter(console, logfile_path)

        writer.write("line1\n")
        writer.write("line2\n")
        writer.write("line3\n")
        writer.close()

        expected = "line1\nline2\nline3\n"
        assert console.getvalue() == expected
        assert Path(logfile_path).read_text(encoding="utf-8") == expected

    def test_write_with_empty_string(self, tmp_path):
        """Writing an empty string should not raise and should not add content."""
        console = io.StringIO()
        logfile_path = str(tmp_path / "empty.log")
        writer = DualWriter(console, logfile_path)

        writer.write("")
        writer.close()

        assert console.getvalue() == ""
        assert Path(logfile_path).read_text(encoding="utf-8") == ""


# ===========================================================================
# NotificationManager -- Init
# ===========================================================================


class TestNotificationManagerInit:
    """Tests for NotificationManager construction."""

    def test_enabled_when_email_enabled_true(self, developer_config):
        cfg = _enabled_config(developer_config)
        nm = NotificationManager(cfg)
        assert nm.enabled is True

    def test_disabled_when_email_enabled_false(self, developer_config):
        nm = NotificationManager(developer_config)
        assert nm.enabled is False

    def test_reads_enabled_from_config(self, developer_config):
        """The .enabled attribute must reflect the config value exactly."""
        # Default fixture has email_enabled = False
        nm_off = NotificationManager(developer_config)
        assert nm_off.enabled == developer_config["notifications"]["email_enabled"]

        cfg_on = _enabled_config(developer_config)
        nm_on = NotificationManager(cfg_on)
        assert nm_on.enabled == cfg_on["notifications"]["email_enabled"]


# ===========================================================================
# send_daily_summary
# ===========================================================================


class TestSendDailySummary:
    """Tests for NotificationManager.send_daily_summary."""

    def test_skips_when_disabled(self, developer_config):
        """When email is disabled, _send_email must NOT be called."""
        nm = NotificationManager(developer_config)
        with patch.object(nm, "_send_email") as mock_send:
            nm.send_daily_summary(_sample_worklogs(), 5.0)
            mock_send.assert_not_called()

    def test_sends_email_when_enabled(self, developer_config):
        """When enabled, _send_email must be called exactly once."""
        cfg = _enabled_config(developer_config)
        nm = NotificationManager(cfg)
        with patch.object(nm, "_send_email") as mock_send:
            nm.send_daily_summary(_sample_worklogs(), 8.0)
            mock_send.assert_called_once()

    def test_includes_worklog_details_in_body(self, developer_config):
        """The HTML body must contain each worklog's issue key and hours."""
        cfg = _enabled_config(developer_config)
        nm = NotificationManager(cfg)
        with patch.object(nm, "_send_email") as mock_send:
            nm.send_daily_summary(_sample_worklogs(), 5.0)

            _, kwargs = mock_send.call_args
            # _send_email(subject, body) -- positional args
            body = mock_send.call_args[0][1]
            assert "PROJ-101" in body
            assert "PROJ-102" in body
            assert "3.00h" in body  # 10800 / 3600
            assert "2.00h" in body  # 7200 / 3600

    def test_shows_complete_status_when_hours_met(self, developer_config):
        """Status should be '[OK] Complete' when total_hours >= daily_hours."""
        cfg = _enabled_config(developer_config)
        nm = NotificationManager(cfg)
        with patch.object(nm, "_send_email") as mock_send:
            nm.send_daily_summary(_sample_worklogs(), 8.0)
            body = mock_send.call_args[0][1]
            assert "[OK] Complete" in body

    def test_shows_incomplete_status_when_hours_short(self, developer_config):
        """Status should be '[!] Incomplete' when total_hours < daily_hours."""
        cfg = _enabled_config(developer_config)
        nm = NotificationManager(cfg)
        with patch.object(nm, "_send_email") as mock_send:
            nm.send_daily_summary(_sample_worklogs(), 5.0)
            body = mock_send.call_args[0][1]
            assert "[!] Incomplete" in body


# ===========================================================================
# send_submission_confirmation
# ===========================================================================


class TestSendSubmissionConfirmation:
    """Tests for NotificationManager.send_submission_confirmation."""

    def test_skips_when_disabled(self, developer_config):
        nm = NotificationManager(developer_config)
        with patch.object(nm, "_send_email") as mock_send:
            nm.send_submission_confirmation("2026-02")
            mock_send.assert_not_called()

    def test_sends_email_when_enabled(self, developer_config):
        cfg = _enabled_config(developer_config)
        nm = NotificationManager(cfg)
        with patch.object(nm, "_send_email") as mock_send:
            nm.send_submission_confirmation("2026-02")
            mock_send.assert_called_once()

    def test_includes_period_in_subject(self, developer_config):
        cfg = _enabled_config(developer_config)
        nm = NotificationManager(cfg)
        with patch.object(nm, "_send_email") as mock_send:
            nm.send_submission_confirmation("2026-02")
            subject = mock_send.call_args[0][0]
            assert "2026-02" in subject


# ===========================================================================
# _send_email (internal SMTP logic)
# ===========================================================================


class TestSendEmail:
    """Tests for NotificationManager._send_email via mocked smtplib."""

    def _make_enabled_nm(self, developer_config):
        """Build NotificationManager with email enabled and a mock-ready config."""
        cfg = _enabled_config(developer_config)
        return NotificationManager(cfg), cfg

    @patch("tempo_automation.CredentialManager.decrypt", return_value="decrypted-password")
    @patch("tempo_automation.smtplib.SMTP")
    def test_connects_to_configured_smtp_server(
        self, mock_smtp_cls, mock_decrypt, developer_config
    ):
        """SMTP should be created with the configured server and port."""
        nm, cfg = self._make_enabled_nm(developer_config)
        mock_server = MagicMock()
        mock_smtp_cls.return_value = mock_server

        nm._send_email("Test Subject", "<html>body</html>")

        mock_smtp_cls.assert_called_once_with(
            cfg["notifications"]["smtp_server"],
            cfg["notifications"]["smtp_port"],
        )

    @patch("tempo_automation.CredentialManager.decrypt", return_value="decrypted-password")
    @patch("tempo_automation.smtplib.SMTP")
    def test_uses_starttls(self, mock_smtp_cls, mock_decrypt, developer_config):
        """SMTP connection must call starttls() for TLS upgrade."""
        nm, _ = self._make_enabled_nm(developer_config)
        mock_server = MagicMock()
        mock_smtp_cls.return_value = mock_server

        nm._send_email("Subject", "<html>body</html>")

        mock_server.starttls.assert_called_once()

    @patch("tempo_automation.CredentialManager.decrypt", return_value="decrypted-password")
    @patch("tempo_automation.smtplib.SMTP")
    def test_logins_with_decrypted_password(self, mock_smtp_cls, mock_decrypt, developer_config):
        """login() must use the smtp_user and the decrypted smtp_password."""
        nm, cfg = self._make_enabled_nm(developer_config)
        mock_server = MagicMock()
        mock_smtp_cls.return_value = mock_server

        nm._send_email("Subject", "<html>body</html>")

        mock_decrypt.assert_called_once_with(
            cfg["notifications"]["smtp_password"], key="smtp_password"
        )
        mock_server.login.assert_called_once_with(
            cfg["notifications"]["smtp_user"],
            "decrypted-password",
        )

    @patch("tempo_automation.CredentialManager.decrypt", return_value="decrypted-password")
    @patch("tempo_automation.smtplib.SMTP")
    def test_sends_html_message(self, mock_smtp_cls, mock_decrypt, developer_config):
        """send_message must be called with a MIMEMultipart containing HTML."""
        nm, _ = self._make_enabled_nm(developer_config)
        mock_server = MagicMock()
        mock_smtp_cls.return_value = mock_server

        nm._send_email("Subject", "<html><body>content</body></html>")

        mock_server.send_message.assert_called_once()
        sent_msg = mock_server.send_message.call_args[0][0]
        assert sent_msg["Subject"] == "Subject"
        # The message payload should contain the HTML body
        payload = sent_msg.get_payload()
        assert len(payload) == 1  # MIMEMultipart('alternative') with one part
        assert "content" in payload[0].get_payload()

    @patch("tempo_automation.CredentialManager.decrypt", return_value="decrypted-password")
    @patch("tempo_automation.smtplib.SMTP")
    def test_logs_error_on_smtp_failure(self, mock_smtp_cls, mock_decrypt, developer_config):
        """An SMTP error should be caught and logged, not re-raised."""
        nm, _ = self._make_enabled_nm(developer_config)
        mock_smtp_cls.side_effect = smtplib.SMTPException("conn refused")

        with patch("tempo_automation.logger") as mock_logger:
            # Should not raise
            nm._send_email("Subject", "<html>body</html>")
            mock_logger.error.assert_called_once()
            assert "conn refused" in str(mock_logger.error.call_args)

    @patch("tempo_automation.CredentialManager.decrypt", return_value="decrypted-password")
    @patch("tempo_automation.smtplib.SMTP")
    def test_sets_correct_from_and_to_headers(self, mock_smtp_cls, mock_decrypt, developer_config):
        """From and To headers must match the config values."""
        nm, cfg = self._make_enabled_nm(developer_config)
        mock_server = MagicMock()
        mock_smtp_cls.return_value = mock_server

        nm._send_email("Subject", "<html>body</html>")

        sent_msg = mock_server.send_message.call_args[0][0]
        assert sent_msg["From"] == cfg["notifications"]["smtp_user"]
        assert sent_msg["To"] == cfg["notifications"]["notification_email"]


# ===========================================================================
# send_teams_notification
# ===========================================================================

TEAMS_WEBHOOK_URL = "https://outlook.office.com/webhook/test-hook"


class TestSendTeamsNotification:
    """Tests for NotificationManager.send_teams_notification."""

    def test_skips_when_no_webhook_url(self, developer_config):
        """If teams_webhook_url is empty, no HTTP request should be made."""
        nm = NotificationManager(developer_config)
        with patch("tempo_automation.requests.post") as mock_post:
            nm.send_teams_notification("Title", "Body")
            mock_post.assert_not_called()

    @responses_lib.activate
    def test_sends_adaptive_card_format(self, developer_config):
        """POST to webhook should include an Adaptive Card attachment."""
        import copy

        cfg = copy.deepcopy(developer_config)
        cfg["notifications"]["teams_webhook_url"] = TEAMS_WEBHOOK_URL
        nm = NotificationManager(cfg)

        responses_lib.add(
            responses_lib.POST,
            TEAMS_WEBHOOK_URL,
            json={"status": "ok"},
            status=200,
        )

        nm.send_teams_notification("Test Title", "Test Body")

        assert len(responses_lib.calls) == 1
        payload = responses_lib.calls[0].request.body
        import json

        data = json.loads(payload)
        assert data["type"] == "message"
        assert len(data["attachments"]) == 1
        card = data["attachments"][0]
        assert card["contentType"] == ("application/vnd.microsoft.card.adaptive")
        assert card["content"]["type"] == "AdaptiveCard"
        assert card["content"]["version"] == "1.4"

        # Body should contain the title and body TextBlocks
        body_blocks = card["content"]["body"]
        assert body_blocks[0]["text"] == "Test Title"
        assert body_blocks[1]["text"] == "Test Body"

    @responses_lib.activate
    def test_includes_facts_in_payload(self, developer_config):
        """When facts are provided, a FactSet block must appear in the card."""
        import copy

        cfg = copy.deepcopy(developer_config)
        cfg["notifications"]["teams_webhook_url"] = TEAMS_WEBHOOK_URL
        nm = NotificationManager(cfg)

        responses_lib.add(
            responses_lib.POST,
            TEAMS_WEBHOOK_URL,
            json={},
            status=200,
        )

        facts = [
            {"title": "Date", "value": "2026-02-22"},
            {"title": "Hours", "value": "8.0"},
        ]
        nm.send_teams_notification("Title", "Body", facts=facts)

        import json

        data = json.loads(responses_lib.calls[0].request.body)
        body_blocks = data["attachments"][0]["content"]["body"]
        # Should have 3 blocks: title, body text, FactSet
        assert len(body_blocks) == 3
        fact_set = body_blocks[2]
        assert fact_set["type"] == "FactSet"
        assert len(fact_set["facts"]) == 2
        assert fact_set["facts"][0]["title"] == "Date"
        assert fact_set["facts"][1]["value"] == "8.0"

    @responses_lib.activate
    def test_logs_error_on_failure(self, developer_config):
        """A non-200 response should be caught and logged as an error."""
        import copy

        cfg = copy.deepcopy(developer_config)
        cfg["notifications"]["teams_webhook_url"] = TEAMS_WEBHOOK_URL
        nm = NotificationManager(cfg)

        responses_lib.add(
            responses_lib.POST,
            TEAMS_WEBHOOK_URL,
            json={"error": "bad request"},
            status=400,
        )

        with patch("tempo_automation.logger") as mock_logger:
            nm.send_teams_notification("Title", "Body")
            mock_logger.error.assert_called_once()

    @responses_lib.activate
    def test_prints_success_message(self, developer_config, capsys):
        """On success, a '[OK] Teams notification sent' message is printed."""
        import copy

        cfg = copy.deepcopy(developer_config)
        cfg["notifications"]["teams_webhook_url"] = TEAMS_WEBHOOK_URL
        nm = NotificationManager(cfg)

        responses_lib.add(
            responses_lib.POST,
            TEAMS_WEBHOOK_URL,
            json={},
            status=200,
        )

        nm.send_teams_notification("Title", "Body")
        captured = capsys.readouterr()
        assert "Teams notification sent" in captured.out


# ===========================================================================
# send_windows_notification
# ===========================================================================


class TestSendWindowsNotification:
    """Tests for NotificationManager.send_windows_notification."""

    @patch("sys.platform", "win32")
    def test_windows_uses_winotify(self, developer_config):
        """On Windows, winotify should be used for toast notifications."""
        nm = NotificationManager(developer_config)

        mock_notification_cls = MagicMock()
        mock_toast = MagicMock()
        mock_notification_cls.return_value = mock_toast

        mock_audio = MagicMock()

        # Patch the import inside the method
        winotify_module = MagicMock()
        winotify_module.Notification = mock_notification_cls
        winotify_module.audio = mock_audio

        with patch.dict("sys.modules", {"winotify": winotify_module}):
            nm.send_windows_notification("Test Title", "Test Body")

        mock_notification_cls.assert_called_once_with(
            app_id="Tempo Automation",
            title="Test Title",
            msg="Test Body",
            duration="long",
        )
        mock_toast.set_audio.assert_called_once_with(mock_audio.Default, loop=False)
        mock_toast.show.assert_called_once()

    @patch("sys.platform", "win32")
    def test_windows_fallback_to_messagebox(self, developer_config):
        """If winotify is unavailable, fall back to ctypes MessageBoxW."""
        nm = NotificationManager(developer_config)

        # Make winotify import raise ImportError
        import builtins

        real_import = builtins.__import__

        mock_windll = MagicMock()

        def side_effect_import(name, *args, **kwargs):
            if name == "winotify":
                raise ImportError("no winotify")
            if name == "ctypes":
                mod = real_import(name, *args, **kwargs)
                mod.windll = mock_windll
                return mod
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=side_effect_import):
            nm.send_windows_notification("Title", "Body")

        mock_windll.user32.MessageBoxW.assert_called_once()

    @patch("sys.platform", "darwin")
    def test_mac_uses_osascript(self, developer_config):
        """On macOS, osascript must be called with a display notification."""
        nm = NotificationManager(developer_config)

        with patch("subprocess.Popen") as mock_popen:
            nm.send_windows_notification("Mac Title", "Mac Body")

            mock_popen.assert_called_once()
            args_list = mock_popen.call_args[0][0]
            assert args_list[0] == "osascript"
            assert args_list[1] == "-e"
            script = args_list[2]
            assert "display notification" in script
            assert "Mac Body" in script
            assert "Mac Title" in script

    @patch("sys.platform", "linux")
    def test_other_platform_does_nothing(self, developer_config):
        """On unsupported platforms (linux), the method does nothing."""
        nm = NotificationManager(developer_config)

        with patch("subprocess.Popen") as mock_popen:
            # Should not raise and should not call anything
            nm.send_windows_notification("Title", "Body")
            mock_popen.assert_not_called()


# ===========================================================================
# send_shortfall_email
# ===========================================================================


class TestSendShortfallEmail:
    """Tests for NotificationManager.send_shortfall_email."""

    def test_skips_when_disabled(self, developer_config):
        """When email is disabled, _send_email must NOT be called."""
        nm = NotificationManager(developer_config)
        with patch.object(nm, "_send_email") as mock_send:
            nm.send_shortfall_email(
                "Shortfall Alert",
                "Missing hours detected.",
                facts=[{"title": "Gap", "value": "2h"}],
            )
            mock_send.assert_not_called()

    def test_sends_email_when_enabled(self, developer_config):
        """When enabled, _send_email must be called with the title as subject."""
        cfg = _enabled_config(developer_config)
        nm = NotificationManager(cfg)
        with patch.object(nm, "_send_email") as mock_send:
            nm.send_shortfall_email("Shortfall Alert", "Missing hours.")
            mock_send.assert_called_once()
            subject = mock_send.call_args[0][0]
            assert subject == "Shortfall Alert"

    def test_includes_facts_table_in_html(self, developer_config):
        """When facts are provided, an HTML table with rows must appear."""
        cfg = _enabled_config(developer_config)
        nm = NotificationManager(cfg)
        with patch.object(nm, "_send_email") as mock_send:
            facts = [
                {"title": "2026-02-10", "value": "6.0h (need 8.0h)"},
                {"title": "2026-02-11", "value": "4.0h (need 8.0h)"},
            ]
            nm.send_shortfall_email("Shortfall", "Gaps found.", facts=facts)

            body = mock_send.call_args[0][1]
            assert "<table" in body
            assert "2026-02-10" in body
            assert "6.0h (need 8.0h)" in body
            assert "2026-02-11" in body
            assert "<tr>" in body

    def test_handles_no_facts(self, developer_config):
        """When facts=None, the email should still be sent without a table."""
        cfg = _enabled_config(developer_config)
        nm = NotificationManager(cfg)
        with patch.object(nm, "_send_email") as mock_send:
            nm.send_shortfall_email("Shortfall", "No details.", facts=None)

            mock_send.assert_called_once()
            body = mock_send.call_args[0][1]
            # No table tag should be present
            assert "<table" not in body
            assert "No details." in body
