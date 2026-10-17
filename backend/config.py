"""Application configuration loaded from environment variables via Pydantic Settings."""

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """Central settings object. All values can be overridden via environment variables."""

    ollama_url: str = Field(
        default="http://localhost:11434",
        description="Base URL of the running Ollama service.",
    )
    world_state_path: str = Field(
        default="/data/world_state.json",
        description="Absolute path to the world_state.json file (single source of graph truth).",
    )
    lancedb_path: str = Field(
        default="/data/lancedb",
        description="Directory used by the embedded LanceDB instance.",
    )
    chronos_path: str = Field(
        default="/data/chronos",
        description="Directory where Chronos ZSTD-compressed patch commits are stored.",
    )
    axiom_rules_path: str = Field(
        default="/data/axiom_rules.json",
        description="Path to the JSON file defining deterministic Axiom constraint rules.",
    )

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
    }


settings = Settings()
