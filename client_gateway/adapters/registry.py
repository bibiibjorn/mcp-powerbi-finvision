"""Which adapter serves a request: ``adapter_for(method, host, path, content_type)``.

Returns the shared adapter instance, or None when no adapter understands the request. What None
means is the front end's decision (phase 3), per the spec: in a chat that is NOT a masked Power BI
chat the message is passed through unchanged (R2); in a masked chat on a routed host an unknown
body is blocked with a clear message (never sent unmasked, §11), so this is the hook for that rule.
"""

from __future__ import annotations

from client_gateway.adapters.base import Adapter
from client_gateway.adapters.chatgpt_web import ChatGPTWebAdapter
from client_gateway.adapters.openai_chat import OpenAIChatAdapter
from client_gateway.adapters.openai_responses import OpenAIResponsesAdapter

_ADAPTERS: tuple[Adapter, ...] = (
    OpenAIChatAdapter(),
    OpenAIResponsesAdapter(),
    ChatGPTWebAdapter(),
)


def adapter_for(method: str, host: str, path: str, content_type: str = "") -> Adapter | None:
    """The adapter for a request line, or None."""
    for adapter in _ADAPTERS:
        if adapter.match(method, host, path, content_type):
            return adapter
    return None


def adapters() -> tuple[Adapter, ...]:
    """Every registered adapter."""
    return _ADAPTERS
