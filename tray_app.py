#!/usr/bin/env python3
"""
Tempo Automation - System Tray Application
============================================

Persistent system tray icon that:
- Notifies at the configured daily_sync_time (default 18:00)
- Lets the user confirm/trigger sync via the tray menu
- Changes icon color to show status (green/yellow/red)
- Auto-starts on login (Windows: registry, Mac: LaunchAgent)

Cross-platform: Windows + macOS

Usage:
    pythonw.exe tray_app.py              # Windows: run without console
    python3 tray_app.py                  # Mac: run the tray app
    python tray_app.py --register        # Register auto-start on login
    python tray_app.py --unregister      # Remove auto-start

Author: Vector Solutions Engineering Team
"""

import argparse
import calendar
import json
import logging
import os
import subprocess
import sys
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

# Platform-specific imports
if sys.platform == "win32":
    import ctypes

# Conditional imports -- tray app degrades gracefully if missing
try:
    import pystray
    from PIL import Image, ImageDraw, ImageFont

    PYSTRAY_OK = True
except ImportError:
    PYSTRAY_OK = False

# Optional colorama for colored terminal output
try:
    import colorama

    colorama.init()
    _C_OK = colorama.Fore.GREEN
    _C_FAIL = colorama.Fore.RED
    _C_WARN = colorama.Fore.YELLOW
    _C_R = colorama.Style.RESET_ALL
except ImportError:
    _C_OK = _C_FAIL = _C_WARN = _C_R = ""

try:
    from winotify import Notification

    WINOTIFY_OK = True
except ImportError:
    WINOTIFY_OK = False

SCRIPT_DIR = Path(__file__).parent
CONFIG_FILE = SCRIPT_DIR / "config.json"
LOG_FILE = SCRIPT_DIR / "daily-timesheet.log"  # legacy fallback
INTERNAL_LOG = SCRIPT_DIR / "tempo_automation.log"


def _monthly_log_file() -> Path:
    """Return the daily log path for the current month (rotates on the 1st).

    Format: daily-timesheet-YYYY-MM.log  (e.g. daily-timesheet-2026-03.log)
    Old monthly files are never deleted -- they accumulate as an archive.
    Falls back to the legacy daily-timesheet.log if date computation fails.
    """
    try:
        from datetime import date

        today = date.today()
        return SCRIPT_DIR / f"daily-timesheet-{today.strftime('%Y-%m')}.log"
    except Exception:
        return LOG_FILE


STOP_FILE = SCRIPT_DIR / "_tray_stop.signal"
MENU_REFRESH_SIGNAL = SCRIPT_DIR / "_menu_refresh.signal"
SHORTFALL_FILE = SCRIPT_DIR / "monthly_shortfall.json"
SUBMITTED_FILE = SCRIPT_DIR / "monthly_submitted.json"

# Windows-only constants
if sys.platform == "win32":
    REG_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
    REG_VALUE = "TempoTrayApp"
    MUTEX_NAME = "TempoTrayApp_SingleInstance_Mutex"

# Mac LaunchAgent constants
LAUNCH_AGENT_LABEL = "com.tempo.trayapp"
LAUNCH_AGENT_PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT_LABEL}.plist"

# Tray app logger (separate from tempo_automation logger)
tray_logger = logging.getLogger("tray_app")
tray_logger.setLevel(logging.INFO)
_handler = logging.FileHandler(INTERNAL_LOG, encoding="utf-8")
_handler.setFormatter(logging.Formatter("%(asctime)s - TRAY - %(levelname)s - %(message)s"))
tray_logger.addHandler(_handler)

# Debug: set TEMPO_DEBUG_DATE=YYYY-MM-DD to override date.today()
# Example: set TEMPO_DEBUG_DATE=2026-03-15
_DEBUG_DATE_STR = os.environ.get("TEMPO_DEBUG_DATE", "")
_DEBUG_DATE = None
if _DEBUG_DATE_STR:
    try:
        _DEBUG_DATE = datetime.strptime(_DEBUG_DATE_STR, "%Y-%m-%d").date()
        tray_logger.info(f"DEBUG: overriding today to {_DEBUG_DATE}")
    except ValueError:
        tray_logger.warning(f"DEBUG: invalid TEMPO_DEBUG_DATE '{_DEBUG_DATE_STR}', ignoring")


def _today() -> date:
    """Return today's date, or debug override if set."""
    return _DEBUG_DATE if _DEBUG_DATE else date.today()


# Status background colors
BG_COLORS = {
    "green": (186, 230, 126),
    "orange": (252, 211, 119),
    "red": (252, 165, 165),
}

# ============================================================================
# ICON GENERATION
# ============================================================================

FAVICON_PATH = SCRIPT_DIR / "assets" / "favicon.ico"
CORNER_RADIUS = 12  # rounded corner radius for the background square

# Cache the loaded favicon to avoid re-reading the file on every icon update
_favicon_cache = None
_favicon_lock = threading.Lock()


def _load_favicon(size: int = 48) -> "Image":
    """Load and cache the company favicon, resized to fit."""
    global _favicon_cache
    with _favicon_lock:
        if _favicon_cache is not None and _favicon_cache.size[0] == size:
            return _favicon_cache.copy()
        try:
            favicon = Image.open(str(FAVICON_PATH))
            if hasattr(favicon, "ico") and favicon.ico:
                best_size = max(favicon.ico.sizes(), key=lambda s: s[0])
                favicon = favicon.ico.getimage(best_size)
            favicon = favicon.convert("RGBA")
            favicon = favicon.resize((size, size), Image.LANCZOS)
            _favicon_cache = favicon
            return favicon.copy()
        except (OSError, Exception) as e:
            tray_logger.warning(f"Could not load favicon: {e}")
            return None


