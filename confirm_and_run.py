#!/usr/bin/env python3
"""
Task Scheduler wrapper: ensures the tray app is running at sync time.

Called by run_daily.bat via pythonw.exe (no console window).
Zero extra dependencies -- only ctypes + subprocess (stdlib).

Behaviour:
- If the tray app is already running: exit immediately.
  The tray manages its own sync timer and will sync at the configured time.
- If the tray app is not running: start it with --sync-on-start so it
  comes back online and immediately syncs if the scheduled time has passed.

No OK/Cancel dialog is shown -- the tray app handles notifications.
"""

import sys
import ctypes
import subprocess
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent

MUTEX_NAME = 'TempoTrayApp_SingleInstance_Mutex'


def _is_tray_running() -> bool:
    """Return True if the tray app is currently running."""
    if sys.platform == 'win32':
        handle = ctypes.windll.kernel32.OpenMutexW(
            0x00100000,  # SYNCHRONIZE
            False,
            MUTEX_NAME
        )
        if handle:
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        return False
    else:
        # Mac/Linux: a held fcntl lock means the tray is running
        import fcntl
        lock_path = SCRIPT_DIR / '.tray_app.lock'
        if not lock_path.exists():
            return False
        try:
            f = open(lock_path, 'r')
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Acquired lock -- tray is NOT running
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            f.close()
            return False
        except IOError:
            # Could not acquire -- tray IS running
            try:
                f.close()
            except Exception:
                pass
            return True


def _start_tray():
    """Launch the tray app with --sync-on-start (recovery/fallback start).

    --sync-on-start tells the tray to trigger an immediate sync if the
    configured sync time has already passed today, covering the case where
    the tray was closed when the Task Scheduler task fired.
    """
    tray_script = SCRIPT_DIR / 'tray_app.py'
    if sys.platform == 'win32':
        pythonw = Path(sys.executable).parent / 'pythonw.exe'
        if not pythonw.exists():
            pythonw = Path(sys.executable)
        subprocess.Popen(
            [str(pythonw), str(tray_script), '--sync-on-start'],
            creationflags=0x00000008  # DETACHED_PROCESS
        )
    else:
        subprocess.Popen(
            [sys.executable, str(tray_script), '--sync-on-start'],
            start_new_session=True
        )


def main():
    """
    If the tray is running: exit immediately -- the tray owns sync scheduling.
    If the tray is not running: restart it so it re-arms its sync timer.
    No dialog is shown; notifications are handled by the tray app.
    """
    if _is_tray_running():
        # Tray is active -- its internal timer handles sync at the
        # configured time.  Nothing to do here.
        sys.exit(0)

    # Tray is not running (e.g. user closed it) -- bring it back.
    _start_tray()


if __name__ == '__main__':
    main()
