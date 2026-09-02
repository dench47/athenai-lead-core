"""Перечисления домена.

Статусы кейса образуют конвейер из задания:
new -> qualified -> draft_ready -> awaiting_approval -> approved -> crm_synced
плюс три боковые ветки: manual_review, opt_out, dead_letter.
"""

from enum import StrEnum


class LeadSource(StrEnum):
    TELEGRAM = "telegram"
    WEBSITE = "website"
    AVITO = "avito"


class EventOutcome(StrEnum):
    """Судьба сырого события на входе."""

    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    INVALID = "invalid"


class ConsentStatus(StrEnum):
    UNKNOWN = "unknown"
    OPT_IN = "opt_in"
    OPT_OUT = "opt_out"


class CaseStatus(StrEnum):
    NEW = "new"
    QUALIFIED = "qualified"
    DRAFT_READY = "draft_ready"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    CRM_SYNCED = "crm_synced"
    MANUAL_REVIEW = "manual_review"
    OPT_OUT = "opt_out"
    DEAD_LETTER = "dead_letter"


class Urgency(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SyncStatus(StrEnum):
    """Статус задачи синхронизации с CRM."""

    PENDING = "pending"
    SUCCEEDED = "succeeded"
    DEAD_LETTER = "dead_letter"
