"""Read-only список кейсов с маскированным контактом.

Управление статусами (approve/reject, симуляция отправки) — отдельный
модуль approval-эндпоинтов; здесь только наблюдение.
"""

from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.db import SessionLocal
from app.models import LeadCase
from app.pii import mask_email, mask_phone

router = APIRouter(prefix="/cases", tags=["cases"])


def _get_session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session


SessionDep = Annotated[Session, Depends(_get_session)]


@router.get("", dependencies=[Depends(require_admin)])
def list_cases(session: SessionDep) -> list[dict]:
    rows = (
        session.execute(
            select(LeadCase).order_by(LeadCase.created_at.desc()).limit(200)
        )
        .scalars()
        .all()
    )
    return [
        {
            "id": str(c.id),
            "external_event_id": c.external_event_id,
            "source": c.source.value,
            "status": c.status.value,
            "consent": c.consent_status.value,
            "received_at": c.received_at.isoformat(),
            "contact_name": c.contact_name,
            "phone": mask_phone(c.phone),
            "email": mask_email(c.email),
            "manual_review_reason": c.manual_review_reason,
            "follow_up_due_at": (
                c.follow_up_due_at.isoformat() if c.follow_up_due_at else None
            ),
        }
        for c in rows
    ]
