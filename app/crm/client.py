"""HTTP-клиент CRM: ретраи с экспоненциальной паузой и идемпотентность.

Разделение ошибок:
- 429/5xx/таймаут/сеть — временные: повторяем с нарастающей паузой
  и джиттером; исчерпали попытки — CrmUnavailableError (задача
  перепланируется, кейс не теряется);
- остальные 4xx — постоянные: CrmRejectedError (повтор бессмыслен,
  задача в dead-letter).
"""

import random
import time
from collections.abc import Callable

import httpx


class CrmUnavailableError(Exception):
    """CRM недоступна после всех повторов (таймаут, 429, 5xx, сеть)."""


class CrmRejectedError(Exception):
    """CRM отклонила запрос насовсем (4xx, кроме 429)."""


class CRMClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout_seconds: float = 5.0,
        max_attempts: int = 5,
        backoff_base: float = 1.0,
        backoff_max: float = 60.0,
        http_client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._max_attempts = max_attempts
        self._backoff_base = backoff_base
        self._backoff_max = backoff_max
        self._sleep = sleep
        self._client = http_client or httpx.Client(timeout=timeout_seconds)

    @property
    def max_attempts(self) -> int:
        return self._max_attempts

    def push_deal(self, payload: dict, idempotency_key: str) -> dict:
        """POST /deals с ключом идемпотентности.

        Возвращает ответ CRM: {"deal_id": ..., "deduplicated": ...}.
        """
        last_error = "unknown"
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._client.post(
                    "/".join((self._base_url, "deals")),
                    json=payload,
                    headers={
                        "X-CRM-Token": self._token,
                        "Idempotency-Key": idempotency_key,
                    },
                )
                if response.status_code == 429 or response.status_code >= 500:
                    last_error = "HTTP " + str(response.status_code)
                elif response.status_code >= 400:
                    raise CrmRejectedError("HTTP " + str(response.status_code))
                else:
                    return response.json()
            except httpx.TimeoutException:
                last_error = "timeout"
            except httpx.TransportError:
                last_error = "connection error"
            if attempt < self._max_attempts:
                self._sleep(self._backoff_delay(attempt))
        raise CrmUnavailableError("CRM недоступна после повторов: " + last_error)

    def _backoff_delay(self, attempt: int) -> float:
        delay = min(self._backoff_max, self._backoff_base * (2 ** (attempt - 1)))
        return delay + random.uniform(0, 0.5)  # джиттер против синхронных штормов

    def retry_delay(self, attempt: int) -> float:
        """Пауза до следующей попытки без джиттера — для планировщика задач."""
        return min(self._backoff_max, self._backoff_base * (2 ** (attempt - 1)))


def build_default_client() -> CRMClient:
    """Клиент из настроек окружения (URL и токен mock-CRM)."""
    from app.config import get_settings

    settings = get_settings()
    return CRMClient(
        base_url=settings.mock_crm_url,
        token=settings.mock_crm_token,
        max_attempts=settings.crm_retry_max_attempts,
        backoff_base=settings.crm_backoff_base_seconds,
        backoff_max=settings.crm_backoff_max_seconds,
    )
