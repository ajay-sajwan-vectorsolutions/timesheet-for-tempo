#!/usr/bin/env python3
"""
Tempo Automation - System Tray Application
============================================

Persistent system tray icon that:
- Notifies at the configured daily_sync_time (default 18:00)
- Lets the user confirm/trigger sync via the tray menu
- Changes icon color to show status (green/yellow/red)
- Auto-starts on Windows login via registry key

Usage:
    pythonw.exe tray_app.py              # Run the tray app (no console)
    python tray_app.py --register        # Register auto-start on login
    python tray_app.py --unregister      # Remove auto-start

Author: Vector Solutions Engineering Team
"""

import sys
import os
import argparse
import threading
import subprocess
import ctypes
import logging
from datetime import datetime, timedelta
from pathlib import Path

# Conditional imports -- tray app degrades gracefully if missing
try:
    import pystray
    from PIL import Image, ImageDraw, ImageFont
    PYSTRAY_OK = True
except ImportError:
    PYSTRAY_OK = False

try:
    from winotify import Notification
    WINOTIFY_OK = True
except ImportError:
    WINOTIFY_OK = False

SCRIPT_DIR = Path(__file__).parent
CONFIG_FILE = SCRIPT_DIR / "config.json"
LOG_FILE = SCRIPT_DIR / "daily-timesheet.log"
INTERNAL_LOG = SCRIPT_DIR / "tempo_automation.log"

REG_KEY = r'SOFTWARE\Microsoft\Windows\CurrentVersion\Run'
REG_VALUE = 'TempoTrayApp'
MUTEX_NAME = 'TempoTrayApp_SingleInstance_Mutex'
STOP_FILE = SCRIPT_DIR / '_tray_stop.signal'

# Tray app logger (separate from tempo_automation logger)
tray_logger = logging.getLogger('tray_app')
tray_logger.setLevel(logging.INFO)
_handler = logging.FileHandler(INTERNAL_LOG, encoding='utf-8')
_handler.setFormatter(
    logging.Formatter('%(asctime)s - TRAY - %(levelname)s - %(message)s')
)
tray_logger.addHandler(_handler)

# Status background colors
BG_COLORS = {
    'green': (186, 230, 126),
    'orange': (252, 211, 119),
    'red': (252, 165, 165),
}

# ============================================================================
# ICON GENERATION
# ============================================================================

FAVICON_PATH = SCRIPT_DIR / 'assets' / 'favicon.ico'
CORNER_RADIUS = 12  # rounded corner radius for the background square

# Cache the loaded favicon to avoid re-reading the file on every icon update
_favicon_cache = None


def _load_favicon(size: int = 48) -> 'Image':
    """Load and cache the company favicon, resized to fit."""
    global _favicon_cache
    if _favicon_cache is not None and _favicon_cache.size[0] == size:
        return _favicon_cache.copy()
    try:
        favicon = Image.open(str(FAVICON_PATH))
        if hasattr(favicon, 'ico') and favicon.ico:
            best_size = max(favicon.ico.sizes(), key=lambda s: s[0])
            favicon = favicon.ico.getimage(best_size)
        favicon = favicon.convert('RGBA')
        favicon = favicon.resize((size, size), Image.LANCZOS)
        _favicon_cache = favicon
        return favicon.copy()
    except (OSError, IOError, Exception) as e:
        tray_logger.warning(f"Could not load favicon: {e}")
        return None


