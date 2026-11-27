"""Controllers for CorvusTUI (queue, history, turn lifecycle, provider auth)."""

from corvustui.tui.controllers.history import PromptHistory
from corvustui.tui.controllers.message_queue import MessageQueue, QueuedMessage
from corvustui.tui.controllers.providers import (
    PROVIDER_SPECS,
    SPEC_BY_TYPE,
    ModelEntry,
    ProviderSpec,
    TUIPrefs,
    discover_all_models,
    get_provider_api_key,
    get_provider_base_url,
    is_provider_configured,
    mask_api_key,
    save_provider_credentials,
    test_and_list_models,
)
from corvustui.tui.controllers.turn_controller import TurnController

__all__ = [
    "MessageQueue",
    "ModelEntry",
    "PROVIDER_SPECS",
    "PromptHistory",
    "ProviderSpec",
    "QueuedMessage",
    "SPEC_BY_TYPE",
    "TUIPrefs",
    "TurnController",
    "discover_all_models",
    "get_provider_api_key",
    "get_provider_base_url",
    "is_provider_configured",
    "mask_api_key",
    "save_provider_credentials",
    "test_and_list_models",
]
