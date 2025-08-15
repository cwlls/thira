#!/usr/bin/env python
## thira - a syncing agent to bring JIRA tickets into Things3
#
# Copyright 2025 Chris Wells <hello@cwlls.com>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
#
import json
import os
import configparser
from typing import Dict, List, Set
from datetime import datetime
import urllib.parse
import subprocess

from jira import JIRA

STATE_FILE = os.path.expanduser("~/.local/state/thira.json")
CONFIG_FILE = os.path.expanduser("~/.config/thira.conf")


class StateManager:
    """Maintain state across sync runs"""

    def __init__(self, state_file: str):
        self.state_file = state_file
        self.synced_tickets = self._load_state()

    def _load_state(self) -> Set[str]:
        """Load sync state from file"""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r") as f:
                    data = json.load(f)
                    return set(data.get("synced_tickets", []))
            except (json.JSONDecodeError, IOError) as e:
                print(f"Warning: could not load sync state: {e}")
        else:
            try:
                open(self.state_file, "w").close()
            except Exception as e:
                print(f"Warning: could not create state file: {e}")

        return set()

    def _save_state(self):
        """Save sync state to file"""
        try:
            state_data = {
                "synced_tickets": list(self.synced_tickets),
                "last_sync": datetime.now().isoformat(),
            }
            with open(self.state_file, "w") as f:
                json.dump(state_data, f, indent=2)
        except IOError as e:
            print(f"Warning: could not save sync state: {e}")

    def is_ticket_synced(self, ticket_key: str) -> bool:
        """Check if a ticket has already been synced"""
        return ticket_key in self.synced_tickets

    def mark_ticket_synced(self, ticket_key: str):
        """Mark a ticket as synced"""
        self.synced_tickets.add(ticket_key)
        self._save_state()

    def remove_ticket(self, ticket_key: str):
        """Remove a ticket from synced state"""
        self.synced_tickets.discard(ticket_key)
        self._save_state()

    def get_sync_stats(self) -> Dict:
        """Get sync statistics"""
        state_info = {}

        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r") as f:
                    data = json.load(f)
                    state_info = {
                        "total_synced": len(data.get("synced_tickets", [])),
                        "last_sync": data.get("last_sync", "Never"),
                        "synced_tickets": data.get("synced_tickets", []),
                    }
            except (json.JSONDecodeError, IOError) as e:
                print(f"Warning: could not load state: {e}")
        return state_info

    def clear_all_state(self):
        """Clears all sync state (forces resync)"""
        self.synced_tickets.clear()
        if os.path.exists(self.state_file):
            os.remove(self.state_file)
        print("Sync state cleared. All tickets will be re-synced on next run")


class Things3Manager:
    """Manage interaction with Things3 via local API"""

    BASE_URL = "things://"

    @classmethod
    def add_todo(
        cls,
        title: str,
        notes: str = "",
        project: str = "GXZujchhWvXFy6jsS7bFWS",  # TICKET PROJECT IN THINGS
        tags: List[str] = [],
        due_date: str = "",
        when: str = "",
    ) -> bool:
        """
        Add a new todo item to Things3

        Args:
            title: The todo title
            notes: Issue notes
            project: Project name (will create if doesn't exist)
            tags: List of tags
            due_date: Due date in YYYY-MM-DD format
            when: When to schedule (today, tomorrow, evening, etc...)
        """
        action = "add"
        params = {"title": title}

        if notes:
            params["notes"] = notes
        if project:
            params["list-id"] = project
        if tags:
            params["tags"] = ",".join(tags)
        if due_date:
            params["due-date"] = due_date
        if when:
            params["when"] = when

        # build up a query with the given parameters
        query_string = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
        full_url = f"{cls.BASE_URL}/{action}?{query_string}"

        try:
            subprocess.run(["open", full_url], check=True)
            return True
        except subprocess.CalledProcessError as e:
            print(f"Error adding todo to Things3: {e}")
            return False

    @classmethod
    def add_project(cls, name: str, area: str = "", notes: str = "", tags: List[str] = []) -> bool:
        """
        Add a new project to Things3

        Args:
            name: A name for the project
            area: An area in which to place the project
            notes: Additional Notes
            tags: List of tags
        """
        action = "add-project"
        params = {"title": name}

        if area:
            params["area"] = area
        if notes:
            params["notes"] = notes
        if tags:
            params["tags"] = ",".join(tags)

        query_string = urllib.parse.urlencode(params, quote_via=urllib.parse.quote)
        full_url = f"{cls.BASE_URL}/{action}?{query_string}"

        try:
            subprocess.run(["open", full_url], check=True)
            return True
        except subprocess.CalledProcessError as e:
            print(f"Error adding project to Things3: {e}")
            return False


