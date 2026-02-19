#!/usr/bin/env python3
"""
Task Scheduler wrapper: shows OK/Cancel dialog before daily sync.

Called by run_daily.bat via pythonw.exe (no console window).
Zero extra dependencies -- only ctypes (stdlib).
"""

import sys
import ctypes
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
MB_OKCANCEL = 0x01
MB_ICONQUESTION = 0x20
IDOK = 1


def ask_user(title: str, msg: str) -> bool:
    """Show a Win32 OK/Cancel dialog. Returns True if OK clicked."""
    result = ctypes.windll.user32.MessageBoxW(
        0, msg, title, MB_OKCANCEL | MB_ICONQUESTION
    )
    return result == IDOK


def main():
    """Show confirmation dialog, then run sync if user clicks OK."""
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
    from tempo_automation import TempoAutomation, CONFIG_FILE
    automation = TempoAutomation(CONFIG_FILE)
    automation.sync_daily()


if __name__ == '__main__':
    main()
