#!/usr/bin/env python3
"""
Notion <-> Apple Reminders Bidirectional Sync

Syncs tasks between a Notion database and Apple Reminders on macOS.
Uses PyObjC/EventKit for native Reminders access.

Sync Logic:
- Query Notion for tasks assigned to me, not Done, Type ≠ Onboarding
- Match to Reminders in "Work" list by Notion page ID stored in URL field
- Create missing reminders with Notion tag, URL, and customer name in notes
- Update due dates and titles using last-modified timestamps
  (Reminders is primary—always push to Notion; Notion only updates Reminders if newer)
- Mark Notion tasks Done when Reminders are completed
- Create Notion tasks from new Reminders tagged "Notion" with no URL

Configuration:
    Create a .env file or config.json in the same directory, or set environment variables.
    See config.example.json for the required fields.

Requirements:
    pip install pyobjc-framework-EventKit pyobjc-framework-Foundation requests

Usage:
    python notion_reminders_sync.py [--dry-run]
"""

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import requests

# EventKit imports - these require macOS
try:
    import EventKit
    import Foundation
except ImportError:
    print("Error: PyObjC frameworks not found. Install with:")
    print("  pip install pyobjc-framework-EventKit pyobjc-framework-Foundation")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Configuration Loading
# ---------------------------------------------------------------------------

