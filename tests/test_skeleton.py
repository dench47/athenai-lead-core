"""Проверка каркаса: настройки по умолчанию безопасны.

В «чистом» окружении (без .env и без переменных) система обязана работать
без единого реального ключа — это требование задания, зашитое в конфигурацию
и зафиксированное тестом.
"""

from app.config import Settings

ENV_KEYS_UNDER_TEST = ("QUALIFIER_PROVIDER", "DEEPSEEK_API_KEY", "CRM_RETRY_MAX_ATTEMPTS")


def test_defaults_are_safe_in_clean_environment(monkeypatch) -> None:
    for key in ENV_KEYS_UNDER_TEST:
        monkeypatch.delenv(key, raising=False)
    s = Settings(_env_file=None)
    assert s.qualifier_provider == "mock"
    assert s.deepseek_api_key == ""
    assert s.crm_retry_max_attempts == 5
