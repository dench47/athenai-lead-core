"""Маскирование персональных данных (PII) для логов и запросов к LLM.

Правило: сырые телефон/e-mail живут только в БД. Всё, что уходит наружу
(логи, промпты модели, ответы API), проходит через маскирование.
"""

import re

_PHONE_RE = re.compile(r"\+?\d[\d\s()\-\u2013]{8,16}\d")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")


def mask_phone(phone: str | None) -> str | None:
    if not phone:
        return phone
    digits = re.sub(r"\D", "", phone)
    if len(digits) < 4:
        return "***"
    return f"+{digits[0]}** ***-**-{digits[-2:]}"


def mask_email(email: str | None) -> str | None:
    if not email or "@" not in email:
        return email
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}"


def mask_text(text: str) -> str:
    """Заменяет все телефоны и e-mail внутри произвольного текста."""
    text = _EMAIL_RE.sub(lambda m: mask_email(m.group(0)) or "", text)
    return _PHONE_RE.sub(_mask_phone_span, text)


def _mask_phone_span(match: re.Match) -> str:
    digits = re.sub(r"\D", "", match.group(0))
    return f"+{digits[0]}** ***-**-{digits[-2:]}"
