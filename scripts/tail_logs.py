"""Цветной хвост логов конвейера — для наблюдения и записи демо.

    python scripts/tail_logs.py            # воркер + api
    python scripts/tail_logs.py worker     # только воркер

Запускается на хосте (нужен docker CLI в PATH). JSON-логи раскрашиваются
по смыслу событий: зелёный — успех, жёлтый — повторы/откладывания,
красный — dead-letter и ошибки, сердцебиение — приглушённой строкой.
"""

import json
import os
import subprocess
import sys

# Включает обработку ANSI-кодов в консоли Windows.
os.system("")

GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
RED = "\x1b[31m"
CYAN = "\x1b[36m"
DIM = "\x1b[2m"
RESET = "\x1b[0m"

GOOD_EVENTS = {"case_qualified", "draft_ready", "crm_synced", "event_accepted"}
WARN_EVENTS = {
    "crm_retry_scheduled",
    "qualification_postponed",
    "drafting_postponed",
    "event_duplicate",
}
BAD_EVENTS = {"crm_dead_letter", "qualification_failed", "drafting_failed"}


def event_color(event: str) -> str:
    if event in GOOD_EVENTS:
        return GREEN
    if event in WARN_EVENTS:
        return YELLOW
    if event in BAD_EVENTS:
        return RED
    return CYAN


def render(line: str) -> None:
    line = line.strip()
    if not line.startswith("{"):
        print(DIM + line + RESET)
        return
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        print(DIM + line + RESET)
        return

    event = record.get("event", "")
    timestamp = record.get("timestamp", "")[11:19]
    extras = {
        key: value
        for key, value in record.items()
        if key not in ("event", "service", "level", "timestamp")
    }
    extra_text = " ".join(f"{key}={value}" for key, value in extras.items())

    if event == "worker_heartbeat":
        print(DIM + f"{timestamp}  ♥ воркер жив (heartbeat)" + RESET)
        return
    service = record.get("service", "?")
    color = event_color(event)
    print(f"{timestamp} {DIM}[{service}]{RESET} {color}{event:<24}{RESET} {extra_text}")


def main() -> None:
    services = sys.argv[1:] or ["worker", "api"]
    command = ["docker", "compose", "logs", "-f", "--no-log-prefix", *services]
    print(DIM + "Следим за: " + " ".join(command) + " (Ctrl+C — выход)" + RESET)
    process = subprocess.Popen(
        command, stdout=subprocess.PIPE, text=True, encoding="utf-8", errors="replace"
    )
    try:
        assert process.stdout is not None
        for line in process.stdout:
            render(line)
    except KeyboardInterrupt:
        pass
    finally:
        process.terminate()


if __name__ == "__main__":
    main()
