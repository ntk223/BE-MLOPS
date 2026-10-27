"""Backend runtime settings contract; environment loading is TODO."""

from dataclasses import dataclass


@dataclass(frozen=True)
class BackendSettings:
    database_url: str = ""
    jwt_secret: str = ""
    llm_api_key: str | None = None
    llm_model: str = ""
    ml_data_api_url: str = ""

    @classmethod
    def from_environment(cls) -> "BackendSettings":
        """TODO: load and validate backend-only configuration."""
        raise NotImplementedError
