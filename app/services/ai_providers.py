"""
AI Service abstraction layer for multiple providers.

Supports:
- OpenAI (ChatGPT: gpt-4, gpt-4-turbo, gpt-3.5-turbo)
- Anthropic (Claude: claude-3-opus, claude-3-sonnet, claude-3-haiku)
- Perplexity (pplx-70b-online, pplx-7b-online, llama-3-sonar-*)

Provides a unified interface for chat completions with streaming support.
"""

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncIterator

import httpx

from app.models.ai_agent import AIProvider


logger = logging.getLogger(__name__)


@dataclass
class ChatMessage:
    """A message in a chat conversation."""
    role: str  # "system", "user", "assistant"
    content: str
    name: str | None = None  # Optional name for multi-user contexts


@dataclass
class ChatCompletionResponse:
    """Response from a chat completion request."""
    content: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    finish_reason: str | None = None


@dataclass
class StreamChunk:
    """A chunk from a streaming response."""
    content: str
    is_final: bool = False
    finish_reason: str | None = None


class AIProviderBase(ABC):
    """Base class for AI provider implementations."""
    
    def __init__(self, api_key: str, model: str, **kwargs):
        self.api_key = api_key
        self.model = model
        self.temperature = kwargs.get("temperature", 0.7)
        self.max_tokens = kwargs.get("max_tokens", 4096)
        self.timeout = kwargs.get("timeout", 60.0)
    
    @abstractmethod
    async def chat(
        self,
        messages: list[ChatMessage],
        **kwargs,
    ) -> ChatCompletionResponse:
        """Send a chat completion request."""
        pass
    
    @abstractmethod
    async def chat_stream(
        self,
        messages: list[ChatMessage],
        **kwargs,
    ) -> AsyncIterator[StreamChunk]:
        """Stream a chat completion response."""
        pass
    
    def _messages_to_dict(self, messages: list[ChatMessage]) -> list[dict]:
        """Convert ChatMessage objects to API-compatible dicts."""
        return [
            {"role": m.role, "content": m.content, **({"name": m.name} if m.name else {})}
            for m in messages
        ]


class OpenAIProvider(AIProviderBase):
    """OpenAI/ChatGPT provider implementation."""
    
    BASE_URL = "https://api.openai.com/v1"
    
    # Available models
    MODELS = {
        "gpt-4o": "GPT-4o (Latest)",
        "gpt-4o-mini": "GPT-4o Mini",
        "gpt-4-turbo": "GPT-4 Turbo",
        "gpt-4": "GPT-4",
        "gpt-3.5-turbo": "GPT-3.5 Turbo",
    }
    
    async def chat(
        self,
        messages: list[ChatMessage],
        **kwargs,
    ) -> ChatCompletionResponse:
        """Send a chat completion request to OpenAI."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": self._messages_to_dict(messages),
                    "temperature": kwargs.get("temperature", self.temperature),
                    "max_tokens": kwargs.get("max_tokens", self.max_tokens),
                },
            )
            response.raise_for_status()
            data = response.json()
            
            choice = data["choices"][0]
            usage = data.get("usage", {})
            
            return ChatCompletionResponse(
                content=choice["message"]["content"],
                model=data["model"],
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                total_tokens=usage.get("total_tokens"),
                finish_reason=choice.get("finish_reason"),
            )
    
    async def chat_stream(
        self,
        messages: list[ChatMessage],
        **kwargs,
    ) -> AsyncIterator[StreamChunk]:
        """Stream a chat completion response from OpenAI."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                "POST",
                f"{self.BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": self._messages_to_dict(messages),
                    "temperature": kwargs.get("temperature", self.temperature),
                    "max_tokens": kwargs.get("max_tokens", self.max_tokens),
                    "stream": True,
                },
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            yield StreamChunk(content="", is_final=True)
                            break
                        try:
                            data = json.loads(data_str)
                            delta = data["choices"][0].get("delta", {})
                            content = delta.get("content", "")
                            finish_reason = data["choices"][0].get("finish_reason")
                            if content or finish_reason:
                                yield StreamChunk(
                                    content=content,
                                    is_final=finish_reason is not None,
                                    finish_reason=finish_reason,
                                )
                        except json.JSONDecodeError:
                            continue


