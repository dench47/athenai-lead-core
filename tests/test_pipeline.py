"""Интеграционные тесты конвейера: new -> qualified / manual_review."""

from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.enums import CaseStatus, Urgency
from app.models import LeadCase, Qualification
from app.schemas import LeadForQualification, QualificationResult
from app.worker.pipeline import run_once


def _create_case_via_webhook(
    client, update_id: int = 6001, text: str = "Нужна регулярная уборка офиса 60 м2"
) -> None:
    settings = get_settings()
    payload = {
        "update_id": update_id,
        "message": {
            "message_id": 42,
            "date": 1756800000,
            "from": {"id": 9001, "first_name": "Иван"},
            "text": text,
        },
    }
    response = client.post(
        "/webhooks/telegram",
        json=payload,
        headers={"X-Webhook-Token": settings.webhook_token_telegram},
    )
    assert response.status_code == 200


def _single_case() -> LeadCase:
    with SessionLocal() as session:
        cases = list(session.execute(select(LeadCase)).scalars())
        assert len(cases) == 1
        return cases[0]


class _ManualStubProvider:
    name = "stub-manual"

    def qualify(self, lead: LeadForQualification) -> QualificationResult:
        return QualificationResult(
            need="Заглушка",
            urgency=Urgency.LOW,
            quality_score=50,
            reasons=["Заглушка требует человека"],
            confidence=0.5,
            manual_review=True,
        )


class _BrokenStubProvider:
    """Имитация LLM, вернувшей мусор: score=150, confidence=5."""

    name = "stub-broken"

    def qualify(self, lead: LeadForQualification) -> QualificationResult:
        return QualificationResult.model_construct(
            need="Мусор",
            urgency="low",
            budget=None,
            budget_explicit=False,
            quality_score=150,
            reasons=[],
            confidence=5.0,
            manual_review=False,
        )


def test_pipeline_qualifies_new_case(client) -> None:
    _create_case_via_webhook(client)
    with SessionLocal() as session:
        stats = run_once(session)

    assert stats == {"claimed": 1, "qualified": 1, "manual_review": 0, "errors": 0}
    case = _single_case()
    assert case.status == CaseStatus.QUALIFIED
    assert case.follow_up_due_at is not None

    with SessionLocal() as session:
        rows = list(session.execute(select(Qualification)).scalars())
    assert len(rows) == 1
    assert rows[0].provider == "mock"
    assert rows[0].need == "Уборка помещений"


def test_stub_manual_provider_routes_to_manual_review(client) -> None:
    _create_case_via_webhook(client, update_id=6002)
    with SessionLocal() as session:
        stats = run_once(session, provider=_ManualStubProvider())

    assert stats["manual_review"] == 1
    case = _single_case()
    assert case.status == CaseStatus.MANUAL_REVIEW
    assert "Заглушка" in case.manual_review_reason


def test_invalid_provider_result_goes_to_manual_review(client) -> None:
    """Обязательный сценарий: невалидный ответ модели -> manual_review."""
    _create_case_via_webhook(client, update_id=6003)
    with SessionLocal() as session:
        stats = run_once(session, provider=_BrokenStubProvider())

    assert stats["manual_review"] == 1
    case = _single_case()
    assert case.status == CaseStatus.MANUAL_REVIEW
    assert case.manual_review_reason  # причина зафиксирована


def test_opt_out_cases_are_never_claimed(client) -> None:
    _create_case_via_webhook(client, update_id=6004, text="Отпишите меня, не звоните")
    with SessionLocal() as session:
        stats = run_once(session)

    assert stats["claimed"] == 0
    case = _single_case()
    assert case.status == CaseStatus.OPT_OUT
