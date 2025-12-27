import sys
import threading
import time

from src.tailscale_utilities import (
    ExitNodeActive,
    ExitNodeListEntry,
    ExitNodeSuggested,
    get_tailscale_list_nodes,
    get_tailscale_current_exit_node,
    get_tailscale_suggested_exit_node,
    set_tailscale_exit_node,
)

from rich.console import Console
from rich.table import Table
from rich.live import Live
from rich.panel import Panel
from rich.text import Text
from readchar import readkey, key


def fuzzy_match(query: str, text: str) -> bool:
    """Simple fuzzy matching - checks if all query chars appear in order in text."""
    query = query.lower()
    text = text.lower()
    query_idx = 0
    for char in text:
        if query_idx < len(query) and char == query[query_idx]:
            query_idx += 1
    return query_idx == len(query)


class CountryFilterMenu:
    def __init__(self, countries: list[str], console: Console) -> None:
        self.all_countries = sorted(set(countries))
        self.filtered_countries = self.all_countries.copy()
        self.console = console
        self.search_query = ""
        self.selection = 0
        self.height = console.height

    def filter_countries(self) -> None:
        if not self.search_query:
            self.filtered_countries = self.all_countries.copy()
        else:
            self.filtered_countries = [
                c for c in self.all_countries if fuzzy_match(self.search_query, c)
            ]
        self.selection = min(self.selection, max(0, len(self.filtered_countries) - 1))

    def generate_menu(self) -> Panel:
        content = Text()
        content.append(f"Filter: {self.search_query}_\n\n", style="bold cyan")

        if not self.filtered_countries:
            content.append("No matching countries", style="dim")
        else:
            min_bounds = max(0, self.selection - 5)
            visible_range = range(min_bounds, min_bounds + self.height // 2)

            if visible_range.start > 0:
                content.append("...\n", style="red")

            for idx, country in enumerate(self.filtered_countries):
                if idx not in visible_range:
                    continue
                if idx == self.selection:
                    content.append(f"> {country}\n", style="bold on blue")
                else:
                    content.append(f"  {country}\n")

            if visible_range.stop < len(self.filtered_countries):
                content.append("...", style="red")

        return Panel(
            content,
            title="Select Country (ESC to cancel, ENTER to select)",
            border_style="cyan",
        )

    def run(self) -> str | None:
        """Run the filter menu and return selected country or None if cancelled."""
        with Live(
            self.generate_menu(),
            console=self.console,
            auto_refresh=False,
        ) as live:
            live.refresh()
            while True:
                try:
                    k = readkey()
                except KeyboardInterrupt:
                    return None

                if k == key.ESC:
                    return None
                elif k == key.ENTER:
                    if self.filtered_countries:
                        return self.filtered_countries[self.selection]
                    return None
                elif k in (key.DOWN, key.CTRL_N, key.CTRL_J):
                    if self.filtered_countries:
                        self.selection = (self.selection + 1) % len(
                            self.filtered_countries
                        )
                elif k in (key.UP, key.CTRL_P, key.CTRL_K):
                    if self.filtered_countries:
                        self.selection = (self.selection - 1) % len(
                            self.filtered_countries
                        )
                elif k == key.BACKSPACE:
                    self.search_query = self.search_query[:-1]
                    self.filter_countries()
                elif len(k) == 1 and k.isprintable():
                    self.search_query += k
                    self.filter_countries()

                live.update(self.generate_menu(), refresh=True)


class TailvadTUI:
    def __init__(self) -> None:
        self.current_selection: int = 0
        self.suggested_index: int | None = None
        self.console: Console = Console()
        self.height: int = self.console.height
        self.active_exit_node: ExitNodeActive | None = None
        self.keybinds: dict[str, str] = {
            "f": "filter",
            "x": "exit",
            "s": "use suggested",
            "d": "disconnect",
            "c": "clear filter",
        }
        self.exit_node_entries: list[ExitNodeListEntry] = get_tailscale_list_nodes()
        self._all_countries: list[str] = [e.country for e in self.exit_node_entries]
        self._country_filter: str | None = None
        self._polling_thread: threading.Thread | None = None
        self._stop_polling: threading.Event = threading.Event()
        self._live: Live | None = None
        self._pending_hostname: str | None = None

    def start_tui(self) -> None:
        self.current_selection = 0
        with Live(
            self.generate_populated_table(),
            console=self.console,
            auto_refresh=False,
        ) as live:
            self._live = live
            while True:
                self.handle_key_press(live)

    def handle_key_press(self, live: Live) -> None:
        try:
            k = readkey()
        except KeyboardInterrupt:
            self._stop_polling.set()
            sys.exit()
        if k in (key.DOWN, "j"):
            self.current_selection = (self.current_selection + 1) % len(
                self.exit_node_entries
            )
        elif k in (key.UP, "k"):
            self.current_selection = (self.current_selection - 1) % len(
                self.exit_node_entries
            )
        elif k == "s":
            suggested: ExitNodeSuggested | None = get_tailscale_suggested_exit_node()
            if suggested is not None:
                set_tailscale_exit_node(suggested.hostname)
                self._start_status_polling(suggested.hostname)
            # if the suggested exit node is in the table, jump to it
            self.suggested_index = self.find_index_for_suggested_exit_node()
            live.update(
                self.generate_populated_table(jump_to_suggested=True),
                refresh=True,
            )
            return
        elif k == key.ENTER:
            hostname = self.exit_node_entries[self.current_selection].hostname
            set_tailscale_exit_node(hostname)
            self._start_status_polling(hostname)
        elif k == "d":
            set_tailscale_exit_node("")
            time.sleep(1)
            self.refresh_entries()
        elif k == "f":
            self._show_filter_menu(live)
            return
        elif k == "c":
            self._country_filter = None
            self.current_selection = 0
            self.refresh_entries()
        elif k in (key.CTRL_C, "x"):
            self._stop_polling.set()
            sys.exit()
        live.update(
            self.generate_populated_table(),
            refresh=True,
        )

    def generate_populated_table(self, jump_to_suggested: bool = False) -> Table:
        active_node = get_tailscale_current_exit_node()

        if (
            self.active_exit_node
            and active_node
            and self.active_exit_node.hostname == active_node.hostname
        ):
            self.refresh_entries()
        self.active_exit_node = active_node

        table_title = "tailvad > Mullvad Exit Nodes\n"
        for k, v in self.keybinds.items():
            table_title = table_title + f"{k} - {v}  "

        if self.active_exit_node:
            table_title = table_title + f"\n[green]{self.active_exit_node}[/green]"
        else:
            table_title = table_title + "\n[red]no active exit node[/red]"

        if self._country_filter:
            table_title = table_title + f"\n[cyan]filter: {self._country_filter}[/cyan]"

        table = Table(title=table_title)

        columns = ("IP", "HOSTNAME", "COUNTRY", "CITY", "STATUS")
        for col in columns:
            table.add_column(col)

        if jump_to_suggested and self.suggested_index is not None:
            self.current_selection = self.suggested_index

        min_bounds = max(0, self.current_selection - 5)
        visible_range = range(
            min_bounds,
            min_bounds + self.height // 2,
        )

        if visible_range.start > 0:
            table.add_row(*(["..."]) * 5, style="red")

        for idx, entry in enumerate(self.exit_node_entries):
            if idx not in visible_range:
                continue
            style = "on green" if self.suggested_index == idx else ""
            style = "on yellow" if entry.status == "selected" else style
            style = "on blue" if self.current_selection == idx else style
            table.add_row(
                entry.ip,
                entry.hostname,
                entry.country,
                entry.city,
                entry.status,
                style=style,
            )
        if visible_range.stop < len(self.exit_node_entries):
            table.add_row(*(["..."]) * 5, style="red")

        return table

    def find_index_for_suggested_exit_node(self) -> int | None:
        suggested: ExitNodeSuggested | None = get_tailscale_suggested_exit_node()
        if suggested is None:
            return None

        hostname = suggested.hostname
        for idx, entry in enumerate(self.exit_node_entries):
            if entry.hostname == hostname:
                return idx

        return None

    def refresh_entries(self) -> None:
        self.exit_node_entries = get_tailscale_list_nodes(self._country_filter)

    def _show_filter_menu(self, live: Live) -> None:
        """Show the country filter menu and apply the selected filter."""
        menu = CountryFilterMenu(self._all_countries, self.console)
        selected_country = menu.run()

        if selected_country:
            self._country_filter = selected_country
            self.current_selection = 0
            self.refresh_entries()

        live.update(self.generate_populated_table(), refresh=True)

    def _start_status_polling(self, hostname: str) -> None:
        """Start a background thread to poll status until node is 'selected'."""
        # Stop any existing polling thread
        self._stop_polling.set()
        if self._polling_thread and self._polling_thread.is_alive():
            self._polling_thread.join(timeout=2)

        self._stop_polling.clear()
        self._pending_hostname = hostname
        self._polling_thread = threading.Thread(
            target=self._poll_status,
            args=(hostname,),
            daemon=True,
        )
        self._polling_thread.start()

    def _poll_status(self, hostname: str) -> None:
        """Poll tailscale status every 1 second until status is 'selected' and active."""
        while not self._stop_polling.is_set():
            self.refresh_entries()

            # Check if the selected node's status is "selected"
            status_selected = any(
                entry.hostname == hostname and entry.status == "selected"
                for entry in self.exit_node_entries
            )

            # Check if the node is now the active exit node
            active_node = get_tailscale_current_exit_node()
            is_active = active_node is not None and active_node.hostname == hostname

            if status_selected and is_active:
                self._pending_hostname = None
                # Final UI update
                if self._live:
                    self._live.update(
                        self.generate_populated_table(),
                        refresh=True,
                    )
                return

            # Update UI
            if self._live:
                self._live.update(
                    self.generate_populated_table(),
                    refresh=True,
                )

            time.sleep(1)


def main() -> None:
    tui = TailvadTUI()
    tui.start_tui()


if __name__ == "__main__":
    main()
