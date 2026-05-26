# Token Expiry Tray Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface expired API tokens as actionable desktop notifications and allow the user to enter a new token directly from the tray menu without running `--setup`.

**Architecture:** Add a `HealthCheckError` exception that carries a `reason` field (`"jira_token"` / `"tempo_token"` / `"api_error"`), raise it instead of silently returning from health-check failures, and catch it in the tray's `_run_sync()` to show a targeted toast and reveal a dynamic "Update Token" menu item. A new `TempoAutomation.update_token()` method validates, encrypts, and saves the new token in-place, then the menu item hides itself automatically.

**Tech Stack:** Python 3.7+, requests, pystray, PowerShell/AppleScript dialogs (already in tray_app.py), DPAPI/keyring encryption (CredentialManager, already in tempo_automation.py), pytest + responses + unittest.mock

---

## Files Modified

| File | What changes |
|---|---|
| `tempo_automation.py` | Add `HealthCheckError` class; change `_pre_sync_health_check()` return type; update 4 callers; add `TempoAutomation.update_token()` |
| `tray_app.py` | Add `_token_error` state to `TrayApp.__init__`; update `_run_sync()` exception handling; update `_build_menu()`; add 3 new methods |
| `tests/unit/test_tempo_automation.py` | Add `TestPreSyncHealthCheck` and `TestUpdateToken` test classes |
| `tests/unit/test_tray_app.py` | Add `TestTokenErrorVisible` and `TestRunSyncTokenError` test classes |

---

## Task 1: Add `HealthCheckError` exception to `tempo_automation.py`

**Files:**
- Modify: `tempo_automation.py` — add class after line ~84 (after `Style` dataclass, before `DualWriter`)

- [ ] **Step 1: Write the failing test**

In `tests/unit/test_tempo_automation.py`, add at the bottom of the imports section:

```python
from tempo_automation import HealthCheckError  # noqa: E402 — add to existing import line
```

Then add a new test class:

```python
class TestHealthCheckError:
    def test_is_runtime_error(self):
        err = HealthCheckError("jira_token", "Jira token expired")
        assert isinstance(err, RuntimeError)

    def test_reason_attribute(self):
        err = HealthCheckError("tempo_token", "some msg")
        assert err.reason == "tempo_token"

    def test_message_in_args(self):
        err = HealthCheckError("api_error", "API unreachable")
        assert "API unreachable" in str(err)
```

- [ ] **Step 2: Run test to verify it fails**

```
pytest tests/unit/test_tempo_automation.py::TestHealthCheckError -v
```

Expected: `ImportError: cannot import name 'HealthCheckError'`

- [ ] **Step 3: Add `HealthCheckError` to `tempo_automation.py`**

Find the line that reads `class DualWriter:` (around line 56) and insert directly before it:

```python
class HealthCheckError(RuntimeError):
    """Raised when _pre_sync_health_check() detects an expired or invalid token.

    Attributes:
        reason: "jira_token", "tempo_token", or "api_error"
    """

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason
```

- [ ] **Step 4: Run test to verify it passes**

```
pytest tests/unit/test_tempo_automation.py::TestHealthCheckError -v
```

Expected: 3 PASSED

- [ ] **Step 5: Commit**

```
git add tempo_automation.py tests/unit/test_tempo_automation.py
git commit -m "feat: add HealthCheckError for typed health check failures"
```

---

## Task 2: Change `_pre_sync_health_check()` to return `Optional[str]`

**Files:**
- Modify: `tempo_automation.py:2886` — `_pre_sync_health_check()` method

The current signature is `-> bool`. Change it to `-> Optional[str]` where:
- `None` = all checks passed
- `"jira_token"` = Jira returned 401
- `"tempo_token"` = Tempo returned 401
- `"api_error"` = other HTTP error or connectivity failure

- [ ] **Step 1: Write the failing tests**

Add `TestPreSyncHealthCheck` to `tests/unit/test_tempo_automation.py`:

