#!/usr/bin/env python3
"""
Tempo Timesheet Automation Script
==================================

Automates daily timesheet entry and monthly submission for Tempo/Jira users.

Features:
- Auto-syncs Jira worklogs to Tempo (for developers)
- Supports manual configuration (for POs, Sales)
- Auto-submits timesheets at month-end
- Email notifications
- Error handling and retry logic

Author: Vector Solutions Engineering Team
Version: 1.0.0
Date: February 2026
"""

import os
import sys
import json
import logging
import argparse
from datetime import datetime, timedelta, date
from pathlib import Path
import requests
from typing import Dict, List, Optional, Tuple
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ============================================================================
# CONFIGURATION
# ============================================================================

# Script directory
SCRIPT_DIR = Path(__file__).parent
CONFIG_FILE = SCRIPT_DIR / "config.json"
LOG_FILE = SCRIPT_DIR / "tempo_automation.log"

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# ============================================================================
# CONFIGURATION MANAGER
# ============================================================================

class ConfigManager:
    """Manages user configuration and credentials."""
    
    def __init__(self, config_path: Path = CONFIG_FILE):
        self.config_path = config_path
        self.config = self.load_config()
    
    def load_config(self) -> Dict:
        """Load configuration from file or create new one."""
        if not self.config_path.exists():
            logger.info("No configuration found. Starting setup wizard...")
            return self.setup_wizard()
        
        try:
            with open(self.config_path, 'r') as f:
                config = json.load(f)
            logger.info("Configuration loaded successfully")
            return config
        except Exception as e:
            logger.error(f"Error loading configuration: {e}")
            raise
    
    def setup_wizard(self) -> Dict:
        """Interactive setup wizard for first-time configuration."""
        print("\n" + "="*60)
        print("TEMPO AUTOMATION - FIRST TIME SETUP")
        print("="*60)
        print("\nThis wizard will help you set up the automation.")
        print("Your credentials will be stored locally and encrypted.\n")
        
        # User information
        print("--- USER INFORMATION ---")
        user_email = input("Enter your email address: ").strip()
        user_name = input("Enter your full name: ").strip()
        user_role = self._select_role()
        
        # Jira/Tempo configuration
        print("\n--- JIRA/TEMPO CONFIGURATION ---")
        jira_url = input("Enter your Jira URL (e.g., yourcompany.atlassian.net): ").strip()
        jira_url = jira_url.replace('https://', '').replace('http://', '')
        
        print("\n📖 To get your Tempo API token:")
        print("   1. Go to https://app.tempo.io/")
        print("   2. Settings → API Integration")
        print("   3. Click 'New Token'")
        tempo_token = input("\nEnter your Tempo API token: ").strip()
        
        if user_role == "developer":
            print("\n📖 To get your Jira API token:")
            print("   1. Go to https://id.atlassian.com/manage-profile/security/api-tokens")
            print("   2. Click 'Create API token'")
            jira_token = input("\nEnter your Jira API token: ").strip()
            jira_email = input("Enter your Jira account email: ").strip()
        else:
            jira_token = ""
            jira_email = ""
        
        # Work schedule
        print("\n--- WORK SCHEDULE ---")
        daily_hours = float(input("Standard work hours per day (default 8): ").strip() or "8")
        
        # Email notifications
        print("\n--- EMAIL NOTIFICATIONS ---")
        enable_email = input("Enable email notifications? (yes/no, default: yes): ").strip().lower()
        enable_email = enable_email in ['yes', 'y', '']
        
        if enable_email:
            smtp_server = input("SMTP server (e.g., smtp.gmail.com): ").strip()
            smtp_port = int(input("SMTP port (default 587): ").strip() or "587")
            smtp_user = input("SMTP username (usually your email): ").strip()
            smtp_password = input("SMTP password: ").strip()
        else:
            smtp_server = smtp_port = smtp_user = smtp_password = ""
        
        # Manual activities (for non-developers)
        manual_activities = []
        if user_role in ["product_owner", "sales"]:
            print("\n--- DEFAULT ACTIVITIES ---")
            print("Set up your typical daily activities (optional)")
            while True:
                add_activity = input("\nAdd a default activity? (yes/no): ").strip().lower()
                if add_activity not in ['yes', 'y']:
                    break
                
                activity = input("Activity name (e.g., 'Stakeholder Meetings'): ").strip()
                hours = float(input("Typical hours per day: ").strip())
                manual_activities.append({
                    "activity": activity,
                    "hours": hours
                })
        
        # Build configuration
        config = {
            "user": {
                "email": user_email,
                "name": user_name,
                "role": user_role
            },
            "jira": {
                "url": jira_url,
                "email": jira_email,
                "api_token": jira_token
            },
            "tempo": {
                "api_token": tempo_token
            },
            "schedule": {
                "daily_hours": daily_hours,
                "daily_sync_time": "18:00",  # 6 PM
                "monthly_submit_day": "last"  # Last day of month
            },
            "notifications": {
                "email_enabled": enable_email,
                "smtp_server": smtp_server,
                "smtp_port": smtp_port,
                "smtp_user": smtp_user,
                "smtp_password": smtp_password,
                "notification_email": user_email
            },
            "manual_activities": manual_activities,
            "options": {
                "auto_submit": True,
                "require_confirmation": False,
                "sync_on_startup": False
            }
        }
        
        # Save configuration
        self.save_config(config)
        
        print("\n" + "="*60)
        print("✓ SETUP COMPLETE!")
        print("="*60)
        print(f"\nConfiguration saved to: {self.config_path}")
        print("You can edit this file manually if needed.\n")
        
        return config
    
    def _select_role(self) -> str:
        """Helper to select user role."""
        print("\nSelect your role:")
        print("  1. Developer (works with Jira tickets)")
        print("  2. Product Owner")
        print("  3. Sales Team")
        
        while True:
            choice = input("Enter choice (1-3): ").strip()
            if choice == "1":
                return "developer"
            elif choice == "2":
                return "product_owner"
            elif choice == "3":
                return "sales"
            else:
                print("Invalid choice. Please enter 1, 2, or 3.")
    
    def save_config(self, config: Dict):
        """Save configuration to file."""
        try:
            with open(self.config_path, 'w') as f:
                json.dump(config, f, indent=2)
            logger.info("Configuration saved successfully")
        except Exception as e:
            logger.error(f"Error saving configuration: {e}")
            raise
    
    def get_account_id(self) -> str:
        """Get Tempo account ID for current user."""
        try:
            url = "https://api.tempo.io/4/user"
            headers = {
                'Authorization': f"Bearer {self.config['tempo']['api_token']}"
            }
            
            response = requests.get(url, headers=headers)
            response.raise_for_status()
            
            user_data = response.json()
            account_id = user_data.get('accountId')
            
            if account_id:
                logger.info(f"Retrieved Tempo account ID: {account_id}")
                return account_id
            else:
                logger.warning("Account ID not found in Tempo response, using email")
                return self.config['user']['email']
                
        except Exception as e:
            logger.error(f"Error fetching Tempo account ID: {e}")
            logger.error(f"Falling back to email as account ID")
            return self.config['user']['email']


