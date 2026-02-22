#!/bin/bash
# ============================================================================
# Tempo Automation - Mac/Linux Installer
# ============================================================================
# This script will:
# 1. Check Python installation
# 2. Install dependencies
# 3. Run setup wizard
# 4. Configure overhead stories
# 5. Schedule daily, weekly, and monthly cron jobs
# 6. Set up system tray app (auto-start on login)
# 7. Optionally run a test sync
# ============================================================================

set -e  # Exit on error

echo ""
echo "============================================================"
echo "TEMPO TIMESHEET AUTOMATION - MAC/LINUX INSTALLER"
echo "============================================================"
echo ""

# Get script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# ============================================================================
# [1/7] Check Python installation
# ============================================================================

echo "[1/7] Checking Python installation..."

if ! command -v python3 &> /dev/null; then
    echo ""
    echo "ERROR: Python 3 is not installed"
    echo ""
    echo "Please install Python 3.7 or higher:"
    if [ "$(uname)" = "Darwin" ]; then
        echo "  brew install python3"
    else
        echo "  sudo apt-get install python3 python3-pip"
    fi
    echo ""
    exit 1
fi

echo "[OK] Python found"
python3 --version
echo ""

# ============================================================================
# [2/7] Install dependencies
# ============================================================================

echo "[2/7] Installing Python dependencies..."
echo ""

python3 -m pip install --upgrade pip --user 2>/dev/null || python3 -m pip install --upgrade pip
python3 -m pip install -r requirements.txt --user 2>/dev/null || python3 -m pip install -r requirements.txt

if [ $? -ne 0 ]; then
    echo ""
    echo "ERROR: Failed to install dependencies"
    exit 1
fi

echo "[OK] Dependencies installed (requests, holidays, pystray, Pillow)"
echo ""

# ============================================================================
# [3/7] Run setup wizard
# ============================================================================

echo "[3/7] Running setup wizard..."
echo ""

python3 "$SCRIPT_DIR/tempo_automation.py" --setup

if [ $? -ne 0 ]; then
    echo ""
    echo "ERROR: Setup failed"
    exit 1
fi

echo ""
echo "[OK] Setup complete"
echo ""

# ============================================================================
# [4/7] Configure overhead stories (developers only)
# ============================================================================

echo "[4/7] Configuring overhead stories..."
echo ""
echo "Overhead stories are used for daily default hours (e.g., 2h/day),"
echo "PTO days, holidays, and days with no active tickets."
echo ""
read -p "Configure overhead stories now? (y/n, default: y): " SELECT_OH

if [ "$SELECT_OH" = "n" ] || [ "$SELECT_OH" = "N" ]; then
    echo "Skipped. You can configure later: python3 tempo_automation.py --select-overhead"
else
    python3 "$SCRIPT_DIR/tempo_automation.py" --select-overhead || {
        echo ""
        echo "[!] Overhead selection skipped or failed"
        echo "    You can configure later: python3 tempo_automation.py --select-overhead"
    }
fi
echo ""

# ============================================================================
# [5/7] Set up cron jobs
# ============================================================================

echo "[5/7] Setting up cron jobs..."
echo ""

# Get full path to Python
PYTHON_PATH=$(which python3)
SCRIPT_PATH="$SCRIPT_DIR/tempo_automation.py"
LOG_PATH="$SCRIPT_DIR/daily-timesheet.log"

# Initialize crontab if empty
if ! crontab -l &> /dev/null; then
    echo "# Tempo Automation Cron Jobs" | crontab -
fi

# Backup existing crontab
BACKUP_FILE="/tmp/tempo_crontab_backup_$$.txt"
crontab -l > "$BACKUP_FILE" 2>/dev/null || true
echo "[OK] Backed up existing crontab to $BACKUP_FILE"

# Remove any existing Tempo Automation entries, then add new ones
NEW_CRON="/tmp/tempo_crontab_new_$$.txt"
crontab -l 2>/dev/null | grep -v "tempo_automation.py" > "$NEW_CRON" || true

# Daily sync at 6:00 PM (Monday-Friday)
echo "" >> "$NEW_CRON"
echo "# Tempo Automation - Daily sync at 6:00 PM (Mon-Fri)" >> "$NEW_CRON"
echo "0 18 * * 1-5 $PYTHON_PATH \"$SCRIPT_PATH\" >> \"$LOG_PATH\" 2>&1" >> "$NEW_CRON"

# Weekly verification (Friday at 4:00 PM)
echo "" >> "$NEW_CRON"
echo "# Tempo Automation - Weekly verify (Fridays at 4:00 PM)" >> "$NEW_CRON"
echo "0 16 * * 5 $PYTHON_PATH \"$SCRIPT_PATH\" --verify-week >> \"$LOG_PATH\" 2>&1" >> "$NEW_CRON"