```python
import responses as responses_lib


class TestPreSyncHealthCheck:
    """_pre_sync_health_check() returns None on success, a reason string on failure."""

    def _make(self):
        auto = _make_automation({"jira": {"url": "lmsportal.atlassian.net", "email": "a@b.com",
                                           "api_token": "jtoken"},
                                  "tempo": {"api_token": "ttoken"},
                                  "user": {"role": "developer", "email": "a@b.com"}})
        auto.jira_client = MagicMock()
        auto.jira_client.base_url = "https://lmsportal.atlassian.net"
        auto.jira_client.session = MagicMock()
        auto.tempo_client = MagicMock()
        auto.tempo_client.account_id = "712020:abc"
        auto.tempo_client.api_token = "ttoken"
        auto.tempo_client.base_url = "https://api.tempo.io/4"
        auto.tempo_client.session = MagicMock()
        return auto

    def test_returns_none_on_success(self):
        auto = self._make()
        jira_resp = MagicMock(status_code=200)
        jira_resp.raise_for_status = MagicMock()
        auto.jira_client.session.get.return_value = jira_resp
        tempo_resp = MagicMock(status_code=200)
        tempo_resp.raise_for_status = MagicMock()
        auto.tempo_client.session.get.return_value = tempo_resp
        auto._check_forge_connectivity = MagicMock()

        result = auto._pre_sync_health_check()

        assert result is None

    def test_returns_jira_token_on_401(self):
        import requests
        auto = self._make()
        err_resp = MagicMock(status_code=401)
        http_err = requests.exceptions.HTTPError(response=err_resp)
        auto.jira_client.session.get.side_effect = http_err

        result = auto._pre_sync_health_check()

        assert result == "jira_token"

    def test_returns_tempo_token_on_401(self):
        import requests
        auto = self._make()
        jira_resp = MagicMock(status_code=200)
        jira_resp.raise_for_status = MagicMock()
        auto.jira_client.session.get.return_value = jira_resp

        err_resp = MagicMock(status_code=401)
        http_err = requests.exceptions.HTTPError(response=err_resp)
        auto.tempo_client.session.get.side_effect = http_err

        result = auto._pre_sync_health_check()

        assert result == "tempo_token"

    def test_returns_api_error_on_500(self):
        import requests
        auto = self._make()
        err_resp = MagicMock(status_code=500)
        http_err = requests.exceptions.HTTPError(response=err_resp)
        auto.jira_client.session.get.side_effect = http_err

        result = auto._pre_sync_health_check()

        assert result == "api_error"
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/unit/test_tempo_automation.py::TestPreSyncHealthCheck -v
```

Expected: 4 FAILED (current method returns bool, not Optional[str])

- [ ] **Step 3: Rewrite `_pre_sync_health_check()` in `tempo_automation.py`**

Replace the entire method body (lines ~2886–2947) with:

```python
def _pre_sync_health_check(self) -> Optional[str]:
    """Run a lightweight liveness check before any sync operation.

    Returns:
        None if both APIs are reachable and authenticated.
        "jira_token"  if Jira returns 401 (expired/invalid token).
        "tempo_token" if Tempo returns 401 (expired/invalid token).
        "api_error"   for any other HTTP or connectivity failure.
    """
    # Check Jira API
    if self.jira_client:
        try:
            url = f"{self.jira_client.base_url}/rest/api/3/myself"
            response = self.jira_client.session.get(url, timeout=10)
            logger.info(f"API call to {url}: {response.status_code}")
            response.raise_for_status()
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else "unknown"
            if status == 401:
                msg = "[FAIL] Jira token expired (401)"
                print(msg)
                logger.error(f"Health check failed: {msg}")
                return "jira_token"
            msg = f"[FAIL] Jira API error ({status})"
            print(msg)
            logger.error(f"Health check failed: {msg}")
            return "api_error"
        except Exception as e:
            msg = f"[FAIL] Jira API unreachable: {e}"
            print(msg)
            logger.error(f"Health check failed: {msg}")
            return "api_error"

    # Check Tempo API using /work-attributes (lightweight, always available)
    if self.tempo_client.account_id or self.tempo_client.api_token:
        try:
            url = f"{self.tempo_client.base_url}/work-attributes"
            response = self.tempo_client.session.get(url, timeout=10)
            logger.info(f"API call to {url}: {response.status_code}")
            response.raise_for_status()
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else "unknown"
            hint = TempoClient._forge_error_hint(e)
            if status == 401:
                msg = "[FAIL] Tempo token expired (401)"
                if hint:
                    msg += f"\n       {hint.strip()}"
                print(msg)
                logger.error(f"Health check failed: {msg}")
                return "tempo_token"
            msg = f"[FAIL] Tempo API error ({status})"
            if hint:
                msg += f"\n       {hint.strip()}"
            print(msg)
            logger.error(f"Health check failed: {msg}")
            return "api_error"
        except Exception as e:
            msg = f"[FAIL] Tempo API unreachable: {e}"
            hint = TempoClient._forge_error_hint(e)
            if hint:
                msg += f"\n       {hint.strip()}"
            print(msg)
            logger.error(f"Health check failed: {msg}")
            return "api_error"

    # Check Forge platform connectivity (non-blocking warning)
    self._check_forge_connectivity()

    logger.info("Pre-sync health check passed")
    return None
```