# ============================================================================
# JIRA API CLIENT
# ============================================================================

class JiraClient:
    """Handles Jira API interactions."""
    
    def __init__(self, config: Dict):
        self.config = config
        self.base_url = f"https://{config['jira']['url']}"
        self.email = config['jira']['email']
        self.api_token = config['jira']['api_token']
        self.session = requests.Session()
        self.session.auth = (self.email, self.api_token)
        self.session.headers.update({'Content-Type': 'application/json'})
    
    def get_my_worklogs(self, date_from: str, date_to: str) -> List[Dict]:
        """
        Fetch worklogs for current user in date range.
        
        Args:
            date_from: Start date (YYYY-MM-DD)
            date_to: End date (YYYY-MM-DD)
        
        Returns:
            List of worklog dictionaries
        """
        try:
            # Get all issues with worklogs by current user
            jql = f'worklogAuthor = currentUser() AND worklogDate >= "{date_from}" AND worklogDate <= "{date_to}"'
            
            url = f"{self.base_url}/rest/api/3/search/jql"
            params = {
                'jql': jql,
                'fields': 'worklog,summary,key',
                'maxResults': 100
            }

            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()

            issues = response.json().get('issues', [])
            
            worklogs = []
            for issue in issues:
                issue_key = issue['key']
                issue_summary = issue['fields']['summary']
                
                # Get worklogs for this issue
                worklog_url = f"{self.base_url}/rest/api/3/issue/{issue_key}/worklog"
                worklog_response = self.session.get(worklog_url)
                worklog_response.raise_for_status()
                
                issue_worklogs = worklog_response.json().get('worklogs', [])
                
                # Filter worklogs by date and current user
                for wl in issue_worklogs:
                    started = wl['started'][:10]  # Extract date part
                    if date_from <= started <= date_to:
                        if wl['author']['emailAddress'] == self.email:
                            worklogs.append({
                                'worklog_id': wl['id'],
                                'issue_key': issue_key,
                                'issue_summary': issue_summary,
                                'time_spent_seconds': wl['timeSpentSeconds'],
                                'started': started,
                                'comment': wl.get('comment', '')
                            })
            
            logger.info(f"Fetched {len(worklogs)} worklogs from Jira")
            return worklogs

        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching Jira worklogs: {e}")
            return []

    def delete_worklog(self, issue_key: str, worklog_id: str) -> bool:
        """
        Delete a worklog from a Jira issue.

        Args:
            issue_key: Jira issue key (e.g., "TS-1234")
            worklog_id: ID of the worklog to delete

        Returns:
            True if successful, False otherwise
        """
        try:
            url = f"{self.base_url}/rest/api/3/issue/{issue_key}/worklog/{worklog_id}"
            response = self.session.delete(url, timeout=30)
            response.raise_for_status()

            logger.info(f"Deleted worklog {worklog_id} from {issue_key}")
            return True

        except requests.exceptions.RequestException as e:
            logger.error(f"Error deleting worklog {worklog_id} from {issue_key}: {e}")
            return False

    def get_my_active_issues(self) -> List[Dict]:
        """
        Fetch issues assigned to current user that are In Progress or Code Review.

        Returns:
            List of dicts with issue_key and issue_summary
        """
        try:
            jql = 'assignee = currentUser() AND status IN ("IN DEVELOPMENT", "CODE REVIEW")'

            url = f"{self.base_url}/rest/api/3/search/jql"
            params = {
                'jql': jql,
                'fields': 'summary',
                'maxResults': 50
            }

            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()

            issues = response.json().get('issues', [])

            result = []
            for issue in issues:
                result.append({
                    'issue_key': issue['key'],
                    'issue_summary': issue['fields']['summary']
                })

            logger.info(f"Found {len(result)} active issues assigned to current user")
            return result

        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching active issues: {e}")
            return []

    def get_issue_details(self, issue_key: str) -> Optional[Dict]:
        """
        Fetch issue description and recent comments.

        Args:
            issue_key: Jira issue key (e.g., "TS-1234")

        Returns:
            Dict with summary, description_text, and recent_comments, or None on failure
        """
        try:
            url = f"{self.base_url}/rest/api/3/issue/{issue_key}"
            params = {'fields': 'summary,description,comment'}

            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()

            data = response.json()
            fields = data.get('fields', {})

            # Extract plain text from ADF description
            description_text = self._extract_adf_text(fields.get('description'))

            # Extract recent comments (latest 3, most recent first)
            comment_body = fields.get('comment', {})
            raw_comments = comment_body.get('comments', [])
            recent_comments = []
            for c in raw_comments[-3:]:
                text = self._extract_adf_text(c.get('body'))
                if text:
                    recent_comments.append(text)

            return {
                'summary': fields.get('summary', ''),
                'description_text': description_text,
                'recent_comments': recent_comments
            }

        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching issue details for {issue_key}: {e}")
            return None

    @staticmethod
    def _extract_adf_text(adf_content) -> str:
        """
        Recursively extract plain text from Jira ADF (Atlassian Document Format).

        ADF is a nested JSON structure. This walks all nodes and collects text.
        """
        if not adf_content or not isinstance(adf_content, dict):
            return ""

        parts = []

        def _walk(node):
            if isinstance(node, dict):
                if node.get('type') == 'text':
                    parts.append(node.get('text', ''))
                for child in node.get('content', []):
                    _walk(child)
            elif isinstance(node, list):
                for item in node:
                    _walk(item)

        _walk(adf_content)
        return ' '.join(parts).strip()

    def create_worklog(self, issue_key: str, time_spent_seconds: int,
                       started: str, comment: str = "") -> bool:
        """
        Create a worklog on a Jira issue.

        Args:
            issue_key: Jira issue key (e.g., "TS-1234")
            time_spent_seconds: Time spent in seconds
            started: Date string (YYYY-MM-DD), will be formatted to ISO datetime
            comment: Optional comment text

        Returns:
            True if successful, False otherwise
        """
        try:
            url = f"{self.base_url}/rest/api/3/issue/{issue_key}/worklog"

            # Format started as ISO datetime (Jira v3 requires full datetime)
            started_dt = f"{started}T09:00:00.000+0000"

            payload = {
                'timeSpentSeconds': time_spent_seconds,
                'started': started_dt,
            }

            # Jira v3 API requires comment in ADF (Atlassian Document Format)
            # Each line becomes its own paragraph for multi-line descriptions
            if comment:
                paragraphs = []
                for line in comment.split('\n'):
                    line = line.strip()
                    if line:
                        paragraphs.append({
                            'type': 'paragraph',
                            'content': [
                                {'type': 'text', 'text': line}
                            ]
                        })
                if paragraphs:
                    payload['comment'] = {
                        'type': 'doc',
                        'version': 1,
                        'content': paragraphs
                    }

            response = self.session.post(url, json=payload, timeout=30)
            response.raise_for_status()

            logger.info(f"Created Jira worklog: {issue_key} - {time_spent_seconds}s on {started}")
            return True

        except requests.exceptions.RequestException as e:
            logger.error(f"Error creating Jira worklog for {issue_key}: {e}")
            return False


