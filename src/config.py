"""Application configuration using Pydantic Settings.

Loads values from environment variables and .env file.
Provides typed, validated config objects (similar to Spring's @ConfigurationProperties).
"""

from pathlib import Path
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


# Singleton instance — import this everywhere
settings = Settings()
