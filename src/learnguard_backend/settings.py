"""Backend runtime settings loaded from environment variables."""

from dataclasses import dataclass
import math
import os
from pathlib import Path

from dotenv import load_dotenv


ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


@dataclass(frozen=True)
class BackendSettings:
    database_url: str = ""
    jwt_secret: str = ""
    llm_api_key: str | None = None
    llm_model: str = "gemini-3.8-flash"
    llm_api_base: str | None = None
    llm_fallback_model: str | None = None
    llm_fallback_api_key: str | None = None
    llm_fallback_api_base: str | None = None
    llm_timeout_seconds: float = 30.0
    llm_fallback_timeout_seconds: float = 120.0
    llm_max_retries: int = 2
    llm_max_input_tokens: int = 8000
    llm_max_output_tokens: int = 1000
    ml_data_api_url: str = ""
    cors_origins: str = ""
    batch_dir: str = ""
    model_manifest_dir: str = ""
    # Published evaluation scorecards (written by the ml-data pipeline,
    # read-only here). Empty disables the /evaluations endpoints.
    evaluations_dir: str = ""
    # UI-triggered evaluation runs (POST /evaluations/run): the ml-data repo
    # that owns the ``learnguard evaluate all`` CLI. Empty disables the
    # trigger endpoint. All eval paths are relative to ``ml_repo_dir``.
    ml_repo_dir: str = ""
    ml_eval_learnguard_bin: str = ".venv/bin/learnguard"
    ml_eval_model_dir: str = ""
    ml_eval_preprocessing_dir: str = ""
    # Empty falls back to the backend DATABASE_URL for the CLI --pg-dsn.
    ml_eval_pg_dsn: str = ""
    eval_judge_sample: int = 4
    # Raw OULAD CSV dataset (studentInfo.csv, studentVle.csv, ...) read by
    # GET /predictions/.../activity. Empty -> 503 oulad_data_not_configured.
    oulad_data_dir: str = ""

    @classmethod
    def from_environment(cls) -> "BackendSettings":
        """Load backend configuration from the project .env and environment."""
        load_dotenv(dotenv_path=ENV_FILE, override=False)

        try:
            timeout_seconds = float(os.getenv("LLM_TIMEOUT_SECONDS", "30"))
            fallback_timeout_seconds = float(
                os.getenv("LLM_FALLBACK_TIMEOUT_SECONDS", "120")
            )
            max_retries = int(os.getenv("LLM_MAX_RETRIES", "2"))
            max_input_tokens = int(os.getenv("LLM_MAX_INPUT_TOKENS", "8000"))
            max_output_tokens = int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "1000"))
        except ValueError as exc:
            raise ValueError(
                "LLM_TIMEOUT_SECONDS and LLM_FALLBACK_TIMEOUT_SECONDS must be "
                "numeric, and "
                "LLM_MAX_RETRIES, LLM_MAX_INPUT_TOKENS, and "
                "LLM_MAX_OUTPUT_TOKENS must be integers."
            ) from exc

        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("LLM_TIMEOUT_SECONDS must be greater than zero.")
        if (
            not math.isfinite(fallback_timeout_seconds)
            or fallback_timeout_seconds <= 0
        ):
            raise ValueError(
                "LLM_FALLBACK_TIMEOUT_SECONDS must be greater than zero."
            )
        if max_retries < 0:
            raise ValueError("LLM_MAX_RETRIES cannot be negative.")
        if max_input_tokens <= 0:
            raise ValueError("LLM_MAX_INPUT_TOKENS must be greater than zero.")
        if max_output_tokens <= 0:
            raise ValueError("LLM_MAX_OUTPUT_TOKENS must be greater than zero.")

        try:
            judge_sample = int(os.getenv("EVAL_JUDGE_SAMPLE", "4"))
        except ValueError as exc:
            raise ValueError("EVAL_JUDGE_SAMPLE must be an integer.") from exc

        return cls(
            database_url=(
                os.getenv("DATABASE_URL") or os.getenv("LEARNGUARD_PG_DSN", "")
            ),
            jwt_secret=os.getenv("JWT_SECRET", ""),
            llm_api_key=os.getenv("LLM_API_KEY") or None,
            llm_model=os.getenv("LLM_MODEL", ""),
            llm_api_base=os.getenv("LLM_API_BASE") or None,
            llm_fallback_model=os.getenv("LLM_FALLBACK_MODEL") or None,
            llm_fallback_api_key=os.getenv("LLM_FALLBACK_API_KEY") or None,
            llm_fallback_api_base=os.getenv("LLM_FALLBACK_API_BASE") or None,
            llm_timeout_seconds=timeout_seconds,
            llm_fallback_timeout_seconds=fallback_timeout_seconds,
            llm_max_retries=max_retries,
            llm_max_input_tokens=max_input_tokens,
            llm_max_output_tokens=max_output_tokens,
            ml_data_api_url=os.getenv("ML_DATA_API_URL", ""),
            cors_origins=os.getenv(
                "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
            ),
            evaluations_dir=os.getenv("EVALUATIONS_DIR", ""),
            ml_repo_dir=os.getenv("ML_REPO_DIR", ""),
            ml_eval_learnguard_bin=os.getenv(
                "ML_EVAL_LEARNGUARD_BIN", ".venv/bin/learnguard"
            ),
            ml_eval_model_dir=os.getenv("ML_EVAL_MODEL_DIR", ""),
            ml_eval_preprocessing_dir=os.getenv(
                "ML_EVAL_PREPROCESSING_DIR", ""
            ),
            ml_eval_pg_dsn=os.getenv("ML_EVAL_PG_DSN", ""),
            eval_judge_sample=judge_sample,
            oulad_data_dir=os.getenv("OULAD_DATA_DIR", ""),
        )
