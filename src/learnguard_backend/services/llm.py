"""Provider-agnostic chat completions powered by LiteLLM."""

from dataclasses import dataclass, field
from importlib import import_module
import math
from numbers import Real
from time import perf_counter
from typing import Any, Mapping, Sequence

from learnguard_backend.settings import BackendSettings


class LLMConfigurationError(ValueError):
    """Raised when the shared LLM client is configured incorrectly."""


class LLMResponseError(RuntimeError):
    """Raised when a provider returns an unusable completion response."""


class LLMFailoverError(RuntimeError):
    """Raised when both the primary and fallback LLM providers fail."""

    def __init__(
        self,
        primary_model: str,
        fallback_model: str,
        primary_error: Exception,
        fallback_error: Exception,
    ) -> None:
        self.primary_error = primary_error
        self.fallback_error = fallback_error
        super().__init__(
            f"Primary model '{primary_model}' failed with "
            f"{type(primary_error).__name__}; fallback model '{fallback_model}' "
            f"failed with {type(fallback_error).__name__}."
        )


@dataclass(frozen=True)
class LLMUsage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None


@dataclass
class LLMResponse:
    content: Any
    model: str
    usage: LLMUsage = field(default_factory=LLMUsage)
    cost_usd: float | None = None
    latency_ms: int = 0
    finish_reason: str | None = None
    tool_calls: list[Any] = field(default_factory=list)