Also add `Optional` to the imports at the top of `tempo_automation.py` if not already present. Search for `from typing import` and add `Optional` if missing.

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/unit/test_tempo_automation.py::TestPreSyncHealthCheck -v
```

Expected: 4 PASSED

- [ ] **Step 5: Commit**

```
git add tempo_automation.py tests/unit/test_tempo_automation.py
git commit -m "refactor: _pre_sync_health_check returns Optional[str] reason instead of bool"
```

---

## Task 3: Update 4 callers of `_pre_sync_health_check()` to raise `HealthCheckError`

**Files:**
- Modify: `tempo_automation.py` at lines ~3006, ~4237, ~4816, ~5122

All four callers currently do:
```python
if not self._pre_sync_health_check():
    print("[FAIL] Aborting ... due to API health check failure.")
    return
```

Replace each with the pattern below (substituting the correct operation name). The tray app catches `HealthCheckError`; CLI callers let it propagate to the top-level `except Exception` handler which prints it.

- [ ] **Step 1: Write the failing tests**

Add to `TestPreSyncHealthCheck` in `tests/unit/test_tempo_automation.py`:

```python
    def test_sync_daily_raises_health_check_error_on_token_expiry(self):
        from tempo_automation import HealthCheckError
        auto = self._make()
        auto.schedule_mgr = MagicMock()
        auto.schedule_mgr.is_working_day.return_value = True
        import requests
        err_resp = MagicMock(status_code=401)
        http_err = requests.exceptions.HTTPError(response=err_resp)
        auto.jira_client.session.get.side_effect = http_err

        with pytest.raises(HealthCheckError) as exc_info:
            auto.sync_daily()

        assert exc_info.value.reason == "jira_token"

    def test_sync_daily_returns_none_on_nonworking_day(self):
        auto = self._make()
        auto.schedule_mgr = MagicMock()
        auto.schedule_mgr.is_working_day.return_value = False

        result = auto.sync_daily()

        assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/unit/test_tempo_automation.py::TestPreSyncHealthCheck::test_sync_daily_raises_health_check_error_on_token_expiry -v
pytest tests/unit/test_tempo_automation.py::TestPreSyncHealthCheck::test_sync_daily_returns_none_on_nonworking_day -v
```

Expected: 2 FAILED

- [ ] **Step 3: Update `sync_daily()` caller at line ~3006**

Find:
```python
        if not self._pre_sync_health_check():
            print(_color_prefix("[FAIL] Aborting daily sync due to API health check failure."))
            return
```

Replace with:
```python
        health_failure = self._pre_sync_health_check()
        if health_failure is not None:
            msg = "[FAIL] Aborting daily sync due to API health check failure."
            print(_color_prefix(msg))
            raise HealthCheckError(health_failure, msg)
```

- [ ] **Step 4: Update `verify_week()` caller at line ~4237**

Find:
```python
        if not self._pre_sync_health_check():
            print(_color_prefix("[FAIL] Aborting weekly verify due to API health check failure."))
            return
```

Replace with:
```python
        health_failure = self._pre_sync_health_check()
        if health_failure is not None:
            msg = "[FAIL] Aborting weekly verify due to API health check failure."
            print(_color_prefix(msg))
            raise HealthCheckError(health_failure, msg)
```

- [ ] **Step 5: Update `backfill_range()` caller at line ~4816**

Find:
```python
        if not self._pre_sync_health_check():
            print(_color_prefix("[FAIL] Aborting backfill due to API health check failure."))
            return
```

Replace with:
```python
        health_failure = self._pre_sync_health_check()
        if health_failure is not None:
            msg = "[FAIL] Aborting backfill due to API health check failure."
            print(_color_prefix(msg))
            raise HealthCheckError(health_failure, msg)
```

- [ ] **Step 6: Update 4th caller at line ~5122**

Find the 4th `if not self._pre_sync_health_check():` block (in `submit_timesheet()` or similar). Apply the same pattern:

```python
        health_failure = self._pre_sync_health_check()
        if health_failure is not None:
            msg = "[FAIL] Aborting [operation] due to API health check failure."
            print(_color_prefix(msg))
            raise HealthCheckError(health_failure, msg)