def _make_icon(color: str = "green") -> "Image":
    """Generate 64x64 tray icon: rounded-rect background + company logo."""
    size = 64
    rgb = BG_COLORS.get(color, BG_COLORS["green"])
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Draw rounded rectangle background
    draw.rounded_rectangle(
        [0, 0, size - 1, size - 1],
        radius=CORNER_RADIUS,
        fill=rgb,
    )

    # Overlay the original favicon centered on the background
    logo_size = 48
    favicon = _load_favicon(logo_size)
    if favicon:
        offset = (size - logo_size) // 2
        img.paste(favicon, (offset, offset), favicon)
    else:
        # Fallback: draw "T" in dark blue
        try:
            font = ImageFont.truetype("arial.ttf", 40)
        except OSError:
            font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), "T", font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(((size - tw) // 2, (size - th) // 2), "T", fill=(0, 70, 150), font=font)

    return img


def _find_pythonw() -> str:
    """Find pythonw.exe (Windows) or python3 (Mac) for background execution."""
    if sys.platform == "win32":
        python_dir = Path(sys.executable).parent
        pythonw = python_dir / "pythonw.exe"
        if pythonw.exists():
            return str(pythonw)
    # Fallback: use current interpreter
    return sys.executable


# ============================================================================
# TRAY APPLICATION
# ============================================================================


class TrayApp:
    """System tray application for Tempo Automation."""

    def __init__(self):
        self._sync_running = threading.Event()
        self._pending_confirmation = False
        self._timer = None
        self._icon = None
        self._automation = None
        self._config = None
        self._import_error = None
        self._anim_timer = None  # animation timer for syncing dot
        self._anim_running = False  # flag to stop animation loop
        self._stdout_lock = threading.Lock()  # protects sys.stdout swap
        self._automation_lock = threading.Lock()  # protects self._automation
        self._next_sync_target = None  # wall-clock target for sleep detection

    def _load_automation(self):
        """
        Deferred import of TempoAutomation.

        tempo_automation.py has module-level side effects (logging setup,
        stdout redirect). Importing inside init lets the tray icon appear
        first. If import fails, icon shows red with error tooltip.
        """
        try:
            import json

            with open(CONFIG_FILE, encoding="utf-8") as f:
                self._config = json.load(f)

            # Import the main automation class
            sys.path.insert(0, str(SCRIPT_DIR))
            from tempo_automation import TempoAutomation

            self._automation = TempoAutomation(CONFIG_FILE)
            tray_logger.info("TempoAutomation loaded successfully")
        except FileNotFoundError:
            self._import_error = (
                "config.json not found. Run setup first: python tempo_automation.py --setup"
            )
            tray_logger.error(self._import_error)
        except Exception as e:
            self._import_error = f"Failed to load automation: {e}"
            tray_logger.error(self._import_error, exc_info=True)

    def _check_single_instance(self) -> bool:
        """
        Ensure only one tray app instance is running.
        Windows: named Win32 mutex.
        Mac/Linux: fcntl file lock.
        """
        if sys.platform == "win32":
            self._mutex = ctypes.windll.kernel32.CreateMutexW(None, True, MUTEX_NAME)
            last_error = ctypes.windll.kernel32.GetLastError()
            if last_error == 183:  # ERROR_ALREADY_EXISTS
                tray_logger.info("Another instance is already running")
                return False
            return True
        else:
            import fcntl

            lock_path = SCRIPT_DIR / ".tray_app.lock"
            # Keep file handle alive for process lifetime
            self._lock_file = open(lock_path, "w")
            try:
                fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except OSError:
                tray_logger.info("Another instance is already running")
                return False

    def _get_sync_time(self) -> str:
        """Get configured daily sync time, default '18:00'."""
        if self._config:
            return self._config.get("schedule", {}).get("daily_sync_time", "18:00")
        return "18:00"

    def _schedule_next_sync(self):
        """Schedule a timer to fire at the next daily_sync_time."""
        if self._timer:
            self._timer.cancel()

        self._reload_config()
        sync_time_str = self._get_sync_time()
        try:
            hour, minute = map(int, sync_time_str.split(":"))
        except (ValueError, AttributeError):
            hour, minute = 18, 0

        now = datetime.now()
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

        # If today's time has passed, schedule for tomorrow
        if target < now:
            target += timedelta(days=1)

        self._next_sync_target = target

        delay = (target - now).total_seconds()
        self._timer = threading.Timer(delay, self._on_timer_fired)
        self._timer.daemon = True
        self._timer.start()

        tray_logger.info(
            f"Next sync notification scheduled for {target:%Y-%m-%d %H:%M} ({delay:.0f}s from now)"
        )

    def _maybe_sync_on_start(self):
        """Trigger an immediate sync if the configured time has already passed today.

        Called on startup when the tray was restarted by confirm_and_run.py
        as a fallback (tray was not running when Task Scheduler fired).
        A 3-second delay lets the icon and pystray message pump settle first.
        """
        sync_time_str = self._get_sync_time()
        try:
            hour, minute = map(int, sync_time_str.split(":"))
        except (ValueError, AttributeError):
            return
        now = datetime.now()
        configured_today = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now >= configured_today:
            tray_logger.info("sync-on-start: configured time has passed, triggering immediate sync")

            def _delayed():
                import time as _time

                _time.sleep(3)
                self._on_sync_now()

            threading.Thread(target=_delayed, daemon=True).start()
        else:
            tray_logger.info(
                "sync-on-start: configured time not yet reached, timer will fire at scheduled time"
            )

    def _reload_config(self):
        """Re-read config.json to pick up changes from CLI commands."""
        try:
            if CONFIG_FILE.exists():
                with open(CONFIG_FILE, encoding="utf-8") as f:
                    self._config = json.load(f)
        except Exception:
            pass

    def _on_timer_fired(self):
        """Called by threading.Timer at the configured time."""
        self._reload_config()

        # Guard: threading.Timer uses monotonic time which pauses during
        # system sleep.  If the computer slept, this timer may fire hours
        # after the configured wall-clock time.  Detect drift and
        # re-schedule instead of running a stale sync.
        sync_time_str = self._get_sync_time()
        try:
            hour, minute = map(int, sync_time_str.split(":"))
        except (ValueError, AttributeError):
            hour, minute = 18, 0
        now = datetime.now()
        expected = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        drift = abs((now - expected).total_seconds())
        if drift > 300:  # more than 5 minutes off
            # Save stale target before _schedule_next_sync overwrites it
            stale_target = self._next_sync_target
            tray_logger.info(
                f"Timer drift detected ({drift:.0f}s from {sync_time_str}), "
                f"re-scheduling instead of syncing"
            )
            self._schedule_next_sync()
            # If today's configured time already passed, backfill any
            # missed working days (the daily sync is idempotent).
            if now > expected:
                stale_date = stale_target.date() if stale_target else now.date()
                tray_logger.info("Catchup: today's sync time already passed, backfilling")
                self._catchup_backfill(stale_date)
            return

        tray_logger.info("Timer fired - starting automatic sync")

        # Re-arm for tomorrow before running sync
        self._schedule_next_sync()

        # Run sync automatically without any upfront notification
        self._on_sync_now()

    def _get_user_label(self) -> str:
        """Build user identity label from config."""
        if not self._config:
            return ""
        name = self._config.get("user", {}).get("name", "")
        if name:
            return f"{name} | Vector Solutions"
        return ""

    def _build_menu(self) -> "pystray.Menu":
        """Build the right-click context menu with submenus."""
        user_label = self._get_user_label()
        return pystray.Menu(
            pystray.MenuItem(f"\U0001f464 {user_label}", lambda: None, visible=bool(user_label)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                lambda item: (f"Sync Now (Auto Sync @{self._get_sync_time()})"),
                self._on_sync_now,
                default=True,  # activates on double-click
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
            pystray.MenuItem("Settings", self._on_settings),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Uninstall", self._on_uninstall),
            pystray.MenuItem("Exit", self._on_exit),
        )

    # --- Dynamic menu visibility ---

    def _shortfall_visible(self, item) -> bool:
        """Show 'Fix Monthly Shortfall' only when shortfall
        file exists."""
        return SHORTFALL_FILE.exists()

    def _submit_visible(self, item) -> bool:
        """Show 'Submit Timesheet' in last 7 days of month
        (or earlier when all remaining days are non-working),
        when no shortfall file exists and not yet submitted."""
        today = _today()
        last_day = calendar.monthrange(today.year, today.month)[1]

        # Check if in normal submission window (last 7 days)
        in_window = today.day >= last_day - 6

        # Early submission: show if no working days remain
        early_eligible = False
        if not in_window and self._automation:
            try:
                tomorrow = today + timedelta(days=1)
                last_date = today.replace(day=last_day)
                if tomorrow <= last_date:
                    remaining = self._automation.schedule_mgr.count_working_days(
                        tomorrow.strftime("%Y-%m-%d"), last_date.strftime("%Y-%m-%d")
                    )
                    early_eligible = remaining == 0
            except Exception:
                pass

        if not in_window and not early_eligible:
            return False

        # Hide if shortfall file exists (must fix first)
        if SHORTFALL_FILE.exists():
            return False

        # Hide if already submitted this month
        if SUBMITTED_FILE.exists():
            try:
                with open(SUBMITTED_FILE, encoding="utf-8") as f:
                    data = json.load(f)
                period = f"{today.year}-{today.month:02d}"
                if data.get("period") == period:
                    return False
            except (json.JSONDecodeError, OSError):
                pass

        return True

    def _on_sync_now(self, icon=None, item=None):
        """Clear pending flag and start sync in a background thread."""
        self._pending_confirmation = False
        if self._sync_running.is_set():
            self._show_toast("Sync Already Running", "A sync is already in progress. Please wait.")
            return

        if self._automation is None:
            msg = self._import_error or "Automation not loaded"
            self._show_toast("Error", msg)
            return

        thread = threading.Thread(target=self._run_sync, daemon=True)
        thread.start()

    def _run_sync(self):
        """Background thread that runs the actual sync."""
        self._sync_running.set()
        self._start_sync_animation("Tempo - Syncing...")
        tray_logger.info("Sync started")

        log_f = None
        old_stdout = sys.stdout  # capture before any potential failure
        sync_succeeded = False

        try:
            # Re-create automation instance to pick up fresh config
            # (overhead, PTO, etc. may have changed since startup)
            from tempo_automation import TempoAutomation

            with self._automation_lock:
                self._automation = TempoAutomation(CONFIG_FILE)

            # Redirect stdout to this month's log so sync output
            # is captured (pythonw.exe has no console).
            from datetime import datetime as dt

            log_path = _monthly_log_file()
            log_f = open(log_path, "a", encoding="utf-8")
            log_f.write(f"\n{'=' * 44}\n")
            log_f.write(f"Run: {dt.now():%Y-%m-%d %H:%M:%S} (Tray App)\n")
            log_f.write(f"{'=' * 44}\n")
            with self._stdout_lock:
                sys.stdout = log_f

            result = self._automation.sync_daily()

            with self._stdout_lock:
                sys.stdout = old_stdout
            log_f.close()
            log_f = None

            if self._icon:
                self._icon.update_menu()

            if result is None:
                # Non-working day / health check abort / early exit
                self._set_icon_state("green", "Tempo Automation")
                self._show_toast(
                    "Sync Skipped",
                    "No hours logged -- today is not a working day.",
                )
                tray_logger.info("Sync skipped (non-working day or early exit)")
                sync_succeeded = True
            elif result["hours_logged"] >= result["target_hours"]:
                self._set_icon_state("green", "Tempo - Sync complete")
                self._show_toast(
                    "Sync Complete",
                    f"Daily timesheet synced: {result['hours_logged']:.1f} hrs logged.",
                )
                tray_logger.info("Sync completed successfully")
                sync_succeeded = True
            else:
                hours = result["hours_logged"]
                target = result["target_hours"]
                reason = result.get("reason", "partial")
                if reason == "no_overhead":
                    tooltip = (
                        f"Tempo - Incomplete: {hours:.1f} hrs of {target:.1f} hrs logged. "
                        f"No overhead stories configured. "
                        f"Right-click > Configure > Select Overhead."
                    )
                    body = (
                        "0.0 hrs logged - no overhead stories configured.\n"
                        "Right-click the tray icon > Configure > Select Overhead."
                    )
                elif reason == "no_tickets":
                    tooltip = (
                        f"Tempo - Incomplete: {hours:.1f} hrs of {target:.1f} hrs logged. "
                        f"No active Jira tickets found."
                    )
                    body = (
                        "0.0 hrs logged - no active Jira tickets found.\n"
                        "Ensure tickets are IN DEVELOPMENT or CODE REVIEW."
                    )
                else:
                    tooltip = f"Tempo - Incomplete: {hours:.1f} hrs of {target:.1f} hrs logged."
                    body = f"Only {hours:.1f} hrs of {target:.1f} hrs logged."
                self._set_icon_state("red", tooltip)
                self._show_toast("Sync Incomplete", body)
                tray_logger.warning(f"Sync incomplete: {hours:.1f}h/{target:.1f}h reason={reason}")
                sync_succeeded = False
        except Exception as e:
            with self._stdout_lock:
                sys.stdout = old_stdout
            if log_f and not log_f.closed:
                log_f.close()
            error_msg = str(e)[:200]
            self._set_icon_state("red", f"Tempo - Error: {error_msg}")
            self._show_toast("Sync Failed", f"Error: {error_msg}")
            tray_logger.error(f"Sync failed: {e}", exc_info=True)
            sync_succeeded = False
        finally:
            self._sync_running.clear()

        # Revert icon to green after 5 seconds only on success
        def _revert():
            if not self._sync_running.is_set() and not self._import_error and sync_succeeded:
                self._set_icon_state("green", "Tempo Automation")

        timer = threading.Timer(5.0, _revert)
        timer.daemon = True
        timer.start()

    def _catchup_backfill(self, stale_date: date):
        """Backfill missed working days from *stale_date* through today.

        Called when the wall-clock check or drift guard detects that the
        timer's target was stale (computer slept / was off for days).
        Runs in a background thread, reuses TempoAutomation.backfill_range.
        """

        today = _today()
        if stale_date >= today:
            # Nothing to backfill -- today's catchup sync handles it
            return

        from_str = stale_date.strftime("%Y-%m-%d")
        to_str = today.strftime("%Y-%m-%d")

        if self._sync_running.is_set():
            tray_logger.info("Catchup backfill skipped -- sync already running")
            return

        def _run():
            self._sync_running.set()
            self._start_sync_animation("Tempo - Syncing missed days...")
            tray_logger.info(f"Catchup backfill started: {from_str} to {to_str}")

            log_f = None
            old_stdout = sys.stdout
            try:
                from tempo_automation import TempoAutomation

                with self._automation_lock:
                    self._automation = TempoAutomation(CONFIG_FILE)

                log_path = _monthly_log_file()
                log_f = open(log_path, "a", encoding="utf-8")
                log_f.write(f"\n{'=' * 44}\n")
                log_f.write(f"Run: {datetime.now():%Y-%m-%d %H:%M:%S} (Tray Catchup Backfill)\n")
                log_f.write(f"{'=' * 44}\n")
                with self._stdout_lock:
                    sys.stdout = log_f

                self._automation.backfill_range(from_str, to_str)

                with self._stdout_lock:
                    sys.stdout = old_stdout
                log_f.close()
                log_f = None

                if self._icon:
                    self._icon.update_menu()

                self._set_icon_state("green", "Tempo - Sync complete")
                self._show_toast(
                    "Hours Synced for Missed Days",
                    f"Your computer was off/asleep -- synced hours "
                    f"for {from_str} to {to_str} "
                    f"(skipped weekends/holidays).",
                )
                tray_logger.info("Catchup backfill completed successfully")
            except Exception as e:
                with self._stdout_lock:
                    sys.stdout = old_stdout
                if log_f and not log_f.closed:
                    log_f.close()
                error_msg = str(e)[:200]
                self._set_icon_state("red", f"Tempo - Sync error: {error_msg}")
                self._show_toast(
                    "Missed Days Sync Failed",
                    f"Could not sync hours for {from_str} to {to_str}.\nError: {error_msg}",
                )
                tray_logger.error(f"Catchup backfill failed: {e}", exc_info=True)
            finally:
                self._sync_running.clear()

            def _revert():
                if not self._sync_running.is_set() and not self._import_error:
                    self._set_icon_state("green", "Tempo Automation")

            t = threading.Timer(5.0, _revert)
            t.daemon = True
            t.start()

        threading.Thread(target=_run, daemon=True).start()

    def _on_add_pto(self, icon=None, item=None):
        """Add PTO via two-step dialog: range or single day, then optional Tempo sync."""
        if self._automation is None:
            msg = self._import_error or "Automation not loaded"
            self._show_toast("Error", msg)
            return
        thread = threading.Thread(target=self._run_add_pto, daemon=True)
        thread.start()

    def _run_add_pto(self):
        """Background thread for the Add PTO dialog flow."""
        try:
            # Reload automation from disk so pto_days reflects any manual config edits
            from tempo_automation import TempoAutomation

            with self._automation_lock:
                self._automation = TempoAutomation(CONFIG_FILE)

            use_range = self._show_yesno_dialog(
                "Add PTO for a date range?\n\n"
                "Yes = enter start and end date\n"
                "No  = enter a single date",
                "Tempo - Add PTO",
            )

            if use_range:
                start = self._show_input_dialog(
                    "Enter the START date (YYYY-MM-DD):", "Tempo - Add PTO Range"
                )
                if not start:
                    return
                end = self._show_input_dialog(
                    "Enter the END date (YYYY-MM-DD):", "Tempo - Add PTO Range"
                )
                if not end:
                    return
                try:
                    dates = self._automation.schedule_mgr.expand_date_range(
                        start.strip(), end.strip()
                    )
                except ValueError as e:
                    self._show_toast("Invalid Range", str(e))
                    return
                if not dates:
                    self._show_toast("No Working Days", "No working days found in that range.")
                    return
            else:
                single = self._show_input_dialog(
                    "Enter the PTO date (YYYY-MM-DD):", "Tempo - Add PTO"
                )
                if not single:
                    return
                dates = [single.strip()]

            added, skipped = self._automation.schedule_mgr.add_pto(dates)

            if added and skipped:
                self._show_toast(
                    "PTO Added (with warnings)",
                    f"Added: {', '.join(added)}\nSkipped: {'; '.join(skipped)}",
                )
            elif added:
                self._show_toast("PTO Added", f"Added {len(added)} day(s): {', '.join(added)}")
            else:
                self._show_toast(
                    "No PTO Added", "\n".join(skipped) if skipped else "No valid dates entered."
                )

            if not added:
                return

            today = _today()
            future_dates = [d for d in added if d >= today.strftime("%Y-%m-%d")]
            if not future_dates:
                return

            if not self._automation._is_overhead_configured():
                self._show_toast(
                    "PTO Added",
                    "Overhead story not configured. PTO saved but cannot sync to Tempo.",
                )
                return

            n = len(future_dates)
            date_list = ", ".join(future_dates)
            want_sync = self._show_yesno_dialog(
                f"Sync {n} PTO day(s) to Tempo now?\n\n{date_list}", "Tempo - Sync PTO"
            )
            if want_sync:
                self._sync_pto_dates_background(future_dates)
            else:
                self._show_toast("PTO Added", "PTO saved. Not synced to Tempo.")

        except Exception as e:
            self._show_toast("Error", f"Could not add PTO: {e}")
            tray_logger.error(f"Add PTO failed: {e}", exc_info=True)

    def _show_input_dialog(self, prompt: str, title: str) -> str:
        """
        Show a text input dialog and return user input.
        Windows: PowerShell InputBox (hidden console).
        Mac: AppleScript dialog.
        Returns empty string if user cancelled.
        """
        if sys.platform == "win32":
            return self._show_input_dialog_win(prompt, title)
        elif sys.platform == "darwin":
            return self._show_input_dialog_mac(prompt, title)
        return ""

    def _show_input_dialog_win(self, prompt: str, title: str) -> str:
        """Windows text input dialog via PowerShell .NET InputBox (no temp files).

        Uses CREATE_NO_WINDOW to suppress the PowerShell console flash.
        Tkinter is not available in the embedded Python distribution.
        """
        ps_prompt = prompt.replace("'", "''").replace("\n", "`n")
        ps_title = title.replace("'", "''")
        cmd = [
            "powershell",
            "-NoProfile",
            "-Command",
            (
                "Add-Type -AssemblyName Microsoft.VisualBasic; "
                "[Microsoft.VisualBasic.Interaction]::InputBox("
                f"'{ps_prompt}', '{ps_title}', '')"
            ),
        ]
        try:
            CREATE_NO_WINDOW = 0x08000000
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
                creationflags=CREATE_NO_WINDOW,
            )
            if proc.returncode == 0:
                return proc.stdout.strip()
        except subprocess.TimeoutExpired:
            pass
        return ""

    def _show_input_dialog_mac(self, prompt: str, title: str) -> str:
        """Mac AppleScript text input dialog."""
        # Escape quotes for AppleScript
        safe_prompt = prompt.replace('"', '\\"').replace("\n", "\\n")
        safe_title = title.replace('"', '\\"')
        script = (
            f"set result to text returned of "
            f'(display dialog "{safe_prompt}" '
            f'default answer "" with title "{safe_title}")'
        )
        try:
            proc = subprocess.run(
                ["osascript", "-e", script], capture_output=True, text=True, timeout=120
            )
            if proc.returncode == 0:
                return proc.stdout.strip()
        except subprocess.TimeoutExpired:
            pass
        return ""

    def _show_yesno_dialog(self, msg: str, title: str) -> bool:
        """
        Show a Yes/No dialog. Returns True if user clicked Yes, False otherwise.
        Windows: MessageBoxW with MB_YESNO.
        Mac: osascript with Yes/No buttons.
        """
        if sys.platform == "win32":
            # MB_YESNO=0x04 | MB_ICONQUESTION=0x20 | MB_TOPMOST=0x40000 | MB_SETFOREGROUND=0x10000
            flags = 0x04 | 0x20 | 0x40000 | 0x10000
            result = ctypes.windll.user32.MessageBoxW(0, msg, title, flags)
            return result == 6  # 6 = IDYES
        elif sys.platform == "darwin":
            safe_msg = msg.replace('"', '\\"').replace("\n", "\\n")
            safe_title = title.replace('"', '\\"')
            script = (
                f'display dialog "{safe_msg}" '
                f'buttons {{"No", "Yes"}} '
                f'default button "Yes" '
                f'with title "{safe_title}"'
            )
            try:
                proc = subprocess.run(
                    ["osascript", "-e", script], capture_output=True, text=True, timeout=120
                )
                return "Yes" in proc.stdout
            except subprocess.TimeoutExpired:
                pass
        return False

    def _sync_pto_dates_background(self, dates: list):
        """Sync PTO overhead hours to Tempo for each date in a daemon thread."""

        def _run():
            synced = 0
            failed = 0
            for d in dates:
                try:
                    self._automation.sync_daily(d)
                    synced += 1
                except Exception as e:
                    failed += 1
                    tray_logger.error(f"PTO sync failed for {d}: {e}", exc_info=True)
                    self._show_toast("Sync Error", f"Failed to sync {d}: {e}")
            if failed == 0:
                self._show_toast("PTO Synced", f"Synced {synced} day(s) to Tempo.")
            else:
                self._show_toast(
                    "PTO Sync Partial",
                    f"Synced {synced} day(s). {failed} failed (see log).",
                )

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()

    def _on_change_sync_time(self, icon=None, item=None):
        """Show input dialog to change the daily sync time."""
        import re

        current = self._get_sync_time()
        try:
            raw = self._show_input_dialog(
                f"Current sync time: {current}\n"
                "Enter new time (HH:MM, 24-hour format):\n\n"
                "Example: 17:30",
                "Tempo - Change Sync Time",
            )
            if not raw:
                return

            raw = raw.strip()
            if not re.match(r"^\d{1,2}:\d{2}$", raw):
                self._show_toast(
                    "Invalid Time", f'"{raw}" is not a valid time.\nUse HH:MM format (e.g. 17:30).'
                )
                return

            hour, minute = map(int, raw.split(":"))
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                self._show_toast("Invalid Time", "Hours must be 0-23, minutes 0-59.")
                return

            # Normalize to zero-padded format
            new_time = f"{hour:02d}:{minute:02d}"

            # Read config, update, write back
            config_data = {}
            if CONFIG_FILE.exists():
                with open(CONFIG_FILE, encoding="utf-8") as f:
                    config_data = json.load(f)

            if "schedule" not in config_data:
                config_data["schedule"] = {}
            config_data["schedule"]["daily_sync_time"] = new_time

            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config_data, f, indent=4)

            self._reload_config()
            self._schedule_next_sync()
            self._update_task_scheduler_time(new_time)

            self._show_toast("Sync Time Updated", f"Daily sync time changed to {new_time}.")
            tray_logger.info(f"Sync time changed to {new_time}")

        except Exception as e:
            self._show_toast("Error", f"Could not change sync time: {e}")
            tray_logger.error(f"Change sync time failed: {e}", exc_info=True)

    def _update_task_scheduler_time(self, new_time: str):
        """Update the Windows Task Scheduler daily sync task to the new time.

        Keeps the Task Scheduler task in sync with the tray-configured time
        so the fallback restart (confirm_and_run.py) fires at the right hour.
        """
        if sys.platform != "win32":
            return
        try:
            result = subprocess.run(
                ["schtasks", "/Change", "/TN", "TempoAutomation-DailySync", "/ST", new_time],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                tray_logger.info(
                    f"Task Scheduler 'TempoAutomation-DailySync' updated to {new_time}"
                )
            else:
                tray_logger.warning(
                    f"Could not update Task Scheduler time: {result.stderr.strip()}"
                )
        except Exception as e:
            tray_logger.warning(f"Could not update Task Scheduler time: {e}")

    def _reconcile_task_scheduler(self):
        """Ensure Windows Task Scheduler time matches config on startup.

        Covers the case where config was edited directly (e.g. --setup)
        but the Task Scheduler task still has the old/default time.
        """
        if sys.platform != "win32":
            return
        sync_time = self._get_sync_time()
        self._update_task_scheduler_time(sync_time)
        tray_logger.info(f"Task Scheduler reconciled to config sync time: {sync_time}")

    def _on_select_overhead(self, icon=None, item=None):
        """Open a terminal window for overhead story selection."""
        self._open_in_terminal("--select-overhead")

    def _on_view_log(self, icon=None, item=None):
        """Open this week's daily log file in the default text editor."""
        current_log = _monthly_log_file()
        if not current_log.exists():
            self._show_toast("No Log", "Log file not found yet.")
            return
        if sys.platform == "win32":
            subprocess.Popen(["notepad.exe", str(current_log)])
        else:
            subprocess.Popen(["open", str(current_log)])

    def _run_and_show_dialog(self, cli_arg: str, title: str):
        """Run a read-only CLI command and show captured output in a popup dialog.

        Same pattern as Add PTO: no terminal window, native popup, closes on OK.
        """
        script = SCRIPT_DIR / "tempo_automation.py"
        python_exe = Path(sys.executable).parent / "python.exe"
        try:
            result = subprocess.run(
                [str(python_exe), str(script), cli_arg],
                capture_output=True,
                text=True,
                cwd=str(SCRIPT_DIR),
                timeout=60,
            )
            output = (result.stdout or "").strip() or (result.stderr or "").strip() or "(no output)"
        except subprocess.TimeoutExpired:
            output = "Timed out."
        except Exception as e:
            output = f"Error: {e}"

        if sys.platform == "win32":
            self._show_text_dialog_win(output, title)
        elif sys.platform == "darwin":
            self._show_text_dialog_mac(output, title)

    def _show_text_dialog_win(self, text: str, title: str):
        """Display multi-line text in a MessageBoxW dialog (ctypes)."""
        MB_OK = 0x00000000
        MB_SETFOREGROUND = 0x00010000
        ctypes.windll.user32.MessageBoxW(0, text, title, MB_OK | MB_SETFOREGROUND)

    def _show_text_dialog_mac(self, text: str, title: str):
        """Display text in an AppleScript dialog (same mechanism as Add PTO)."""
        safe_text = text.replace('"', '\\"').replace("\n", "\\n")
        safe_title = title.replace('"', '\\"')
        script = f'display dialog "{safe_text}" buttons {{"OK"}} with title "{safe_title}"'
        try:
            subprocess.run(["osascript", "-e", script], timeout=300)
        except subprocess.TimeoutExpired:
            pass

    def _on_view_schedule(self, icon=None, item=None):
        """Show schedule calendar in a terminal window."""
        self._open_in_terminal("--show-schedule")

    def _on_view_monthly(self, icon=None, item=None):
        """Show monthly hours report in a terminal window."""
        self._open_in_terminal("--view-monthly")

    def _on_fix_shortfall(self, icon=None, item=None):
        """Open a terminal window for interactive shortfall fix."""
        self._open_in_terminal("--fix-shortfall")

    def _on_submit_timesheet(self, icon=None, item=None):
        """Run monthly submission in a background thread."""
        if self._sync_running.is_set():
            self._show_toast("Busy", "A sync is already in progress. Please wait.")
            return

        if self._automation is None:
            msg = self._import_error or "Automation not loaded"
            self._show_toast("Error", msg)
            return

        thread = threading.Thread(target=self._run_submit, daemon=True)
        thread.start()

    def _run_submit(self):
        """Background thread that runs timesheet submission."""
        self._sync_running.set()
        self._start_sync_animation("Tempo - Submitting timesheet...")
        tray_logger.info("Timesheet submission started from tray")

        try:
            # Re-create automation instance for fresh config
            from tempo_automation import TempoAutomation

            with self._automation_lock:
                self._automation = TempoAutomation(CONFIG_FILE)

            # Redirect stdout to this month's log (rotates on the 1st)
            log_path = _monthly_log_file()
            log_f = open(log_path, "a", encoding="utf-8")
            log_f.write(f"\n{'=' * 44}\n")
            log_f.write(f"Run: {datetime.now():%Y-%m-%d %H:%M:%S} (Tray Submit)\n")
            log_f.write(f"{'=' * 44}\n")
            with self._stdout_lock:
                old_stdout = sys.stdout
                sys.stdout = log_f

            self._automation.submit_timesheet()

            with self._stdout_lock:
                sys.stdout = old_stdout
            log_f.close()

            # Check result by looking at marker files
            today = _today()
            period = f"{today.year}-{today.month:02d}"

            if SUBMITTED_FILE.exists():
                try:
                    with open(SUBMITTED_FILE, encoding="utf-8") as f:
                        sdata = json.load(f)
                    if sdata.get("period") == period:
                        self._set_icon_state("green", "Tempo - Submitted!")
                        # Refresh menu to hide Submit Timesheet
                        if self._icon:
                            self._icon.update_menu()
                        self._show_toast(
                            "Timesheet Submitted",
                            f"Your timesheet for {period} has been submitted.",
                        )
                        tray_logger.info("Submission successful from tray")
                        return
                except (json.JSONDecodeError, OSError):
                    pass

            if SHORTFALL_FILE.exists():
                self._set_icon_state("orange", "Tempo - Shortfall detected")
                # Force menu refresh so "Fix Monthly Shortfall"
                # becomes visible in the Log and Reports submenu
                if self._icon:
                    self._icon.update_menu()
                self._show_toast(
                    "Shortfall Detected",
                    'Your timesheet has gaps. Use "Fix Monthly Shortfall" from the tray menu.',
                )
            else:
                # No submitted marker and no shortfall file:
                # check if today is last day -- if so, submit
                # failed; otherwise it's a pre-check
                last_day_num = calendar.monthrange(today.year, today.month)[1]
                if today.day == last_day_num:
                    self._set_icon_state("red", "Tempo - Submission failed")
                    self._show_toast(
                        "Submission Failed",
                        "Timesheet submission failed. Check this month's log file for details.",
                    )
                    tray_logger.error("Submission failed: no marker file created on last day")
                else:
                    self._set_icon_state("green", "Tempo - Submission check complete")
                    self._show_toast(
                        "Submission Check",
                        "Hours look good. Auto-submission "
                        "will happen on the last day of "
                        "the month.",
                    )

        except Exception as e:
            with self._stdout_lock:
                sys.stdout = old_stdout
            if "log_f" in locals() and not log_f.closed:
                log_f.close()
            error_msg = str(e)[:200]
            self._set_icon_state("red", f"Tempo - Error: {error_msg}")
            self._show_toast("Submit Failed", f"Error: {error_msg}")
            tray_logger.error(f"Submission failed: {e}", exc_info=True)
        finally:
            self._sync_running.clear()

            # Revert icon after 5 seconds
            def _revert():
                if not self._sync_running.is_set():
                    self._set_icon_state("green", "Tempo Automation")

            revert_timer = threading.Timer(5.0, _revert)
            revert_timer.daemon = True
            revert_timer.start()

    def _open_in_terminal(self, cli_arg: str):
        """
        Open tempo_automation.py with a CLI argument in a new terminal.
        Windows: cmd /c with CREATE_NEW_CONSOLE, appends a custom echo
                 so the window shows 'Press any key to exit...' before closing.
        Mac: osascript to open Terminal.app with command + read prompt.

        Waits for the process to finish in a daemon thread, then
        refreshes the tray menu (so dynamic items like Fix Shortfall
        appear/disappear based on file changes).
        """
        script = SCRIPT_DIR / "tempo_automation.py"
        if sys.platform == "win32":
            python_dir = Path(sys.executable).parent
            python_exe = python_dir / "python.exe"
            # cmd /c runs the command and exits.  The trailing echo+pause
            # shows a custom 'Press any key to exit...' prompt so the
            # user can read the output before the window closes.
            # pause >nul suppresses the default "Press any key to
            # continue..." text; our echo replaces it.
            # Outer quotes required: cmd strips first and last " from
            # the command line; without them inner quotes get mangled.
            proc = subprocess.Popen(
                f'cmd /c ""{python_exe}" "{script}" {cli_arg} & echo. & echo Press any key to exit... & pause >nul"',
                cwd=str(SCRIPT_DIR),
                creationflags=subprocess.CREATE_NEW_CONSOLE,
            )
        elif sys.platform == "darwin":
            cmd = (
                f'cd "{SCRIPT_DIR}" && python3 "{script}" {cli_arg}'
                '; echo ""; echo "Press Enter to exit..."; read'
            )
            proc = subprocess.Popen(
                ["osascript", "-e", f'tell app "Terminal" to do script "{cmd}"']
            )
        else:
            # Linux fallback
            proc = subprocess.Popen(
                ["x-terminal-emulator", "-e", "python3", str(script), cli_arg], cwd=str(SCRIPT_DIR)
            )

        # Wait for the terminal to close, then refresh dynamic menu items
        def _wait_and_refresh():
            try:
                proc.wait()
            except Exception:
                pass
            if self._icon:
                self._icon.update_menu()

        t = threading.Thread(target=_wait_and_refresh, daemon=True)
        t.start()

    def _on_settings(self, icon=None, item=None):
        """Open config.json in the default editor."""
        config_path = str(CONFIG_FILE)
        if not CONFIG_FILE.exists():
            self._show_toast("No Config", "config.json not found. Run setup first.")
            return
        if sys.platform == "win32":
            os.startfile(config_path)
        else:
            subprocess.Popen(["open", config_path])

    def _on_uninstall(self, icon=None, item=None):
        """Start uninstall flow in a daemon thread (pystray callback must return quickly)."""
        thread = threading.Thread(target=self._uninstall_flow, daemon=True)
        thread.start()

    def _uninstall_confirm_dialog(self) -> bool:
        """Show destructive-action confirmation. Returns True if user confirmed uninstall."""
        msg = (
            "This will permanently delete:\n"
            f"  - {SCRIPT_DIR}\n"
            "  - All scheduled tasks\n"
            "  - Autostart registration\n\n"
            "Your config and logs will be deleted.\n\n"
            "Are you sure you want to uninstall?"
        )
        title = "Uninstall Tempo Automation"
        if sys.platform == "win32":
            # MB_YESNO | MB_ICONEXCLAMATION | MB_TOPMOST | MB_DEFBUTTON2
            flags = 0x04 | 0x30 | 0x40000 | 0x100  # default = No
            result = ctypes.windll.user32.MessageBoxW(0, msg, title, flags)
            return result == 6  # 6 = IDYES
        elif sys.platform == "darwin":
            safe_msg = msg.replace('"', '\\"').replace("\n", "\\n")
            script = (
                f'display dialog "{safe_msg}" '
                f'buttons {{"Cancel", "Uninstall"}} '
                f'default button "Cancel" '
                f'with title "{title}" with icon stop'
            )
            proc = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
            return "Uninstall" in proc.stdout
        return False

    def _uninstall_scheduled_tasks(self):
        """Delete the 3 Task Scheduler tasks created by install.bat (Windows only)."""
        tasks = [
            "TempoAutomation-DailySync",
            "TempoAutomation-WeeklyVerify",
            "TempoAutomation-MonthlySubmit",
        ]
        for task in tasks:
            try:
                subprocess.run(
                    ["schtasks", "/Delete", "/TN", task, "/F"], capture_output=True, timeout=10
                )
                tray_logger.info(f"Deleted task: {task}")
            except Exception as e:
                tray_logger.warning(f"Could not delete task {task}: {e}")

    def _schedule_folder_delete(self):
        """Launch a detached process to delete SCRIPT_DIR after Python exits."""
        if sys.platform == "win32":
            import os

            bat = f'@echo off\nping -n 4 localhost >nul\nrmdir /s /q "{SCRIPT_DIR}"\n'
            bat_path = Path(os.environ.get("TEMP", str(SCRIPT_DIR.parent))) / "_tempo_uninstall.bat"
            bat_path.write_text(bat)
            subprocess.Popen(
                ["cmd", "/c", str(bat_path)],
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
            )
        elif sys.platform == "darwin":
            subprocess.Popen(["bash", "-c", f'sleep 3 && rm -rf "{SCRIPT_DIR}"'])

    def _uninstall_flow(self):
        """Orchestrate full uninstall: confirm, clean up, delete folder, stop icon."""
        if not self._uninstall_confirm_dialog():
            return  # user cancelled

        tray_logger.info("Uninstall initiated")

        # Step 1: unregister autostart (removes registry key / LaunchAgent plist)
        try:
            unregister_autostart()
        except Exception as e:
            tray_logger.warning(f"Could not unregister autostart: {e}")

        # Step 2: remove scheduled tasks (Windows) or cron entries (Mac)
        if sys.platform == "win32":
            self._uninstall_scheduled_tasks()
        elif sys.platform == "darwin":
            try:
                result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
                new_cron = "\n".join(
                    line for line in result.stdout.splitlines() if "tempo_automation.py" not in line
                )
                subprocess.run(["crontab", "-"], input=new_cron, text=True, capture_output=True)
                tray_logger.info("Cron entries removed")
            except Exception as e:
                tray_logger.warning(f"Could not remove cron entries: {e}")

        # Step 3: remove AppData config backup so a fresh re-install doesn't inherit old credentials
        try:
            from tempo_automation import CONFIG_BACKUP_FILE

            if CONFIG_BACKUP_FILE.exists():
                CONFIG_BACKUP_FILE.unlink()
                tray_logger.info(f"Removed AppData config backup: {CONFIG_BACKUP_FILE}")
        except Exception as e:
            tray_logger.warning(f"Could not remove AppData config backup: {e}")

        # Step 4: stop animation and timer
        self._stop_sync_animation()
        if self._timer:
            self._timer.cancel()

        # Step 5: goodbye toast
        self._show_toast(
            "Uninstall Complete",
            "Tempo Automation has been uninstalled. Goodbye!",
            app_name="Tempo Automation",
        )

        # Step 6: schedule folder deletion then stop the icon
        self._schedule_folder_delete()
        if self._icon:
            self._icon.stop()

    def _on_exit(self, icon=None, item=None):
        """Start smart exit in a separate thread (pystray callback must return quickly)."""
        thread = threading.Thread(target=self._exit_flow, daemon=True)
        thread.start()

    def _exit_flow(self):
        """Smart exit: check hours, show dialog, then stop."""
        should_warn = False
        hours_logged = 0.0
        daily_hours = 8.0
        today = _today().strftime("%Y-%m-%d")

        if self._automation:
            schedule_mgr = self._automation.schedule_mgr
            daily_hours = schedule_mgr.daily_hours
            is_working, reason = schedule_mgr.is_working_day(today)

            if is_working:
                self._set_icon_state("orange", "Checking hours...")
                try:
                    # Tempo is source of truth (catches manual entries)
                    tc = self._automation.tempo_client
                    if tc.account_id:
                        tempo_wls = tc.get_user_worklogs(today, today)
                        hours_logged = sum(w.get("timeSpentSeconds", 0) for w in tempo_wls) / 3600
                    # Fallback: Jira API if Tempo returned 0
                    if hours_logged == 0.0 and self._automation.jira_client:
                        jira_wls = self._automation.jira_client.get_my_worklogs(today, today)
                        hours_logged = sum(w.get("time_spent_seconds", 0) for w in jira_wls) / 3600
                    if hours_logged < daily_hours:
                        should_warn = True
                except Exception as e:
                    tray_logger.warning(f"Could not check hours on exit: {e}")
                    should_warn = True
                self._set_icon_state("green", "Tempo Automation")

        if should_warn:
            msg = (
                f"You haven't logged hours for today "
                f"({hours_logged:.1f} hrs / {daily_hours:.1f} hrs).\n\n"
                f"The app will remind you at "
                f"{self._get_sync_time()}.\n\n"
                f"Exit anyway?"
            )
            user_wants_to_stay = self._show_confirm_dialog(msg, "Tempo Automation")
            if user_wants_to_stay:
                tray_logger.info("User chose to stay running")
                return

        self._schedule_restart()

        tray_logger.info("Tray app exiting")
        self._stop_sync_animation()
        if self._timer:
            self._timer.cancel()
        if self._icon:
            self._icon.stop()

    def _show_confirm_dialog(self, msg: str, title: str) -> bool:
        """
        Show a Yes/No confirmation dialog. Returns True if user chose No
        (i.e., wants to stay / cancel the action).
        Windows: MessageBoxW.
        Mac: osascript dialog.
        """
        if sys.platform == "win32":
            # MB_YESNO | MB_ICONWARNING | MB_TOPMOST | MB_SETFOREGROUND
            flags = 0x04 | 0x30 | 0x40000 | 0x10000
            result = ctypes.windll.user32.MessageBoxW(0, msg, title, flags)
            return result != 6  # 6 = IDYES
        elif sys.platform == "darwin":
            safe_msg = msg.replace('"', '\\"').replace("\n", "\\n")
            script = (
                f'display dialog "{safe_msg}" '
                f'buttons {{"Exit", "Stay Running"}} '
                f'default button "Stay Running" '
                f'with title "{title}" '
                f"with icon caution"
            )
            proc = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
            return "Stay Running" in proc.stdout
        return False  # Default: allow exit

    def _schedule_restart(self):
        """
        Schedule tray app to relaunch at sync time.
        Windows: one-time Task Scheduler task.
        Mac: log a reminder (launchd one-shot is too complex).
        """
        if sys.platform != "win32":
            sync_time = self._get_sync_time()
            tray_logger.info(
                f"Tray app exiting. Restart manually or it will "
                f"launch at next login. Sync time: {sync_time}"
            )
            return
        try:
            pythonw = _find_pythonw()
            tray_script = str(SCRIPT_DIR / "tray_app.py")
            sync_time = self._get_sync_time()

            cmd = [
                "schtasks",
                "/Create",
                "/TN",
                "TempoTrayRestart",
                "/SC",
                "ONCE",
                "/ST",
                sync_time,
                "/TR",
                f'"{pythonw}" "{tray_script}"',
                "/F",
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                tray_logger.info(f"Restart scheduled at {sync_time} via TempoTrayRestart task")
            else:
                tray_logger.warning(f"Failed to schedule restart: {result.stderr}")
        except Exception as e:
            tray_logger.error(f"Could not schedule restart: {e}")

    def _start_sync_animation(self, tooltip: str = "Tempo - Syncing..."):
        """Start animated dot: alternates orange <-> red every 700ms."""
        self._anim_running = True
        self._anim_phase = False  # False=orange, True=red

        def _tick():
            if not self._anim_running or not self._icon:
                return
            dot = "red" if self._anim_phase else "orange"
            self._anim_phase = not self._anim_phase
            try:
                self._icon.icon = _make_icon(dot)
                self._icon.title = tooltip
            except Exception:
                pass
            # Schedule next tick
            self._anim_timer = threading.Timer(0.7, _tick)
            self._anim_timer.daemon = True
            self._anim_timer.start()

        _tick()

    def _stop_sync_animation(self):
        """Stop the syncing dot animation."""
        self._anim_running = False
        if self._anim_timer:
            self._anim_timer.cancel()
            self._anim_timer = None

    def _set_icon_state(self, color: str, tooltip: str):
        """Thread-safe icon and tooltip update. Stops animation first."""
        self._stop_sync_animation()
        if self._icon:
            try:
                self._icon.icon = _make_icon(color)
                self._icon.title = tooltip
            except Exception as e:
                tray_logger.error(f"Failed to update icon: {e}")

    def _show_toast(self, title: str, body: str, app_name: str = ""):
        """Show a desktop notification (Windows toast or Mac osascript)."""
        if sys.platform == "win32":
            if not WINOTIFY_OK:
                tray_logger.warning("winotify not available, skipping toast")
                return
            try:
                toast = Notification(
                    app_id=app_name or "Tempo Automation",
                    title=title,
                    msg=body,
                    duration="long",
                    icon=(str(FAVICON_PATH) if FAVICON_PATH.exists() else ""),
                )
                toast.show()
            except Exception as e:
                tray_logger.error(f"Toast notification failed: {e}")
        elif sys.platform == "darwin":
            try:
                safe_title = title.replace('"', '\\"')
                safe_body = body.replace('"', '\\"')
                script = f'display notification "{safe_body}" with title "{safe_title}"'
                subprocess.Popen(["osascript", "-e", script])
            except Exception as e:
                tray_logger.error(f"Mac notification failed: {e}")
        else:
            tray_logger.info(f"Notification: {title} - {body}")

    def _ensure_autostart(self):
        """Register auto-start if not already present."""
        if sys.platform == "win32":
            import winreg

            try:
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY, 0, winreg.KEY_READ)
                try:
                    winreg.QueryValueEx(key, REG_VALUE)
                    winreg.CloseKey(key)
                    return  # Already registered
                except FileNotFoundError:
                    winreg.CloseKey(key)
            except Exception:
                pass
        elif sys.platform == "darwin":
            if LAUNCH_AGENT_PLIST.exists():
                return  # Already registered

        # Not registered -- register now
        tray_logger.info("Auto-start not found, registering...")
        register_autostart()

    def run(self, quiet: bool = False, upgraded: bool = False, sync_on_start: bool = False):
        """Main entry point -- blocks on pystray message pump.

        Args:
            quiet: If True, show a 'back online' toast instead of
                   the full welcome greeting (used when restarted
                   by the daily scheduler).
            upgraded: If True, show an upgrade success toast instead
                      of the normal welcome greeting.
            sync_on_start: If True and the configured sync time has
                           already passed today, trigger an immediate
                           sync.  Used when confirm_and_run.py restarts
                           the tray as a fallback.
        """
        if not PYSTRAY_OK:
            print(
                "ERROR: pystray and Pillow are required.\nInstall with: pip install pystray Pillow"
            )
            sys.exit(1)

        # Clean up stale stop signal from previous runs
        if STOP_FILE.exists():
            STOP_FILE.unlink()

        if not self._check_single_instance():
            print("Another instance of Tempo Tray App is already running.")
            sys.exit(0)

        # Ensure auto-start is registered (default behavior)
        self._ensure_autostart()

        # Load automation (deferred import)
        self._load_automation()

        # Determine initial state
        if self._import_error:
            initial_color = "red"
            initial_tooltip = f"Tempo - Error: {self._import_error[:100]}"
        else:
            initial_color = "green"
            initial_tooltip = "Tempo Automation"
            # Schedule the notification timer
            self._schedule_next_sync()
            self._reconcile_task_scheduler()
            if sync_on_start:
                self._maybe_sync_on_start()

        self._icon = pystray.Icon(
            name="TempoAutomation",
            icon=_make_icon(initial_color),
            title=initial_tooltip,
            menu=self._build_menu(),
        )

        # Clean up any one-time restart task from a previous "Exit Anyway"
        if sys.platform == "win32":
            subprocess.run(
                ["schtasks", "/Delete", "/TN", "TempoTrayRestart", "/F"], capture_output=True
            )

        # Start stop-file watcher (allows --stop from another process)
        self._stop_watcher_running = True

        def _watch_stop_file():
            check_counter = 0
            while self._stop_watcher_running:
                if STOP_FILE.exists():
                    tray_logger.info("Stop signal received, shutting down")
                    try:
                        STOP_FILE.unlink()
                    except OSError:
                        pass
                    self._stop_sync_animation()
                    if self._timer:
                        self._timer.cancel()
                    if self._icon:
                        self._icon.stop()
                    return
                # External processes (CLI) signal menu refresh by
                # creating this file after changing shortfall state.
                if MENU_REFRESH_SIGNAL.exists():
                    try:
                        MENU_REFRESH_SIGNAL.unlink()
                    except OSError:
                        pass
                    if self._icon:
                        self._icon.update_menu()
                # Every ~60s, check if the timer's wall-clock target has
                # passed (catches monotonic clock drift from system sleep).
                check_counter += 1
                if check_counter >= 60 and self._next_sync_target is not None:
                    check_counter = 0
                    now = datetime.now()
                    if now > self._next_sync_target + timedelta(minutes=5):
                        # Save stale target before re-scheduling
                        stale_target = self._next_sync_target
                        tray_logger.info(
                            f"Wall-clock check: target "
                            f"{stale_target:%Y-%m-%d %H:%M} passed, "
                            f"re-scheduling"
                        )
                        self._schedule_next_sync()
                        # Backfill missed days from stale target
                        # through today (includes today if time passed).
                        self._catchup_backfill(stale_target.date())
                import time

                time.sleep(1)

        watcher = threading.Thread(target=_watch_stop_file, daemon=True)
        watcher.start()

        tray_logger.info("Tray app started")

        # Show startup toast after icon is visible (slight delay
        # so the icon renders before the notification fires)
        if not self._import_error:

            def _startup_toast():
                user_name = ""
                if self._config:
                    user_name = self._config.get("user", {}).get("name", "")
                    user_name = user_name.split()[0] if user_name else ""
                welcome_app = (
                    f"Welcome back, {user_name}! \U0001f44f"
                    if user_name
                    else "Welcome back! \U0001f44f"
                )
                if upgraded:
                    self._show_toast(
                        "The app has been upgraded and is now "
                        "running from C:\\tempo-timesheet\\\n"
                        "Right-click the tray icon to get started.",
                        "",
                        app_name="Upgrade Complete! \U0001f389",
                    )
                elif quiet:
                    # Restarted by daily scheduler
                    self._show_toast(
                        "The Tempo app was previously terminated and is now back online.",
                        "You can continue to use it.",
                        app_name=welcome_app,
                    )
                else:
                    # Normal login start -- full welcome greeting
                    sync_time = self._get_sync_time()
                    hour = datetime.now().hour
                    if hour < 12:
                        time_greeting = "Good Morning"
                        emoji = "\u2600\ufe0f"  # sun
                    elif hour < 17:
                        time_greeting = "Good Afternoon"
                        emoji = "\U0001f324\ufe0f"  # sun behind cloud
                    else:
                        time_greeting = "Good Evening"
                        emoji = "\U0001f319"  # crescent moon
                    welcome_app = (
                        f"Welcome, {user_name}! \U0001f44f" if user_name else "Welcome! \U0001f44f"
                    )
                    self._show_toast(
                        f"{time_greeting}! {emoji}",
                        f"Tempo Automation is running.\n"
                        f"Your hours will be logged at "
                        f"{sync_time} today.\n"
                        f"Right-click the tray icon to sync "
                        f"now, add PTO, or manage your "
                        f"schedule.",
                        app_name=welcome_app,
                    )

            welcome_timer = threading.Timer(2.0, _startup_toast)
            welcome_timer.daemon = True
            welcome_timer.start()

        self._icon.run()  # Blocks (Win32 message pump)


# ============================================================================
# AUTO-START REGISTRATION
# ============================================================================


def register_autostart():
    """
    Register the tray app to start on login.
    Windows: HKCU registry key.
    Mac: LaunchAgent plist in ~/Library/LaunchAgents/.
    """
    pythonw = _find_pythonw()
    tray_script = str(SCRIPT_DIR / "tray_app.py")

    if sys.platform == "win32":
        import winreg

        command = f'"{pythonw}" "{tray_script}"'
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY, 0, winreg.KEY_SET_VALUE)
            winreg.SetValueEx(key, REG_VALUE, 0, winreg.REG_SZ, command)
            winreg.CloseKey(key)
            print(f"{_C_OK}[OK]{_C_R} Auto-start registered")
            tray_logger.info(f"Auto-start registered: {command}")
        except Exception as e:
            print(f"{_C_FAIL}[FAIL]{_C_R} Could not register auto-start: {e}")
            tray_logger.error(f"Auto-start registration failed: {e}")

    elif sys.platform == "darwin":
        plist_content = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"'
            ' "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0">\n'
            "<dict>\n"
            f"    <key>Label</key>"
            f"<string>{LAUNCH_AGENT_LABEL}</string>\n"
            "    <key>ProgramArguments</key>\n"
            "    <array>\n"
            f"        <string>{sys.executable}</string>\n"
            f"        <string>{tray_script}</string>\n"
            "    </array>\n"
            "    <key>RunAtLoad</key><true/>\n"
            "    <key>KeepAlive</key><false/>\n"
            "</dict>\n"
            "</plist>\n"
        )
        try:
            LAUNCH_AGENT_PLIST.parent.mkdir(parents=True, exist_ok=True)
            LAUNCH_AGENT_PLIST.write_text(plist_content)
            subprocess.run(["launchctl", "load", str(LAUNCH_AGENT_PLIST)], capture_output=True)
            print(f"{_C_OK}[OK]{_C_R} Auto-start registered: {LAUNCH_AGENT_PLIST}")
            tray_logger.info(f"LaunchAgent registered: {LAUNCH_AGENT_PLIST}")
        except Exception as e:
            print(f"{_C_FAIL}[FAIL]{_C_R} Could not register auto-start: {e}")
            tray_logger.error(f"LaunchAgent registration failed: {e}")
    else:
        print(f"{_C_WARN}[!]{_C_R} Auto-start not supported on this platform")


