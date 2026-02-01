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
- Python 3.9+
- Notion integration with access to your database

## Quick Start

### 1. Clone and set up

```bash
git clone <repo-url> notion_sync
cd notion_sync
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Run the setup helper

```bash
python setup_config.py
```

This will ask for:
1. Your Notion API key
2. A URL to any task assigned to you in the database

It automatically extracts the database ID and your user ID, then creates `config.json`.

### 3. Grant Reminders access

On first run, macOS will prompt for Reminders access. Grant **Full Access** in:

**System Settings → Privacy & Security → Reminders**

### 4. Run the sync

```bash
# Test first (no changes made)
python notion_reminders_sync.py --dry-run

# Run for real
python notion_reminders_sync.py
```

## Manual Configuration

If you prefer to configure manually, copy `config.example.json` to `config.json`:

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

1. Create a reminder in your Work list
2. Add `#Notion` to the title or notes
3. Run the sync

The script will create a Notion task and link the reminder to it via the URL field.

## Automation

To run automatically, set up a cron job or launchd:

```bash
# Example: sync every 15 minutes
*/15 * * * * cd /path/to/notion_sync && ./venv/bin/python notion_reminders_sync.py >> sync.log 2>&1
```

## Files

| File | Purpose |
|------|---------|
| `notion_reminders_sync.py` | Main sync script |
| `setup_config.py` | Interactive setup helper |
| `config.json` | Your configuration (gitignored) |
| `config.example.json` | Template for manual setup |
| `.sync_state.json` | Tracks synced pairs for deletion detection (gitignored) |
| `requirements.txt` | Python dependencies |