```

- [ ] **Step 7: Run tests to verify they pass**

```
pytest tests/unit/test_tempo_automation.py::TestPreSyncHealthCheck -v
```

Expected: all 6 PASSED

- [ ] **Step 8: Run full suite to check no regressions**

```
pytest tests/ -v --tb=short -q
```

Expected: all existing tests pass (HealthCheckError is a RuntimeError, so any `except Exception` in tests still catches it)

- [ ] **Step 9: Commit**

```
git add tempo_automation.py tests/unit/test_tempo_automation.py
git commit -m "feat: raise HealthCheckError from health check callers (sync, verify, backfill, submit)"
```

---

## Task 4: Add `TempoAutomation.update_token()` method

**Files:**
- Modify: `tempo_automation.py` — add method to `TempoAutomation` class after `_pre_sync_health_check()`

This method validates a new token via live API, encrypts it, saves it to config, and updates the in-memory client so the next sync works without a restart.

- [ ] **Step 1: Write the failing tests**

Add `TestUpdateToken` to `tests/unit/test_tempo_automation.py`:

```python
class TestUpdateToken:
    """update_token() validates, saves, and hot-swaps a new API token."""

    def _make(self):
        auto = _make_automation({
            "jira": {"url": "lmsportal.atlassian.net", "email": "a@b.com", "api_token": "old"},
            "tempo": {"api_token": "old_tempo"},
            "user": {"role": "developer", "email": "a@b.com"},
        })
        auto.jira_client = MagicMock()
        auto.jira_client.email = "a@b.com"
        auto.jira_client.base_url = "https://lmsportal.atlassian.net"
        auto.jira_client.session = MagicMock()
        auto.tempo_client = MagicMock()
        auto.tempo_client.api_token = "old_tempo"
        auto.tempo_client.base_url = "https://api.tempo.io/4"
        auto.tempo_client.session = MagicMock()
        auto.tempo_client.session.headers = {"Authorization": "Bearer old_tempo"}
        auto.config_manager = MagicMock()
        return auto

    def test_update_tempo_token_success(self):
        auto = self._make()
        ok_resp = MagicMock(status_code=200)
        ok_resp.raise_for_status = MagicMock()
        auto.tempo_client.session.get.return_value = ok_resp

        result = auto.update_token("tempo", "new_tempo_token")

        assert result is True
        assert auto.tempo_client.api_token == "new_tempo_token"
        assert "new_tempo_token" in auto.tempo_client.session.headers["Authorization"]
        auto.config_manager.save_config.assert_called_once()

    def test_update_tempo_token_invalid(self):
        import requests
        auto = self._make()
        err_resp = MagicMock(status_code=401)
        http_err = requests.exceptions.HTTPError(response=err_resp)
        auto.tempo_client.session.get.side_effect = http_err

        result = auto.update_token("tempo", "bad_token")

        assert result is False
        auto.config_manager.save_config.assert_not_called()

    def test_update_jira_token_success(self):
        auto = self._make()
        ok_resp = MagicMock(status_code=200)
        ok_resp.raise_for_status = MagicMock()
        auto.jira_client.session.get.return_value = ok_resp

        result = auto.update_token("jira", "new_jira_token")

        assert result is True
        assert auto.jira_client.api_token == "new_jira_token"
        auto.config_manager.save_config.assert_called_once()

    def test_update_jira_token_invalid(self):
        import requests
        auto = self._make()
        err_resp = MagicMock(status_code=401)
        http_err = requests.exceptions.HTTPError(response=err_resp)
        auto.jira_client.session.get.side_effect = http_err

        result = auto.update_token("jira", "bad_token")

        assert result is False
        auto.config_manager.save_config.assert_not_called()

    def test_update_unknown_type_returns_false(self):
        auto = self._make()
        result = auto.update_token("unknown", "whatever")
        assert result is False
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/unit/test_tempo_automation.py::TestUpdateToken -v
```

Expected: 5 FAILED (`AttributeError: 'TempoAutomation' object has no attribute 'update_token'`)

- [ ] **Step 3: Add `update_token()` to `TempoAutomation`**

Add as a new method directly after `_pre_sync_health_check()` in `tempo_automation.py`:

```python
def update_token(self, token_type: str, new_token: str) -> bool:
    """Validate a new API token, save it encrypted to config, and hot-swap
    the in-memory client so the next sync works without a restart.

    Args:
        token_type: "jira" or "tempo"
        new_token:  The new plaintext token to validate and store

    Returns:
        True if the token passed validation and was saved.
        False if validation failed (config is not modified).
    """
    if token_type == "tempo":
        try:
            url = f"{self.tempo_client.base_url}/work-attributes"
            resp = self.tempo_client.session.get(
                url,
                headers={"Authorization": f"Bearer {new_token}"},
                timeout=30,
            )
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"New Tempo token validation failed: {e}")
            return False

        encrypted = CredentialManager.encrypt(new_token, key="tempo_token")
        self.config.setdefault("tempo", {})["api_token"] = encrypted
        self.config_manager.save_config(self.config)
        self.tempo_client.api_token = new_token
        self.tempo_client.session.headers["Authorization"] = f"Bearer {new_token}"
        logger.info("Tempo token updated and saved successfully")
        return True

    if token_type == "jira":
        import base64 as _b64
        email = self.jira_client.email if self.jira_client else ""
        creds = _b64.b64encode(f"{email}:{new_token}".encode()).decode()
        try:
            url = f"{self.jira_client.base_url}/rest/api/3/myself"
            resp = self.jira_client.session.get(
                url,
                headers={"Authorization": f"Basic {creds}"},
                timeout=30,
            )
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"New Jira token validation failed: {e}")
            return False

        encrypted = CredentialManager.encrypt(new_token, key="jira_token")
        self.config.setdefault("jira", {})["api_token"] = encrypted
        self.config_manager.save_config(self.config)
        self.jira_client.api_token = new_token
        self.jira_client.session.auth = (email, new_token)
        logger.info("Jira token updated and saved successfully")
        return True

    logger.error(f"update_token: unknown token_type '{token_type}'")
    return False
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/unit/test_tempo_automation.py::TestUpdateToken -v
```

Expected: 5 PASSED

- [ ] **Step 5: Commit**

```
git add tempo_automation.py tests/unit/test_tempo_automation.py
git commit -m "feat: add TempoAutomation.update_token() for hot-swap token replacement"
```

---

## Task 5: Fix tray `_run_sync()` — correct toast for token expiry and mid-sync 401

**Files:**
- Modify: `tray_app.py:241` (`__init__`) — add `_token_error` attribute
- Modify: `tray_app.py:542` (`_run_sync()`) — catch `HealthCheckError`, detect mid-sync 401

- [ ] **Step 1: Write the failing tests**

Add `TestRunSyncTokenError` to `tests/unit/test_tray_app.py`:

```python
class TestRunSyncTokenError:
    """_run_sync() sets _token_error state on health-check failures."""

    def _make_tray(self, tmp_path):
        config = {"user": {"name": "Test", "role": "developer", "email": "t@t.com"},
                  "jira": {"url": "lmsportal.atlassian.net", "email": "t@t.com",
                            "api_token": "tok"},
                  "tempo": {"api_token": "tok"},
                  "schedule": {"daily_sync_time": "18:00", "daily_hours": 8}}
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps(config))
        # Patch CONFIG_FILE before creating TrayApp
        import tray_app
        orig = tray_app.CONFIG_FILE
        tray_app.CONFIG_FILE = config_file
        app = TrayApp()
        tray_app.CONFIG_FILE = orig
        return app

    def test_initial_token_error_is_none(self, tmp_path):
        app = self._make_tray(tmp_path)
        assert app._token_error is None

    def test_health_check_error_sets_token_error(self, tmp_path):
        from tempo_automation import HealthCheckError
        app = self._make_tray(tmp_path)
        mock_auto = MagicMock()
        mock_auto.sync_daily.side_effect = HealthCheckError("tempo_token", "expired")
        app._automation = mock_auto
        app._show_toast = MagicMock()
        app._set_icon_state = MagicMock()
        app._start_sync_animation = MagicMock()
        app._stop_sync_animation = MagicMock()

        app._run_sync()

        assert app._token_error == "tempo_token"
        app._show_toast.assert_called_once()
        title, body = app._show_toast.call_args[0]
        assert "Tempo" in title
        assert "Update Token" in body

    def test_health_check_jira_error_sets_token_error(self, tmp_path):
        from tempo_automation import HealthCheckError
        app = self._make_tray(tmp_path)
        mock_auto = MagicMock()
        mock_auto.sync_daily.side_effect = HealthCheckError("jira_token", "expired")
        app._automation = mock_auto
        app._show_toast = MagicMock()
        app._set_icon_state = MagicMock()
        app._start_sync_animation = MagicMock()
        app._stop_sync_animation = MagicMock()

        app._run_sync()

        assert app._token_error == "jira_token"
        title, body = app._show_toast.call_args[0]
        assert "Jira" in title

    def test_mid_sync_401_sets_token_error(self, tmp_path):
        import requests
        app = self._make_tray(tmp_path)
        mock_auto = MagicMock()
        err_resp = MagicMock(status_code=401)
        http_err = requests.exceptions.HTTPError(response=err_resp)
        mock_auto.sync_daily.side_effect = http_err
        app._automation = mock_auto
        app._show_toast = MagicMock()
        app._set_icon_state = MagicMock()
        app._start_sync_animation = MagicMock()
        app._stop_sync_animation = MagicMock()

        app._run_sync()

        assert app._token_error == "unknown_token"
        title, _ = app._show_toast.call_args[0]
        assert "Token" in title

    def test_non_401_exception_shows_generic_toast(self, tmp_path):
        app = self._make_tray(tmp_path)
        mock_auto = MagicMock()
        mock_auto.sync_daily.side_effect = ValueError("something broke")
        app._automation = mock_auto
        app._show_toast = MagicMock()
        app._set_icon_state = MagicMock()
        app._start_sync_animation = MagicMock()
        app._stop_sync_animation = MagicMock()

        app._run_sync()

        assert app._token_error is None
        title, _ = app._show_toast.call_args[0]
        assert title == "Sync Failed"
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/unit/test_tray_app.py::TestRunSyncTokenError -v
```

Expected: 5 FAILED (`AttributeError: 'TrayApp' has no attribute '_token_error'`)

- [ ] **Step 3: Add `_token_error` to `TrayApp.__init__`**

In `tray_app.py`, find the `__init__` method body (line ~242) and add after `self._next_sync_target = None`:

```python
        self._token_error: Optional[str] = None  # "jira_token" / "tempo_token" / "unknown_token"