# ============================================================================
# TEMPO API CLIENT
# ============================================================================

class TempoClient:
    """Handles Tempo API interactions."""
    
    def __init__(self, config: Dict):
        self.config = config
        self.api_token = config['tempo']['api_token']
        self.base_url = "https://api.tempo.io/4"
        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': f'Bearer {self.api_token}',
            'Content-Type': 'application/json'
        })
    
    def get_user_worklogs(self, date_from: str, date_to: str) -> List[Dict]:
        """
        Fetch Tempo worklogs for current user.
        
        Args:
            date_from: Start date (YYYY-MM-DD)
            date_to: End date (YYYY-MM-DD)
        
        Returns:
            List of worklog dictionaries
        """
        try:
            url = f"{self.base_url}/worklogs/user/{self.config['user']['email']}"
            params = {
                'from': date_from,
                'to': date_to
            }
            
            response = self.session.get(url, params=params)
            response.raise_for_status()
            
            worklogs = response.json().get('results', [])
            logger.info(f"Fetched {len(worklogs)} worklogs from Tempo")
            return worklogs
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching Tempo worklogs: {e}")
            return []
    
    def create_worklog(self, issue_key: str, time_seconds: int, 
                       start_date: str, description: str = "") -> bool:
        """
        Create a new Tempo worklog entry.
        
        Args:
            issue_key: Jira issue key (e.g., "TS-1234")
            time_seconds: Time spent in seconds
            start_date: Date (YYYY-MM-DD)
            description: Optional description
        
        Returns:
            True if successful, False otherwise
        """
        try:
            url = f"{self.base_url}/worklogs"
            
            data = {
                'issueKey': issue_key,
                'timeSpentSeconds': time_seconds,
                'startDate': start_date,
                'startTime': '09:00:00',
                'authorAccountId': self.config['user']['email'],
                'description': description
            }
            
            response = self.session.post(url, json=data)
            response.raise_for_status()
            
            logger.info(f"Created Tempo worklog: {issue_key} - {time_seconds}s on {start_date}")
            return True
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Error creating Tempo worklog: {e}")
            return False
    
    def submit_timesheet(self, period_key: str = None) -> bool:
        """
        Submit timesheet for approval.
        
        Args:
            period_key: Timesheet period key (auto-detects if None)
        
        Returns:
            True if successful, False otherwise
        """
        try:
            # If no period specified, use current period
            if not period_key:
                period_key = self._get_current_period()
            
            url = f"{self.base_url}/timesheet-approvals/submit"
            
            data = {
                'worker': {
                    'accountId': self.config['user']['email']
                },
                'period': {
                    'key': period_key
                }
            }
            
            response = self.session.post(url, json=data)
            response.raise_for_status()
            
            logger.info(f"Successfully submitted timesheet for period: {period_key}")
            return True
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Error submitting timesheet: {e}")
            return False
    
    def _get_current_period(self) -> str:
        """Get current timesheet period key."""
        try:
            url = "https://api.tempo.io/4/timesheet-approvals/periods"
            
            # Get current date to find matching period
            today = date.today().strftime('%Y-%m-%d')
            
            response = self.session.get(url)
            response.raise_for_status()
            
            periods = response.json().get('results', [])
            
            # Find period containing today's date
            for period in periods:
                period_from = period.get('dateFrom')
                period_to = period.get('dateTo')
                
                if period_from and period_to:
                    if period_from <= today <= period_to:
                        period_key = period.get('key')
                        logger.info(f"Found current period: {period_key}")
                        return period_key
            
            # Fallback to simplified format
            logger.warning("No period found in API, using simplified format")
            today_obj = date.today()
            return f"{today_obj.year}-{today_obj.month:02d}"
            
        except Exception as e:
            logger.error(f"Error fetching Tempo period: {e}")
            # Fallback to simplified format
            today_obj = date.today()
            return f"{today_obj.year}-{today_obj.month:02d}"


