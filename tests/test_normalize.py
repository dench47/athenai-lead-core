"""Юнит-тесты нормализации трёх источников."""

import time

from app.enums import ConsentStatus, LeadSource
from app.normalize import (
    detect_opt_out,
    normalize_avito,
    normalize_telegram,
    normalize_website,
)
from app.schemas import (
    AvitoMessage,
    AvitoUser,
    TelegramFrom,
    TelegramMessage,
    TelegramUpdate,
    WebsiteForm,
)


def _telegram(text: str, update_id: int = 100) -> TelegramUpdate:
    return TelegramUpdate(
        update_id=update_id,
        message=TelegramMessage(
            message_id=42,
            date=1756800000,
            **{"from": TelegramFrom(id=9001, first_name="Иван", username="ivan_p")},
            text=text,
        ),
    )


def test_telegram_normalizes_fields() -> None:
    lead = normalize_telegram(_telegram("Нужна уборка офиса 120 м2"))
    assert lead.source == LeadSource.TELEGRAM
    assert lead.external_event_id == "tg-100"
    assert lead.contact_name == "Иван"
    assert lead.consent_status == ConsentStatus.UNKNOWN


def test_telegram_without_date_uses_current_time() -> None:
    """Источник не прислал время — берём текущее (не 1970-й и не прошлое)."""
    update = TelegramUpdate(
        update_id=101,
        message=TelegramMessage(
            message_id=1,
            date=None,
            **{"from": TelegramFrom(id=1, first_name="Без Даты")},
            text="Нужна уборка офиса",
        ),
    )
    lead = normalize_telegram(update)
    assert abs(lead.received_at.timestamp() - time.time()) < 60


def test_telegram_opt_out_words_set_consent() -> None:
    lead = normalize_telegram(_telegram("Не звоните мне больше"))
    assert lead.consent_status == ConsentStatus.OPT_OUT


def test_website_consent_checkbox_mapping() -> None:
    opt_in = normalize_website(WebsiteForm(message="Нужен расчёт", consent=True))
    opt_out = normalize_website(WebsiteForm(message="Нужен расчёт", consent=False))
    unknown = normalize_website(WebsiteForm(message="Нужен расчёт"))
    assert opt_in.consent_status == ConsentStatus.OPT_IN
    assert opt_out.consent_status == ConsentStatus.OPT_OUT
    assert unknown.consent_status == ConsentStatus.UNKNOWN


def test_website_derived_event_id_is_deterministic() -> None:
    """Одинаковый payload без event_id даёт одинаковый идентификатор —
    повторная доставка распознается как дубль."""
    first = normalize_website(
        WebsiteForm(name="Мария", phone="+7 921 555-01-02", message="Расчёт уборки")
    )
    second = normalize_website(
        WebsiteForm(name="Мария", phone="+7 921 555-01-02", message="Расчёт уборки")
    )
    assert first.external_event_id == second.external_event_id
    assert first.external_event_id.startswith("web-")


def test_website_opt_out_text_overrides_checkbox() -> None:
    lead = normalize_website(
        WebsiteForm(message="Отпишите меня от рассылки", consent=True)
    )
    assert lead.consent_status == ConsentStatus.OPT_OUT


def test_avito_uses_source_event_id() -> None:
    msg = AvitoMessage(
        chat_id="chat-1",
        listing_id="svc-01",
        user=AvitoUser(name="Ольга"),
        message="Делаете уборку после ремонта?",
    )
    lead = normalize_avito(msg)
    assert lead.source == LeadSource.AVITO
    assert lead.external_event_id == "avito-chat-1-svc-01"
    assert lead.consent_status == ConsentStatus.UNKNOWN


def test_detect_opt_out_no_false_positive_on_normal_request() -> None:
    assert detect_opt_out("Нужно срочно убрать офис, бюджет 50 тысяч") is False
