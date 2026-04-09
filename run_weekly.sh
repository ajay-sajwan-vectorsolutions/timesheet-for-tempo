#!/bin/bash
# ============================================================================
# Tempo Automation - Weekly Verify (Mac/Linux wrapper)
# Mirrors run_weekly.bat -- called by cron, provides log rotation.
# ============================================================================

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PYTHON_EXE="python3"

MONTH=$("$PYTHON_EXE" "$SCRIPT_DIR/_get_month.py" 2>/dev/null || date +%Y-%m)
LOGFILE="$SCRIPT_DIR/daily-timesheet-${MONTH}.log"

echo "============================================" >> "$LOGFILE"
echo "Weekly Verify Run: $(date '+%Y-%m-%d %H:%M:%S')" >> "$LOGFILE"
echo "============================================" >> "$LOGFILE"

"$PYTHON_EXE" "$SCRIPT_DIR/tempo_automation.py" --verify-week --logfile "$LOGFILE"