# ============================================================================
# NOTIFICATION MANAGER
# ============================================================================

class NotificationManager:
    """Handles email notifications."""
    
    def __init__(self, config: Dict):
        self.config = config
        self.enabled = config['notifications']['email_enabled']
    
    def send_daily_summary(self, worklogs: List[Dict], total_hours: float):
        """Send daily timesheet summary email."""
        if not self.enabled:
            return
        
        subject = f"Tempo Daily Summary - {date.today().strftime('%Y-%m-%d')}"
        
        body = f"""
        <html>
        <body>
        <h2>Daily Tempo Timesheet Summary</h2>
        <p><strong>Date:</strong> {date.today().strftime('%B %d, %Y')}</p>
        <p><strong>Total Hours Logged:</strong> {total_hours:.2f} / {self.config['schedule']['daily_hours']}</p>
        
        <h3>Entries:</h3>
        <ul>
        """
        
        for wl in worklogs:
            hours = wl['time_spent_seconds'] / 3600
            body += f"<li>{wl['issue_key']}: {hours:.2f}h - {wl.get('issue_summary', '')}</li>\n"
        
        status = "✓ Complete" if total_hours >= self.config['schedule']['daily_hours'] else "⚠ Incomplete"
        body += f"""
        </ul>
        
        <p><strong>Status:</strong> {status}</p>
        
        <hr>
        <p style="color: gray; font-size: 0.9em;">
        This is an automated message from Tempo Automation Script.
        </p>
        </body>
        </html>
        """
        
        self._send_email(subject, body)
    
    def send_submission_confirmation(self, period: str):
        """Send timesheet submission confirmation."""
        if not self.enabled:
            return
        
        subject = f"Timesheet Submitted - {period}"
        
        body = f"""
        <html>
        <body>
        <h2>✓ Timesheet Submitted Successfully</h2>
        <p>Your timesheet for <strong>{period}</strong> has been automatically submitted for approval.</p>
        
        <p>No further action needed!</p>
        
        <hr>
        <p style="color: gray; font-size: 0.9em;">
        This is an automated message from Tempo Automation Script.
        </p>
        </body>
        </html>
        """
        
        self._send_email(subject, body)
    
    def _send_email(self, subject: str, body: str):
        """Send email via SMTP."""
        try:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From'] = self.config['notifications']['smtp_user']
            msg['To'] = self.config['notifications']['notification_email']
            
            msg.attach(MIMEText(body, 'html'))
            
            server = smtplib.SMTP(
                self.config['notifications']['smtp_server'],
                self.config['notifications']['smtp_port']
            )
            server.starttls()
            server.login(
                self.config['notifications']['smtp_user'],
                self.config['notifications']['smtp_password']
            )
            server.send_message(msg)
            server.quit()
            
            logger.info(f"Email sent: {subject}")
            
        except Exception as e:
            logger.error(f"Error sending email: {e}")


