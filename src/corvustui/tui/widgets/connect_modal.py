"""OpenCode-inspired /connect modal for configuring cloud & local LLM providers."""

from __future__ import annotations

import asyncio

from rich.text import Text
from textual import events, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList, Static
from textual.widgets.option_list import Option

from corvustui.providers import ProviderType
from corvustui.tui.controllers.providers import (
    PROVIDER_SPECS,
    SPEC_BY_TYPE,
    ProviderSpec,
    get_provider_api_key,
    get_provider_base_url,
    is_provider_configured,
    mask_api_key,
    save_provider_credentials,
    test_and_list_models,
)


class ConnectProviderScreen(ModalScreen[ProviderType | None]):
    """Interactive provider manager to add/update/test cloud API keys & endpoints."""

    CSS = """
    ConnectProviderScreen {
        align: center middle;
        background: rgba(10, 10, 14, 0.65);
    }
    """

    BINDINGS = [
        Binding("escape", "back_or_close", "Back / Close"),
    ]

    def __init__(self, active_provider: ProviderType | None = None) -> None:
        super().__init__()
        self._active_provider = active_provider
        self._selected_spec: ProviderSpec | None = None
        self._changed_provider: ProviderType | None = None

    def compose(self) -> ComposeResult:
        with Container(id="connect-dialog", classes="opencode-modal"):
            with Horizontal(classes="modal-header"):
                yield Label("Connect a provider", id="connect-title", classes="modal-title", markup=False)
                yield Label("esc", classes="modal-esc", markup=False)

            with Vertical(id="connect-list-view"):
                yield Input(placeholder="Search providers...", id="connect-search", classes="modal-search")
                yield OptionList(id="connect-list", classes="opencode-list")
                yield Static(
                    "Enter: configure provider   ·   Esc: close",
                    classes="modal-footer-hint",
                    markup=False,
                )

            with Vertical(id="connect-form-view", classes="hidden"):
                yield Static("", id="connect-form-desc", markup=False)
                yield Label("API Key", id="connect-key-label", classes="form-label", markup=False)
                yield Input(
                    placeholder="Paste API key...",
                    password=True,
                    id="connect-key-input",
                )
                yield Label("Base URL", classes="form-label", markup=False)
                yield Input(placeholder="https://...", id="connect-url-input")
                yield Static("", id="connect-status-msg", markup=False)
                with Horizontal(id="connect-form-buttons"):
                    yield Button("Back", id="connect-back", variant="default")
                    yield Button("Remove Key", id="connect-remove", variant="error")
                    yield Button("Save & Test", id="connect-save", variant="primary")

    def on_mount(self) -> None:
        self._populate_list("")
        self.query_one("#connect-search", Input).focus()

    def _populate_list(self, query: str) -> None:
        ol = self.query_one("#connect-list", OptionList)
        ol.clear_options()
        q = query.strip().lower()

        first_idx: int | None = None
        for idx, spec in enumerate(PROVIDER_SPECS):
            if q and q not in spec.display_name.lower() and q not in spec.provider_type.value:
                continue
            configured = is_provider_configured(spec.provider_type)
            existing_key = get_provider_api_key(spec.provider_type)
            row = Text()
            if spec.provider_type == self._active_provider:
                row.append("● ", style="bold #fab283")
            elif configured:
                row.append("● ", style="bold #7fd88f")
            else:
                row.append("○ ", style="dim")

            row.append(f"{spec.display_name:<22}", style="bold" if configured else "")
            if spec.requires_api_key:
                status = f"Connected ({mask_api_key(existing_key)})" if configured else "Not configured"
                row.append(f"{status:>28}", style="#7fd88f" if configured else "dim")
            else:
                url = get_provider_base_url(spec.provider_type)
                row.append(f"Local ({url})"[:28].rjust(28), style="#7dcfff")

            ol.add_option(Option(row, id=spec.provider_type.value))
            if first_idx is None:
                first_idx = 0

        if first_idx is not None:
            ol.highlighted = first_idx

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "connect-search":
            self._populate_list(event.value)

    def on_key(self, event: events.Key) -> None:
        if self._selected_spec is not None:
            return
        ol = self.query_one("#connect-list", OptionList)
        if event.key == "down":
            event.prevent_default()
            ol.action_cursor_down()
        elif event.key == "up":
            event.prevent_default()
            ol.action_cursor_up()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "connect-search":
            ol = self.query_one("#connect-list", OptionList)
            if ol.highlighted is not None:
                opt = ol.get_option_at_index(ol.highlighted)
                if opt.id:
                    self._open_form(ProviderType(opt.id))
        elif event.input.id in {"connect-key-input", "connect-url-input"}:
            self._save_and_test()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option.id:
            self._open_form(ProviderType(event.option.id))

    def _open_form(self, provider_type: ProviderType) -> None:
        spec = SPEC_BY_TYPE[provider_type]
        self._selected_spec = spec

        self.query_one("#connect-list-view").add_class("hidden")
        self.query_one("#connect-form-view").remove_class("hidden")
        self.query_one("#connect-title", Label).update(f"Configure {spec.display_name}")

        key_label = self.query_one("#connect-key-label", Label)
        key_input = self.query_one("#connect-key-input", Input)
        url_input = self.query_one("#connect-url-input", Input)
        remove_btn = self.query_one("#connect-remove", Button)
        status_msg = self.query_one("#connect-status-msg", Static)

        existing_key = get_provider_api_key(provider_type) or ""
        existing_url = get_provider_base_url(provider_type)

        if spec.requires_api_key:
            key_label.display = True
            key_input.display = True
            key_input.value = existing_key
            key_input.placeholder = spec.placeholder
            remove_btn.display = bool(existing_key)
            self.query_one("#connect-form-desc", Static).update(
                f"Stored in .env as {spec.env_key} (owner-only 0600)."
            )
            key_input.focus()
        else:
            key_label.display = False
            key_input.display = False
            remove_btn.display = False
            self.query_one("#connect-form-desc", Static).update(
                f"Local endpoint stored in .env as {spec.base_url_env}."
            )
            url_input.focus()

        url_input.value = existing_url
        status_msg.update("")

    def _close_form(self) -> None:
        self._selected_spec = None
        self.query_one("#connect-form-view").add_class("hidden")
        self.query_one("#connect-list-view").remove_class("hidden")
        self.query_one("#connect-title", Label).update("Connect a provider")
        self._populate_list(self.query_one("#connect-search", Input).value)
        self.query_one("#connect-search", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "connect-back":
            self._close_form()
        elif event.button.id == "connect-remove":
            self._remove_key()
        elif event.button.id == "connect-save":
            self._save_and_test()

    def _remove_key(self) -> None:
        if self._selected_spec is None:
            return
        ptype = self._selected_spec.provider_type
        save_provider_credentials(ptype, api_key="", base_url=None)
        self._changed_provider = ptype
        self.query_one("#connect-key-input", Input).value = ""
        self.query_one("#connect-status-msg", Static).update(
            Text("Removed API key from session and .env.", style="#e0af68")
        )
        self._close_form()

    @work(exclusive=True, group="connect_test")
    async def _save_and_test(self) -> None:
        if self._selected_spec is None:
            return
        spec = self._selected_spec
        key_val = self.query_one("#connect-key-input", Input).value.strip()
        url_val = self.query_one("#connect-url-input", Input).value.strip()
        status_msg = self.query_one("#connect-status-msg", Static)

        if spec.requires_api_key and not key_val:
            status_msg.update(Text("Please enter an API key.", style="#f7768e"))
            return

        save_provider_credentials(
            spec.provider_type,
            api_key=key_val if spec.requires_api_key else None,
            base_url=url_val or spec.default_base_url,
        )
        status_msg.update(Text(f"Testing {spec.display_name} connection...", style="#f1fa8c"))

        models, err = await test_and_list_models(spec.provider_type, timeout=10.0)
        if err:
            status_msg.update(
                Text(f"Saved key, but model check failed: {err[:120]}", style="#e0af68")
            )
            self._changed_provider = spec.provider_type
            return

        self._changed_provider = spec.provider_type
        status_msg.update(
            Text(f"✓ Connected! Found {len(models)} model(s).", style="bold #7fd88f")
        )
        await asyncio.sleep(0.45)
        self.dismiss(spec.provider_type)

    def action_back_or_close(self) -> None:
        if self._selected_spec is not None:
            self._close_form()
        else:
            self.dismiss(self._changed_provider)
