"""Нормализация payloads трёх источников к единому NormalizedLead.

Чистые функции без БД — легко тестировать и объяснять на защите.
external_event_id выводится детерминированно: одно и то же событие всегда
даёт один и тот же идентификатор, поэтому повторная доставка распознаётся
даже если источник не прислал собственный event_id.
"""

import hashlib
from datetime import UTC, datetime

from app.enums import ConsentStatus, LeadSource
from app.schemas import AvitoMessage, NormalizedLead, TelegramUpdate, WebsiteForm

# Признаки отказа от коммуникации в тексте обращения (в любом источнике).
# Обе формы корня: «отписать/отписаться» (с) и «отпишите/отпишитесь» (ш).
_OPT_OUT_MARKERS = (
    "отписа",
    "отпиш",
    "не звонит",  # не звоните / не звонили
    "не беспоко",  # не беспокойте / не беспокоить
    "не присылай",  # не присылайте
    "удалите мои данные",
    "unsubscribe",
    "стоп рассылк",
)


def detect_opt_out(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in _OPT_OUT_MARKERS)


def normalize_telegram(update: TelegramUpdate) -> NormalizedLead:
    msg = update.message
    received_at = (
        datetime.fromtimestamp(msg.date, tz=UTC)
        if msg.date is not None
        else datetime.now(tz=UTC)
    )
    return NormalizedLead(
        source=LeadSource.TELEGRAM,
        external_event_id=f"tg-{update.update_id}",
        received_at=received_at,
        contact_name=msg.from_.first_name or msg.from_.username,
        body_text=msg.text,
        consent_status=_text_consent(msg.text),
    )


def normalize_website(form: WebsiteForm) -> NormalizedLead:
    if form.consent is True:
        consent = ConsentStatus.OPT_IN
    elif form.consent is False:
        consent = ConsentStatus.OPT_OUT
    else:
        consent = ConsentStatus.UNKNOWN
    # Отказ, написанный в тексте, сильнее чекбокса формы.
    if detect_opt_out(form.message):
        consent = ConsentStatus.OPT_OUT

    event_id = form.event_id or _content_id(
        f"{form.name}|{form.phone}|{form.email}|{form.message}|{form.submitted_at}"
    )
    return NormalizedLead(
        source=LeadSource.WEBSITE,
        external_event_id=f"web-{event_id}",
        received_at=form.submitted_at or datetime.now(tz=UTC),
        contact_name=form.name,
        phone=form.phone,
        email=form.email,
        body_text=form.message,
        consent_status=consent,
    )


def normalize_avito(msg: AvitoMessage) -> NormalizedLead:
    event_id = msg.event_id or f"{msg.chat_id}-{msg.listing_id or 'na'}"
    return NormalizedLead(
        source=LeadSource.AVITO,
        external_event_id=f"avito-{event_id}",
        received_at=msg.created_at or datetime.now(tz=UTC),
        contact_name=msg.user.name,
        phone=msg.user.phone,
        body_text=msg.message,
        consent_status=_text_consent(msg.message),
    )


def _text_consent(text: str) -> ConsentStatus:
    if detect_opt_out(text):
        return ConsentStatus.OPT_OUT
    return ConsentStatus.UNKNOWN


def _content_id(seed: str) -> str:
    """Детерминированный идентификатор из содержимого: одинаковый payload
    всегда даёт одинаковый id, значит повторы ловятся даже без event_id."""
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
