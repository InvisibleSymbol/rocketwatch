from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rocketwatch.utils.config import LLMConfig


class LLMProvider(ABC):
    def __init__(self, api_key: str, model: str) -> None:
        self._api_key = api_key
        self._model = model

    @abstractmethod
    async def complete(
        self, system: str, user_message: str, *, max_tokens: int = 1024
    ) -> str: ...


class AnthropicProvider(LLMProvider):
    async def complete(
        self, system: str, user_message: str, *, max_tokens: int = 1024
    ) -> str:
        from anthropic import AsyncAnthropic
        from anthropic.types import TextBlock

        if not hasattr(self, "_client"):
            self._client = AsyncAnthropic(api_key=self._api_key)

        response = await self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_message}],
        )
        block = response.content[0]
        assert isinstance(block, TextBlock)
        return block.text


class OpenAIProvider(LLMProvider):
    async def complete(
        self, system: str, user_message: str, *, max_tokens: int = 1024
    ) -> str:
        from openai import AsyncOpenAI

        if not hasattr(self, "_client"):
            self._client = AsyncOpenAI(api_key=self._api_key)

        response = await self._client.chat.completions.create(
            model=self._model,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_message},
            ],
        )
        content = response.choices[0].message.content
        assert isinstance(content, str)
        return content


class GoogleProvider(LLMProvider):
    async def complete(
        self, system: str, user_message: str, *, max_tokens: int = 1024
    ) -> str:
        from google import genai

        if not hasattr(self, "_client"):
            self._client = genai.Client(api_key=self._api_key)

        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=user_message,
            config=genai.types.GenerateContentConfig(
                system_instruction=system,
                max_output_tokens=max_tokens,
            ),
        )
        text = response.text
        assert isinstance(text, str)
        return text


_PROVIDERS: dict[str, type[LLMProvider]] = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "google": GoogleProvider,
}


def create_provider(config: LLMConfig) -> LLMProvider | None:
    """Create an LLM provider from config. Returns None if not configured."""
    if not config.provider or not config.api_key:
        return None
    cls = _PROVIDERS[config.provider]
    return cls(api_key=config.api_key, model=config.model)
