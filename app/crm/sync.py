"""Движок синхронизации одобренных кейсов с CRM.

Идемпотентность сквозная: ключ задачи «deal:<case_id>» uniqueness в БД,
тот же ключ уходит в CRM заголовком Idempotency-Key, CRM не создаёт
вторую сделку. Ретраи планировщика: next_attempt_at с нарастающей паузой;
исчерпание попыток — dead-letter и у задачи, и у кейса (видно в UI/аналитике).
"""

from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.crm.client import CRMClient, CrmRejectedError, CrmUnavailableError
from app.enums import CaseStatus, SyncStatus
from app.models import AuditLog, CrmSyncJob, LeadCase

log = structlog.get_logger("crm-sync")

JOB_BATCH_SIZE = 10


def build_crm_payload(case: LeadCase) -> dict:
    """Пакет данных кейса для CRM.

    Контакт уходит полностью (телефон/почта без масок): CRM — внутренняя
    система учёта, в отличие от LLM и логов. Маскирование PII защищает
    внешние вызовы, а не собственную базу клиента.
    """
    qualification = case.qualification
    draft = case.draft
    return {
        "contact": {
            "name": case.contact_name,
            "phone": case.phone,
            "email": case.email,
        },
        "deal": {
            "title": qualification.need,
            "source": case.source.value,
            "quality_score": qualification.quality_score,
            "case_id": str(case.id),
        },
        "draft": {
            "reply_text": draft.reply_text,
            "clarifying_question": draft.clarifying_question,
            "next_action": draft.next_action,
        },
        "task": {
            "title": "Связаться с клиентом по обращению",
            "due_at": (
                case.follow_up_due_at.isoformat() if case.follow_up_due_at else None
            ),
        },
    }


def sync_due_jobs(session: Session, client: CRMClient) -> dict[str, int]:
    """Один проход: взять задачи к отправке и провести их через CRM."""
    stats = {"crm_synced": 0, "crm_retry_scheduled": 0, "crm_dead_letter": 0}
    now = datetime.now(tz=UTC)
    jobs = (
        session.execute(
            select(CrmSyncJob)
            .where(
                CrmSyncJob.status == SyncStatus.PENDING,
                (
                    CrmSyncJob.next_attempt_at.is_(None)
                    | (CrmSyncJob.next_attempt_at <= now)
                ),
            )
            .order_by(CrmSyncJob.created_at)
            .limit(JOB_BATCH_SIZE)
            .with_for_update(skip_locked=True)
        )
        .scalars()
        .all()
    )

    for job in jobs:
        case = session.get(LeadCase, job.case_id)
        if case is None or case.status != CaseStatus.APPROVED:
            # Задача без одобренного кейса — не наш сценарий; не отправляем.
            continue

        payload = build_crm_payload(case)
        try:
            response = client.push_deal(payload, job.idempotency_key)
        except CrmUnavailableError as exc:
            _handle_retry_or_dead_letter(session, job, case, client, str(exc))
            stats[
                "crm_dead_letter" if job.status == SyncStatus.DEAD_LETTER
                else "crm_retry_scheduled"
            ] += 1
            continue
        except CrmRejectedError as exc:
            _mark_dead_letter(session, job, case, "отклонено CRM: " + str(exc))
            stats["crm_dead_letter"] += 1
            continue

        job.status = SyncStatus.SUCCEEDED
        job.attempts += 1
        job.last_error = None
        job.crm_deal_ref = response.get("deal_id")
        case.status = CaseStatus.CRM_SYNCED
        session.add(
            AuditLog(
                case_id=case.id,
                actor="crm-sync",
                action="crm_synced",
                details={
                    "deal_ref": job.crm_deal_ref,
                    "deduplicated": response.get("deduplicated"),
                },
            )
        )
        session.commit()
        stats["crm_synced"] += 1
        log.info("crm_synced", case_id=str(case.id), deal_ref=job.crm_deal_ref)
    return stats


def _handle_retry_or_dead_letter(
    session: Session, job: CrmSyncJob, case: LeadCase, client: CRMClient, error: str
) -> None:
    job.attempts += 1
    job.last_error = error[:1000]
    if job.attempts >= client.max_attempts:
        _mark_dead_letter(session, job, case, error)
        return
    delay = client.retry_delay(job.attempts)
    job.next_attempt_at = datetime.now(tz=UTC) + timedelta(seconds=delay)
    session.commit()
    log.warning(
        "crm_retry_scheduled",
        case_id=str(case.id),
        attempts=job.attempts,
        next_attempt_at=job.next_attempt_at.isoformat(),
    )


def _mark_dead_letter(
    session: Session, job: CrmSyncJob, case: LeadCase, error: str
) -> None:
    job.status = SyncStatus.DEAD_LETTER
    job.last_error = error[:1000]
    case.status = CaseStatus.DEAD_LETTER
    case.manual_review_reason = "CRM недоступна, синк в dead-letter: " + error[:800]
    session.add(
        AuditLog(
            case_id=case.id,
            actor="crm-sync",
            action="crm_dead_letter",
            details={"attempts": job.attempts, "error": error[:500]},
        )
    )
    session.commit()
    log.error("crm_dead_letter", case_id=str(case.id), attempts=job.attempts)