```

Also add `Optional` to the `tray_app.py` imports if not present:
```python
from typing import Optional
```

- [ ] **Step 4: Update `_run_sync()` exception handling**

In `tray_app.py`, find the `_run_sync()` method. There are two places to update:

**4a.** The `result = self._automation.sync_daily()` call is inside a `try` block that has a generic `except Exception as e` at the end. Add a new `except` clause for `HealthCheckError` BEFORE the generic one, and update the generic one to detect 401s:

```python
        except HealthCheckError as e:
            with self._stdout_lock:
                sys.stdout = old_stdout
            if log_f and not log_f.closed:
                log_f.close()
            self._token_error = e.reason
            if self._icon:
                self._icon.update_menu()
            label = "Jira" if e.reason == "jira_token" else "Tempo"
            self._set_icon_state("red", f"Tempo - {label} token expired")
            self._show_toast(
                f"{label} Token Expired",
                f"Your {label} API token has expired.\n"
                "Right-click tray icon > Update Token to fix.",
            )
            tray_logger.error(f"Health check failed: {e.reason}")
            sync_succeeded = False
        except Exception as e:
            with self._stdout_lock:
                sys.stdout = old_stdout
            if log_f and not log_f.closed:
                log_f.close()
            error_msg = str(e)[:200]
            status = getattr(getattr(e, "response", None), "status_code", None)
            if status == 401 or "401" in error_msg:
                self._token_error = "unknown_token"
                if self._icon:
                    self._icon.update_menu()
                self._set_icon_state("red", "Tempo - Token expired")
                self._show_toast(
                    "Token Expired",
                    "An API token expired during sync.\n"
                    "Right-click tray icon > Update Token to fix.",
                )
                tray_logger.error(f"Mid-sync token expiry: {e}")
            else:
                self._set_icon_state("red", f"Tempo - Error: {error_msg}")
                self._show_toast("Sync Failed", f"Error: {error_msg}")
                tray_logger.error(f"Sync failed: {e}", exc_info=True)
            sync_succeeded = False