def load_config() -> dict:
    """
    Load configuration from (in order of priority):
    1. Environment variables
    2. .env file in script directory
    3. config.json in script directory

    Required config keys:
    - NOTION_API_KEY: Your Notion integration API key
    - NOTION_DATABASE_ID: The ID of the Notion database to sync
    - NOTION_USER_ID: Your Notion user ID (for filtering tasks assigned to you)

    Optional config keys:
    - REMINDERS_LIST_NAME: Name of the Reminders list (default: "Work")
    - NOTION_TAG: Tag added to reminders (default: "#Notion")
    """
    script_dir = Path(__file__).parent.resolve()

    config = {
        "NOTION_API_KEY": None,
        "NOTION_DATABASE_ID": None,
        "NOTION_USER_ID": None,
        "REMINDERS_LIST_NAME": "Work",
        "NOTION_TAG": "#Notion",
        # Notion property names (customizable)
        "PROP_TITLE": "Request",
        "PROP_ASSIGNEE": "Assignee",
        "PROP_STATUS": "Status",
        "PROP_DUE_DATE": "Due date",
        "PROP_CUSTOMER": "Customer",
        "PROP_TYPE": "Type",
        # Status values
        "STATUS_DONE": "Done",
        "STATUS_CANCELED": "Canceled",
        "STATUS_NEW": "New",
        # Type value to exclude (empty string disables filtering)
        "TYPE_EXCLUDE": "Onboarding",
    }

    # Try config.json first (lowest priority)
    config_json_path = script_dir / "config.json"
    if config_json_path.exists():
        try:
            with open(config_json_path) as f:
                json_config = json.load(f)
                for key in config:
                    if key in json_config and json_config[key]:
                        config[key] = json_config[key]
        except (json.JSONDecodeError, IOError) as e:
            print(f"Warning: Failed to load config.json: {e}")

    # Try .env file (medium priority)
    env_path = script_dir / ".env"
    if env_path.exists():
        try:
            with open(env_path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, value = line.split("=", 1)
                        key = key.strip()
                        value = value.strip().strip("\"'")
                        if key in config and value:
                            config[key] = value
        except IOError as e:
            print(f"Warning: Failed to load .env: {e}")

    # Environment variables override everything (highest priority)
    for key in config:
        env_value = os.environ.get(key)
        if env_value:
            config[key] = env_value

    # Validate required fields
    missing = [k for k in ["NOTION_API_KEY", "NOTION_DATABASE_ID", "NOTION_USER_ID"]
               if not config.get(k)]
    if missing:
        print("Error: Missing required configuration:")
        for key in missing:
            print(f"  - {key}")
        print("\nSet these via environment variables, .env file, or config.json")
        print("See config.example.json for the format.")
        sys.exit(1)

    return config


# Load configuration at module level
CONFIG = load_config()
NOTION_API_KEY = CONFIG["NOTION_API_KEY"]
NOTION_DATABASE_ID = CONFIG["NOTION_DATABASE_ID"]
NOTION_USER_ID = CONFIG["NOTION_USER_ID"]
REMINDERS_LIST_NAME = CONFIG["REMINDERS_LIST_NAME"]
NOTION_TAG = CONFIG["NOTION_TAG"]

# Notion property names
PROP_TITLE = CONFIG["PROP_TITLE"]
PROP_ASSIGNEE = CONFIG["PROP_ASSIGNEE"]
PROP_STATUS = CONFIG["PROP_STATUS"]
PROP_DUE_DATE = CONFIG["PROP_DUE_DATE"]
PROP_CUSTOMER = CONFIG["PROP_CUSTOMER"]
PROP_TYPE = CONFIG["PROP_TYPE"]

# Status values
STATUS_DONE = CONFIG["STATUS_DONE"]
STATUS_CANCELED = CONFIG["STATUS_CANCELED"]
STATUS_NEW = CONFIG["STATUS_NEW"]

# Type exclusion
TYPE_EXCLUDE = CONFIG["TYPE_EXCLUDE"]

# State file for tracking synced items (to detect deletions)
STATE_FILE = Path(__file__).parent.resolve() / ".sync_state.json"


def load_sync_state() -> dict:
    """Load the previous sync state from disk."""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {"synced_pairs": {}}  # notion_page_id -> reminder_id


def save_sync_state(state: dict):
    """Save the current sync state to disk."""
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except IOError as e:
        print(f"Warning: Failed to save sync state: {e}")


@dataclass
class NotionTask:
    """Represents a task from Notion."""
    page_id: str
    title: str
    due_date: Optional[datetime]
    status: str
    last_updated: datetime
    customer_name: Optional[str]
    url: str

    @property
    def is_done(self) -> bool:
        return self.status in (STATUS_DONE, STATUS_CANCELED)


@dataclass
class Reminder:
    """Represents an Apple Reminder."""
    id: str
    title: str
    due_date: Optional[datetime]
    notes: Optional[str]
    url: Optional[str]
    completed: bool
    last_modified: Optional[datetime]
    tags: list[str]  # List of tag names
    ek_reminder: object  # The underlying EKReminder object

    @property
    def notion_page_id(self) -> Optional[str]:
        """Extract Notion page ID from the URL field."""
        if not self.url:
            return None
        # Notion URLs: https://www.notion.so/workspace/Page-Title-abc123def456
        # or https://www.notion.so/abc123def456
        match = re.search(r'notion\.so/(?:[^/]+/)?(?:[^-]+-)?([a-f0-9]{32})', self.url)
        if match:
            return match.group(1)
        # Also try extracting raw ID from URL
        match = re.search(r'([a-f0-9]{32})', self.url)
        return match.group(1) if match else None

    @property
    def has_notion_tag(self) -> bool:
        """Check if reminder has the Notion tag in title or notes.

        Note: EventKit doesn't expose actual Reminders tags, so we check
        for #Notion in the title or notes instead.
        """
        tag_pattern = NOTION_TAG.lower()  # e.g., "#notion"
        # Also check without the # in case they wrote just "Notion"
        tag_word = NOTION_TAG.lstrip("#").lower()  # e.g., "notion"

        # Check title
        if self.title:
            title_lower = self.title.lower()
            if tag_pattern in title_lower or f"#{tag_word}" in title_lower:
                return True

        # Check notes
        if self.notes:
            notes_lower = self.notes.lower()
            if tag_pattern in notes_lower or f"#{tag_word}" in notes_lower:
                return True

        return False


class NotionClient:
    """Client for Notion API operations."""

    BASE_URL = "https://api.notion.com/v1"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {api_key}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        })
        # Cache for customer names
        self._customer_cache: dict[str, str] = {}

    def query_my_tasks(self, database_id: str, user_id: str) -> list[NotionTask]:
        """Query tasks assigned to user, not Done, optionally excluding a Type."""
        url = f"{self.BASE_URL}/databases/{database_id}/query"

        filter_conditions = [
            {
                "property": PROP_ASSIGNEE,
                "people": {
                    "contains": user_id
                }
            },
            {
                "property": PROP_STATUS,
                "status": {
                    "does_not_equal": STATUS_DONE
                }
            },
            {
                "property": PROP_STATUS,
                "status": {
                    "does_not_equal": STATUS_CANCELED
                }
            },
        ]

        # Only add type filter if TYPE_EXCLUDE is set
        if TYPE_EXCLUDE:
            filter_conditions.append({
                "property": PROP_TYPE,
                "select": {
                    "does_not_equal": TYPE_EXCLUDE
                }
            })

        filter_payload = {"and": filter_conditions}

        tasks = []
        has_more = True
        start_cursor = None

        while has_more:
            payload = {"filter": filter_payload, "page_size": 100}
            if start_cursor:
                payload["start_cursor"] = start_cursor

            response = self.session.post(url, json=payload)
            response.raise_for_status()
            data = response.json()

            for page in data.get("results", []):
                task = self._parse_page(page)
                if task:
                    tasks.append(task)

            has_more = data.get("has_more", False)
            start_cursor = data.get("next_cursor")

        return tasks

    def _parse_page(self, page: dict) -> Optional[NotionTask]:
        """Parse a Notion page into a NotionTask."""
        try:
            page_id = page["id"].replace("-", "")
            properties = page["properties"]

            # Title (configurable property name)
            title_prop = properties.get(PROP_TITLE, {})
            title_items = title_prop.get("title", [])
            title = title_items[0]["plain_text"] if title_items else "Untitled"

            # Due date
            due_date = None
            due_prop = properties.get(PROP_DUE_DATE, {})
            date_obj = due_prop.get("date")
            if date_obj and date_obj.get("start"):
                date_str = date_obj["start"]
                # Handle both date and datetime formats
                if "T" in date_str:
                    due_date = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                else:
                    due_date = datetime.strptime(date_str, "%Y-%m-%d").replace(
                        tzinfo=timezone.utc
                    )

            # Status
            status_prop = properties.get(PROP_STATUS, {})
            status = status_prop.get("status", {}).get("name", STATUS_NEW)

            # Last updated
            last_updated_str = properties.get("Last updated", {}).get(
                "last_edited_time", page.get("last_edited_time")
            )
            last_updated = datetime.fromisoformat(
                last_updated_str.replace("Z", "+00:00")
            )

            # Customer (relation)
            customer_name = None
            customer_prop = properties.get(PROP_CUSTOMER, {})
            customer_relations = customer_prop.get("relation", [])
            if customer_relations:
                customer_id = customer_relations[0].get("id")
                if customer_id:
                    customer_name = self._get_customer_name(customer_id)

            # Page URL
            url = page.get("url", f"https://notion.so/{page_id}")

            return NotionTask(
                page_id=page_id,
                title=title,
                due_date=due_date,
                status=status,
                last_updated=last_updated,
                customer_name=customer_name,
                url=url,
            )
        except Exception as e:
            print(f"Warning: Failed to parse page: {e}")
            return None

    def _get_customer_name(self, customer_page_id: str) -> Optional[str]:
        """Fetch customer name from related page."""
        if customer_page_id in self._customer_cache:
            return self._customer_cache[customer_page_id]

        try:
            url = f"{self.BASE_URL}/pages/{customer_page_id}"
            response = self.session.get(url)
            response.raise_for_status()
            page = response.json()

            # Try to find the title property
            for prop_name, prop_value in page.get("properties", {}).items():
                if prop_value.get("type") == "title":
                    title_items = prop_value.get("title", [])
                    if title_items:
                        name = title_items[0]["plain_text"]
                        self._customer_cache[customer_page_id] = name
                        return name
        except Exception as e:
            print(f"Warning: Failed to fetch customer name: {e}")

        return None

    def update_task_due_date(self, page_id: str, due_date: Optional[datetime]) -> bool:
        """Update the due date of a Notion task."""
        url = f"{self.BASE_URL}/pages/{page_id}"

        if due_date:
            # Format as date only (no time component)
            date_str = due_date.strftime("%Y-%m-%d")
            date_value = {"start": date_str}
        else:
            date_value = None

        payload = {
            "properties": {
                PROP_DUE_DATE: {
                    "date": date_value
                }
            }
        }

        try:
            response = self.session.patch(url, json=payload)
            response.raise_for_status()
            return True
        except Exception as e:
            print(f"Error updating due date for {page_id}: {e}")
            return False

    def update_task_title(self, page_id: str, title: str) -> bool:
        """Update the title of a Notion task."""
        url = f"{self.BASE_URL}/pages/{page_id}"

        payload = {
            "properties": {
                PROP_TITLE: {
                    "title": [
                        {
                            "type": "text",
                            "text": {"content": title}
                        }
                    ]
                }
            }
        }

        try:
            response = self.session.patch(url, json=payload)
            response.raise_for_status()
            return True
        except Exception as e:
            print(f"Error updating title for {page_id}: {e}")
            return False

    def mark_task_done(self, page_id: str) -> bool:
        """Mark a Notion task as Done."""
        url = f"{self.BASE_URL}/pages/{page_id}"

        payload = {
            "properties": {
                PROP_STATUS: {
                    "status": {"name": STATUS_DONE}
                }
            }
        }

        try:
            response = self.session.patch(url, json=payload)
            response.raise_for_status()
            return True
        except Exception as e:
            print(f"Error marking task done for {page_id}: {e}")
            return False

    def mark_task_canceled(self, page_id: str) -> bool:
        """Mark a Notion task as Canceled."""
        url = f"{self.BASE_URL}/pages/{page_id}"

        payload = {
            "properties": {
                PROP_STATUS: {
                    "status": {"name": STATUS_CANCELED}
                }
            }
        }

        try:
            response = self.session.patch(url, json=payload)
            response.raise_for_status()
            return True
        except Exception as e:
            print(f"Error marking task canceled for {page_id}: {e}")
            return False

    def get_task_status(self, page_id: str) -> Optional[str]:
        """Get the current status of a Notion task. Returns None if task doesn't exist."""
        url = f"{self.BASE_URL}/pages/{page_id}"

        try:
            response = self.session.get(url)
            if response.status_code == 404:
                return None  # Task was deleted
            response.raise_for_status()
            page = response.json()

            # Check if archived (deleted)
            if page.get("archived", False):
                return None

            status_prop = page.get("properties", {}).get(PROP_STATUS, {})
            return status_prop.get("status", {}).get("name")
        except Exception as e:
            print(f"Error getting task status for {page_id}: {e}")
            return None

    def create_task(
        self,
        database_id: str,
        title: str,
        user_id: str,
        due_date: Optional[datetime] = None,
    ) -> Optional[str]:
        """Create a new task in Notion. Returns the page ID if successful."""
        url = f"{self.BASE_URL}/pages"

        properties = {
            PROP_TITLE: {
                "title": [
                    {
                        "type": "text",
                        "text": {"content": title}
                    }
                ]
            },
            PROP_ASSIGNEE: {
                "people": [{"id": user_id}]
            },
            PROP_STATUS: {
                "status": {"name": STATUS_NEW}
            },
        }

        if due_date:
            properties[PROP_DUE_DATE] = {
                "date": {"start": due_date.strftime("%Y-%m-%d")}
            }

        payload = {
            "parent": {"database_id": database_id},
            "properties": properties,
        }

        try:
            response = self.session.post(url, json=payload)
            response.raise_for_status()
            page = response.json()
            return page["id"].replace("-", "")
        except Exception as e:
            print(f"Error creating task: {e}")
            return None


