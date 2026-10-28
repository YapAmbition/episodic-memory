from typing import Protocol
from . import model


class ModelAdapter(Protocol):
    async def complete(self, settings, api_key, messages, json_mode=False,
                       purpose="other", metrics=None, max_tokens=None) -> str:
        """Return plain completion text; propagate cancellation and domain failures."""
        ...


class RemoteModelAdapter:
    async def complete(self, *args, **kwargs):
        return await model.complete(*args, **kwargs)
