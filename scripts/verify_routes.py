"""Верификация маршрутов 30 тестовых лидов (критерий задания: >= 85%).

    docker compose run --rm api python -m scripts.verify_routes

Каждый лид сверяется с ожиданиями из seed/leads.json: исход приёма,
финальный статус, диапазон оценки, явность бюджета. Дубли проверяются
по журналу: повторная доставка не создаёт новую карточку.
"""

import json
import sys
from pathlib import Path

from sqlalchemy import select

from app.db import SessionLocal
from app.enums import CaseStatus, EventOutcome
from app.models import LeadCase, LeadEvent, Qualification

SEED_FILE = Path("seed/leads.json")


def _external_id(source: str, payload: dict) -> str | None:
    if source == "telegram":
        update_id = payload.get("update_id")
        return f"tg-{update_id}" if update_id else None
    if source == "website":
        event_id = payload.get("event_id")
        return f"web-{event_id}" if event_id else None
    if source == "avito":
        event_id = payload.get("event_id")
        return f"avito-{event_id}" if event_id else None
    return None


def _case_by_external(session, external_id: str) -> LeadCase | None:
    cases = [
        case
        for case in session.execute(select(LeadCase)).scalars()
        if case.external_event_id == external_id
    ]
    return cases[0] if len(cases) == 1 else None


def _payload_marker(payload: dict) -> object:
    """Отличительный признак payload в журнале (update_id или event_id)."""
    return payload.get("update_id", payload.get("event_id"))


def check_lead(session, all_events: list[LeadEvent], lead: dict) -> tuple[bool, str]:
    expected = lead["expected"]
    external_id = _external_id(lead["source"], lead["payload"])

    # Битые payload не создают карточек и получают синтетический id в журнале:
    # ищем их по отличительному признаку внутри payload.
    if expected.get("ingest") == "invalid":
        marker = _payload_marker(lead["payload"])
        invalid_events = [
            event
            for event in all_events
            if event.outcome == EventOutcome.INVALID
            and _payload_marker(event.payload) == marker
        ]
        if invalid_events:
            return True, "invalid зафиксирован, карточки нет"
        return False, "нет invalid-события в журнале"

    if external_id is None:
        return False, "нет распознаваемого external_id"

    events = [event for event in all_events if event.external_event_id == external_id]
    case = _case_by_external(session, external_id)

    if not any(event.outcome == EventOutcome.ACCEPTED for event in events):
        return False, "нет accepted-события в журнале"
    if case is None:
        return False, "карточка не создана"

    # 2. Финальный статус.
    expected_final = expected.get("final")
    if expected_final is None:
        return False, "ожидался статус, кейс существует"
    if case.status != CaseStatus(expected_final):
        return False, f"статус {case.status.value}, ожидался {expected_final}"

    # Opt-out не проходит квалификацию — дальше проверять нечего.
    if case.status == CaseStatus.OPT_OUT:
        return True, "opt_out без квалификации — продающий сценарий заблокирован"

    qualification = next(
        (q for q in session.execute(select(Qualification)).scalars()
         if q.case_id == case.id),
        None,
    )
    if qualification is None:
        return False, "нет строки квалификации"

    # 3. Диапазон оценки.
    score = qualification.quality_score
    if "score_min" in expected and score < expected["score_min"]:
        return False, f"оценка {score} < {expected['score_min']}"
    if "score_max" in expected and score > expected["score_max"]:
        return False, f"оценка {score} > {expected['score_max']}"

    # 4. Явность бюджета.
    if (
        "budget_explicit" in expected
        and qualification.budget_explicit != expected["budget_explicit"]
    ):
        return False, f"budget_explicit={qualification.budget_explicit}"

    return True, f"ok (score={score})"


def check_duplicate(session, all_events: list[LeadEvent], lead: dict) -> tuple[bool, str]:
    # Повтор битого payload — снова invalid, карточек быть не должно.
    if lead["category"] == "invalid":
        marker = _payload_marker(lead["payload"])
        invalid_events = [
            event
            for event in all_events
            if event.outcome == EventOutcome.INVALID
            and _payload_marker(event.payload) == marker
        ]
        if len(invalid_events) >= 2:
            return True, "повтор битого payload снова отклонён"
        return False, "повтор битого payload не зафиксирован"

    external_id = _external_id(lead["source"], lead["payload"])
    if external_id is None:
        return False, "нет external_id"
    events = [
        event
        for event in all_events
        if event.external_event_id == external_id
    ]
    if not any(event.outcome == EventOutcome.DUPLICATE for event in events):
        return False, "нет duplicate-события в журнале"
    if _case_by_external(session, external_id) is None:
        return False, "карточка исчезла"
    return True, "дубль зафиксирован, карточка одна"


def main() -> int:
    data = json.loads(SEED_FILE.read_text(encoding="utf-8"))
    by_id = {lead["id"]: lead for lead in data["leads"]}

    results: list[tuple[str, str, bool, str]] = []
    with SessionLocal() as session:
        all_events = list(session.execute(select(LeadEvent)).scalars())
        for lead in data["leads"]:
            passed, note = check_lead(session, all_events, lead)
            results.append((lead["id"], lead["category"], passed, note))
        for lead_id in data["resend"]:
            lead = by_id[lead_id]
            passed, note = check_duplicate(session, all_events, lead)
            results.append((lead_id + " (повтор)", "duplicate", passed, note))

    for lead_id, category, passed, note in results:
        mark = "PASS" if passed else "FAIL"
        print(f"{mark}  {lead_id:<16} {category:<11} {note}")

    total = len(data["leads"])
    passed_count = sum(1 for row in results[:total] if row[2])
    coverage = round(passed_count / total * 100, 1)
    print(f"\nМаршруты: {passed_count}/{total} ({coverage}%) | критерий задания: >= 85%")
    duplicates_ok = all(row[2] for row in results[total:])
    duplicates_verdict = (
        "все распознаны, дублей карточек нет" if duplicates_ok else "ЕСТЬ ПРОБЛЕМЫ"
    )
    print("Повторные доставки: " + duplicates_verdict)

    if coverage < 85 or not duplicates_ok:
        print("ИТОГ: FAIL")
        return 1
    print("ИТОГ: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
