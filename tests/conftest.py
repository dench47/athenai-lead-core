"""Общая фикстура тестов: миграции на тестовой базе + очистка таблиц.

Тестовая база athenai_test создаётся самим PostgreSQL при инициализации
тома (docker/initdb/01-create-test-db.sql) — тут только накатываем миграции.
Запуск: docker compose run --rm api pytest.
Основная (демо) база при тестах не затрагивается.
"""

import os

# Тестовая база — ДО импорта приложения: движок создаётся при импорте.
_BASE_URL, _, _DB_NAME = os.environ["DATABASE_URL"].rpartition("/")
os.environ["DATABASE_URL"] = "/".join((_BASE_URL, "athenai_test"))

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

from app.db import engine  # noqa: E402
from app.models import Base  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def prepared_database():
    """Миграции доводим до head: свежая база создаёт схему, уже Used —
    докатывает только новые ревизии."""
    command.upgrade(Config("alembic.ini"), "head")
    yield
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_tables():
    """Очистка таблиц после каждого теста через ORM-выражения."""
    yield
    with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            if table.name != "tenants":  # демо-арендатор нужен каждому тесту
                conn.execute(table.delete())


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from app.api.main import app

    with TestClient(app) as test_client:
        yield test_client
