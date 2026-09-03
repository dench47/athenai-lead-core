"""Имитация внешней CRM-системы для локальной разработки и тестов.

Ведёт себя как отдельная система:
- собственная база SQLite на томе (том crmdata монтируется compose-ом
  в /var/lib/mockcrm) — переживает перезапуск контейнера, что важно для
  сценария «CRM падала и восстановилась — сделка одна»;
- авторизация по заголовку X-CRM-Token;
- идемпотентность на уровне БД: UNIQUE(idempotency_key) + INSERT с
  ON CONFLICT DO NOTHING — второй запрос с тем же ключом не создаёт
  вторую сделку, а возвращает признак дедупликации;
- режимы отказа (chaos) для проверки устойчивости ядра:
    POST /__chaos/fail_429?times=N   — следующие N запросов получат 429
    POST /__chaos/fail_500?times=N   — ... получат 500
    POST /__chaos/timeout?times=N    — ... будут молчать 8 секунд
    POST /__chaos/reset              — сброс отказов и хранилища
"""

import json
import os
import time
import uuid
from threading import Lock

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    Table,
    Text,
    create_engine,
    insert,
    select,
    update,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

CHAOS_MODES = ("fail_429", "fail_500", "timeout")
MOCK_CRM_TOKEN = os.getenv("MOCK_CRM_TOKEN", "dev-crm-token")

# Своя SQLite-база mock-CRM: абсолютный путь внутри контейнера, том crmdata.
_engine = create_engine("sqlite:////var/lib/mockcrm/store.db")
_metadata = MetaData()
_deals_table = Table(
    "deals",
    _metadata,
    Column("id", Text, primary_key=True),
    Column("idempotency_key", Text, unique=True, nullable=False),
    Column("payload", Text, nullable=False),
)
_chaos_table = Table(
    "chaos",
    _metadata,
    Column("id", Integer, primary_key=True),
    Column("mode", Text),
    Column("remaining", Integer, nullable=False),
)
_metadata.create_all(_engine)

with _engine.begin() as conn:
    if conn.execute(select(_chaos_table.c.id)).first() is None:
        conn.execute(insert(_chaos_table).values(id=1, mode=None, remaining=0))

app = FastAPI(title="Mock CRM (AthenAI DemoService)", version="1.0.0")

_lock = Lock()


def _chaos_state() -> tuple[str | None, int]:
    with _engine.begin() as conn:
        row = conn.execute(select(_chaos_table.c.mode, _chaos_table.c.remaining)).first()
    if row is None:
        return None, 0
    return row[0], row[1] or 0


def _set_chaos(mode: str | None, remaining: int) -> None:
    with _engine.begin() as conn:
        conn.execute(update(_chaos_table).values(mode=mode, remaining=remaining))


def _apply_chaos() -> None:
    mode, remaining = _chaos_state()
    if mode and remaining > 0:
        _set_chaos(mode, remaining - 1)
        if mode == "fail_429":
            raise HTTPException(status_code=429, detail="chaos: rate limited")
        if mode == "fail_500":
            raise HTTPException(status_code=500, detail="chaos: internal error")
        if mode == "timeout":
            time.sleep(8)
            raise HTTPException(status_code=504, detail="chaos: timeout")


class DealBundle(BaseModel):
    """Пакет данных одного кейса: контакт, сделка, черновик, задача."""

    contact: dict
    deal: dict
    draft: dict
    task: dict


@app.get("/healthz")
def healthz() -> dict:
    with _engine.begin() as conn:
        rows = conn.execute(select(_deals_table.c.id)).fetchall()
    return {"status": "ok", "service": "mock-crm", "deals": len(rows)}


@app.post("/deals")
def create_deal(
    bundle: DealBundle,
    x_crm_token: str | None = Header(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict:
    if x_crm_token != MOCK_CRM_TOKEN:
        raise HTTPException(status_code=401, detail="invalid crm token")
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key is required")

    with _lock:
        _apply_chaos()

        deal_id = "-".join(("deal", uuid.uuid4().hex[:12]))
        payload = json.dumps(bundle.model_dump(), ensure_ascii=False)
        stmt = (
            sqlite_insert(_deals_table)
            .values(id=deal_id, idempotency_key=idempotency_key, payload=payload)
            .on_conflict_do_nothing(index_elements=[_deals_table.c.idempotency_key])
            .returning(_deals_table.c.id)
        )
        with _engine.begin() as conn:
            inserted = conn.execute(stmt).first()

    if inserted is None:
        # Ключ уже был: сделка с этим ключом существует, второй не создаём.
        return {"deal_id": None, "deduplicated": True}
    return {"deal_id": deal_id, "deduplicated": False}


@app.get("/deals")
def list_deals(x_crm_token: str | None = Header(default=None)) -> dict:
    if x_crm_token != MOCK_CRM_TOKEN:
        raise HTTPException(status_code=401, detail="invalid crm token")
    with _engine.begin() as conn:
        rows = conn.execute(
            select(
                _deals_table.c.id,
                _deals_table.c.idempotency_key,
                _deals_table.c.payload,
            )
        ).fetchall()
    deals = []
    for deal_id, key, payload in rows:
        record = json.loads(payload)
        record["deal_id"] = deal_id
        record["idempotency_key"] = key
        deals.append(record)
    return {"deals": deals}


@app.post("/__chaos/{mode}")
def set_chaos(
    mode: str, times: int = 1, x_crm_token: str | None = Header(default=None)
) -> dict:
    if x_crm_token != MOCK_CRM_TOKEN:
        raise HTTPException(status_code=401, detail="invalid crm token")
    if mode not in CHAOS_MODES and mode != "reset":
        raise HTTPException(status_code=404, detail="unknown chaos mode")
    with _lock:
        if mode == "reset":
            with _engine.begin() as conn:
                conn.execute(_deals_table.delete())
            _set_chaos(None, 0)
            return {"status": "reset"}
        _set_chaos(mode, times)
        return {"status": "armed", "mode": mode, "times": times}