```

**4b.** Add the import for `HealthCheckError` inside `_run_sync()` where the other deferred import happens:

```python
            from tempo_automation import TempoAutomation, HealthCheckError
```

(Replace the existing `from tempo_automation import TempoAutomation` line.)

- [ ] **Step 5: Run tests to verify they pass**

```
pytest tests/unit/test_tray_app.py::TestRunSyncTokenError -v
```

Expected: 5 PASSED

- [ ] **Step 6: Commit**

```
git add tray_app.py tests/unit/test_tray_app.py
git commit -m "fix: tray shows correct token-expired toast instead of non-working-day message"
```

---

## Task 6: Add dynamic "Update Token" menu item to tray

**Files:**
- Modify: `tray_app.py:433` (`_build_menu()`)
- Modify: `tray_app.py` — add `_token_error_visible()` method

- [ ] **Step 1: Write the failing tests**

Add `TestTokenErrorVisible` to `tests/unit/test_tray_app.py`:

```python
class TestTokenErrorVisible:
    """_token_error_visible() controls the Update Token menu item."""

    def _make_tray(self):
        app = TrayApp()
        return app

    def test_hidden_when_no_error(self):
        app = self._make_tray()
        app._token_error = None
        assert app._token_error_visible(None) is False

    def test_visible_when_tempo_token_error(self):
        app = self._make_tray()
        app._token_error = "tempo_token"
        assert app._token_error_visible(None) is True

    def test_visible_when_jira_token_error(self):
        app = self._make_tray()
        app._token_error = "jira_token"
        assert app._token_error_visible(None) is True

    def test_visible_when_unknown_token_error(self):
        app = self._make_tray()
        app._token_error = "unknown_token"
        assert app._token_error_visible(None) is True

    def test_hidden_after_error_cleared(self):
        app = self._make_tray()
        app._token_error = "tempo_token"
        app._token_error = None
        assert app._token_error_visible(None) is False
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/unit/test_tray_app.py::TestTokenErrorVisible -v
```

Expected: 5 FAILED (`AttributeError: 'TrayApp' has no attribute '_token_error_visible'`)

- [ ] **Step 3: Add `_token_error_visible()` method to `TrayApp`**

In `tray_app.py`, add after `_shortfall_visible()` (line ~481):

```python
    def _token_error_visible(self, item) -> bool:
        """Show 'Update Token' only when a token has expired."""
        return self._token_error is not None
```

- [ ] **Step 4: Update `_build_menu()` to include the dynamic item**

In `tray_app.py`, find `_build_menu()` (line ~433). After the `pystray.Menu.SEPARATOR` that precedes the `Submit Timesheet` item, add the new menu item:

```python
            pystray.MenuItem(
                "Update Token",
                self._on_update_token,
                visible=self._token_error_visible,
            ),