# ============================================================================
# AUTOMATION ENGINE
# ============================================================================

class TempoAutomation:
    """Main automation engine."""
    
    def __init__(self, config_path: Path = CONFIG_FILE):
        self.config_manager = ConfigManager(config_path)
        self.config = self.config_manager.config
        
        self.jira_client = None
        if self.config['user']['role'] == 'developer':
            self.jira_client = JiraClient(self.config)
        
        self.tempo_client = TempoClient(self.config)
        self.notifier = NotificationManager(self.config)
    
    def sync_daily(self, target_date: str = None):
        """
        Sync daily timesheet entries.
        
        Args:
            target_date: Date to sync (YYYY-MM-DD), defaults to today
        """
        if not target_date:
            target_date = date.today().strftime('%Y-%m-%d')
        
        logger.info(f"Starting daily sync for {target_date}")
        print(f"\n{'='*60}")
        print(f"TEMPO DAILY SYNC - {target_date}")
        print(f"{'='*60}\n")
        
        worklogs_created = []
        
        if self.config['user']['role'] == 'developer':
            # Auto-log time across active Jira tickets
            worklogs_created = self._auto_log_jira_worklogs(target_date)
        else:
            # Use manual configuration
            worklogs_created = self._sync_manual_activities(target_date)
        
        # Calculate total hours
        total_hours = sum(wl['time_spent_seconds'] for wl in worklogs_created) / 3600
        
        # Send notification
        self.notifier.send_daily_summary(worklogs_created, total_hours)
        
        # Print summary
        print(f"\n{'='*60}")
        print(f"✓ SYNC COMPLETE")
        print(f"{'='*60}")
        print(f"Total entries: {len(worklogs_created)}")
        print(f"Total hours: {total_hours:.2f} / {self.config['schedule']['daily_hours']}")
        
        if total_hours >= self.config['schedule']['daily_hours']:
            print("Status: ✓ Complete")
        else:
            print(f"Status: ⚠ Incomplete ({total_hours:.2f}h logged)")
        print()
        
        logger.info(f"Daily sync completed: {len(worklogs_created)} entries, {total_hours:.2f}h")
    
    def _sync_jira_worklogs(self, target_date: str) -> List[Dict]:
        """Sync Jira worklogs to Tempo."""
        # Fetch Jira worklogs
        jira_worklogs = self.jira_client.get_my_worklogs(target_date, target_date)
        
        if not jira_worklogs:
            logger.info("No Jira worklogs found for today")
            return []
        
        # Check which ones already exist in Tempo
        tempo_worklogs = self.tempo_client.get_user_worklogs(target_date, target_date)
        tempo_issue_keys = {wl.get('issue', {}).get('key') for wl in tempo_worklogs}
        
        # Create missing entries
        created = []
        for wl in jira_worklogs:
            if wl['issue_key'] not in tempo_issue_keys:
                success = self.tempo_client.create_worklog(
                    issue_key=wl['issue_key'],
                    time_seconds=wl['time_spent_seconds'],
                    start_date=target_date,
                    description=wl.get('comment', '')
                )
                
                if success:
                    print(f"✓ Created: {wl['issue_key']} - {wl['time_spent_seconds']/3600:.2f}h")
                    created.append(wl)
                else:
                    print(f"✗ Failed: {wl['issue_key']}")
            else:
                print(f"⊙ Exists: {wl['issue_key']}")
        
        return created

    def _auto_log_jira_worklogs(self, target_date: str) -> List[Dict]:
        """
        Auto-log worklogs by distributing daily hours across active Jira tickets.

        Finds all IN DEVELOPMENT / CODE REVIEW tickets assigned to the current user
        and splits the configured daily hours equally across them.
        """
        # Delete existing worklogs for target date first
        existing = self.jira_client.get_my_worklogs(target_date, target_date)
        if existing:
            print(f"Removing {len(existing)} existing worklog(s) for {target_date}...")
            for wl in existing:
                deleted = self.jira_client.delete_worklog(wl['issue_key'], wl['worklog_id'])
                if deleted:
                    print(f"  ✓ Removed {wl['time_spent_seconds']/3600:.2f}h from {wl['issue_key']}")
                else:
                    print(f"  ✗ Failed to remove worklog from {wl['issue_key']}")
            print()

        active_issues = self.jira_client.get_my_active_issues()

        if not active_issues:
            logger.warning("No active issues found (IN DEVELOPMENT / CODE REVIEW)")
            print("⚠ No active tickets found. Make sure you have tickets IN DEVELOPMENT or CODE REVIEW.")
            return []

        daily_hours = self.config.get('schedule', {}).get('daily_hours', 8)
        hours_per_ticket = daily_hours / len(active_issues)
        seconds_per_ticket = int(hours_per_ticket * 3600)

        print(f"Found {len(active_issues)} active ticket(s):")
        for issue in active_issues:
            print(f"  - {issue['issue_key']}: {issue['issue_summary']}")
        print(f"\n{daily_hours}h / {len(active_issues)} tickets = {hours_per_ticket:.2f}h each\n")

        created = []
        for issue in active_issues:
            # Generate a meaningful description from ticket content
            comment = self._generate_work_summary(issue['issue_key'], issue['issue_summary'])
            success = self.jira_client.create_worklog(
                issue_key=issue['issue_key'],
                time_spent_seconds=seconds_per_ticket,
                started=target_date,
                comment=comment
            )

            if success:
                print(f"  ✓ Logged {hours_per_ticket:.2f}h on {issue['issue_key']}")
                print(f"    Description: {comment[:80]}{'...' if len(comment) > 80 else ''}")
                created.append({
                    'issue_key': issue['issue_key'],
                    'issue_summary': issue['issue_summary'],
                    'time_spent_seconds': seconds_per_ticket
                })
            else:
                print(f"  ✗ Failed: {issue['issue_key']}")

        return created

    def _generate_work_summary(self, issue_key: str, issue_summary: str) -> str:
        """
        Generate a brief worklog description from a Jira ticket's content.

        Reads the ticket's description and recent comments, then builds
        a concise 1-3 line summary of work done.

        Args:
            issue_key: Jira issue key
            issue_summary: Issue title/summary

        Returns:
            A brief description string for the worklog comment
        """
        details = self.jira_client.get_issue_details(issue_key)

        if not details:
            return f"Worked on {issue_key}: {issue_summary}"

        lines = []

        # Line 1: What the ticket is about (from summary + description)
        desc = details.get('description_text', '')
        if desc:
            # Take the first sentence or up to 120 chars from description
            first_sentence = desc.split('.')[0].strip()
            if len(first_sentence) > 120:
                first_sentence = first_sentence[:117] + '...'
            lines.append(first_sentence)
        else:
            lines.append(issue_summary)

        # Lines 2-3: What was actually done (from recent comments)
        comments = details.get('recent_comments', [])
        for c in reversed(comments):  # most recent first
            # Take first meaningful line from the comment
            c_line = c.split('\n')[0].strip()
            if c_line and len(c_line) > 5:
                if len(c_line) > 120:
                    c_line = c_line[:117] + '...'
                lines.append(c_line)
            if len(lines) >= 3:
                break

        return '\n'.join(lines)

    def _sync_manual_activities(self, target_date: str) -> List[Dict]:
        """Sync manual activities from configuration."""
        manual_activities = self.config.get('manual_activities', [])
        
        if not manual_activities:
            logger.warning("No manual activities configured")
            print("⚠ No manual activities configured. Please edit config.json")
            return []
        
        # Check existing entries
        tempo_worklogs = self.tempo_client.get_user_worklogs(target_date, target_date)
        
        if tempo_worklogs:
            logger.info("Manual entries already exist for today")
            print("⊙ Timesheet entries already exist for today")
            return tempo_worklogs
        
        # Create entries from configuration
        created = []
        for activity in manual_activities:
            # Get issue key from config, or use default
            # Ask your Jira admin what issue key to use for general time tracking
            issue_key = self.config.get('organization', {}).get('default_issue_key', 'GENERAL-001')
            
            time_seconds = int(activity['hours'] * 3600)
            
            success = self.tempo_client.create_worklog(
                issue_key=issue_key,
                time_seconds=time_seconds,
                start_date=target_date,
                description=activity['activity']
            )
            
            if success:
                print(f"✓ Created: {activity['activity']} - {activity['hours']}h")
                created.append({
                    'issue_key': issue_key,
                    'issue_summary': activity['activity'],
                    'time_spent_seconds': time_seconds
                })
        
        return created
    
    def submit_timesheet(self):
        """Submit monthly timesheet."""
        logger.info("Starting timesheet submission")
        print(f"\n{'='*60}")
        print("TEMPO MONTHLY TIMESHEET SUBMISSION")
        print(f"{'='*60}\n")
        
        # Get current period
        today = date.today()
        period = f"{today.year}-{today.month:02d}"
        
        # Submit
        success = self.tempo_client.submit_timesheet(period)
        
        if success:
            print(f"✓ Timesheet submitted successfully for {period}")
            self.notifier.send_submission_confirmation(period)
        else:
            print(f"✗ Failed to submit timesheet for {period}")
        
        print()
        logger.info(f"Timesheet submission {'successful' if success else 'failed'}")


