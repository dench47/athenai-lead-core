"""Отправка одного демо-лида с честным UTF-8 (для записи видео).

    docker compose run --rm api python -m scripts.live_send_one
"""

import time

import httpx

PAYLOAD = {
    "update_id": 99001,
    "message": {
        "message_id": 1,
        "date": int(time.time()),
        "from": {"id": 42, "first_name": "Демо"},
        "text": "Срочно нужна уборка офиса 200 м2 после ремонта, бюджет 80000 руб",
    },
}


def main() -> None:
    response = httpx.post(
        "http://api:8000/webhooks/telegram",
        json=PAYLOAD,
        headers={"X-Webhook-Token": "dev-token-telegram"},
        timeout=10.0,
    )
    print(response.json())


if __name__ == "__main__":
    main()