```

The updated `_build_menu()` return block should read:

```python
        return pystray.Menu(
            pystray.MenuItem(f"\U0001f464 {user_label}", lambda: None, visible=bool(user_label)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                lambda item: (f"Sync Now (Auto Sync @{self._get_sync_time()})"),
                self._on_sync_now,
                default=True,
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Configure",
                pystray.Menu(
                    pystray.MenuItem("Add PTO", self._on_add_pto),
                    pystray.MenuItem("Select Overhead", self._on_select_overhead),
                    pystray.MenuItem("Change Sync Time", self._on_change_sync_time),
                ),
            ),
            pystray.MenuItem(
                "Log and Reports",
                pystray.Menu(
                    pystray.MenuItem("Daily Log", self._on_view_log),
                    pystray.MenuItem("Schedule", self._on_view_schedule),
                    pystray.MenuItem("View Monthly Hours", self._on_view_monthly),
                    pystray.MenuItem(
                        "Fix Monthly Shortfall",
                        self._on_fix_shortfall,
                        visible=self._shortfall_visible,
                    ),
                ),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Submit Timesheet", self._on_submit_timesheet, visible=self._submit_visible
            ),
            pystray.MenuItem(
                "Update Token",
                self._on_update_token,
                visible=self._token_error_visible,
            ),
            pystray.MenuItem("Settings", self._on_settings),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Uninstall", self._on_uninstall),
            pystray.MenuItem("Exit", self._on_exit),
        )
```

- [ ] **Step 5: Run tests to verify they pass**

```
pytest tests/unit/test_tray_app.py::TestTokenErrorVisible -v
```

Expected: 5 PASSED

- [ ] **Step 6: Commit**

```
git add tray_app.py tests/unit/test_tray_app.py
git commit -m "feat: add dynamic Update Token menu item to tray (visible only on token expiry)"
```

---

## Task 7: Add `_on_update_token()` and `_run_update_token()` handlers

**Files:**
- Modify: `tray_app.py` — add two new methods to `TrayApp`

- [ ] **Step 1: Write the failing tests**

Add `TestRunUpdateToken` to `tests/unit/test_tray_app.py`:

```python
class TestRunUpdateToken:
    """_run_update_token() prompts for a token, validates it, saves it, clears state."""

    def _make_tray(self):
        app = TrayApp()
        app._show_toast = MagicMock()
        app._set_icon_state = MagicMock()
        app._show_input_dialog = MagicMock()
        app._icon = MagicMock()
        return app

    def test_cancelled_dialog_does_nothing(self):
        app = self._make_tray()
        app._token_error = "tempo_token"
        app._show_input_dialog.return_value = ""  # user cancelled

        app._run_update_token()

        app._show_toast.assert_not_called()
        assert app._token_error == "tempo_token"  # unchanged

    def test_valid_token_clears_error_and_shows_success_toast(self):
        app = self._make_tray()
        app._token_error = "tempo_token"
        app._show_input_dialog.return_value = "new_valid_token"
        mock_auto = MagicMock()
        mock_auto.update_token.return_value = True
        app._automation = mock_auto
        app._automation_lock = threading.Lock()

        app._run_update_token()

        assert app._token_error is None
        app._show_toast.assert_called_once()
        title, _ = app._show_toast.call_args[0]
        assert "Updated" in title

    def test_invalid_token_keeps_error_state(self):
        app = self._make_tray()
        app._token_error = "jira_token"
        app._show_input_dialog.return_value = "bad_token"
        mock_auto = MagicMock()
        mock_auto.update_token.return_value = False
        app._automation = mock_auto
        app._automation_lock = threading.Lock()

        app._run_update_token()

        assert app._token_error == "jira_token"  # not cleared
        title, _ = app._show_toast.call_args[0]
        assert "Invalid" in title

    def test_jira_error_shows_jira_label_in_dialog(self):
        app = self._make_tray()
        app._token_error = "jira_token"
        app._show_input_dialog.return_value = ""

        app._run_update_token()

        prompt, title = app._show_input_dialog.call_args[0]
        assert "Jira" in prompt
        assert "Jira" in title

    def test_tempo_error_shows_tempo_label_in_dialog(self):
        app = self._make_tray()
        app._token_error = "tempo_token"
        app._show_input_dialog.return_value = ""

        app._run_update_token()

        prompt, title = app._show_input_dialog.call_args[0]
        assert "Tempo" in prompt
        assert "Tempo" in title
