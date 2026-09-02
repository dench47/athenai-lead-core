-- Отдельная база для автотестов: создаётся один раз при инициализации
-- тома PostgreSQL (docker compose up на чистом томе).
-- Основная демо-база athenai создаётся самой переменной POSTGRES_DB.
CREATE DATABASE athenai_test;
