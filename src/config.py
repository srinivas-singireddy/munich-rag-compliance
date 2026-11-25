"""Application configuration using Pydantic Settings.

Loads values from environment variables and .env file.
Provides typed, validated config objects (similar to Spring's @ConfigurationProperties).
"""

from pathlib import Path
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Project paths
    project_root: Path = Path(__file__).parent.parent
    data_dir: Path = project_root / "data"
    raw_dir: Path = data_dir / "raw"
    processed_dir: Path = data_dir / "processed"

    # Download settings
    download_timeout_seconds: int = 30
    max_pdfs_to_download: int = 30
    request_delay_seconds: float = 1.0  # politeness delay between requests

    # Vector store (Qdrant) — environment-specific: localhost on the laptop,
    # the Kubernetes service name on EKS (e.g. QDRANT_HOST=qdrant)
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    qdrant_collection: str = "munich_rag_children"

    # LLM (Mistral) — the key has no default and is never logged or printed
    mistral_api_key: SecretStr | None = None
    mistral_model: str = "mistral-small-2603"  # pinned version, not "-latest"
    llm_timeout_seconds: int = 30

    # Local model cache — a permanent folder, not macOS $TMPDIR (L-017)
    model_cache_dir: Path = Path.home() / ".cache" / "munich-rag" / "models"


# Singleton instance — import this everywhere
settings = Settings()