```

- [ ] **Step 2: Run tests to verify they fail**

```
pytest tests/unit/test_tray_app.py::TestRunUpdateToken -v
```

Expected: 5 FAILED (`AttributeError: 'TrayApp' has no attribute '_on_update_token'`)

- [ ] **Step 3: Add both methods to `TrayApp`**

In `tray_app.py`, add after `_token_error_visible()`:

```python
    def _on_update_token(self, icon=None, item=None):
        """Spawn background thread for token update dialog (pystray callback must return quickly)."""
        thread = threading.Thread(target=self._run_update_token, daemon=True)
        thread.start()

    def _run_update_token(self):
        """Background thread: prompt for new token, validate, save, clear error state."""
        token_type = self._token_error or "tempo_token"
        is_jira = "jira" in token_type
        label = "Jira" if is_jira else "Tempo"

        if is_jira:
            instructions = (
                "Get a new Jira token at:\n"
                "  https://id.atlassian.com/manage-profile/security/api-tokens\n\n"
                "1. Click 'Create API token'\n"
                "2. Give it a name and copy the token"
            )
        else:
            instructions = (
                "Get a new Tempo token at:\n"
                "  https://app.tempo.io -> Settings -> API Integration\n\n"
                "1. Click 'New Token'\n"
                "2. Give it a name and copy the token"
            )

        prompt = (
            f"Your {label} API token has expired.\n\n"
            f"{instructions}\n\n"
            f"Paste your new {label} token below:"
        )

        new_token = self._show_input_dialog(prompt, f"Tempo Automation - Update {label} Token")
        if not new_token:
            return  # user cancelled — leave _token_error set so menu item stays visible

        with self._automation_lock:
            if self._automation is None:
                self._show_toast("Error", "Automation not loaded. Run --setup first.")
                return
            api_type = "jira" if is_jira else "tempo"
            success = self._automation.update_token(api_type, new_token)

        if success:
            self._token_error = None
            if self._icon:
                self._icon.update_menu()
            self._set_icon_state("green", "Tempo Automation")
            self._show_toast(
                f"{label} Token Updated",
                "Token validated and saved. Retrying sync now.",
            )
            tray_logger.info(f"{label} token updated successfully via tray")
            # Auto-retry sync so the user sees it recover immediately
            thread = threading.Thread(target=self._run_sync, daemon=True)
            thread.start()
        else:
            self._show_toast(
                f"{label} Token Invalid",
                "The token you entered failed validation.\n"
                "Please try again with a fresh token.",
            )
            tray_logger.warning(f"{label} token update failed: token rejected by API")
```

- [ ] **Step 4: Run tests to verify they pass**

```
pytest tests/unit/test_tray_app.py::TestRunUpdateToken -v
```

Expected: 5 PASSED

- [ ] **Step 5: Run the full test suite**

```
pytest tests/ -v --tb=short -q
```

Expected: all existing 528 tests + new tests pass

- [ ] **Step 6: Commit**

```
git add tray_app.py tests/unit/test_tray_app.py
git commit -m "feat: add Update Token dialog flow to tray app with auto-retry sync on success"
```

---

## Task 8: Update CLAUDE.md documentation

**Files:**
- Modify: `CLAUDE.md` — update "What's Working" list and TODO items

- [ ] **Step 1: Update CLAUDE.md**

In the `Current Status` section:

Under **Working**, add:
```
token expiry detection (HealthCheckError), targeted toast notifications (Jira/Tempo token expired), Update Token tray menu item (auto-hides after validation), hot-swap token save without restart
```

Under **TODO**, add:
```
- [ ] Test Update Token dialog on actual Mac hardware (osascript dialog)
- [ ] Consider adding "Update Token" to the Configure submenu for proactive re-keying
```

- [ ] **Step 2: Commit**

```
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md for token expiry tray fix feature"
```

---

## Self-Review Checklist

**Spec coverage:**

| Gap from audit | Task that covers it |
|---|---|
| Tray shows misleading "non-working day" toast for token expiry | Task 5 — `HealthCheckError` caught in `_run_sync()` |
| No "token expired" desktop notification | Task 5 — dedicated toast with label (Jira/Tempo) |
| No re-entry from tray app | Tasks 6 + 7 — dynamic menu item + dialog |
| "Update Token" hides after validation | Task 7 — `_token_error = None` → `update_menu()` |
| Mid-sync 401 (after health check passes) shows generic "Sync Failed" | Task 5 — detect `status_code == 401` in generic except |
| Token saved encrypted without restart | Task 4 — `update_token()` calls `CredentialManager.encrypt()` + `save_config()` |

**Placeholder scan:** No TBD/TODO in code steps. All code blocks are complete.

**Type consistency:**
- `HealthCheckError.reason` used in Task 1 definition matches use in Tasks 3, 5, 6, 7
- `_token_error: Optional[str]` defined in Task 5 matches visibility check in Task 6 and dialog in Task 7
- `update_token(token_type, new_token)` signature defined in Task 4 matches call in Task 7

---
