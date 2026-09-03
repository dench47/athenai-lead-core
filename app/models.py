"""Модель данных: семь таблиц ядра.

Ключевые решения (для защиты):

1. Защита от дублей — на уровне БД. UNIQUE(tenant_id, external_event_id)
   стоит и на журнале событий, и на карточках: даже если два одинаковых
   вебхука придут одновременно, второй INSERT нарушит ограничение, а не
   создаст вторую карточку и вторую CRM-сделку.
2. PII (телефон, e-mail) — в отдельных колонках lead_cases. В запросы к LLM
   и в логи эти поля не попадают (маскируются на этапе сборки промпта).
3. Сырой журнал (lead_events) отделён от карточек (lead_cases): история
   «что приходило» не зависит от судьбы карточки и нужна для аналитики
   (событий получено / уникальных лидов).
4. Перечисления — VARCHAR с валидацией на уровне приложения (pydantic/enums),
   а не нативные enum PostgreSQL и не CHECK-ограничения: добавление нового
   значения статуса не требует ни ALTER TYPE, ни миграции схемы.
5. crm_sync_jobs.idempotency_key — ключ идемпотентности синка: CRM-адаптер
   шлёт его с каждым повтором, и CRM не создаёт сделку дважды.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    MetaData,
    Numeric,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.enums import (
    CaseStatus,
    ConsentStatus,
    EventOutcome,
    LeadSource,
    SyncStatus,
    Urgency,
)

# Фиксированный идентификатор демо-арендатора AthenAI DemoService.
# Синтетический UUID, никаких реальных данных.
DEFAULT_TENANT_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")

# Стабильные имена ограничений — обязательное правило для безболезненных
# будущих миграций (имена не генерируются заново при каждом изменении).
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _enum(enum_cls: type) -> Enum:
    # values_callable: в БД храним значения ('telegram'), а не имена ('TELEGRAM')
    return Enum(
        enum_cls,
        native_enum=False,
        create_constraint=True,
        length=32,
        values_callable=lambda cls: [m.value for m in cls],
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
        nullable=False,
    )


class Tenant(TimestampMixin, Base):
    """Бизнес-профиль (мультиарендность): второй клиент подключается
    новой строкой и конфигурацией, ядро не копируется."""

    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class LeadEvent(Base):
    """Сырой журнал входящих событий: что пришло, от кого и чем закончилось.

    Журнал хранит ВСЕ попытки доставки, включая повторы (outcome=duplicate) —
    разница между «получено событий» и «уникальных лидов» и есть метрика
    дублей. Защита от дублей карточек — UNIQUE на lead_cases, не здесь.
    """

    __tablename__ = "lead_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", name="fk_lead_events_tenant_id_tenants"), nullable=False
    )
    source: Mapped[LeadSource] = mapped_column(_enum(LeadSource), nullable=False)
    external_event_id: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    outcome: Mapped[EventOutcome] = mapped_column(_enum(EventOutcome), nullable=False)
    error_detail: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class LeadCase(TimestampMixin, Base):
    """Единая сущность обращения из трёх источников — «карточка лида»."""

    __tablename__ = "lead_cases"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "external_event_id", name="uq_lead_cases_external_event_id"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id", name="fk_lead_cases_tenant_id_tenants"), nullable=False
    )
    external_event_id: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[LeadSource] = mapped_column(_enum(LeadSource), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Контакт. Телефон и e-mail — PII: отдельные колонки, в логи и в LLM
    # уходит только маскированная копия.
    contact_name: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    body_text: Mapped[str] = mapped_column(Text, nullable=False)

    consent_status: Mapped[ConsentStatus] = mapped_column(
        _enum(ConsentStatus), default=ConsentStatus.UNKNOWN, nullable=False
    )
    status: Mapped[CaseStatus] = mapped_column(
        _enum(CaseStatus), default=CaseStatus.NEW, nullable=False
    )
    manual_review_reason: Mapped[str | None] = mapped_column(Text)
    follow_up_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    qualification: Mapped["Qualification | None"] = relationship(back_populates="case")
    draft: Mapped["Draft | None"] = relationship(back_populates="case")
    crm_sync_job: Mapped["CrmSyncJob | None"] = relationship(back_populates="case")


class Qualification(Base):
    """Результат квалификации — строго по схеме из задания.

    budget заполняется только если клиент явно назвал сумму
    (флаг budget_explicit); модель не имеет права его «додумывать».
    """

    __tablename__ = "qualifications"
    __table_args__ = (
        CheckConstraint("quality_score BETWEEN 0 AND 100", name="quality_score_range"),
        CheckConstraint("confidence BETWEEN 0 AND 1", name="confidence_range"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("lead_cases.id", name="fk_qualifications_case_id_lead_cases"),
        unique=True, nullable=False,
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)  # mock | deepseek
    need: Mapped[str] = mapped_column(Text, nullable=False)
    urgency: Mapped[Urgency] = mapped_column(_enum(Urgency), nullable=False)
    budget: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    budget_explicit: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    quality_score: Mapped[int] = mapped_column(Integer, nullable=False)
    reasons: Mapped[list] = mapped_column(JSONB, nullable=False)
    confidence: Mapped[float] = mapped_column(nullable=False)
    manual_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    case: Mapped[LeadCase] = relationship(back_populates="qualification")


class Draft(Base):
    """Безопасный продающий черновик для менеджера.

    guardrail_report фиксирует, какие запреты (цены/скидки/сроки/гарантии)
    были проверены и что найдено.
    """

    __tablename__ = "drafts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("lead_cases.id", name="fk_drafts_case_id_lead_cases"),
        unique=True, nullable=False,
    )
    reply_text: Mapped[str] = mapped_column(Text, nullable=False)
    clarifying_question: Mapped[str] = mapped_column(Text, nullable=False)
    next_action: Mapped[str] = mapped_column(Text, nullable=False)
    guardrail_report: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    case: Mapped[LeadCase] = relationship(back_populates="draft")


class CrmSyncJob(TimestampMixin, Base):
    """Задача записи кейса в CRM: идемпотентная, с ретраями и dead-letter."""

    __tablename__ = "crm_sync_jobs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("lead_cases.id", name="fk_crm_sync_jobs_case_id_lead_cases"),
        unique=True, nullable=False,
    )
    # Ключ идемпотентности: CRM дедуплицирует по нему при любых повторах.
    idempotency_key: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    status: Mapped[SyncStatus] = mapped_column(
        _enum(SyncStatus), default=SyncStatus.PENDING, nullable=False
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    crm_deal_ref: Mapped[str | None] = mapped_column(Text)  # ID сделки в CRM
    case: Mapped[LeadCase] = relationship(back_populates="crm_sync_job")


class AuditLog(Base):
    """Журнал действий: кто, что и когда сделал (approval, отправка, отказы)."""

    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    case_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("lead_cases.id", name="fk_audit_log_case_id_lead_cases")
    )
    actor: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