def _make_icon(color: str = 'green') -> 'Image':
    """Generate 64x64 tray icon: rounded-rect background + company logo."""
    size = 64
    rgb = BG_COLORS.get(color, BG_COLORS['green'])
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
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
        except (OSError, IOError):
            font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), "T", font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text(((size - tw) // 2, (size - th) // 2),
                  "T", fill=(0, 70, 150), font=font)

    return img


def _find_pythonw() -> str:
    """Find pythonw.exe alongside the current Python interpreter."""
    python_dir = Path(sys.executable).parent
    pythonw = python_dir / "pythonw.exe"
    if pythonw.exists():
        return str(pythonw)
    # Fallback: just use python.exe
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
        self._anim_timer = None       # animation timer for syncing dot
        self._anim_running = False     # flag to stop animation loop

    def _load_automation(self):
        """
        Deferred import of TempoAutomation.

        tempo_automation.py has module-level side effects (logging setup,
        stdout redirect). Importing inside init lets the tray icon appear
        first. If import fails, icon shows red with error tooltip.
        """
        try:
            import json
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                self._config = json.load(f)

            # Import the main automation class
            sys.path.insert(0, str(SCRIPT_DIR))
            from tempo_automation import TempoAutomation
            self._automation = TempoAutomation(CONFIG_FILE)
            tray_logger.info("TempoAutomation loaded successfully")
        except FileNotFoundError:
            self._import_error = (
                "config.json not found. Run setup first: "
                "python tempo_automation.py --setup"
            )
            tray_logger.error(self._import_error)
        except Exception as e:
            self._import_error = f"Failed to load automation: {e}"
            tray_logger.error(self._import_error, exc_info=True)

    def _check_single_instance(self) -> bool:
        """
        Create a named Win32 mutex. Returns False if another instance
        is already running.
        """
        self._mutex = ctypes.windll.kernel32.CreateMutexW(
            None, True, MUTEX_NAME
        )
        last_error = ctypes.windll.kernel32.GetLastError()
        if last_error == 183:  # ERROR_ALREADY_EXISTS
            tray_logger.info("Another instance is already running")
            return False
        return True

    def _get_sync_time(self) -> str:
        """Get configured daily sync time, default '18:00'."""
        if self._config:
            return self._config.get(
                'schedule', {}
            ).get('daily_sync_time', '18:00')
        return '18:00'

    def _schedule_next_sync(self):
        """Schedule a timer to fire at the next daily_sync_time."""
        if self._timer:
            self._timer.cancel()

        sync_time_str = self._get_sync_time()
        try:
            hour, minute = map(int, sync_time_str.split(':'))
        except (ValueError, AttributeError):
            hour, minute = 18, 0

        now = datetime.now()
        target = now.replace(hour=hour, minute=minute, second=0,
                             microsecond=0)

        # If today's time has passed, schedule for tomorrow
        if target <= now:
            target += timedelta(days=1)

        delay = (target - now).total_seconds()
        self._timer = threading.Timer(delay, self._on_timer_fired)
        self._timer.daemon = True
        self._timer.start()

        tray_logger.info(
            f"Next sync notification scheduled for {target:%Y-%m-%d %H:%M} "
            f"({delay:.0f}s from now)"
        )

    def _on_timer_fired(self):
        """Called by threading.Timer at the configured time."""
        self._pending_confirmation = True
        self._set_icon_state('orange', 'Tempo - Time to log hours!')
        self._show_toast(
            'Time to Log Hours',
            'Click the Tempo tray icon to confirm and sync your timesheet.'
        )
        tray_logger.info("Timer fired - awaiting user confirmation")

        # Re-arm for tomorrow
        self._schedule_next_sync()

    def _build_menu(self) -> 'pystray.Menu':
        """Build the right-click context menu."""
        return pystray.Menu(
            pystray.MenuItem(
                'Sync Now',
                self._on_sync_now,
                default=True  # activates on double-click
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem('Add PTO', self._on_add_pto),
            pystray.MenuItem('View Log', self._on_view_log),
            pystray.MenuItem('View Schedule', self._on_view_schedule),
            pystray.MenuItem('Settings', self._on_settings),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem('Exit', self._on_exit),
        )

    def _on_sync_now(self, icon=None, item=None):
        """Clear pending flag and start sync in a background thread."""
        self._pending_confirmation = False
        if self._sync_running.is_set():
            self._show_toast(
                'Sync Already Running',
                'A sync is already in progress. Please wait.'
            )
            return

        if self._automation is None:
            msg = self._import_error or 'Automation not loaded'
            self._show_toast('Error', msg)
            return

        thread = threading.Thread(target=self._run_sync, daemon=True)
        thread.start()

    def _run_sync(self):
        """Background thread that runs the actual sync."""
        self._sync_running.set()
        self._start_sync_animation('Tempo - Syncing...')
        tray_logger.info("Sync started")

        try:
            # Redirect stdout to daily-timesheet.log so sync output
            # is captured (pythonw.exe has no console)
            import io
            from datetime import datetime as dt
            log_path = SCRIPT_DIR / 'daily-timesheet.log'
            log_f = open(log_path, 'a', encoding='utf-8')
            log_f.write(f"\n{'='*44}\n")
            log_f.write(f"Run: {dt.now():%Y-%m-%d %H:%M:%S} (Tray App)\n")
            log_f.write(f"{'='*44}\n")
            old_stdout = sys.stdout
            sys.stdout = log_f

            self._automation.sync_daily()

            sys.stdout = old_stdout
            log_f.close()

            self._set_icon_state('green', 'Tempo - Sync complete')
            self._show_toast(
                'Sync Complete',
                'Daily timesheet has been synced successfully.'
            )
            tray_logger.info("Sync completed successfully")
        except Exception as e:
            # Restore stdout on error
            sys.stdout = old_stdout
            if 'log_f' in locals() and not log_f.closed:
                log_f.close()
            error_msg = str(e)[:200]
            self._set_icon_state('red', f'Tempo - Error: {error_msg}')
            self._show_toast('Sync Failed', f'Error: {error_msg}')
            tray_logger.error(f"Sync failed: {e}", exc_info=True)
        finally:
            self._sync_running.clear()

        # Revert icon to green after 5 seconds (if it was success)
        def _revert():
            if not self._sync_running.is_set() and not self._import_error:
                if self._pending_confirmation:
                    self._set_icon_state(
                        'orange', 'Tempo - Time to log hours!'
                    )
                else:
                    self._set_icon_state('green', 'Tempo Automation')
        timer = threading.Timer(5.0, _revert)
        timer.daemon = True
        timer.start()

    def _on_add_pto(self, icon=None, item=None):
        """Show input dialog to add PTO dates."""
        if self._automation is None:
            msg = self._import_error or 'Automation not loaded'
            self._show_toast('Error', msg)
            return

        # VBScript InputBox — works under pythonw, no extra deps.
        # Writes user input to a temp file so Python can read it.
        tmp_file = SCRIPT_DIR / "_pto_input.tmp"
        vbs_file = SCRIPT_DIR / "_pto_input.vbs"
        tmp_path_escaped = str(tmp_file).replace("\\", "\\\\")

        vbs_content = (
            'result = InputBox('
            '"Enter PTO date(s) in YYYY-MM-DD format."'
            ' & vbCrLf & '
            '"Separate multiple dates with commas."'
            ' & vbCrLf & vbCrLf & '
            '"Example: 2026-03-10,2026-03-11", '
            '"Tempo - Add PTO")\n'
            'If result <> "" Then\n'
            '  Dim fso, f\n'
            '  Set fso = CreateObject("Scripting.FileSystemObject")\n'
            '  Set f = fso.CreateTextFile("'
            + tmp_path_escaped
            + '", True)\n'
            '  f.Write result\n'
            '  f.Close\n'
            'End If\n'
        )

        # Clean up previous temp file
        if tmp_file.exists():
            tmp_file.unlink()

        try:
            with open(vbs_file, 'w') as f:
                f.write(vbs_content)

            subprocess.run(
                ['wscript.exe', str(vbs_file)], timeout=120
            )

            if tmp_file.exists():
                raw = tmp_file.read_text().strip()
                # Sanitize: only allow digits, '-', commas, spaces
                import re
                cleaned = re.sub(r'[^\d\-,\s]', '', raw)
                if cleaned:
                    dates = [
                        d.strip() for d in cleaned.split(',')
                        if d.strip()
                    ]
                    added, skipped = (
                        self._automation.schedule_mgr.add_pto(dates)
                    )
                    if added and not skipped:
                        self._show_toast(
                            'PTO Added',
                            f'Added {len(added)} day(s): '
                            f'{", ".join(added)}'
                        )
                    elif added and skipped:
                        self._show_toast(
                            'PTO Added (with warnings)',
                            f'Added: {", ".join(added)}\n'
                            f'Skipped: {"; ".join(skipped)}'
                        )
                    else:
                        self._show_toast(
                            'No PTO Added',
                            '\n'.join(skipped) if skipped
                            else 'No valid dates entered.'
                        )
        except subprocess.TimeoutExpired:
            pass
        except Exception as e:
            self._show_toast('Error', f'Could not add PTO: {e}')
            tray_logger.error(f"Add PTO failed: {e}", exc_info=True)
        finally:
            if vbs_file.exists():
                vbs_file.unlink()
            if tmp_file.exists():
                tmp_file.unlink()

    def _on_view_log(self, icon=None, item=None):
        """Open the daily log file in Notepad."""
        log_path = str(LOG_FILE)
        if not LOG_FILE.exists():
            self._show_toast('No Log', 'Log file not found yet.')
            return
        subprocess.Popen(['notepad.exe', log_path])

    def _on_view_schedule(self, icon=None, item=None):
        """Open a cmd window showing the schedule calendar."""
        # Use python.exe (not pythonw.exe) so cmd window has console output
        python_dir = Path(sys.executable).parent
        python_exe = python_dir / "python.exe"
        script = SCRIPT_DIR / 'tempo_automation.py'
        subprocess.Popen(
            ['cmd', '/k', str(python_exe), str(script), '--show-schedule'],
            cwd=str(SCRIPT_DIR)
        )

    def _on_settings(self, icon=None, item=None):
        """Open config.json in the default editor."""
        config_path = str(CONFIG_FILE)
        if CONFIG_FILE.exists():
            os.startfile(config_path)
        else:
            self._show_toast(
                'No Config',
                'config.json not found. Run setup first.'
            )

    def _on_exit(self, icon=None, item=None):
        """Start smart exit in a separate thread (pystray callback must return quickly)."""
        thread = threading.Thread(target=self._exit_flow, daemon=True)
        thread.start()

    def _exit_flow(self):
        """Smart exit: check hours, show dialog, then stop."""
        from datetime import date as date_cls

        should_warn = False
        hours_logged = 0.0
        daily_hours = 8.0
        today = date_cls.today().strftime('%Y-%m-%d')

        if self._automation:
            schedule_mgr = self._automation.schedule_mgr
            daily_hours = schedule_mgr.daily_hours
            is_working, reason = schedule_mgr.is_working_day(today)

            if is_working and self._automation.jira_client:
                self._set_icon_state('orange', 'Checking hours...')
                try:
                    worklogs = (
                        self._automation.jira_client.get_my_worklogs(
                            today, today
                        )
                    )
                    hours_logged = sum(
                        w.get('time_spent_seconds', 0) for w in worklogs
                    ) / 3600
                    if hours_logged < daily_hours:
                        should_warn = True
                except Exception as e:
                    tray_logger.warning(
                        f"Could not check hours on exit: {e}"
                    )
                    should_warn = True
                self._set_icon_state('green', 'Tempo Automation')

        if should_warn:
            # MB_YESNO | MB_ICONWARNING | MB_TOPMOST | MB_SETFOREGROUND
            flags = 0x04 | 0x30 | 0x40000 | 0x10000
            msg = (
                f"You haven't logged hours for today "
                f"({hours_logged:.1f}h / {daily_hours:.1f}h).\n\n"
                f"The app will remind you at "
                f"{self._get_sync_time()}.\n\n"
                f"Exit anyway?"
            )
            result = ctypes.windll.user32.MessageBoxW(
                0, msg, 'Tempo Automation', flags
            )
            if result != 6:  # User chose "No" (Stay Running)
                tray_logger.info("User chose to stay running")
                return

            self._schedule_restart()

        tray_logger.info("Tray app exiting")
        self._stop_sync_animation()
        if self._timer:
            self._timer.cancel()
        if self._icon:
            self._icon.stop()

    def _schedule_restart(self):
        """Create a one-time Task Scheduler task to relaunch at sync time."""
        try:
            pythonw = _find_pythonw()
            tray_script = str(SCRIPT_DIR / 'tray_app.py')
            sync_time = self._get_sync_time()

            cmd = [
                'schtasks', '/Create',
                '/TN', 'TempoTrayRestart',
                '/SC', 'ONCE',
                '/ST', sync_time,
                '/TR', f'"{pythonw}" "{tray_script}"',
                '/F'
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                tray_logger.info(
                    f"Restart scheduled at {sync_time} via "
                    f"TempoTrayRestart task"
                )
            else:
                tray_logger.warning(
                    f"Failed to schedule restart: {result.stderr}"
                )
        except Exception as e:
            tray_logger.error(f"Could not schedule restart: {e}")

    def _start_sync_animation(self, tooltip: str = 'Tempo - Syncing...'):
        """Start animated dot: alternates orange <-> red every 700ms."""
        self._anim_running = True
        self._anim_phase = False  # False=orange, True=red

        def _tick():
            if not self._anim_running or not self._icon:
                return
            dot = 'red' if self._anim_phase else 'orange'
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

    def _show_toast(self, title: str, body: str):
        """Show a Windows toast notification."""
        if not WINOTIFY_OK:
            tray_logger.warning(
                "winotify not available, skipping toast"
            )
            return
        try:
            toast = Notification(
                app_id='Tempo Automation',
                title=title,
                msg=body,
                duration='long'
            )
            toast.show()
        except Exception as e:
            tray_logger.error(f"Toast notification failed: {e}")

    def _ensure_autostart(self):
        """Register auto-start if not already present."""
        import winreg
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, REG_KEY,
                0, winreg.KEY_READ
            )
            try:
                winreg.QueryValueEx(key, REG_VALUE)
                winreg.CloseKey(key)
                # Already registered
                return
            except FileNotFoundError:
                winreg.CloseKey(key)
        except Exception:
            pass

        # Not registered -- register now
        tray_logger.info("Auto-start not found, registering...")
        register_autostart()

    def run(self):
        """Main entry point -- blocks on pystray message pump."""
        if not PYSTRAY_OK:
            print(
                "ERROR: pystray and Pillow are required.\n"
                "Install with: pip install pystray Pillow"
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
            initial_color = 'red'
            initial_tooltip = f'Tempo - Error: {self._import_error[:100]}'
        else:
            initial_color = 'green'
            initial_tooltip = 'Tempo Automation'
            # Schedule the notification timer
            self._schedule_next_sync()

        self._icon = pystray.Icon(
            name='TempoAutomation',
            icon=_make_icon(initial_color),
            title=initial_tooltip,
            menu=self._build_menu()
        )

        # Clean up any one-time restart task from a previous "Exit Anyway"
        subprocess.run(
            ['schtasks', '/Delete', '/TN', 'TempoTrayRestart', '/F'],
            capture_output=True
        )

        # Start stop-file watcher (allows --stop from another process)
        self._stop_watcher_running = True

        def _watch_stop_file():
            while self._stop_watcher_running:
                if STOP_FILE.exists():
                    tray_logger.info(
                        "Stop signal received, shutting down"
                    )
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
                import time
                time.sleep(1)

        watcher = threading.Thread(
            target=_watch_stop_file, daemon=True
        )
        watcher.start()

        tray_logger.info("Tray app started")

        # Show welcome toast after icon is visible (slight delay
        # so the icon renders before the notification fires)
        if not self._import_error:
            def _welcome():
                sync_time = self._get_sync_time()
                user_name = ''
                if self._config:
                    user_name = self._config.get(
                        'user', {}
                    ).get('name', '')
                greeting = f'Hi {user_name}! ' if user_name else ''
                self._show_toast(
                    'Tempo Automation is Running',
                    f'{greeting}The app is now running in your '
                    f'system tray. You will be notified at '
                    f'{sync_time} to log your hours.\n'
                    f'Right-click the tray icon for options.'
                )
            welcome_timer = threading.Timer(2.0, _welcome)
            welcome_timer.daemon = True
            welcome_timer.start()

        self._icon.run()  # Blocks (Win32 message pump)


# ============================================================================
# AUTO-START REGISTRATION
# ============================================================================

def register_autostart():
    """Register the tray app to start on Windows login (HKCU, no admin)."""
    import winreg
    pythonw = _find_pythonw()
    tray_script = str(SCRIPT_DIR / 'tray_app.py')
    command = f'"{pythonw}" "{tray_script}"'

    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, REG_KEY, 0, winreg.KEY_SET_VALUE
        )
        winreg.SetValueEx(key, REG_VALUE, 0, winreg.REG_SZ, command)
        winreg.CloseKey(key)
        print(f"[OK] Auto-start registered: {command}")
        tray_logger.info(f"Auto-start registered: {command}")
    except Exception as e:
        print(f"[FAIL] Could not register auto-start: {e}")
        tray_logger.error(f"Auto-start registration failed: {e}")


def unregister_autostart():
    """Remove the auto-start registry entry."""
    import winreg
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, REG_KEY, 0, winreg.KEY_SET_VALUE
        )
        winreg.DeleteValue(key, REG_VALUE)
        winreg.CloseKey(key)
        print("[OK] Auto-start removed")
        tray_logger.info("Auto-start removed")
    except FileNotFoundError:
        print("[OK] Auto-start was not registered")
    except Exception as e:
        print(f"[FAIL] Could not remove auto-start: {e}")
        tray_logger.error(f"Auto-start removal failed: {e}")


def stop_app():
    """Signal a running tray app instance to shut down via stop file."""
    STOP_FILE.write_text('stop')
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
    parser = argparse.ArgumentParser(
        description='Tempo Automation - System Tray App'
    )
    parser.add_argument(
        '--register', action='store_true',
        help='Register auto-start on Windows login'
    )
    parser.add_argument(
        '--unregister', action='store_true',
        help='Remove auto-start from Windows login'
    )
    parser.add_argument(
        '--stop', action='store_true',
        help='Stop a running tray app instance'
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
        app.run()


if __name__ == '__main__':
    main()
