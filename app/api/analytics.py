"""Минимальная аналитика конвейера (6 метрик задания).

Считаем честно, из базы:
- события и уникальные лиды (разница = дубли);
- ведро qualified (прошли оценку) и manual_review;
- среднее время от получения обращения до готовности черновика;
- просроченные follow-up (дедлайн прошёл, кейс ещё не закрыт);
- распределение по источникам.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from app.api.deps import SessionDep, require_admin
from app.enums import CaseStatus
from app.models import Draft, LeadCase, LeadEvent

router = APIRouter(prefix="/analytics", tags=["analytics"])

# Статусы «успешного пути» после квалификации.
_QUALIFIED_BUCKET = (
    CaseStatus.QUALIFIED,
    CaseStatus.DRAFT_READY,
    CaseStatus.AWAITING_APPROVAL,
    CaseStatus.APPROVED,
    CaseStatus.CRM_SYNCED,
)
# Кейс закрыт — follow-up больше не актуален.
_CLOSED = (CaseStatus.CRM_SYNCED, CaseStatus.REJECTED, CaseStatus.OPT_OUT)


@router.get("", dependencies=[Depends(require_admin)])
def analytics(session: SessionDep) -> dict:
    events_received = session.execute(select(func.count()).select_from(LeadEvent)).scalar_one()
    unique_leads = session.execute(select(func.count()).select_from(LeadCase)).scalar_one()

    status_rows = session.execute(
        select(LeadCase.status, func.count()).group_by(LeadCase.status)
    ).all()
    by_status = {status.value: count for status, count in status_rows}
    qualified = sum(by_status.get(status.value, 0) for status in _QUALIFIED_BUCKET)

    source_rows = session.execute(
        select(LeadCase.source, func.count()).group_by(LeadCase.source)
    ).all()
    by_source = {source.value: count for source, count in source_rows}

    # Среднее время до черновика: демо-масштаб — считаем в Python,
    # чтобы формула была видна и проверяема глазами.
    durations = session.execute(
        select(Draft.created_at, LeadCase.received_at).join(
            LeadCase, Draft.case_id == LeadCase.id
        )
    ).all()
    avg_time_to_draft = None
    if durations:
        total = sum(
            (draft_at - received_at).total_seconds()
            for draft_at, received_at in durations
        )
        avg_time_to_draft = round(total / len(durations), 1)

    now = datetime.now(tz=UTC)
    overdue_follow_ups = session.execute(
        select(func.count()).select_from(LeadCase).where(
            LeadCase.follow_up_due_at.is_not(None),
            LeadCase.follow_up_due_at < now,
            LeadCase.status.not_in(_CLOSED),
        )
    ).scalar_one()

    return {
        "events_received": events_received,
        "unique_leads": unique_leads,
        "duplicates": events_received - unique_leads,
        "qualified": qualified,
        "manual_review": by_status.get(CaseStatus.MANUAL_REVIEW.value, 0),
        "rejected": by_status.get(CaseStatus.REJECTED.value, 0),
        "opt_out": by_status.get(CaseStatus.OPT_OUT.value, 0),
        "dead_letter": by_status.get(CaseStatus.DEAD_LETTER.value, 0),
        "avg_time_to_draft_seconds": avg_time_to_draft,
        "overdue_follow_ups": overdue_follow_ups,
        "by_source": by_source,
        "generated_at": now.isoformat(),
    }
