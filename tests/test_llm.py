import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from learnguard_backend.services.llm import (
    LLMConfigurationError,
    LLMFailoverError,
    LLMResponseError,
    LiteLLMClient,
)
from learnguard_backend.settings import BackendSettings
import learnguard_backend.settings as settings_module


class LiteLLMClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = LiteLLMClient(
            "openai/gpt-4o-mini",
            api_key="test-key",
            timeout_seconds=12,
            max_retries=1,
        )
        self.messages = [{"role": "user", "content": "Hello"}]
        self.response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="Hi", tool_calls=None),
                    finish_reason="stop",
                )
            ],
            model="openai/gpt-4o-mini",
            usage=SimpleNamespace(
                prompt_tokens=4,
                completion_tokens=2,
                total_tokens=6,
            ),
            _hidden_params={"response_cost": 0.0001},
        )

    def test_complete_normalizes_response_and_forwards_options(self) -> None:
        provider = SimpleNamespace(
            completion=unittest.mock.Mock(return_value=self.response),
            token_counter=unittest.mock.Mock(return_value=4),
        )
        with patch.dict(sys.modules, {"litellm": provider}):
            result = self.client.complete(self.messages, temperature=0.2)

        self.assertEqual(result.content, "Hi")
        self.assertEqual(result.model, "openai/gpt-4o-mini")
        self.assertEqual(result.usage.total_tokens, 6)
        self.assertEqual(result.cost_usd, 0.0001)
        provider.completion.assert_called_once_with(
            model="openai/gpt-4o-mini",
            messages=self.messages,
            timeout=12,
            num_retries=1,
            max_tokens=1000,
            api_key="test-key",
            temperature=0.2,
        )
        provider.token_counter.assert_called_once_with(
            model="openai/gpt-4o-mini", messages=self.messages
        )

    def test_complete_uses_fallback_when_primary_provider_fails(self) -> None:
        fallback_response_data = vars(self.response).copy()
        fallback_response_data["model"] = "ollama/qwen2.5:3b"
        fallback_response = SimpleNamespace(**fallback_response_data)
        provider = SimpleNamespace(
            completion=unittest.mock.Mock(
                side_effect=[RuntimeError("primary unavailable"), fallback_response]
            ),
            token_counter=unittest.mock.Mock(return_value=4),
        )
        client = LiteLLMClient(
            "openai/gpt-4o-mini",
            fallback_model="ollama/qwen2.5:3b",
            fallback_api_base="http://localhost:11434",
        )
        with patch.dict(sys.modules, {"litellm": provider}):
            result = client.complete(self.messages)

        self.assertEqual(result.content, "Hi")
        self.assertEqual(result.model, "ollama/qwen2.5:3b")
        self.assertEqual(provider.completion.call_count, 2)
        self.assertEqual(
            provider.completion.call_args_list[1].kwargs["api_base"],
            "http://localhost:11434",
        )

    def test_complete_reports_error_when_both_providers_fail(self) -> None:
        provider = SimpleNamespace(
            completion=unittest.mock.Mock(
                side_effect=[RuntimeError("primary unavailable"), RuntimeError("fallback unavailable")]
            ),
            token_counter=unittest.mock.Mock(return_value=4),
        )
        client = LiteLLMClient(
            "openai/gpt-4o-mini",
            fallback_model="ollama/qwen2.5:3b",
        )
        with patch.dict(sys.modules, {"litellm": provider}):
            with self.assertRaises(LLMFailoverError) as error:
                client.complete(self.messages)

        self.assertIsInstance(error.exception.__cause__, RuntimeError)
        self.assertEqual(str(error.exception.primary_error), "primary unavailable")
        self.assertEqual(provider.completion.call_count, 2)

    def test_rejects_empty_messages_and_reserved_options(self) -> None:
        with self.assertRaisesRegex(ValueError, "At least one"):
            self.client.complete([])
        with self.assertRaisesRegex(ValueError, "cannot override: model"):
            self.client.complete(self.messages, model="other/model")
        with self.assertRaisesRegex(ValueError, "cannot override: max_tokens"):
            self.client.complete(self.messages, max_tokens=10000)

    def test_rejects_missing_model_and_malformed_response(self) -> None:
        with self.assertRaises(LLMConfigurationError):
            LiteLLMClient("")

        with self.assertRaises(LLMConfigurationError):
            LiteLLMClient("model", timeout_seconds=float("inf"))
        with self.assertRaises(LLMConfigurationError):
            LiteLLMClient("model", max_retries=1.5)

        with self.assertRaises(LLMResponseError):
            self.client._normalize_response(SimpleNamespace(choices=[]), 0)

    def test_reads_llm_settings_from_environment(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "LLM_MODEL": "openai/gpt-4o-mini",
                "LLM_API_KEY": "test-key",
                "LLM_TIMEOUT_SECONDS": "10",
                "LLM_MAX_RETRIES": "3",
                "LLM_MAX_INPUT_TOKENS": "7000",
                "LLM_MAX_OUTPUT_TOKENS": "900",
                "LLM_FALLBACK_MODEL": "ollama/qwen2.5:3b",
                "LLM_FALLBACK_API_BASE": "http://localhost:11434",
                "LLM_FALLBACK_API_KEY": "ollama",
            },
            clear=True,
        ):
            settings = BackendSettings.from_environment()

        self.assertEqual(settings.llm_model, "openai/gpt-4o-mini")
        self.assertEqual(settings.llm_api_key, "test-key")
        self.assertEqual(settings.llm_timeout_seconds, 10)
        self.assertEqual(settings.llm_max_retries, 3)
        self.assertEqual(settings.llm_max_input_tokens, 7000)
        self.assertEqual(settings.llm_max_output_tokens, 900)
        self.assertEqual(settings.llm_fallback_model, "ollama/qwen2.5:3b")
        self.assertEqual(settings.llm_fallback_api_base, "http://localhost:11434")
        self.assertEqual(settings.llm_fallback_api_key, "ollama")
        client = LiteLLMClient.from_settings(settings)
        self.assertIsNotNone(client.fallback_client)
        self.assertEqual(client.fallback_client.model, "ollama/qwen2.5:3b")

    def test_loads_dotenv_file_without_overriding_process_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / ".env"
            env_file.write_text(
                "LLM_MODEL=openrouter/example:free\nLLM_API_KEY=file-key\n",
                encoding="utf-8",
            )
            with (
                patch.object(settings_module, "ENV_FILE", env_file),
                patch.dict("os.environ", {"LLM_MODEL": "openrouter/process-model"}, clear=True),
            ):
                settings = BackendSettings.from_environment()

        self.assertEqual(settings.llm_model, "openrouter/process-model")
        self.assertEqual(settings.llm_api_key, "file-key")

    def test_rejects_input_over_token_limit_before_provider_call(self) -> None:
        provider = SimpleNamespace(
            token_counter=unittest.mock.Mock(return_value=8001),
            completion=unittest.mock.Mock(),
        )
        with patch.dict(sys.modules, {"litellm": provider}):
            with self.assertRaisesRegex(ValueError, "limit is 8000"):
                self.client.complete(self.messages)

        provider.completion.assert_not_called()


class AsyncLiteLLMClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_acomplete_normalizes_response(self) -> None:
        message = SimpleNamespace(content="Async reply", tool_calls=[])
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="stop")],
            model="anthropic/claude-3-5-sonnet",
            usage=None,
            _hidden_params={},
        )

        async def acompletion(**_kwargs):
            return response

        provider = SimpleNamespace(
            acompletion=acompletion,
            token_counter=unittest.mock.Mock(return_value=4),
        )
        client = LiteLLMClient("anthropic/claude-3-5-sonnet")
        with patch.dict(sys.modules, {"litellm": provider}):
            result = await client.acomplete([{"role": "user", "content": "Hello"}])

        self.assertEqual(result.content, "Async reply")
        self.assertIsNone(result.cost_usd)

    async def test_acomplete_uses_fallback_when_primary_provider_fails(self) -> None:
        fallback_response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="Fallback reply", tool_calls=[]),
                    finish_reason="stop",
                )
            ],
            model="ollama/qwen2.5:3b",
            usage=None,
            _hidden_params={},
        )
        calls = 0

        async def acompletion(**_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("primary unavailable")
            return fallback_response

        provider = SimpleNamespace(
            acompletion=acompletion,
            token_counter=unittest.mock.Mock(return_value=4),
        )
        client = LiteLLMClient(
            "openai/gpt-4o-mini",
            fallback_model="ollama/qwen2.5:3b",
        )
        with patch.dict(sys.modules, {"litellm": provider}):
            result = await client.acomplete([{"role": "user", "content": "Hello"}])

        self.assertEqual(result.content, "Fallback reply")
        self.assertEqual(result.model, "ollama/qwen2.5:3b")
        self.assertEqual(calls, 2)


if __name__ == "__main__":
    unittest.main()
