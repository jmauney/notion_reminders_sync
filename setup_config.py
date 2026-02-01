#!/usr/bin/env python3
"""
Interactive setup helper for Notion-Reminders sync.

This script helps new users create their config.json by:
1. Asking for their Notion API key
2. Asking for a URL to a task page assigned to them
3. Automatically extracting the database ID and user ID from that page
4. Writing the config.json file
"""

import json
import re
import sys
from pathlib import Path

import requests


def extract_page_id(url: str) -> str | None:
    """Extract the Notion page ID from a URL."""
    # Notion URLs can be:
    # https://www.notion.so/workspace/Page-Title-abc123def456...
    # https://www.notion.so/abc123def456...
    # https://notion.so/abc123def456...

    # Look for a 32-character hex string (with or without dashes)
    # The page ID is usually at the end of the URL path
    match = re.search(r'([a-f0-9]{32})', url.replace('-', ''))
    if match:
        return match.group(1)

    # Try with dashes (8-4-4-4-12 format)
    match = re.search(r'([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})', url)
    if match:
        return match.group(1).replace('-', '')

    return None


def get_page_info(api_key: str, page_id: str) -> dict | None:
    """Fetch page information from Notion API."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Notion-Version": "2022-06-28",
    }

    url = f"https://api.notion.com/v1/pages/{page_id}"

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as e:
        if response.status_code == 401:
            print("\nError: Invalid API key or the integration doesn't have access to this page.")
            print("Make sure the integration is connected to the database in Notion.")
        elif response.status_code == 404:
            print("\nError: Page not found. Check the URL and make sure the integration has access.")
        else:
            print(f"\nError fetching page: {e}")
        return None
    except Exception as e:
        print(f"\nError: {e}")
        return None


def get_user_info(api_key: str, user_id: str) -> dict | None:
    """Fetch user information from Notion API."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Notion-Version": "2022-06-28",
    }

    url = f"https://api.notion.com/v1/users/{user_id}"

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json()
    except Exception:
        return None


def main():
    print("=" * 60)
    print("Notion-Reminders Sync Setup")
    print("=" * 60)
    print()
    print("This helper will create your config.json file.")
    print("You'll need:")
    print("  1. Your Notion integration API key")
    print("  2. A URL to any task in the database that's assigned to you")
    print()

    script_dir = Path(__file__).parent.resolve()
    config_path = script_dir / "config.json"

    # Check if config already exists
    if config_path.exists():
        print(f"Warning: {config_path} already exists.")
        response = input("Overwrite it? (y/N): ").strip().lower()
        if response != 'y':
            print("Setup cancelled.")
            sys.exit(0)
        print()

    # Step 1: Get API key
    print("-" * 40)
    print("Step 1: Notion API Key")
    print("-" * 40)
    print()
    print("Enter your Notion integration API key.")
    print("(It starts with 'ntn_' or 'secret_')")
    print()

    api_key = input("API Key: ").strip()
    if not api_key:
        print("Error: API key is required.")
        sys.exit(1)

    # Validate API key format
    if not (api_key.startswith('ntn_') or api_key.startswith('secret_')):
        print("\nWarning: API key doesn't start with 'ntn_' or 'secret_'.")
        print("This might not be a valid Notion API key.")
        response = input("Continue anyway? (y/N): ").strip().lower()
        if response != 'y':
            sys.exit(1)

    print()

    # Step 2: Get page URL
    print("-" * 40)
    print("Step 2: Task Page URL")
    print("-" * 40)
    print()
    print("Open the Notion database and find any task that is assigned to YOU.")
    print("Copy the URL of that task page and paste it here.")
    print()
    print("Example: https://www.notion.so/workspace/My-Task-abc123...")
    print()

    page_url = input("Task URL: ").strip()
    if not page_url:
        print("Error: Page URL is required.")
        sys.exit(1)

    # Extract page ID
    page_id = extract_page_id(page_url)
    if not page_id:
        print("\nError: Could not extract page ID from URL.")
        print("Make sure you copied the full URL from Notion.")
        sys.exit(1)

    print(f"\nExtracted page ID: {page_id}")
    print("Fetching page information...")

    # Fetch page info
    page_info = get_page_info(api_key, page_id)
    if not page_info:
        sys.exit(1)

    # Extract database ID from parent
    parent = page_info.get("parent", {})
    if parent.get("type") != "database_id":
        print("\nError: This page is not in a database.")
        print("Make sure you're linking to a task page, not the database itself.")
        sys.exit(1)

    database_id = parent.get("database_id", "").replace("-", "")
    print(f"Found database ID: {database_id}")

    # Extract user ID from Assignee
    properties = page_info.get("properties", {})
    assignee_prop = properties.get("Assignee", {})
    assignees = assignee_prop.get("people", [])

    if not assignees:
        print("\nError: This task has no one assigned to it.")
        print("Please use a task that is assigned to you.")
        sys.exit(1)

    if len(assignees) > 1:
        print("\nThis task has multiple assignees:")
        for i, person in enumerate(assignees):
            user_info = get_user_info(api_key, person.get("id", ""))
            name = user_info.get("name", "Unknown") if user_info else person.get("id", "Unknown")
            email = ""
            if user_info and user_info.get("person"):
                email = f" ({user_info['person'].get('email', '')})"
            print(f"  {i + 1}. {name}{email}")

        print()
        choice = input(f"Which one is you? (1-{len(assignees)}): ").strip()
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(assignees):
                user_id = assignees[idx].get("id", "").replace("-", "")
            else:
                print("Invalid choice.")
                sys.exit(1)
        except ValueError:
            print("Invalid choice.")
            sys.exit(1)
    else:
        user_id = assignees[0].get("id", "").replace("-", "")

    # Get user name for confirmation
    user_info = get_user_info(api_key, user_id)
    user_name = user_info.get("name", "Unknown") if user_info else "Unknown"
    user_email = ""
    if user_info and user_info.get("person"):
        user_email = user_info["person"].get("email", "")

    print(f"Found user: {user_name}" + (f" ({user_email})" if user_email else ""))

    # Step 3: Optional settings
    print()
    print("-" * 40)
    print("Step 3: Optional Settings")
    print("-" * 40)
    print()

    reminders_list = input("Reminders list name (default: Work): ").strip()
    if not reminders_list:
        reminders_list = "Work"

    notion_tag = input("Notion tag for new reminders (default: #Notion): ").strip()
    if not notion_tag:
        notion_tag = "#Notion"

    # Build config
    config = {
        "NOTION_API_KEY": api_key,
        "NOTION_DATABASE_ID": database_id,
        "NOTION_USER_ID": user_id,
        "REMINDERS_LIST_NAME": reminders_list,
        "NOTION_TAG": notion_tag,
    }

    # Confirm
    print()
    print("-" * 40)
    print("Configuration Summary")
    print("-" * 40)
    print()
    print(f"  Database ID:    {database_id}")
    print(f"  User:           {user_name}" + (f" ({user_email})" if user_email else ""))
    print(f"  User ID:        {user_id}")
    print(f"  Reminders List: {reminders_list}")
    print(f"  Notion Tag:     {notion_tag}")
    print()

    response = input("Save this configuration? (Y/n): ").strip().lower()
    if response == 'n':
        print("Setup cancelled.")
        sys.exit(0)

    # Write config
    try:
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)
        print(f"\nConfiguration saved to {config_path}")
        print()
        print("You're all set! Run the sync with:")
        print("  python notion_reminders_sync.py --dry-run")
        print()
    except IOError as e:
        print(f"\nError writing config file: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
