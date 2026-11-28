"""Application configuration.

Credentials are read from environment variables or a local ``.env`` file.
The service is intended to run on localhost only.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = PROJECT_ROOT / "backend"


class Settings(BaseSettings):
    host: str = "127.0.0.1"
    port: int = 8010

    database_path: str = str(PROJECT_ROOT / "data" / "personaltrading.db")
    market_db_path: str = str(PROJECT_ROOT / "data" / "market.duckdb")

    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"

    tushare_token: str = ""
    tushare_base_url: str = "https://api.tushare.pro"
    sync_concurrency: int = 4
    sync_years: int = 10
    # Gateway asks for <=200 req/min; leave headroom for retries.
    tushare_rate_per_minute: int = 180

    # Multi-agent research
    agent_model: str = ""              # empty -> reuse deepseek_model
    agent_debate_rounds: int = 2
    agent_max_tokens: int = 1600
    agent_temperature: float = 0.25
    agent_timeout: float = 120.0

    # Memory retrieval (goat 3.1): as-of bounded, with an explicit window.
    memory_window_days: int = 180
    memory_topic_window_days: int = 365
    memory_block_chars: int = 900
    memory_peer_enabled: bool = False
    memory_peer_limit: int = 2

    # Experience memory (P8-2): active cards only, small prompt budget.
    experience_limit: int = 2
    experience_block_chars: int = 300

    http_timeout: float = 12.0
    quote_ttl: int = 5
    kline_ttl: int = 300
    fundamentals_ttl: int = 3600

    model_config = SettingsConfigDict(
        env_prefix="PERSONALTRADING_",
        env_file=(PROJECT_ROOT / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()