def unregister_autostart():
    """
    Remove auto-start registration.
    Windows: delete registry entry.
    Mac: unload and delete LaunchAgent plist.
    """
    if sys.platform == "win32":
        import winreg

        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY, 0, winreg.KEY_SET_VALUE)
            winreg.DeleteValue(key, REG_VALUE)
            winreg.CloseKey(key)
            print(f"{_C_OK}[OK]{_C_R} Auto-start removed")
            tray_logger.info("Auto-start removed")
        except FileNotFoundError:
            print(f"{_C_OK}[OK]{_C_R} Auto-start was not registered")
        except Exception as e:
            print(f"{_C_FAIL}[FAIL]{_C_R} Could not remove auto-start: {e}")
            tray_logger.error(f"Auto-start removal failed: {e}")

    elif sys.platform == "darwin":
        try:
            if LAUNCH_AGENT_PLIST.exists():
                subprocess.run(
                    ["launchctl", "unload", str(LAUNCH_AGENT_PLIST)], capture_output=True
                )
                LAUNCH_AGENT_PLIST.unlink()
                print(f"{_C_OK}[OK]{_C_R} Auto-start removed")
                tray_logger.info("LaunchAgent removed")
            else:
                print(f"{_C_OK}[OK]{_C_R} Auto-start was not registered")
        except Exception as e:
            print(f"{_C_FAIL}[FAIL]{_C_R} Could not remove auto-start: {e}")
            tray_logger.error(f"LaunchAgent removal failed: {e}")
    else:
        print(f"{_C_WARN}[!]{_C_R} Auto-start not supported on this platform")


