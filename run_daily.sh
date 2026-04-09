#!/bin/bash
# ============================================================================
# Tempo Automation - Daily Sync (Mac/Linux wrapper)
# Mirrors run_daily.bat -- called by cron, provides log rotation.
# ============================================================================

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PYTHON_EXE="python3"

MONTH=$("$PYTHON_EXE" "$SCRIPT_DIR/_get_month.py" 2>/dev/null || date +%Y-%m)
LOGFILE="$SCRIPT_DIR/daily-timesheet-${MONTH}.log"

echo "============================================" >> "$LOGFILE"
echo "Run: $(date '+%Y-%m-%d %H:%M:%S')" >> "$LOGFILE"
echo "============================================" >> "$LOGFILE"

"$PYTHON_EXE" "$SCRIPT_DIR/confirm_and_run.py"