class RemindersClient:
    """Client for Apple Reminders via EventKit."""

    def __init__(self):
        self.store = EventKit.EKEventStore.alloc().init()
        self._authorized = False
        self._work_list = None

    def request_access(self) -> bool:
        """Request access to Reminders. Blocks until permission is granted or denied."""
        # Use a flag to track authorization result
        result = {"granted": False, "done": False}

        def completion_handler(granted, error):
            result["granted"] = granted
            if error:
                print(f"Authorization error: {error}")
            result["done"] = True

        # Request full access (required for writing)
        self.store.requestFullAccessToRemindersWithCompletion_(completion_handler)

        # Wait for completion (with timeout)
        timeout = 30
        start = time.time()
        while not result["done"] and (time.time() - start) < timeout:
            # Process events to allow callback to execute
            Foundation.NSRunLoop.currentRunLoop().runUntilDate_(
                Foundation.NSDate.dateWithTimeIntervalSinceNow_(0.1)
            )

        self._authorized = result["granted"]
        if not self._authorized:
            print("Error: Reminders access not granted.")
            print("Please grant access in System Settings > Privacy & Security > Reminders")
        return self._authorized

    def get_work_list(self) -> Optional[object]:
        """Get or create the Work reminders list."""
        if self._work_list:
            return self._work_list

        calendars = self.store.calendarsForEntityType_(EventKit.EKEntityTypeReminder)

        for calendar in calendars:
            if calendar.title() == REMINDERS_LIST_NAME:
                self._work_list = calendar
                return self._work_list

        # Create the list if it doesn't exist
        new_list = EventKit.EKCalendar.calendarForEntityType_eventStore_(
            EventKit.EKEntityTypeReminder, self.store
        )
        new_list.setTitle_(REMINDERS_LIST_NAME)
        new_list.setSource_(self.store.defaultCalendarForNewReminders().source())

        error = None
        success = self.store.saveCalendar_commit_error_(new_list, True, error)
        if success:
            self._work_list = new_list
            print(f"Created new Reminders list: {REMINDERS_LIST_NAME}")
        else:
            print(f"Error creating Work list: {error}")
            return None

        return self._work_list

    def get_all_reminders(self) -> list[Reminder]:
        """Fetch all reminders from the Work list."""
        work_list = self.get_work_list()
        if not work_list:
            return []

        # Create predicate for all reminders in Work list
        predicate = self.store.predicateForRemindersInCalendars_([work_list])

        # Fetch reminders synchronously
        reminders_result = {"reminders": None, "done": False}

        def fetch_completion(reminders):
            reminders_result["reminders"] = reminders
            reminders_result["done"] = True

        self.store.fetchRemindersMatchingPredicate_completion_(
            predicate, fetch_completion
        )

        # Wait for completion
        timeout = 30
        start = time.time()
        while not reminders_result["done"] and (time.time() - start) < timeout:
            Foundation.NSRunLoop.currentRunLoop().runUntilDate_(
                Foundation.NSDate.dateWithTimeIntervalSinceNow_(0.1)
            )

        if reminders_result["reminders"] is None:
            return []

        return [
            self._parse_reminder(r) for r in reminders_result["reminders"]
        ]

    def _parse_reminder(self, ek_reminder) -> Reminder:
        """Parse an EKReminder into our Reminder dataclass."""
        # Get due date from dueDateComponents
        due_date = None
        due_components = ek_reminder.dueDateComponents()
        if due_components:
            calendar = Foundation.NSCalendar.currentCalendar()
            ns_date = calendar.dateFromComponents_(due_components)
            if ns_date:
                # Convert NSDate to Python datetime
                timestamp = ns_date.timeIntervalSince1970()
                due_date = datetime.fromtimestamp(timestamp, tz=timezone.utc)

        # Get URL
        url = None
        ek_url = ek_reminder.URL()
        if ek_url:
            url = str(ek_url.absoluteString())

        # Get last modified date
        last_modified = None
        mod_date = ek_reminder.lastModifiedDate()
        if mod_date:
            timestamp = mod_date.timeIntervalSince1970()
            last_modified = datetime.fromtimestamp(timestamp, tz=timezone.utc)

        # Get tags (hashtags in Reminders)
        tags = []
        if hasattr(ek_reminder, 'hashtagTexts') and ek_reminder.hashtagTexts():
            tags = [str(t) for t in ek_reminder.hashtagTexts()]

        return Reminder(
            id=str(ek_reminder.calendarItemIdentifier()),
            title=str(ek_reminder.title()) if ek_reminder.title() else "",
            due_date=due_date,
            notes=str(ek_reminder.notes()) if ek_reminder.notes() else None,
            url=url,
            completed=bool(ek_reminder.isCompleted()),
            last_modified=last_modified,
            tags=tags,
            ek_reminder=ek_reminder,
        )

    def create_reminder(
        self,
        title: str,
        due_date: Optional[datetime] = None,
        notes: Optional[str] = None,
        url: Optional[str] = None,
        add_notion_tag: bool = True,
    ) -> Optional[Reminder]:
        """Create a new reminder in the Work list."""
        work_list = self.get_work_list()
        if not work_list:
            return None

        reminder = EventKit.EKReminder.reminderWithEventStore_(self.store)
        reminder.setTitle_(title)
        reminder.setCalendar_(work_list)

        if notes:
            reminder.setNotes_(notes)

        if url:
            ns_url = Foundation.NSURL.URLWithString_(url)
            reminder.setURL_(ns_url)

        if due_date:
            self._set_due_date(reminder, due_date)

        # Add the Notion tag if requested
        if add_notion_tag:
            tag_name = NOTION_TAG.lstrip("#")  # Remove # if present
            if hasattr(reminder, 'setHashtagTexts_'):
                reminder.setHashtagTexts_([tag_name])

        error = None
        success = self.store.saveReminder_commit_error_(reminder, True, error)
        if success:
            return self._parse_reminder(reminder)
        else:
            print(f"Error creating reminder: {error}")
            return None

    def _set_due_date(self, reminder, due_date: datetime):
        """Set the due date on a reminder."""
        components = Foundation.NSDateComponents.alloc().init()
        components.setYear_(due_date.year)
        components.setMonth_(due_date.month)
        components.setDay_(due_date.day)
        # Only set time if the datetime has a non-midnight time
        if due_date.hour != 0 or due_date.minute != 0:
            components.setHour_(due_date.hour)
            components.setMinute_(due_date.minute)
        reminder.setDueDateComponents_(components)

    def update_reminder(
        self,
        reminder: Reminder,
        title: Optional[str] = None,
        due_date: Optional[datetime] = None,
        clear_due_date: bool = False,
    ) -> bool:
        """Update an existing reminder."""
        ek_reminder = reminder.ek_reminder

        if title is not None:
            ek_reminder.setTitle_(title)

        if clear_due_date:
            ek_reminder.setDueDateComponents_(None)
        elif due_date is not None:
            self._set_due_date(ek_reminder, due_date)

        error = None
        success = self.store.saveReminder_commit_error_(ek_reminder, True, error)
        if not success:
            print(f"Error updating reminder: {error}")
        return success

    def complete_reminder(self, reminder: Reminder) -> bool:
        """Mark a reminder as completed."""
        ek_reminder = reminder.ek_reminder
        ek_reminder.setCompleted_(True)

        error = None
        success = self.store.saveReminder_commit_error_(ek_reminder, True, error)
        if not success:
            print(f"Error completing reminder: {error}")
        return success

    def delete_reminder(self, reminder: Reminder) -> bool:
        """Delete a reminder."""
        ek_reminder = reminder.ek_reminder

        error = None
        success = self.store.removeReminder_commit_error_(ek_reminder, True, error)
        if not success:
            print(f"Error deleting reminder: {error}")
        return success

    def get_reminder_by_id(self, reminder_id: str) -> Optional[Reminder]:
        """Fetch a specific reminder by its ID. Returns None if not found."""
        # calendarItemWithIdentifier returns the item or None
        ek_reminder = self.store.calendarItemWithIdentifier_(reminder_id)
        if ek_reminder is None:
            return None
        # Make sure it's actually a reminder (not an event)
        if not isinstance(ek_reminder, EventKit.EKReminder):
            return None
        return self._parse_reminder(ek_reminder)


