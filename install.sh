#!/bin/bash
# ============================================================================
# Tempo Automation - Mac/Linux Installer
# ============================================================================
# This script will:
# 1. Check Python installation
# 2. Install dependencies
# 3. Run setup wizard
# 4. Schedule daily and monthly cron jobs
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
# Check Python installation
# ============================================================================

echo "[1/5] Checking Python installation..."

if ! command -v python3 &> /dev/null; then
    echo ""
    echo "ERROR: Python 3 is not installed"
    echo ""
    echo "Please install Python 3.7 or higher:"
    echo "  macOS: brew install python3"
    echo "  Ubuntu/Debian: sudo apt-get install python3 python3-pip"
    echo "  Fedora: sudo dnf install python3 python3-pip"
    echo ""
    exit 1
fi

echo "✓ Python found"
python3 --version
echo ""

# ============================================================================
# Install dependencies
# ============================================================================

echo "[2/5] Installing Python dependencies..."

python3 -m pip install --upgrade pip --quiet --user
python3 -m pip install -r requirements.txt --quiet --user

if [ $? -ne 0 ]; then
    echo ""
    echo "ERROR: Failed to install dependencies"
    exit 1
fi

echo "✓ Dependencies installed"
echo ""

# ============================================================================
# Run setup wizard
# ============================================================================

echo "[3/5] Running setup wizard..."
echo ""

python3 tempo_automation.py --setup

if [ $? -ne 0 ]; then
    echo ""
    echo "ERROR: Setup failed"
    exit 1
fi

echo ""
echo "✓ Setup complete"
echo ""

# ============================================================================
# Create cron jobs
# ============================================================================

echo "[4/5] Setting up cron jobs..."
echo ""

# Get full path to Python and script
PYTHON_PATH=$(which python3)
SCRIPT_PATH="$SCRIPT_DIR/tempo_automation.py"

# Check if crontab exists
if ! crontab -l &> /dev/null; then
    echo "# Tempo Automation Cron Jobs" | crontab -
fi

# Backup existing crontab
crontab -l > /tmp/tempo_crontab_backup_$$.txt
echo "✓ Backed up existing crontab to /tmp/tempo_crontab_backup_$$.txt"

# Remove any existing Tempo Automation entries
crontab -l | grep -v "tempo_automation.py" > /tmp/tempo_crontab_new_$$.txt

# Add new cron jobs
echo "" >> /tmp/tempo_crontab_new_$$.txt
echo "# Tempo Automation - Daily sync at 6:00 PM" >> /tmp/tempo_crontab_new_$$.txt
echo "0 18 * * * $PYTHON_PATH $SCRIPT_PATH >> $SCRIPT_DIR/tempo_automation.log 2>&1" >> /tmp/tempo_crontab_new_$$.txt

echo "" >> /tmp/tempo_crontab_new_$$.txt
echo "# Tempo Automation - Monthly submission at 11:00 PM on last day of month" >> /tmp/tempo_crontab_new_$$.txt
echo "0 23 28-31 * * [ \$(date -d tomorrow +\%d) -eq 1 ] && $PYTHON_PATH $SCRIPT_PATH --submit >> $SCRIPT_DIR/tempo_automation.log 2>&1" >> /tmp/tempo_crontab_new_$$.txt

# Install new crontab
crontab /tmp/tempo_crontab_new_$$.txt

if [ $? -eq 0 ]; then
    echo "✓ Cron jobs created"
    rm /tmp/tempo_crontab_new_$$.txt
else
    echo "✗ Failed to create cron jobs"
    echo "  Restoring backup..."
    crontab /tmp/tempo_crontab_backup_$$.txt
    exit 1
fi

echo ""

# ============================================================================
# Test run
# ============================================================================

echo "[5/5] Testing..."
echo ""
read -p "Would you like to test the automation now? (y/n): " TEST_RUN

if [ "$TEST_RUN" = "y" ] || [ "$TEST_RUN" = "Y" ]; then
    echo ""
    echo "Running test sync..."
    python3 tempo_automation.py
fi

echo ""

# ============================================================================
# Installation complete
# ============================================================================

echo ""
echo "============================================================"
echo "✓ INSTALLATION COMPLETE!"
echo "============================================================"
echo ""
echo "Your automation is now set up and will run automatically:"
echo "  - Daily: 6:00 PM (sync timesheets)"
echo "  - Monthly: 11:00 PM on last day (submit for approval)"
echo ""
echo "Configuration file: $SCRIPT_DIR/config.json"
echo "Log file: $SCRIPT_DIR/tempo_automation.log"
echo ""
echo "You can manually run the script anytime:"
echo "  python3 tempo_automation.py          (sync today)"
echo "  python3 tempo_automation.py --submit (submit timesheet)"
echo ""
echo "To view cron jobs:"
echo "  crontab -l"
echo ""
echo "To uninstall:"
echo "  crontab -e"
echo "  (Remove lines containing 'tempo_automation.py')"
echo ""
echo "============================================================"
echo ""