class LiteLLMClient:
    """A shared chat-completion client for LiteLLM-supported providers/models."""

    _RESERVED_OPTIONS = {
        "model",
        "messages",
        "api_key",
        "api_base",
        "timeout",
        "num_retries",
        "max_tokens",
        "max_completion_tokens",
    }

    def __init__(
        self,
        model: str,
        *,
        api_key: str | None = None,
        api_base: str | None = None,
        timeout_seconds: float = 30.0,
        max_retries: int = 2,
        max_input_tokens: int = 8000,
        max_output_tokens: int = 1000,
        fallback_model: str | None = None,
        fallback_api_key: str | None = None,
        fallback_api_base: str | None = None,
    ) -> None:
        if not model.strip():
            raise LLMConfigurationError("An LLM model must be configured.")
        if (
            isinstance(timeout_seconds, bool)
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
        ):
            raise LLMConfigurationError("LLM timeout must be greater than zero.")
        if (
            isinstance(max_retries, bool)
            or not isinstance(max_retries, int)
            or max_retries < 0
        ):
            raise LLMConfigurationError("LLM max_retries must be a non-negative integer.")
        if (
            isinstance(max_input_tokens, bool)
            or not isinstance(max_input_tokens, int)
            or max_input_tokens <= 0
        ):
            raise LLMConfigurationError(
                "LLM max_input_tokens must be a positive integer."
            )
        if (
            isinstance(max_output_tokens, bool)
            or not isinstance(max_output_tokens, int)
            or max_output_tokens <= 0
        ):
            raise LLMConfigurationError(
                "LLM max_output_tokens must be a positive integer."
            )

        self.model = model.strip()
        self.api_key = api_key
        self.api_base = api_base
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.max_input_tokens = max_input_tokens
        self.max_output_tokens = max_output_tokens
        self.fallback_client = (
            LiteLLMClient(
                fallback_model,
                api_key=fallback_api_key,
                api_base=fallback_api_base,
                timeout_seconds=timeout_seconds,
                max_retries=max_retries,
                max_input_tokens=max_input_tokens,
                max_output_tokens=max_output_tokens,
            )
            if fallback_model
            else None
        )

    @classmethod
    def from_settings(cls, settings: BackendSettings) -> "LiteLLMClient":
        return cls(
            settings.llm_model,
            api_key=settings.llm_api_key,
            api_base=settings.llm_api_base,
            timeout_seconds=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
            max_input_tokens=settings.llm_max_input_tokens,
            max_output_tokens=settings.llm_max_output_tokens,
            fallback_model=settings.llm_fallback_model,
            fallback_api_key=settings.llm_fallback_api_key,
            fallback_api_base=settings.llm_fallback_api_base,
        )

    @classmethod
    def from_environment(cls) -> "LiteLLMClient":
        return cls.from_settings(BackendSettings.from_environment())

    def complete(
        self,
        messages: Sequence[Mapping[str, Any]],
        **request_options: Any,
    ) -> LLMResponse:
        """Run a synchronous completion, using the configured fallback on provider failure."""
        self._validate_request(messages, request_options)
        litellm = import_module("litellm")
        self._validate_input_tokens(litellm, messages)

        try:
            return self._complete_with(litellm, messages, request_options)
        except Exception as primary_error:
            if self.fallback_client is None:
                raise
            try:
                self.fallback_client._validate_input_tokens(litellm, messages)
                return self.fallback_client._complete_with(
                    litellm, messages, request_options
                )
            except Exception as fallback_error:
                raise LLMFailoverError(
                    self.model,
                    self.fallback_client.model,
                    primary_error,
                    fallback_error,
                ) from fallback_error

    async def acomplete(
        self,
        messages: Sequence[Mapping[str, Any]],
        **request_options: Any,
    ) -> LLMResponse:
        """Run an asynchronous completion, using the configured fallback on provider failure."""
        self._validate_request(messages, request_options)
        litellm = import_module("litellm")
        self._validate_input_tokens(litellm, messages)

        try:
            return await self._acomplete_with(litellm, messages, request_options)
        except Exception as primary_error:
            if self.fallback_client is None:
                raise
            try:
                self.fallback_client._validate_input_tokens(litellm, messages)
                return await self.fallback_client._acomplete_with(
                    litellm, messages, request_options
                )
            except Exception as fallback_error:
                raise LLMFailoverError(
                    self.model,
                    self.fallback_client.model,
                    primary_error,
                    fallback_error,
                ) from fallback_error

    def _complete_with(
        self,
        litellm: Any,
        messages: Sequence[Mapping[str, Any]],
        request_options: Mapping[str, Any],
    ) -> LLMResponse:
        started_at = perf_counter()
        response = litellm.completion(
            **self._request_arguments(messages, request_options)
        )
        return self._normalize_response(
            response, int((perf_counter() - started_at) * 1000)
        )

    async def _acomplete_with(
        self,
        litellm: Any,
        messages: Sequence[Mapping[str, Any]],
        request_options: Mapping[str, Any],
    ) -> LLMResponse:
        started_at = perf_counter()
        response = await litellm.acompletion(
            **self._request_arguments(messages, request_options)
        )
        return self._normalize_response(
            response, int((perf_counter() - started_at) * 1000)
        )

    def _request_arguments(
        self,
        messages: Sequence[Mapping[str, Any]],
        request_options: Mapping[str, Any],
    ) -> dict[str, Any]:
        arguments = dict(request_options)
        arguments.update(
            model=self.model,
            messages=messages,
            timeout=self.timeout_seconds,
            num_retries=self.max_retries,
            max_tokens=self.max_output_tokens,
        )
        if self.api_key is not None:
            arguments["api_key"] = self.api_key
        if self.api_base is not None:
            arguments["api_base"] = self.api_base
        return arguments

    @classmethod
    def _validate_request(
        cls,
        messages: Sequence[Mapping[str, Any]],
        request_options: Mapping[str, Any],
    ) -> None:
        if not messages:
            raise ValueError("At least one chat message is required.")
        reserved = cls._RESERVED_OPTIONS.intersection(request_options)
        if reserved:
            names = ", ".join(sorted(reserved))
            raise ValueError(f"Request options cannot override: {names}.")

    def _validate_input_tokens(
        self,
        litellm: Any,
        messages: Sequence[Mapping[str, Any]],
    ) -> None:
        try:
            token_count = litellm.token_counter(model=self.model, messages=messages)
        except Exception as exc:
            raise LLMConfigurationError(
                f"Unable to count input tokens for model '{self.model}'."
            ) from exc

        if isinstance(token_count, bool) or not isinstance(token_count, int):
            raise LLMResponseError(
                f"LiteLLM returned an invalid token count for model '{self.model}'."
            )
        if token_count > self.max_input_tokens:
            raise ValueError(
                f"Input contains {token_count} tokens; the limit is "
                f"{self.max_input_tokens} for model '{self.model}'."
            )

    def _normalize_response(self, response: Any, latency_ms: int) -> LLMResponse:
        choices = self._get(response, "choices")
        if not choices:
            raise LLMResponseError("The LLM provider returned no completion choices.")

        choice = choices[0]
        message = self._get(choice, "message")
        if message is None:
            raise LLMResponseError("The LLM provider returned a choice without a message.")

        usage_data = self._get(response, "usage")
        usage = LLMUsage(
            prompt_tokens=self._get(usage_data, "prompt_tokens"),
            completion_tokens=self._get(usage_data, "completion_tokens"),
            total_tokens=self._get(usage_data, "total_tokens"),
        )
        hidden_params = self._get(response, "_hidden_params", {})
        response_cost = self._get(hidden_params, "response_cost")
        cost = (
            float(response_cost)
            if isinstance(response_cost, Real) and not isinstance(response_cost, bool)
            else None
        )
        tool_calls = self._get(message, "tool_calls") or []

        return LLMResponse(
            content=self._get(message, "content"),
            model=self._get(response, "model") or self.model,
            usage=usage,
            cost_usd=cost,
            latency_ms=latency_ms,
            finish_reason=self._get(choice, "finish_reason"),
            tool_calls=list(tool_calls),
        )

    @staticmethod
    def _get(value: Any, name: str, default: Any = None) -> Any:
        if isinstance(value, Mapping):
            return value.get(name, default)
        return getattr(value, name, default)
