"""Интеграционные тесты синхронизации с CRM: ретраи, dead-letter, «ровно один».

Обязательные сценарии задания:
- повтор external_event_id -> один LeadCase и одна CRM-сделка;
- 429/500 и недоступность CRM обрабатываются ретраями без дублей;
- после восстановления CRM отложенный кейс синхронизируется ровно один раз.
"""

import httpx
import pytest
from sqlalchemy import select

from app.config import get_settings
from app.crm.client import CRMClient
from app.db import SessionLocal
from app.enums import CaseStatus, SyncStatus
from app.models import CrmSyncJob, LeadCase
from app.worker.pipeline import run_once


@pytest.fixture(autouse=True)
def clean_crm():
    settings = get_settings()
    httpx.post(
        "/".join((settings.mock_crm_url, "__chaos", "reset")),
        headers={"X-CRM-Token": settings.mock_crm_token},
    )


def _fast_client(max_attempts: int = 5) -> CRMClient:
    settings = get_settings()
    return CRMClient(
        base_url=settings.mock_crm_url,
        token=settings.mock_crm_token,
        max_attempts=max_attempts,
        backoff_base=0.0,
        backoff_max=0.0,
    )


def _chaos(mode: str, times: int) -> None:
    settings = get_settings()
    httpx.post(
        "/".join((settings.mock_crm_url, "__chaos", mode)),
        params={"times": times},
        headers={"X-CRM-Token": settings.mock_crm_token},
    )


def _crm_deals() -> list[dict]:
    settings = get_settings()
    response = httpx.get(
        "/".join((settings.mock_crm_url, "deals")),
        headers={"X-CRM-Token": settings.mock_crm_token},
    )
    return response.json()["deals"]


def _prepare_case_via_webhook(client, update_id: int = 7001) -> None:
    settings = get_settings()
    payload = {
        "update_id": update_id,
        "message": {
            "message_id": 1,
            "date": 1756800000,
            "from": {"id": 5, "first_name": "Ольга"},
            "text": "Срочно нужна уборка офиса 100 м2, бюджет 40000 руб",
        },
    }
    response = client.post(
        "/webhooks/telegram",
        json=payload,
        headers={"X-Webhook-Token": settings.webhook_token_telegram},
    )
    assert response.status_code == 200
    with SessionLocal() as session:
        run_once(session)  # квалификация + черновик


def _approve_single_case() -> None:
    """Имитация решения менеджера (эндпоинт approval появится на этапе 8).

    Одобрять можно только кейс, прошедший конвейер до awaiting_approval.
    """
    with SessionLocal() as session:
        case = next(iter(session.execute(select(LeadCase)).scalars()))
        assert case.status == CaseStatus.AWAITING_APPROVAL
        case.status = CaseStatus.APPROVED
        session.add(
            CrmSyncJob(
                case_id=case.id,
                idempotency_key="deal:" + str(case.id),
                status=SyncStatus.PENDING,
            )
        )
        session.commit()


def _single_case() -> LeadCase:
    with SessionLocal() as session:
        cases = list(session.execute(select(LeadCase)).scalars())
        assert len(cases) == 1
        return cases[0]


def test_approved_case_synced_exactly_once(client) -> None:
    _prepare_case_via_webhook(client)
    _approve_single_case()

    with SessionLocal() as session:
        stats = run_once(session, crm_client=_fast_client())

    assert stats["crm_synced"] == 1
    case = _single_case()
    assert case.status == CaseStatus.CRM_SYNCED

    deals = _crm_deals()
    assert len(deals) == 1
    deal = deals[0]
    # В CRM записано всё из задания: контакт, источник, score, черновик, задача
    assert deal["contact"]["name"] == "Ольга"
    assert deal["deal"]["source"] == "telegram"
    assert deal["deal"]["quality_score"] >= 80
    assert deal["draft"]["reply_text"]
    assert deal["task"]["due_at"] is not None

    # Повторный проход конвейера не создаёт вторую сделку.
    with SessionLocal() as session:
        run_once(session, crm_client=_fast_client())
    assert len(_crm_deals()) == 1


def test_duplicate_webhook_single_case_and_single_deal(client) -> None:
    """Обязательный сценарий: дубль события -> одна карточка, одна сделка."""
    settings = get_settings()
    payload = {
        "update_id": 7002,
        "message": {
            "message_id": 1,
            "date": 1756800000,
            "from": {"id": 6, "first_name": "Пётр"},
            "text": "Нужна регулярная уборка, бюджет 20 тыс",
        },
    }
    headers = {"X-Webhook-Token": settings.webhook_token_telegram}
    assert client.post("/webhooks/telegram", json=payload,
                       headers=headers).json()["status"] == "accepted"
    assert client.post("/webhooks/telegram", json=payload,
                       headers=headers).json()["status"] == "duplicate"

    with SessionLocal() as session:
        run_once(session)  # квалификация + черновик единственной карточки
    _approve_single_case()
    with SessionLocal() as session:
        stats = run_once(session, crm_client=_fast_client())

    assert stats["crm_synced"] == 1
    assert len(_crm_deals()) == 1  # сделка ровно одна


def test_transient_429_handled_by_retry_without_duplicates(client) -> None:
    """Обязательный сценарий: 429/5xx обрабатываются ретраями, дублей нет."""
    _prepare_case_via_webhook(client, update_id=7003)
    _approve_single_case()
    _chaos("fail_429", 2)

    with SessionLocal() as session:
        stats = run_once(session, crm_client=_fast_client(max_attempts=3))

    assert stats["crm_synced"] == 1
    assert len(_crm_deals()) == 1


def test_recovery_after_unavailability_syncs_exactly_once(client) -> None:
    """Обязательный сценарий: CRM «падала», восстановилась — синк ровно один."""
    _prepare_case_via_webhook(client, update_id=7004)
    _approve_single_case()
    _chaos("fail_500", 2)  # ровно на один вызов push с внутренними ретраями

    fast = _fast_client(max_attempts=2)
    with SessionLocal() as session:
        stats = run_once(session, crm_client=fast)
    assert stats["crm_retry_scheduled"] == 1
    case = _single_case()
    assert case.status == CaseStatus.APPROVED  # ждёт повторной попытки

    # CRM «восстановилась» (chaos исчерпан): следующий проход синкает.
    with SessionLocal() as session:
        stats = run_once(session, crm_client=fast)
    assert stats["crm_synced"] == 1
    assert _single_case().status == CaseStatus.CRM_SYNCED
    assert len(_crm_deals()) == 1


def test_persistent_failure_ends_in_dead_letter(client) -> None:
    """Обязательный сценарий: исчерпание попыток -> dead-letter, без дублей."""
    _prepare_case_via_webhook(client, update_id=7005)
    _approve_single_case()
    _chaos("fail_500", 99)

    fast = _fast_client(max_attempts=2)
    with SessionLocal() as session:
        run_once(session, crm_client=fast)
    with SessionLocal() as session:
        stats = run_once(session, crm_client=fast)

    assert stats["crm_dead_letter"] == 1
    case = _single_case()
    assert case.status == CaseStatus.DEAD_LETTER
    assert case.manual_review_reason

    with SessionLocal() as session:
        jobs = list(session.execute(select(CrmSyncJob)).scalars())
    assert jobs[0].status == SyncStatus.DEAD_LETTER
    assert jobs[0].last_error
    assert len(_crm_deals()) == 0
