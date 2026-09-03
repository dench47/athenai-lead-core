"""Конфигурация приложения.

Все настройки читаются ТОЛЬКО из переменных окружения (или локального .env,
который исключён из git). Значения по умолчанию подобраны так, чтобы проект
полностью работал без единого реального секрета — это требование задания.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- База данных и mock-CRM ---
    database_url: str = "postgresql+psycopg://athenai:athenai_dev@localhost:5432/athenai"
    mock_crm_url: str = "http://localhost:8081"
    mock_crm_token: str = "dev-crm-token"

    # --- Квалификатор: mock (детерминированный, без ключей) или deepseek ---
    qualifier_provider: str = "mock"
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-pro"
    deepseek_timeout_seconds: float = 30.0
    llm_max_attempts: int = 2

    # --- Токены доступа (в демо-развёртывании это локальные заглушки) ---
    webhook_token_telegram: str = "dev-token-telegram"
    webhook_token_website: str = "dev-token-website"
    webhook_token_avito: str = "dev-token-avito"
    admin_token: str = "dev-admin-token"

    # --- Поведение конвейера ---
    worker_poll_interval_seconds: float = 2.0
    crm_retry_max_attempts: int = 5
    crm_backoff_base_seconds: float = 1.0
    crm_backoff_max_seconds: float = 60.0


@lru_cache
def get_settings() -> Settings:
    """Кэшированный доступ к настройкам: создаём один раз на процесс."""
    return Settings()