class JiraManager:
    """Main class for accessing JIRA via API"""

    def __init__(self, jira_url: str, username: str, api_token: str):
        """
        Initialize the Syncer

        Args:
            jira_url: JIRA instance URL (eg, 'https://jira-url.tld')
            username: JIRA username/email
            api_token: JIRA API token
        """
        self.jira_url = jira_url
        self.username = username
        self.api_token = api_token

        # Initialize a connection to the JIRA server
        try:
            self.jira = JIRA(server=jira_url, options={"Accept": "application/json"}, token_auth=self.api_token)
            print(f"Connected to JIRA at {jira_url}")
        except Exception as e:
            raise Exception(f"Failed to connect to JIRA: {e}")

    def get_assigned_tickets(self, max_results: int = 50) -> List:
        """Get tickets assigned to current user"""
        try:
            # JQL for tickets assigned to current user that are not completed
            jql = "assignee = currentUser() AND resolution = Unresolved ORDER BY created DESC"
            issues = self.jira.search_issues(jql, maxResults=max_results)
            return issues
        except Exception as e:
            print(f"Error fetching tickets: {e}")
            return []

    def get_tickets_by_jql(self, jql: str, max_results: int = 50) -> List:
        """Get tickets using custom JQL"""
        try:
            issues = self.jira.search_issues(jql, maxResults=max_results)
            return issues
        except Exception as e:
            print(f"Error fetching tickets: {e}")
            return []

    def format_ticket_name(self, issue) -> str:
        """Format ticket title"""
        return f"[{issue.key}] {issue.fields.summary}"

    def format_ticket_notes(self, issue) -> str:
        """Format ticket information into Things3 note"""
        notes_parts = []

        # Basic information
        notes_parts.append(f"**Ticket** {issue.key}")
        notes_parts.append(f"**URL** {issue.permalink()}")

        # if a reporter is present
        if hasattr(issue.fields, "reporter"):
            notes_parts.append(f"**Reporter** {issue.fields.reporter.displayName}")

        # if there is a description
        if hasattr(issue.fields, "description"):
            description = getattr(issue.fields, "description")
            if description and len(description) > 500:
                description = description[:500] + "..."
            notes_parts.append(f"\n**Description**\n{description}")

        return "\n".join(notes_parts)

    def get_ticket_tags(self, issue) -> List[str]:
        """Generate tags for the ticket"""
        tags = ["jira"]

        return tags


class Thira:
    """Class to weave all the managers together"""

    def __init__(self, jira_url: str, jira_username: str, jira_token: str):
        self.state = StateManager(STATE_FILE)
        self.things3 = Things3Manager()
        self.jira = JiraManager(jira_url, jira_username, jira_token)

    def sync_tickets(
        self,
        jql: str = "",
        project_name: str = "Jira Tickets",
        force_resync: bool = False,
        update_existing: bool = False,
    ):
        """
        Sync JIRA tickets to Things3

        Args:
            jql: Custom JQL query (if None, uses "assigned tickets" query)
            project_name: Things3 project name to organize tickets
            force_resync: If True, sync all tickets regardless of previous sync state
            update_existing: If True, update tickets that have already been synced
        """
        print("Starting JIRA to Things3 sync...")

        # Show sync stats
        stats = self.state.get_sync_stats()
        if stats:
            print(
                f"Previous sync stats: {stats.get('total_synced', 0)} tickets synced, last sync: {stats.get('last_sync', 'Never')}"
            )

        # Get tickets
        if jql:
            issues = self.jira.get_tickets_by_jql(jql)
        else:
            issues = self.jira.get_assigned_tickets()

        if not issues:
            print("No tickets found to sync")
            return

        print(f"Found {len(issues)} ticket(s) from JIRA")

        # filter out already synced tickets, unless force_resync or update_existing is set
        tickets_to_sync = []
        skipped_count = 0

        for issue in issues:
            if force_resync or update_existing or not self.state.is_ticket_synced(issue.key):
                tickets_to_sync.append(issue)
            else:
                skipped_count += 1

        if skipped_count > 0:
            print(f"Skipping {skipped_count} already synced tickets. Use --force-resync to force re-sync")

        if not tickets_to_sync:
            print("No new tickets to sync.")
            return

        print(f"Syncing {len(tickets_to_sync)} tickets...")

        # create project, if specified, otherwise use a default
        # if project_name:
        #     print(f"Creating/updating project: {project_name}")
        #     self.things3.add_project(name=project_name, notes="Synced JIRA tickets", tags=["jira", "sync"])

        # sync each ticket
        synced_count = 0
        for issue in tickets_to_sync:
            try:
                action = "Updated" if (update_existing and self.state.is_ticket_synced(issue.key)) else "Synced"

                title = self.jira.format_ticket_name(issue)
                notes = self.jira.format_ticket_notes(issue)
                tags = self.jira.get_ticket_tags(issue)

                success = self.things3.add_todo(title=title, notes=notes, tags=tags)

                if success:
                    print(f"✅ {action}: {issue.key} - {issue.fields.summary}")
                    self.state.mark_ticket_synced(issue.key)
                    synced_count += 1
                else:
                    print(f"❗️ Failed to sync: {issue.key}")

            except Exception as e:
                print(f"❗️ Error syncing {issue.key}: {e}")

        print(f"\nSync completed! {synced_count}/{len(tickets_to_sync)} tickets synced successfully.")

        final_stats = self.state.get_sync_stats()
        print(f"Total tickets ever synced {final_stats.get('total_synced', 0)}")

    def get_sync_status(self):
        """Display current sync status and statistics"""
        stats = self.state.get_sync_stats()
        print("\n=== Sync Status ===")
        print(f"Total tickets synced: {stats.get('total_synced', 0)}")
        print(f"Last sync: {stats.get('last_sync', 'Never')}")

        # could print out stats['synced_tickets'] here

    def reset_sync_state(self):
        """Reset sync state to force a re-sync of all tickets"""
        self.state.clear_all_state()
        print("Cleared sync state of all tickets. They will all be re-synced at next run.")

    def remove_ticket_from_sync(self, ticket_key: str):
        """Remove ticket from sync state"""
        self.state.remove_ticket(ticket_key)
        print(f"Removed {ticket_key} from sync state. It will be re-synced at next run.")


def main():
    """Main function"""
    cfg = configparser.ConfigParser()
    with open(CONFIG_FILE, "r") as cfg_file:
        cfg.read_file(cfg_file)

    try:
        # initialize syncer
        thira = Thira(cfg["jira"]["url"], cfg["jira"]["username"], cfg["jira"]["api_token"])

        # show current sync status
        thira.get_sync_status()

        thira.sync_tickets()
    except Exception as e:
        print(f"Sync failed: {e}")


if __name__ == "__main__":
    main()
