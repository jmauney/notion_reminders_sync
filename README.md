# Notion ↔ Apple Reminders Sync

Bidirectional sync between a Notion database and Apple Reminders on macOS.

## Features

- **Notion → Reminders**: Creates reminders for tasks assigned to you
- **Reminders → Notion**: Updates flow back (Reminders is primary for edits)
- **Completion sync**: Complete a reminder → marks Notion task as Done
- **New task creation**: Add `#Notion` to a reminder title/notes → creates Notion task
- **Deletion handling**: Deleting on either side syncs appropriately
- **Customer tracking**: Customer names from Notion appear in reminder notes

## Requirements

- macOS (uses native Reminders via EventKit)
- Python 3.9 or newer
- Notion integration with access to your database

### Notion Database Requirements

Your Notion database needs these types of properties. The **default names** are shown below, but you can customize them in your config (see [Customizing Property Names](#customizing-property-names)).

| Default Name | Type | Purpose | Required? |
|--------------|------|---------|-----------|
| `Request` | Title | The task name | Yes |
| `Assignee` | Person | Who the task is assigned to | Yes |
| `Status` | Status | Must include "Done" and "Canceled" options | Yes |
| `Due date` | Date | When the task is due | Yes |
| `Customer` | Relation | Links to a customer database | No |
| `Type` | Select | Used to exclude certain task types | No |

> **Note**: Property names are case-sensitive. "Due date" is not the same as "Due Date".

---

## Installation Guide

This guide assumes you're new to using Terminal. Follow each step carefully.

### Step 1: Open Terminal

1. Press `Cmd + Space` to open Spotlight
2. Type `Terminal` and press Enter
3. A window with a command prompt will appear

### Step 2: Check Your Python Version

macOS comes with Python, but you need version 3.9 or newer.

Type this command and press Enter:

```bash
python3 --version
```

You should see something like `Python 3.11.5` or `Python 3.13.0`.

**If your version is 3.9 or higher**, skip to Step 3.

**If your version is lower than 3.9**, or you get an error, you need to install/update Python:

#### Installing Python on macOS

The easiest way is using Homebrew (a package manager for macOS):

1. **Install Homebrew** (if you don't have it):

   Copy and paste this entire command into Terminal, then press Enter:
   ```bash
   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
   ```

   Follow the prompts. You may need to enter your Mac password (you won't see it as you type - that's normal).

2. **Install Python**:
   ```bash
   brew install python@3.13
   ```

3. **Verify it worked**:
   ```bash
   python3 --version
   ```

   You should now see Python 3.13 or similar.

### Step 3: Download This Project

1. **Navigate to your Documents folder** (or another folder of your choice):
   ```bash
   cd ~/Documents
   ```

2. **Download the project**:
   ```bash
   git clone https://github.com/jmauney/notion_reminders_sync.git
   cd notion_reminders_sync
   ```

> **Note**: You can install this anywhere you like. Just remember the location - you'll need it later for setting up automatic sync.

### Step 4: Set Up Python Environment

A "virtual environment" keeps this project's packages separate from your system. Run these commands one at a time:

```bash
python3 -m venv venv
```

```bash
source venv/bin/activate
```

Your prompt should now show `(venv)` at the beginning.

```bash
pip install -r requirements.txt
```

This installs the required packages. You'll see some output - wait for it to finish.

### Step 5: Get a Notion Integration

> **Note**: Creating a Notion integration requires **workspace admin** permissions. If you're not an admin, ask your workspace owner to create the integration and share the API key with you.

#### If you're a workspace admin:

1. Go to [https://www.notion.so/my-integrations](https://www.notion.so/my-integrations)
2. Click **"+ New integration"**
3. Give it a name like "Reminders Sync"
4. Select your workspace
5. Click **Submit**
6. Copy the **"Internal Integration Secret"** (starts with `ntn_`)

Now connect it to your database:

1. Open your task database in Notion
2. Click the **`···`** menu in the top right
3. Scroll to **Connections** and click **"+ Add connections"**
4. Find and select your integration

#### If you're not a workspace admin:

Ask your workspace admin to:
1. Create an integration (steps above)
2. Connect it to the task database
3. Share the API key with you securely (not via email or chat - use a password manager or in person)

### Step 6: Configure the Sync

Run the setup helper:

```bash
python setup_config.py
```

It will ask for:
1. Your Notion API key (the secret you copied)
2. A URL to any task assigned to you in the database

The helper automatically finds your database ID and user ID, then creates the config file.

### Step 7: Test the Sync

First, do a dry run to see what would happen without making changes:

```bash
python notion_reminders_sync.py --dry-run
```

**On first run**, macOS will ask for Reminders access. A dialog will appear - click **OK** or **Allow**.

If you don't see the prompt, go to:
**System Settings → Privacy & Security → Reminders** and enable access for Terminal (or your terminal app).

### Step 8: Run for Real

If the dry run looks good:

```bash
python notion_reminders_sync.py
```

You should see reminders appear in your Reminders app under the "Work" list.

---

## Setting Up Automatic Sync (Cron)

Right now you have to run the script manually. To make it run automatically every 5 minutes, you'll set up a "cron job".

### Step 1: Find Your Project Path

Run this command and copy the output:

```bash
echo "$(pwd)"
```

It will show something like `/Users/yourname/Documents/notion_reminders_sync`. You'll need this path.

### Step 2: Open the Cron Editor

```bash
crontab -e
```

This opens a text editor in Terminal.

- If you see a question about which editor to use, type `nano` and press Enter (it's the easiest).

### Step 3: Add the Sync Schedule

You'll see an empty file (or existing cron jobs). Add this line at the bottom, **replacing `/Users/yourname/Documents/notion_reminders_sync` with your actual path**:

```
*/5 * * * * cd /Users/yourname/Documents/notion_reminders_sync && ./venv/bin/python notion_reminders_sync.py >> sync.log 2>&1
```

**What this means:**
- `*/5 * * * *` = Run every 5 minutes
- `cd /path/...` = Go to the project folder
- `./venv/bin/python notion_reminders_sync.py` = Run the sync script
- `>> sync.log 2>&1` = Save output to a log file (helpful for troubleshooting)

### Step 4: Save and Exit

If you're in **nano** (the default editor):
1. Press `Ctrl + O` (that's the letter O, not zero) to save
2. Press `Enter` to confirm the filename
3. Press `Ctrl + X` to exit

You should see: `crontab: installing new crontab`

### Step 5: Verify It's Working

Wait 5 minutes, then check the log file:

```bash
cat ~/Documents/notion_reminders_sync/sync.log
```

You should see sync output with timestamps.

### Changing the Sync Frequency

To change how often it runs, edit the first part of the cron line:

| Schedule | Cron Pattern |
|----------|--------------|
| Every 5 minutes | `*/5 * * * *` |
| Every 10 minutes | `*/10 * * * *` |
| Every 15 minutes | `*/15 * * * *` |
| Every hour | `0 * * * *` |

To edit: run `crontab -e` again, make your change, and save.

### Stopping Automatic Sync

To stop the automatic sync:

```bash
crontab -e
```

Delete the line you added (or put a `#` at the beginning to comment it out), then save.

---

## Sync Behavior

| Action | Result |
|--------|--------|
| New Notion task assigned to you | Creates reminder with URL link |
| Complete reminder | Marks Notion task as Done |
| Edit reminder title/due date | Updates Notion (Reminders is primary) |
| Edit Notion title/due date | Updates reminder (if Notion is newer) |
| Delete reminder | Cancels Notion task |
| Delete Notion task | Deletes reminder |
| Mark Notion task Done | Completes reminder |
| Mark Notion task Canceled | Deletes reminder |
| New reminder with `#Notion` in title/notes | Creates Notion task |

## Creating Tasks from Reminders

To create a new Notion task from a reminder:

1. Create a reminder in your "Work" list
2. Add `#Notion` to the title or notes
3. Wait for the next sync (or run manually)

The script will create a Notion task and link the reminder to it via the URL field.

---

## Manual Configuration

If you prefer to configure manually instead of using the setup helper, copy `config.example.json` to `config.json`:

```json
{
  "NOTION_API_KEY": "ntn_your_api_key_here",
  "NOTION_DATABASE_ID": "your_database_id_here",
  "NOTION_USER_ID": "your_user_id_here",
  "REMINDERS_LIST_NAME": "Work",
  "NOTION_TAG": "#Notion"
}
```

To find your user ID:
```bash
python notion_reminders_sync.py whoami
```

---

## Customizing Property Names

If your Notion database uses different property names, you can customize them in your `config.json`. Add any of these optional settings:

```json
{
  "NOTION_API_KEY": "ntn_your_api_key_here",
  "NOTION_DATABASE_ID": "your_database_id_here",
  "NOTION_USER_ID": "your_user_id_here",
  "REMINDERS_LIST_NAME": "Work",
  "NOTION_TAG": "#Notion",

  "PROP_TITLE": "Request",
  "PROP_ASSIGNEE": "Assignee",
  "PROP_STATUS": "Status",
  "PROP_DUE_DATE": "Due date",
  "PROP_CUSTOMER": "Customer",
  "PROP_TYPE": "Type",

  "STATUS_DONE": "Done",
  "STATUS_CANCELED": "Canceled",
  "STATUS_NEW": "New",

  "TYPE_EXCLUDE": "Onboarding"
}
```

### Property Name Options

| Setting | Default | Description |
|---------|---------|-------------|
| `PROP_TITLE` | `Request` | The title property of your tasks |
| `PROP_ASSIGNEE` | `Assignee` | The person property for task assignment |
| `PROP_STATUS` | `Status` | The status property |
| `PROP_DUE_DATE` | `Due date` | The date property for due dates |
| `PROP_CUSTOMER` | `Customer` | (Optional) Relation to customer database |
| `PROP_TYPE` | `Type` | (Optional) Select property for task types |

### Status Value Options

| Setting | Default | Description |
|---------|---------|-------------|
| `STATUS_DONE` | `Done` | Status value that marks a task complete |
| `STATUS_CANCELED` | `Canceled` | Status value that marks a task canceled |
| `STATUS_NEW` | `New` | Status value for newly created tasks |

### Type Exclusion

| Setting | Default | Description |
|---------|---------|-------------|
| `TYPE_EXCLUDE` | `Onboarding` | Tasks with this Type value are ignored. Set to empty string `""` to disable filtering |

**Example**: If your database uses "Task Name" instead of "Request" and "Completed" instead of "Done":

```json
{
  "NOTION_API_KEY": "...",
  "NOTION_DATABASE_ID": "...",
  "NOTION_USER_ID": "...",
  "PROP_TITLE": "Task Name",
  "STATUS_DONE": "Completed"
}
```

You only need to include settings that differ from the defaults.

---

## Troubleshooting

### "Permission denied" when running the script
Make sure you activated the virtual environment:
```bash
cd ~/Documents/notion_reminders_sync
source venv/bin/activate
```

### Reminders aren't appearing
1. Check that you granted Reminders access in System Settings
2. Look at the sync.log file for errors
3. Make sure the task is assigned to you in Notion

### "Python not found" errors
Use `python3` instead of `python`:
```bash
python3 notion_reminders_sync.py
```

### Cron job not running
1. Make sure you used the full path to the project
2. Check that the path doesn't have any typos
3. Look at sync.log for error messages

---

## Files

| File | Purpose |
|------|---------|
| `notion_reminders_sync.py` | Main sync script |
| `setup_config.py` | Interactive setup helper |
| `config.json` | Your configuration (gitignored) |
| `config.example.json` | Template for manual setup |
| `.sync_state.json` | Tracks synced pairs for deletion detection (gitignored) |
| `requirements.txt` | Python dependencies |
| `sync.log` | Log file created by cron (gitignored) |
