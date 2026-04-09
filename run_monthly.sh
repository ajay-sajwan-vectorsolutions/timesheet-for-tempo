#!/bin/bash
# ============================================================================
# Tempo Automation - Monthly Submit (Mac/Linux wrapper)
# Mirrors run_monthly.bat -- called by cron, provides log rotation.
# ============================================================================

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
PYTHON_EXE="python3"

MONTH=$("$PYTHON_EXE" "$SCRIPT_DIR/_get_month.py" 2>/dev/null || date +%Y-%m)
LOGFILE="$SCRIPT_DIR/daily-timesheet-${MONTH}.log"

echo "============================================" >> "$LOGFILE"
echo "Monthly Submit Run: $(date '+%Y-%m-%d %H:%M:%S')" >> "$LOGFILE"
echo "============================================" >> "$LOGFILE"

"$PYTHON_EXE" "$SCRIPT_DIR/tempo_automation.py" --submit --logfile "$LOGFILE"
