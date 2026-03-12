#!/usr/bin/env python3
"""
Task Scheduler wrapper: shows OK/Cancel dialog before daily sync.

Called by run_daily.bat via pythonw.exe (no console window).
Zero extra dependencies -- only ctypes + subprocess (stdlib).
Also ensures the tray app is running (restarts it if closed).
"""

import sys
import ctypes
import subprocess
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
MB_OKCANCEL = 0x01
MB_ICONQUESTION = 0x20
IDOK = 1
MUTEX_NAME = 'TempoTrayApp_SingleInstance_Mutex'


def ask_user(title: str, msg: str) -> bool:
    """Show a Win32 OK/Cancel dialog. Returns True if OK clicked."""
    if sys.platform == 'win32':
        result = ctypes.windll.user32.MessageBoxW(
            0, msg, title, MB_OKCANCEL | MB_ICONQUESTION
        )
        return result == IDOK
    # Mac/Linux: print prompt and auto-confirm
    print(f"{title}: {msg}")
    return True


def _ensure_tray_running():
    """Start the tray app if it's not already running.

    Windows: checks for the named mutex that tray_app.py creates.
    Mac: checks for the fcntl lock file.
    """
    if sys.platform == 'win32':
        # Try to open the existing mutex (not create)
        handle = ctypes.windll.kernel32.OpenMutexW(
            0x00100000,  # SYNCHRONIZE
            False,
            MUTEX_NAME
        )
        if handle:
            # Mutex exists -- tray is running, close our handle
            ctypes.windll.kernel32.CloseHandle(handle)
            return
        # Tray is not running -- launch it with --quiet
        pythonw = Path(sys.executable).parent / 'pythonw.exe'
        if not pythonw.exists():
            pythonw = Path(sys.executable)
        tray_script = SCRIPT_DIR / 'tray_app.py'
        subprocess.Popen(
            [str(pythonw), str(tray_script), '--quiet'],
            creationflags=0x00000008  # DETACHED_PROCESS
        )
    else:
        # Mac/Linux: check if lock file is held
        import fcntl
        lock_path = SCRIPT_DIR / '.tray_app.lock'
        if lock_path.exists():
            try:
                f = open(lock_path, 'r')
                fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                # Got the lock -- tray is NOT running, release and start
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                f.close()
                tray_script = SCRIPT_DIR / 'tray_app.py'
                subprocess.Popen(
                    [sys.executable, str(tray_script), '--quiet'],
                    start_new_session=True
                )
            except IOError:
                # Lock held -- tray is running
                f.close()
        else:
            # No lock file -- tray never started, launch it
            tray_script = SCRIPT_DIR / 'tray_app.py'
            subprocess.Popen(
                [sys.executable, str(tray_script), '--quiet'],
                start_new_session=True
            )


def main():
    """Ensure tray app is running, then show dialog and sync."""
    _ensure_tray_running()

    confirmed = ask_user(
        'Tempo Automation',
        'It is time to log your daily hours.\n\n'
        'Click OK to sync now, or Cancel to skip today.'
    )

    if not confirmed:
        print('[SKIP] User cancelled daily sync')
        sys.exit(0)

    # Import and run sync
    sys.path.insert(0, str(SCRIPT_DIR))
    try:
        from tempo_automation import TempoAutomation, CONFIG_FILE
        automation = TempoAutomation(CONFIG_FILE)
        automation.sync_daily()
    except Exception as e:
        import logging
        logging.basicConfig(filename=str(SCRIPT_DIR / 'daily-timesheet.log'))
        logging.error(f"Daily sync failed: {e}", exc_info=True)
        print(f"[FAIL] Daily sync error: {e}")


if __name__ == '__main__':
    main()