# Monthly submission at 11:00 PM on last day of month
# macOS (BSD date) uses -v+1d; Linux (GNU date) uses -d tomorrow
echo "" >> "$NEW_CRON"
echo "# Tempo Automation - Monthly submission (last day of month at 11:00 PM)" >> "$NEW_CRON"
if [ "$(uname)" = "Darwin" ]; then
    # macOS: BSD date syntax
    echo "0 23 28-31 * * [ \$(date -v+1d +\\%d) -eq 1 ] && $PYTHON_PATH \"$SCRIPT_PATH\" --submit >> \"$LOG_PATH\" 2>&1" >> "$NEW_CRON"
else
    # Linux: GNU date syntax
    echo "0 23 28-31 * * [ \$(date -d tomorrow +\\%d) -eq 1 ] && $PYTHON_PATH \"$SCRIPT_PATH\" --submit >> \"$LOG_PATH\" 2>&1" >> "$NEW_CRON"
fi

# Install new crontab
if crontab "$NEW_CRON"; then
    echo "[OK] Cron jobs created:"
    echo "     - Daily:   Mon-Fri at 6:00 PM (sync timesheets)"
    echo "     - Weekly:  Fridays at 4:00 PM (verify hours, backfill gaps)"
    echo "     - Monthly: Last day at 11:00 PM (submit for approval)"
    rm -f "$NEW_CRON"
else
    echo "[FAIL] Failed to create cron jobs"
    echo "  Restoring backup..."
    crontab "$BACKUP_FILE"
    exit 1
fi

echo ""

# ============================================================================
# [6/7] Set up system tray app
# ============================================================================

echo "[6/7] Setting up System Tray App..."
echo ""
echo "The tray app lives in your menu bar, shows a notification at your"
echo "configured sync time, and lets you sync with one click."
echo "It will start automatically every time you log in."
echo ""

# Stop any existing tray app instance
python3 "$SCRIPT_DIR/tray_app.py" --stop 2>/dev/null || true
sleep 2

# Register auto-start on login (LaunchAgent on Mac)
python3 "$SCRIPT_DIR/tray_app.py" --register

# Start the tray app now (background, no console)
echo "Starting tray app..."
nohup python3 "$SCRIPT_DIR/tray_app.py" > /dev/null 2>&1 &
sleep 3
echo "[OK] Tray app is running in the menu bar"
echo ""
echo "NOTE: The tray app and cron jobs can coexist safely."
echo "      The sync is idempotent (re-running overwrites previous entries)."

echo ""

# ============================================================================
# [7/7] Test sync (optional)
# ============================================================================

echo "[7/7] Test sync (optional)"
echo ""
echo "Would you like to test the automation now?"
echo "This will sync today's timesheet to verify everything works."
echo ""
read -p "Run test? (y/n): " TEST_RUN

if [ "$TEST_RUN" = "y" ] || [ "$TEST_RUN" = "Y" ]; then
    echo ""
    echo "Running test sync..."
    echo ""
    python3 "$SCRIPT_DIR/tempo_automation.py"
fi

echo ""

# ============================================================================
# Installation complete
# ============================================================================

echo ""
echo "============================================================"
echo "[OK] INSTALLATION COMPLETE!"
echo "============================================================"
echo ""
echo "Your automation is now set up and will run automatically:"
echo ""
echo "  Tray App:"
echo "    - Starts on login (menu bar icon)"
echo "    - Notifies at your configured sync time (default 6:00 PM)"
echo "    - Right-click for menu: Sync Now, Add PTO, View Schedule, etc."
echo ""
echo "  Cron Jobs:"
echo "    - Daily:   Mon-Fri at 6:00 PM (sync timesheets)"
echo "    - Weekly:  Fridays at 4:00 PM (verify hours, backfill gaps)"
echo "    - Monthly: Last day at 11:00 PM (submit for approval)"
echo ""
echo "Files:"
echo "  Config:  $SCRIPT_DIR/config.json"
echo "  Log:     $SCRIPT_DIR/daily-timesheet.log"
echo "  Runtime: $SCRIPT_DIR/tempo_automation.log"
echo ""
echo "Manual commands:"
echo "  python3 tempo_automation.py              (sync today)"
echo "  python3 tempo_automation.py --date DATE  (sync specific date)"
echo "  python3 tempo_automation.py --verify-week (verify this week)"
echo "  python3 tempo_automation.py --submit     (submit monthly)"
echo "  python3 tempo_automation.py --show-schedule (view calendar)"
echo "  python3 tempo_automation.py --manage     (schedule menu)"
echo ""
echo "Uninstall:"
echo "  python3 tray_app.py --unregister"
echo "  crontab -e  (remove lines containing 'tempo_automation.py')"
echo "  Then delete this folder."
echo ""
echo "============================================================"
echo ""