class AnthropicProvider(AIProviderBase):
    """Anthropic/Claude provider implementation."""
    
    BASE_URL = "https://api.anthropic.com/v1"
    API_VERSION = "2023-06-01"
    
    # Available models
    MODELS = {
        "claude-3-5-sonnet-20241022": "Claude 3.5 Sonnet (Latest)",
        "claude-3-opus-20240229": "Claude 3 Opus",
        "claude-3-sonnet-20240229": "Claude 3 Sonnet",
        "claude-3-haiku-20240307": "Claude 3 Haiku",
    }
    
    def _convert_messages(self, messages: list[ChatMessage]) -> tuple[str | None, list[dict]]:
        """Convert messages to Anthropic format (system prompt separate)."""
        system_prompt = None
        converted = []
        
        for msg in messages:
            if msg.role == "system":
                system_prompt = msg.content
            else:
                converted.append({"role": msg.role, "content": msg.content})
        
        return system_prompt, converted
    
    async def chat(
        self,
        messages: list[ChatMessage],
        **kwargs,
    ) -> ChatCompletionResponse:
        """Send a chat completion request to Anthropic."""
        system_prompt, converted_messages = self._convert_messages(messages)
        
        request_body = {
            "model": self.model,
            "messages": converted_messages,
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            "temperature": kwargs.get("temperature", self.temperature),
        }
        if system_prompt:
            request_body["system"] = system_prompt
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.BASE_URL}/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": self.API_VERSION,
                    "Content-Type": "application/json",
                },
                json=request_body,
            )
            response.raise_for_status()
            data = response.json()
            
            # Anthropic returns content as a list of blocks
            content = ""
            for block in data.get("content", []):
                if block["type"] == "text":
                    content += block["text"]
            
            usage = data.get("usage", {})
            
            return ChatCompletionResponse(
                content=content,
                model=data["model"],
                prompt_tokens=usage.get("input_tokens"),
                completion_tokens=usage.get("output_tokens"),
                total_tokens=(usage.get("input_tokens", 0) + usage.get("output_tokens", 0)) or None,
                finish_reason=data.get("stop_reason"),
            )
    
    async def chat_stream(
        self,
        messages: list[ChatMessage],
        **kwargs,
    ) -> AsyncIterator[StreamChunk]:
        """Stream a chat completion response from Anthropic."""
        system_prompt, converted_messages = self._convert_messages(messages)
        
        request_body = {
            "model": self.model,
            "messages": converted_messages,
            "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            "temperature": kwargs.get("temperature", self.temperature),
            "stream": True,
        }
        if system_prompt:
            request_body["system"] = system_prompt
        
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                "POST",
                f"{self.BASE_URL}/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": self.API_VERSION,
                    "Content-Type": "application/json",
                },
                json=request_body,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        try:
                            data = json.loads(line[6:])
                            event_type = data.get("type")
                            
                            if event_type == "content_block_delta":
                                delta = data.get("delta", {})
                                if delta.get("type") == "text_delta":
                                    yield StreamChunk(content=delta.get("text", ""))
                            elif event_type == "message_stop":
                                yield StreamChunk(content="", is_final=True, finish_reason="end_turn")
                        except json.JSONDecodeError:
                            continue


class PerplexityProvider(AIProviderBase):
    """Perplexity AI provider implementation."""
    
    BASE_URL = "https://api.perplexity.ai"
    
    # Available models
    MODELS = {
        "llama-3.1-sonar-small-128k-online": "Sonar Small (Online)",
        "llama-3.1-sonar-large-128k-online": "Sonar Large (Online)", 
        "llama-3.1-sonar-huge-128k-online": "Sonar Huge (Online)",
        "llama-3.1-sonar-small-128k-chat": "Sonar Small (Chat)",
        "llama-3.1-sonar-large-128k-chat": "Sonar Large (Chat)",
    }
    
    async def chat(
        self,
        messages: list[ChatMessage],
        **kwargs,
    ) -> ChatCompletionResponse:
        """Send a chat completion request to Perplexity."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": self._messages_to_dict(messages),
                    "temperature": kwargs.get("temperature", self.temperature),
                    "max_tokens": kwargs.get("max_tokens", self.max_tokens),
                },
            )
            response.raise_for_status()
            data = response.json()
            
            choice = data["choices"][0]
            usage = data.get("usage", {})
            
            return ChatCompletionResponse(
                content=choice["message"]["content"],
                model=data["model"],
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
                total_tokens=usage.get("total_tokens"),
                finish_reason=choice.get("finish_reason"),
            )
    
    async def chat_stream(
        self,
        messages: list[ChatMessage],
        **kwargs,
    ) -> AsyncIterator[StreamChunk]:
        """Stream a chat completion response from Perplexity."""
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream(
                "POST",
                f"{self.BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "messages": self._messages_to_dict(messages),
                    "temperature": kwargs.get("temperature", self.temperature),
                    "max_tokens": kwargs.get("max_tokens", self.max_tokens),
                    "stream": True,
                },
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str == "[DONE]":
                            yield StreamChunk(content="", is_final=True)
                            break
                        try:
                            data = json.loads(data_str)
                            delta = data["choices"][0].get("delta", {})
                            content = delta.get("content", "")
                            finish_reason = data["choices"][0].get("finish_reason")
                            if content or finish_reason:
                                yield StreamChunk(
                                    content=content,
                                    is_final=finish_reason is not None,
                                    finish_reason=finish_reason,
                                )
                        except json.JSONDecodeError:
                            continue


def get_provider(provider: AIProvider, api_key: str, model: str, **kwargs) -> AIProviderBase:
    """Factory function to get the appropriate provider instance."""
    providers = {
        AIProvider.OPENAI: OpenAIProvider,
        AIProvider.ANTHROPIC: AnthropicProvider,
        AIProvider.PERPLEXITY: PerplexityProvider,
    }
    
    provider_class = providers.get(provider)
    if not provider_class:
        raise ValueError(f"Unknown AI provider: {provider}")
    
    return provider_class(api_key=api_key, model=model, **kwargs)


def get_available_models(provider: AIProvider) -> dict[str, str]:
    """Get available models for a provider."""
    model_maps = {
        AIProvider.OPENAI: OpenAIProvider.MODELS,
        AIProvider.ANTHROPIC: AnthropicProvider.MODELS,
        AIProvider.PERPLEXITY: PerplexityProvider.MODELS,
    }
    return model_maps.get(provider, {})


# Default models per provider
DEFAULT_MODELS = {
    AIProvider.OPENAI: "gpt-4o",
    AIProvider.ANTHROPIC: "claude-3-5-sonnet-20241022",
    AIProvider.PERPLEXITY: "llama-3.1-sonar-large-128k-online",
}
