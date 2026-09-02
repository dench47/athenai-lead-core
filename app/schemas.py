"""Схемы вебхуков трёх источников и единый нормализованный вид лида.

Контракты строгие (extra='forbid'): неожиданные поля — это невалидный
payload, который фиксируется в журнале, а не то, что мы молча проглатываем.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.enums import ConsentStatus, LeadSource


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- Telegram (упрощённый update Bot API) ---


class TelegramFrom(StrictModel):
    id: int
    first_name: str | None = None
    username: str | None = None


class TelegramMessage(StrictModel):
    message_id: int
    date: int  # unix time
    from_: TelegramFrom = Field(alias="from")
    text: str = Field(min_length=1, max_length=4000)


class TelegramUpdate(StrictModel):
    update_id: int
    message: TelegramMessage


# --- Форма сайта ---


class WebsiteForm(StrictModel):
    event_id: str | None = None  # если сайт не прислал — выведем детерминированно
    submitted_at: datetime | None = None
    name: str | None = None
    phone: str | None = None
    email: str | None = None
    message: str = Field(min_length=1, max_length=4000)
    consent: bool | None = None


# --- Avito-like канал ---


class AvitoUser(StrictModel):
    name: str | None = None
    phone: str | None = None


class AvitoMessage(StrictModel):
    event_id: str | None = None
    chat_id: str
    listing_id: str | None = None
    created_at: datetime | None = None
    user: AvitoUser
    message: str = Field(min_length=1, max_length=4000)


# --- Общие контракты ---


class NormalizedLead(BaseModel):
    """Единый вид обращения из любого источника."""

    source: LeadSource
    external_event_id: str
    received_at: datetime
    contact_name: str | None = None
    phone: str | None = None
    email: str | None = None
    body_text: str
    consent_status: ConsentStatus = ConsentStatus.UNKNOWN


class IngestResponse(BaseModel):
    status: str  # accepted | duplicate
    case_id: str | None = None
    external_event_id: str | None = None