class NotionRemindersSync:
    """Bidirectional sync between Notion and Apple Reminders."""

    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.notion = NotionClient(NOTION_API_KEY)
        self.reminders = RemindersClient()

        # Stats
        self.stats = {
            "reminders_created": 0,
            "reminders_updated": 0,
            "reminders_completed": 0,
            "reminders_deleted": 0,
            "notion_tasks_created": 0,
            "notion_tasks_updated": 0,
            "notion_tasks_completed": 0,
            "notion_tasks_canceled": 0,
        }

        # Load previous sync state for deletion detection
        self.sync_state = load_sync_state()

    def run(self):
        """Run the full sync process."""
        print("=" * 60)
        print("Notion <-> Apple Reminders Sync")
        print("=" * 60)

        if self.dry_run:
            print("** DRY RUN MODE - No changes will be made **\n")

        # Request Reminders access
        print("Requesting Reminders access...")
        if not self.reminders.request_access():
            print("Cannot proceed without Reminders access.")
            return

        # Fetch data from both sources
        print("\nFetching Notion tasks...")
        notion_tasks = self.notion.query_my_tasks(NOTION_DATABASE_ID, NOTION_USER_ID)
        print(f"  Found {len(notion_tasks)} tasks")

        print("\nFetching Reminders...")
        all_reminders = self.reminders.get_all_reminders()
        print(f"  Found {len(all_reminders)} reminders in '{REMINDERS_LIST_NAME}' list")

        # Build lookup maps
        notion_by_id = {t.page_id: t for t in notion_tasks}
        reminders_by_id = {r.id: r for r in all_reminders}
        reminders_by_notion_id: dict[str, Reminder] = {}
        unlinked_notion_reminders: list[Reminder] = []

        for r in all_reminders:
            notion_id = r.notion_page_id
            if notion_id:
                reminders_by_notion_id[notion_id] = r
            elif r.has_notion_tag and not r.url and not r.completed:
                # New reminder tagged for Notion but not yet linked
                unlinked_notion_reminders.append(r)

        # Sync operations
        print("\n" + "-" * 40)
        print("Processing sync...")
        print("-" * 40)

        # 1. Handle deletions based on previous sync state
        self._handle_deletions(notion_by_id, reminders_by_id, reminders_by_notion_id)

        # 2. Handle Notion tasks marked Done or Canceled
        self._handle_notion_status_changes(reminders_by_notion_id)

        # 3. Create reminders for Notion tasks that don't have one
        self._create_missing_reminders(notion_tasks, reminders_by_notion_id)

        # 4. Sync existing pairs (bidirectional)
        self._sync_existing_pairs(notion_by_id, reminders_by_notion_id)

        # 5. Handle completed reminders -> mark Notion done
        self._handle_completed_reminders(notion_by_id, all_reminders)

        # 6. Create Notion tasks from new tagged reminders
        self._create_notion_from_reminders(unlinked_notion_reminders)

        # 7. Update and save sync state
        self._update_sync_state(reminders_by_notion_id)

        # Print summary
        print("\n" + "=" * 60)
        print("Sync Summary")
        print("=" * 60)
        print(f"  Reminders created:      {self.stats['reminders_created']}")
        print(f"  Reminders updated:      {self.stats['reminders_updated']}")
        print(f"  Reminders completed:    {self.stats['reminders_completed']}")
        print(f"  Reminders deleted:      {self.stats['reminders_deleted']}")
        print(f"  Notion tasks created:   {self.stats['notion_tasks_created']}")
        print(f"  Notion tasks updated:   {self.stats['notion_tasks_updated']}")
        print(f"  Notion tasks completed: {self.stats['notion_tasks_completed']}")
        print(f"  Notion tasks canceled:  {self.stats['notion_tasks_canceled']}")

    def _handle_deletions(
        self,
        notion_by_id: dict[str, NotionTask],
        reminders_by_id: dict[str, Reminder],
        reminders_by_notion_id: dict[str, Reminder],
    ):
        """
        Handle deletions based on previous sync state.

        - If a reminder was deleted -> cancel the Notion task
        - If a Notion task was deleted -> delete the reminder
        """
        previous_pairs = self.sync_state.get("synced_pairs", {})

        for notion_id, reminder_id in list(previous_pairs.items()):
            reminder_exists = reminder_id in reminders_by_id
            notion_exists = notion_id in notion_by_id

            # Check if Notion task was truly deleted (not just filtered out)
            notion_deleted = False
            if not notion_exists:
                # Query Notion directly to see if it's deleted vs just Done/Canceled
                status = self.notion.get_task_status(notion_id)
                notion_deleted = (status is None)  # None means deleted/archived

            if not reminder_exists and notion_exists:
                # Reminder was deleted -> cancel Notion task
                task = notion_by_id[notion_id]
                print(f"\n  Canceling Notion task (reminder deleted): {task.title}")

                if not self.dry_run:
                    if self.notion.mark_task_canceled(notion_id):
                        self.stats["notion_tasks_canceled"] += 1
                        # Remove from state since it's now canceled
                        del previous_pairs[notion_id]
                else:
                    self.stats["notion_tasks_canceled"] += 1

            elif reminder_exists and notion_deleted:
                # Notion task was deleted -> delete the reminder
                reminder = reminders_by_id[reminder_id]
                print(f"\n  Deleting reminder (Notion task deleted): {reminder.title}")

                if not self.dry_run:
                    if self.reminders.delete_reminder(reminder):
                        self.stats["reminders_deleted"] += 1
                        # Remove from state
                        del previous_pairs[notion_id]
                        # Remove from lookup maps
                        if notion_id in reminders_by_notion_id:
                            del reminders_by_notion_id[notion_id]
                else:
                    self.stats["reminders_deleted"] += 1

    def _handle_notion_status_changes(
        self,
        reminders_by_notion_id: dict[str, Reminder],
    ):
        """
        Handle Notion tasks that were marked Done or Canceled.

        - Done -> complete the reminder
        - Canceled -> delete the reminder
        """
        previous_pairs = self.sync_state.get("synced_pairs", {})

        for notion_id, reminder_id in list(previous_pairs.items()):
            if notion_id not in reminders_by_notion_id:
                continue

            reminder = reminders_by_notion_id[notion_id]
            if reminder.completed:
                continue  # Already completed

            # Check current Notion status
            status = self.notion.get_task_status(notion_id)

            if status == STATUS_DONE:
                print(f"\n  Completing reminder (Notion marked Done): {reminder.title}")

                if not self.dry_run:
                    if self.reminders.complete_reminder(reminder):
                        self.stats["reminders_completed"] += 1
                else:
                    self.stats["reminders_completed"] += 1

            elif status == STATUS_CANCELED:
                print(f"\n  Deleting reminder (Notion marked Canceled): {reminder.title}")

                if not self.dry_run:
                    if self.reminders.delete_reminder(reminder):
                        self.stats["reminders_deleted"] += 1
                        # Remove from state and lookup
                        del previous_pairs[notion_id]
                        del reminders_by_notion_id[notion_id]
                else:
                    self.stats["reminders_deleted"] += 1

    def _update_sync_state(self, reminders_by_notion_id: dict[str, Reminder]):
        """Update and save the sync state with current pairs."""
        if self.dry_run:
            return

        # Build new state from current synced pairs
        new_pairs = {}
        for notion_id, reminder in reminders_by_notion_id.items():
            if not reminder.completed:  # Only track active pairs
                new_pairs[notion_id] = reminder.id

        self.sync_state["synced_pairs"] = new_pairs
        save_sync_state(self.sync_state)

    def _create_missing_reminders(
        self,
        notion_tasks: list[NotionTask],
        reminders_by_notion_id: dict[str, Reminder],
    ):
        """Create reminders for Notion tasks that don't have a matching reminder."""
        for task in notion_tasks:
            if task.page_id in reminders_by_notion_id:
                continue

            # Put customer name in notes (tag is added automatically via add_notion_tag)
            notes = f"Customer: {task.customer_name}" if task.customer_name else None

            print(f"\n  Creating reminder: {task.title}")
            print(f"    Due: {task.due_date.date() if task.due_date else 'None'}")
            if task.customer_name:
                print(f"    Customer: {task.customer_name}")

            if not self.dry_run:
                reminder = self.reminders.create_reminder(
                    title=task.title,
                    due_date=task.due_date,
                    notes=notes,
                    url=task.url,
                    add_notion_tag=True,
                )
                if reminder:
                    self.stats["reminders_created"] += 1
                    reminders_by_notion_id[task.page_id] = reminder
            else:
                self.stats["reminders_created"] += 1

    def _sync_existing_pairs(
        self,
        notion_by_id: dict[str, NotionTask],
        reminders_by_notion_id: dict[str, Reminder],
    ):
        """
        Sync existing Notion-Reminder pairs.

        Rules:
        - Reminders is primary: always push Reminders changes to Notion
        - Notion only updates Reminders if Notion is newer
        """
        for notion_id, reminder in reminders_by_notion_id.items():
            if reminder.completed:
                continue  # Handle completed separately

            notion_task = notion_by_id.get(notion_id)
            if not notion_task:
                continue  # Task no longer in our filtered set

            notion_modified = notion_task.last_updated
            reminder_modified = reminder.last_modified

            # Strip #Notion tag from reminder title for comparison and sync
            clean_reminder_title = re.sub(r'\s*#notion\b\s*', ' ', reminder.title, flags=re.IGNORECASE).strip()
            clean_reminder_title = ' '.join(clean_reminder_title.split())  # Normalize whitespace

            # Compare titles (using cleaned reminder title)
            title_changed = clean_reminder_title != notion_task.title

            # Compare due dates (normalize to date only for comparison)
            notion_due = notion_task.due_date.date() if notion_task.due_date else None
            reminder_due = reminder.due_date.date() if reminder.due_date else None
            due_changed = notion_due != reminder_due

            if not title_changed and not due_changed:
                continue  # No changes needed

            # Determine which direction to sync
            # Reminders is primary, so we always push its changes to Notion
            # But if Notion is newer and Reminders hasn't changed, update Reminders

            # For simplicity: compare timestamps
            # If Reminders is newer or equal -> push to Notion
            # If Notion is newer -> push to Reminders

            reminder_is_newer = True
            if reminder_modified and notion_modified:
                reminder_is_newer = reminder_modified >= notion_modified

            if reminder_is_newer:
                # Push Reminders -> Notion (use cleaned title without #Notion tag)
                if title_changed:
                    print(f"\n  Updating Notion title: '{clean_reminder_title}'")
                    print(f"    Was: '{notion_task.title}'")
                    if not self.dry_run:
                        self.notion.update_task_title(notion_task.page_id, clean_reminder_title)
                    self.stats["notion_tasks_updated"] += 1

                if due_changed:
                    print(f"\n  Updating Notion due date: {reminder_due}")
                    print(f"    Was: {notion_due}")
                    if not self.dry_run:
                        self.notion.update_task_due_date(
                            notion_task.page_id, reminder.due_date
                        )
                    self.stats["notion_tasks_updated"] += 1
            else:
                # Push Notion -> Reminders (Notion is newer)
                # Note: We don't add #Notion tag back - the reminder keeps its existing tag
                if title_changed or due_changed:
                    print(f"\n  Updating Reminder (Notion is newer): {notion_task.title}")

                    if not self.dry_run:
                        self.reminders.update_reminder(
                            reminder,
                            title=notion_task.title if title_changed else None,
                            due_date=notion_task.due_date if due_changed else None,
                            clear_due_date=due_changed and notion_task.due_date is None,
                        )
                    self.stats["reminders_updated"] += 1

    def _handle_completed_reminders(
        self,
        notion_by_id: dict[str, NotionTask],
        all_reminders: list[Reminder],
    ):
        """Mark Notion tasks as Done when their linked Reminder is completed."""
        for reminder in all_reminders:
            if not reminder.completed:
                continue

            notion_id = reminder.notion_page_id
            if not notion_id:
                continue

            notion_task = notion_by_id.get(notion_id)
            if not notion_task or notion_task.is_done:
                continue

            print(f"\n  Marking Notion task as Done: {notion_task.title}")

            if not self.dry_run:
                self.notion.mark_task_done(notion_task.page_id)
            self.stats["notion_tasks_completed"] += 1

    def _create_notion_from_reminders(self, unlinked_reminders: list[Reminder]):
        """Create Notion tasks from Reminders tagged with #Notion but having no URL."""
        for reminder in unlinked_reminders:
            # Strip the #Notion tag from the title before creating in Notion
            clean_title = re.sub(r'\s*#notion\b\s*', ' ', reminder.title, flags=re.IGNORECASE).strip()
            clean_title = ' '.join(clean_title.split())  # Normalize whitespace

            print(f"\n  Creating Notion task from reminder: {clean_title}")

            if not self.dry_run:
                page_id = self.notion.create_task(
                    NOTION_DATABASE_ID,
                    clean_title,
                    NOTION_USER_ID,
                    reminder.due_date,
                )
                if page_id:
                    # Update reminder with the Notion URL
                    notion_url = f"https://notion.so/{page_id}"
                    ek_reminder = reminder.ek_reminder
                    ns_url = Foundation.NSURL.URLWithString_(notion_url)
                    ek_reminder.setURL_(ns_url)

                    error = None
                    self.reminders.store.saveReminder_commit_error_(
                        ek_reminder, True, error
                    )

                    self.stats["notion_tasks_created"] += 1
            else:
                self.stats["notion_tasks_created"] += 1