# ============================================================================
# COMMAND LINE INTERFACE
# ============================================================================

def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Tempo Timesheet Automation',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python tempo_automation.py                    # Sync today's timesheet
  python tempo_automation.py --submit           # Submit monthly timesheet
  python tempo_automation.py --date 2026-02-01  # Sync specific date
  python tempo_automation.py --setup            # Run setup wizard again
        """
    )
    
    parser.add_argument('--submit', action='store_true',
                       help='Submit monthly timesheet')
    parser.add_argument('--date', type=str,
                       help='Target date (YYYY-MM-DD)')
    parser.add_argument('--setup', action='store_true',
                       help='Run setup wizard')
    
    args = parser.parse_args()
    
    try:
        # Run setup if requested
        if args.setup:
            config_manager = ConfigManager()
            config_manager.setup_wizard()
            return
        
        # Initialize automation
        automation = TempoAutomation()
        
        # Submit timesheet
        if args.submit:
            automation.submit_timesheet()
        # Daily sync
        else:
            automation.sync_daily(args.date)
            
    except KeyboardInterrupt:
        print("\n\nOperation cancelled by user.")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        print(f"\n✗ Error: {e}")
        print(f"See {LOG_FILE} for details")
        sys.exit(1)


if __name__ == "__main__":
    main()
