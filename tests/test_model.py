"""Проверки модели данных: состав таблиц и защита от дублей на уровне схемы."""

import sqlalchemy
from sqlalchemy import CheckConstraint, UniqueConstraint

from app import models
from app.enums import CaseStatus

EXPECTED_TABLES = {
    "tenants",
    "lead_events",
    "lead_cases",
    "qualifications",
    "drafts",
    "crm_sync_jobs",
    "audit_log",
}


def test_metadata_contains_exactly_core_tables() -> None:
    assert set(models.Base.metadata.tables) == EXPECTED_TABLES


def test_case_statuses_cover_assignment_pipeline() -> None:
    chain = [
        "new",
        "qualified",
        "draft_ready",
        "awaiting_approval",
        "approved",
        "crm_synced",
    ]
    assert [CaseStatus(s).value for s in chain] == chain
    assert {"manual_review", "opt_out", "dead_letter"} <= {s.value for s in CaseStatus}


def test_unique_constraints_protect_against_duplicates() -> None:
    """Дубль события должен ломать INSERT карточки, а не создавать вторую.

    Журнал lead_events, наоборот, хранит все попытки (включая повторы) —
    уникальности там быть не должно.
    """
    cases = models.Base.metadata.tables["lead_cases"]
    uq_columns = {
        tuple(uq.columns.keys())
        for uq in cases.constraints
        if isinstance(uq, UniqueConstraint)
    }
    assert ("tenant_id", "external_event_id") in uq_columns

    events = models.Base.metadata.tables["lead_events"]
    events_uq = {
        tuple(uq.columns.keys())
        for uq in events.constraints
        if isinstance(uq, UniqueConstraint)
    }
    assert ("tenant_id", "external_event_id") not in events_uq


def test_crm_sync_idempotency_key_is_unique() -> None:
    table = models.Base.metadata.tables["crm_sync_jobs"]
    unique_columns = {c.name for c in table.columns if c.unique}
    assert "idempotency_key" in unique_columns


def test_qualification_range_constraints_exist() -> None:
    table = models.Base.metadata.tables["qualifications"]
    names = {ck.name for ck in table.constraints if isinstance(ck, CheckConstraint)}
    # Имена раскрыты через конвенцию: ck_<таблица>_<имя_ограничения>
    assert {
        "ck_qualifications_quality_score_range",
        "ck_qualifications_confidence_range",
    } <= names


def test_enum_columns_use_check_constraints() -> None:
    """Перечисления — VARCHAR + CHECK: добавление значения не требует ALTER TYPE."""
    table = models.Base.metadata.tables["lead_cases"]
    assert isinstance(table.columns["status"].type, sqlalchemy.Enum)
    assert table.columns["status"].type.create_constraint is True