def cmd_sync(args):
    """Run the sync process."""
    sync = NotionRemindersSync(dry_run=args.dry_run)
    sync.run()


def cmd_fix_urls(args):
    """Fix reminders that have #Notion tag but are missing URLs.

    This matches reminders to Notion tasks by title and adds the missing URLs.
    Run this once if you have reminders that were created before URL tracking was added.
    """
    print("=" * 60)
    print("Fix Missing URLs")
    print("=" * 60)

    if args.dry_run:
        print("** DRY RUN MODE - No changes will be made **\n")

    # Initialize clients
    notion = NotionClient(CONFIG["NOTION_API_KEY"])
    reminders_client = RemindersClient()

    # Get all Notion tasks
    print("Fetching Notion tasks...")
    notion_tasks = notion.query_my_tasks(NOTION_DATABASE_ID, NOTION_USER_ID)
    print(f"  Found {len(notion_tasks)} tasks assigned to you\n")

    # Get all reminders with #Notion tag but no URL
    print("Fetching reminders with #Notion tag but no URL...")
    all_reminders = reminders_client.get_all_reminders()
    missing_url = [r for r in all_reminders if r.has_notion_tag and not r.url and not r.completed]
    print(f"  Found {len(missing_url)} reminders missing URLs\n")

    if not missing_url:
        print("All reminders already have URLs. Nothing to fix!")
        return

    # Build a lookup by normalized title
    def normalize_title(title: str) -> str:
        """Normalize title for matching - remove #Notion tag and extra whitespace."""
        import re
        # Remove #Notion tag (case insensitive)
        title = re.sub(r'#notion\b', '', title, flags=re.IGNORECASE)
        # Remove extra whitespace
        title = ' '.join(title.split())
        return title.strip().lower()

    notion_by_title = {}
    for task in notion_tasks:
        normalized = normalize_title(task.title)
        notion_by_title[normalized] = task

    # Match and fix
    fixed_count = 0
    not_found = []

    print("Matching reminders to Notion tasks...")
    print("-" * 60)

    for reminder in missing_url:
        normalized = normalize_title(reminder.title)

        if normalized in notion_by_title:
            task = notion_by_title[normalized]
            print(f"\n  Fixing: {reminder.title[:50]}")
            print(f"    Adding URL: {task.url}")

            if not args.dry_run:
                # Set the URL on the reminder
                ns_url = Foundation.NSURL.URLWithString_(task.url)
                reminder.ek_reminder.setURL_(ns_url)

                error = None
                success = reminders_client.store.saveReminder_commit_error_(
                    reminder.ek_reminder, True, error
                )
                if success:
                    fixed_count += 1
                else:
                    print(f"    ERROR: Failed to save reminder")
            else:
                fixed_count += 1
        else:
            not_found.append(reminder.title)

    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    print(f"  Reminders fixed: {fixed_count}")
    print(f"  Not matched: {len(not_found)}")

    if not_found:
        print("\n  Reminders that couldn't be matched to Notion tasks:")
        for title in not_found:
            print(f"    - {title[:60]}")

    if args.dry_run:
        print("\n** DRY RUN - No changes were made. Run without --dry-run to apply fixes. **")


