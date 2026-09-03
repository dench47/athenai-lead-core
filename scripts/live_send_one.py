"""Отправка одного демо-лида с честным UTF-8 (для записи видео и репетиций).

На хосте (нужен httpx из .venv):
    .venv\\Scripts\\python scripts\\live_send_one.py
    .venv\\Scripts\\python scripts\\live_send_one.py --text "Нужна уборка..."

Внутри контейнера:
    docker compose run --rm -e API_BASE=http://api:8000 api \
        python -m scripts.live_send_one
"""

import argparse
import os
import random
import time

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(description="Отправить один демо-лид")
    parser.add_argument(
        "--text",
        default="Срочно нужна уборка офиса 200 м2 после ремонта, бюджет 80000 руб",
    )
    args = parser.parse_args()

    base = os.getenv("API_BASE", "http://localhost:8080")
    payload = {
        "update_id": random.randint(90000, 99999),
        "message": {
            "message_id": 1,
            "date": int(time.time()),
            "from": {"id": 42, "first_name": "Демо"},
            "text": args.text,
        },
    }
    response = httpx.post(
        base + "/webhooks/telegram",
        json=payload,
        headers={"X-Webhook-Token": "dev-token-telegram"},
        timeout=10.0,
    )
    print(response.json())


if __name__ == "__main__":
    main()