def stop_app():
    """Signal a running tray app instance to shut down via stop file."""
    STOP_FILE.write_text("stop")
    print("[OK] Stop signal sent to running tray app")
    tray_logger.info("Stop signal file created")

    # Wait up to 5 seconds for the process to exit
    import time

    for _ in range(10):
        time.sleep(0.5)
        if not STOP_FILE.exists():
            print("[OK] Tray app stopped")
            return
    # Clean up if it didn't pick up the signal
    if STOP_FILE.exists():
        try:
            STOP_FILE.unlink()
        except OSError:
            pass
    print("[OK] Stop signal sent (app may take a moment to exit)")


# ============================================================================
# ENTRY POINT
# ============================================================================


def main():
    """Entry point with argument parsing."""
    parser = argparse.ArgumentParser(description="Tempo Automation - System Tray App")
    parser.add_argument("--register", action="store_true", help="Register auto-start on login")
    parser.add_argument("--unregister", action="store_true", help="Remove auto-start from login")
    parser.add_argument("--stop", action="store_true", help="Stop a running tray app instance")
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Start with a restart toast instead of welcome greeting",
    )
    parser.add_argument(
        "--upgraded", action="store_true", help="Show upgrade success toast on startup"
    )
    parser.add_argument(
        "--sync-on-start",
        action="store_true",
        help="Trigger an immediate sync if configured time has passed today",
    )
    args = parser.parse_args()

    if args.register:
        register_autostart()
    elif args.unregister:
        unregister_autostart()
    elif args.stop:
        stop_app()
    else:
        app = TrayApp()
        app.run(quiet=args.quiet, upgraded=args.upgraded, sync_on_start=args.sync_on_start)


if __name__ == "__main__":
    main()
