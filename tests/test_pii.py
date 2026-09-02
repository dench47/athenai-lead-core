"""Тесты маскирования PII: в логи и наружу уходит только маска."""

from app.pii import mask_email, mask_phone, mask_text


def test_mask_phone_keeps_only_country_and_last_digits() -> None:
    assert mask_phone("+7 921 555-01-02") == "+7** ***-**-02"


def test_mask_email_keeps_domain() -> None:
    assert mask_email("maria@company.ru") == "m***@company.ru"


def test_mask_text_replaces_phones_and_emails_inside() -> None:
    text = "Мой телефон +7 921 555-01-02, почта maria@company.ru, жду ответа"
    masked = mask_text(text)
    assert "maria@company.ru" not in masked
    assert "+7 921 555-01-02" not in masked
    assert "m***@company.ru" in masked


def test_mask_none_values_pass_through() -> None:
    assert mask_phone(None) is None
    assert mask_email(None) is None
