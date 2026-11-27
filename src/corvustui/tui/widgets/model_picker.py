"""OpenCode-inspired Select Model modal (Ctrl+O / /model) across all connected providers."""

from __future__ import annotations

from dataclasses import dataclass

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Input, Label, OptionList, Static
from textual.widgets.option_list import Option

from corvustui.providers import ProviderType
from corvustui.tui.controllers.providers import ModelEntry, TUIPrefs


@dataclass(frozen=True, slots=True)
class ModelPickerResult:
    action: str  # "select" or "connect"
    provider_type: ProviderType | None = None
    model: str | None = None


class ModelPickerScreen(ModalScreen[ModelPickerResult | None]):
    """Interactive multi-provider model selector with search, favorites, and /connect shortcut."""

    CSS = """
    ModelPickerScreen {
        align: center middle;
        background: rgba(10, 10, 14, 0.65);
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+a", "connect_provider", "Connect provider"),
        Binding("ctrl+f", "toggle_favorite", "Favorite"),
    ]

    def __init__(
        self,
        entries: list[ModelEntry],
        current_provider: ProviderType,
        current_model: str,
        *,
        prefs: TUIPrefs | None = None,
        provider_errors: dict[ProviderType, str] | None = None,
    ) -> None:
        super().__init__()
        self.entries = entries
        self.current_provider = current_provider
        self.current_model = current_model
        self.prefs = prefs or TUIPrefs()
        self.provider_errors = provider_errors or {}
        self._entry_by_id: dict[str, ModelEntry] = {}

    def compose(self) -> ComposeResult:
        with Container(id="model-picker-dialog", classes="opencode-modal"):
            with Horizontal(classes="modal-header"):
                yield Label("Select model", classes="modal-title", markup=False)
                yield Label("esc", classes="modal-esc", markup=False)
            yield Input(placeholder="Search", id="model-filter", classes="modal-search")
            yield OptionList(id="model-list", classes="opencode-list")
            with Horizontal(id="model-footer", classes="modal-footer-bar"):
                yield Static(
                    "Connect a provider  ctrl+a     Favorite  ctrl+f",
                    id="model-footer-hint",
                    classes="modal-footer-hint",
                    markup=False,
                )

    def on_mount(self) -> None:
        self._populate("")
        self.query_one("#model-filter", Input).focus()

    def _populate(self, query: str) -> None:
        ol = self.query_one("#model-list", OptionList)
        ol.clear_options()
        self._entry_by_id.clear()
        q = query.strip().lower()

        filtered = [
            e
            for e in self.entries
            if not q
            or q in e.model.lower()
            or q in e.provider_label.lower()
            or q in e.provider_type.value
        ]

        # Build Favorite & Recent bucket + per-provider groups
        recent_or_fav: list[ModelEntry] = []
        seen_recent: set[str] = set()
        for e in filtered:
            k = TUIPrefs.key_for(e.provider_type, e.model)
            if k in self.prefs.favorites or k in self.prefs.recent:
                if k not in seen_recent:
                    recent_or_fav.append(e)
                    seen_recent.add(k)

        groups: list[tuple[str, list[ModelEntry]]] = []
        if recent_or_fav:
            groups.append(("Recent / Favorites", recent_or_fav))

        by_provider: dict[str, list[ModelEntry]] = {}
        for e in filtered:
            by_provider.setdefault(e.provider_label, []).append(e)
        for label, items in by_provider.items():
            groups.append((label, items))

        first_selectable: int | None = None
        active_idx: int | None = None
        idx = 0

        for group_name, items in groups:
            ol.add_option(Option(Text(group_name, style="bold #bb9af7"), disabled=True))
            idx += 1
            for e in items:
                opt_id = f"{group_name}::{e.provider_type.value}::{e.model}"
                self._entry_by_id[opt_id] = e
                is_active = (
                    e.provider_type == self.current_provider
                    and e.model == self.current_model
                )
                is_fav = self.prefs.is_favorite(e.provider_type, e.model)

                row = Text()
                if is_active:
                    row.append("● ", style="bold #fab283")
                    if active_idx is None:
                        active_idx = idx
                elif is_fav:
                    row.append("★ ", style="#f1fa8c")
                else:
                    row.append("  ")

                name_part = e.model if len(e.model) <= 34 else e.model[:31] + "..."
                row.append(f"{name_part:<35}", style="bold" if is_active else "")
                row.append(f"{e.provider_label:<13}", style="dim")
                row.append(f"{e.badge:>6}", style="#7fd88f" if e.badge == "Free" else "dim")

                ol.add_option(Option(row, id=opt_id))
                if first_selectable is None:
                    first_selectable = idx
                idx += 1

        if not groups:
            if q:
                ol.add_option(
                    Option(Text(f'Use custom model: "{query.strip()}" (auto-detect provider)', style="#7dcfff"), id=f"__custom__::{query.strip()}")
                )
                first_selectable = 0
            else:
                ol.add_option(
                    Option(Text("No models found. Press Ctrl+A to connect a cloud provider.", style="dim"), disabled=True)
                )

        if active_idx is not None and not q:
            ol.highlighted = active_idx
        elif first_selectable is not None:
            ol.highlighted = first_selectable

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "model-filter":
            self._populate(event.value)

    def on_key(self, event: events.Key) -> None:
        ol = self.query_one("#model-list", OptionList)
        if event.key == "down":
            event.prevent_default()
            ol.action_cursor_down()
        elif event.key == "up":
            event.prevent_default()
            ol.action_cursor_up()

    def _dismiss_option(self, opt_id: str) -> None:
        if opt_id.startswith("__custom__::"):
            model_name = opt_id.split("::", 1)[1]
            self.dismiss(ModelPickerResult("select", None, model_name))
            return
        entry = self._entry_by_id.get(opt_id)
        if entry is not None:
            self.dismiss(ModelPickerResult("select", entry.provider_type, entry.model))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "model-filter":
            return
        ol = self.query_one("#model-list", OptionList)
        if ol.highlighted is not None:
            opt = ol.get_option_at_index(ol.highlighted)
            if opt.id and not opt.disabled:
                self._dismiss_option(opt.id)
                return
        typed = event.value.strip()
        if typed:
            self.dismiss(ModelPickerResult("select", None, typed))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id:
            self._dismiss_option(event.option.id)

    def action_connect_provider(self) -> None:
        self.dismiss(ModelPickerResult("connect"))

    def action_toggle_favorite(self) -> None:
        ol = self.query_one("#model-list", OptionList)
        if ol.highlighted is None:
            return
        opt = ol.get_option_at_index(ol.highlighted)
        if not opt.id or opt.id not in self._entry_by_id:
            return
        entry = self._entry_by_id[opt.id]
        self.prefs.toggle_favorite(entry.provider_type, entry.model)
        current_idx = ol.highlighted
        self._populate(self.query_one("#model-filter", Input).value)
        if current_idx < ol.option_count:
            ol.highlighted = current_idx

    def action_cancel(self) -> None:
        self.dismiss(None)