def cmd_whoami(args):
    """Show current Notion user info to help find your user ID."""
    print("Fetching Notion user info...")
    print("(Requires NOTION_API_KEY to be set)\n")

    api_key = CONFIG.get("NOTION_API_KEY")
    if not api_key:
        print("Error: NOTION_API_KEY not configured")
        sys.exit(1)

    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {api_key}",
        "Notion-Version": "2022-06-28",
    })

    # Get bot user info
    try:
        response = session.get("https://api.notion.com/v1/users/me")
        response.raise_for_status()
        bot = response.json()
        print("Bot/Integration Info:")
        print(f"  Name: {bot.get('name', 'Unknown')}")
        print(f"  ID:   {bot.get('id', 'Unknown')}")
        print(f"  Type: {bot.get('type', 'Unknown')}")
    except Exception as e:
        print(f"Error fetching bot info: {e}")

    # List all users in the workspace
    print("\nWorkspace Users:")
    print("-" * 60)
    try:
        response = session.get("https://api.notion.com/v1/users?page_size=100")
        response.raise_for_status()
        data = response.json()

        for user in data.get("results", []):
            user_type = user.get("type", "unknown")
            name = user.get("name", "Unknown")
            user_id = user.get("id", "Unknown")

            if user_type == "person":
                email = user.get("person", {}).get("email", "")
                print(f"  {name}")
                print(f"    ID:    {user_id}")
                if email:
                    print(f"    Email: {email}")
                print()
            elif user_type == "bot":
                print(f"  [Bot] {name}")
                print(f"    ID: {user_id}")
                print()

    except Exception as e:
        print(f"Error listing users: {e}")

    print("-" * 60)
    print("Copy your user ID to config.json or .env as NOTION_USER_ID")


def main():
    parser = argparse.ArgumentParser(
        description="Sync tasks between Notion and Apple Reminders"
    )

    # Top-level --dry-run for backwards compatibility
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Sync command
    sync_parser = subparsers.add_parser("sync", help="Run the sync process")
    sync_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )

    # Whoami command
    subparsers.add_parser(
        "whoami",
        help="Show Notion workspace users to find your user ID"
    )

    # Fix URLs command
    fix_urls_parser = subparsers.add_parser(
        "fix-urls",
        help="Fix reminders missing URLs by matching to Notion tasks"
    )
    fix_urls_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )

    args = parser.parse_args()

    if args.command == "whoami":
        cmd_whoami(args)
    elif args.command == "fix-urls":
        cmd_fix_urls(args)
    else:
        # Default to sync (works for both "sync" command and no command)
        cmd_sync(args)


if __name__ == "__main__":
    main()
