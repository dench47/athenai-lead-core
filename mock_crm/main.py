"""Имитация внешней CRM-системы для локальной разработки и тестов."""

import os

from fastapi import FastAPI

MOCK_CRM_STORE = os.getenv("MOCK_CRM_STORE", "/tmp/mock_crm_store.json")

app = FastAPI(title="Mock CRM (AthenAI DemoService)", version="0.1.0")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "mock-crm"}
