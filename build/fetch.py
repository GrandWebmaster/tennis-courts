#!/usr/bin/env python3
"""Собирает data.json со свободными слотами кортов из открытого API DiKiDi.

Запускается по расписанию из GitHub Actions. Страница на Pages читает только
готовый data.json и никуда наружу не ходит.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ENDPOINT = "https://dikidi.net/ru/mobile/ajax/newrecord/get_datetimes/"
USER_AGENT = "tennis-courts-calendar/1.0 (+https://github.com/)"
TIMEOUT = 20
ATTEMPTS = 3

ROOT = Path(__file__).resolve().parent.parent


class DikidiError(RuntimeError):
    pass


def fetch_day(company_id: int, service_id: int, day: str) -> dict[int, list[str]]:
    """Свободные получасовки всех кортов за одну дату: master_id -> ['07:00', ...].

    Без master_id DiKiDi отдаёт сразу все корты, поэтому на день хватает
    одного запроса.
    """
    query = urllib.parse.urlencode(
        {
            "company_id": company_id,
            "service_id[]": service_id,
            "day_month": day,
            "with_first": 1,
        }
    )
    request = urllib.request.Request(
        f"{ENDPOINT}?{query}",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )

    last_error = "причина неизвестна"
    for attempt in range(ATTEMPTS):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                payload = json.loads(response.read().decode("utf-8"))
            break
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            last_error = str(error)
    else:
        raise DikidiError(f"{day}: {last_error}")

    if payload.get("error", {}).get("code") not in (0, "0"):
        raise DikidiError(f"{day}: DiKiDi вернул {payload.get('error')}")

    slots: dict[int, list[str]] = {}
    for master_id, stamps in (payload.get("data", {}).get("times") or {}).items():
        # Ключ 0 — «любой мастер», объединение остальных.
        if str(master_id) == "0" or not isinstance(stamps, list):
            continue
        # with_first=1 умеет подсунуть ближайшую дату вместо запрошенной.
        times = sorted(s[11:16] for s in stamps if isinstance(s, str) and s.startswith(day + " "))
        slots[int(master_id)] = times

    return slots


def to_minutes(clock: str) -> int:
    hours, minutes = clock.split(":")
    return int(hours) * 60 + int(minutes)


def to_clock(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def derive(slots: list[str], duration: int, step: int) -> list[str]:
    """Начала слотов нужной длительности.

    DiKiDi отдаёт получасовки. Час свободен там, где свободны две смежные,
    полтора часа — три. Проверено на живом API: совпадает с выдачей часовой
    и полуторачасовой услуг.
    """
    if duration % step:
        raise ValueError(f"длительность {duration} не кратна шагу {step}")

    needed = duration // step
    free = {to_minutes(s) for s in slots}
    starts = [m for m in sorted(free) if all(m + i * step in free for i in range(1, needed))]

    return [to_clock(m) for m in starts]


def is_day_off(day: str, holidays: list[str]) -> bool:
    return day in holidays or date.fromisoformat(day).isoweekday() >= 6


def main() -> int:
    config = json.loads((ROOT / "build" / "config.json").read_text(encoding="utf-8"))

    # Часовой пояс центра: день, с которого начинается таблица, считается по нему,
    # а не по UTC, в котором живёт раннер GitHub.
    offset = timezone(timedelta(hours=3))
    today = datetime.now(offset).date()
    days = [(today + timedelta(days=i)).isoformat() for i in range(config["days"])]

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(
            pool.map(
                lambda day: (day, fetch_day(config["company_id"], config["probe_service_id"], day)),
                days,
            )
        )

    raw = dict(results)
    courts = {court["master_id"]: court["n"] for court in config["courts"]}
    step = config["slot_step"]

    grids: dict[str, dict[str, dict[str, list[int]]]] = {}
    for duration in config["durations"]:
        grid: dict[str, dict[str, list[int]]] = {}
        for day in days:
            cells: dict[str, list[int]] = {}
            for master_id, number in courts.items():
                for time in derive(raw[day].get(master_id, []), duration, step):
                    cells.setdefault(time, []).append(number)
            grid[day] = {time: sorted(cells[time]) for time in sorted(cells)}
        grids[str(duration)] = grid

    data = {
        "generated_at": datetime.now(offset).isoformat(timespec="seconds"),
        "company": {
            "name": config["company_name"],
            "city": config["company_city"],
            "booking_base": f"https://dikidi.net/ru/record/{config['company_id']}",
        },
        "courts": config["courts"],
        "days": days,
        "day_off": {day: is_day_off(day, config["holidays"]) for day in days},
        "grid_from": config["grid_from"],
        "grid_to": config["grid_to"],
        "slot_step": step,
        "durations": config["durations"],
        "default_duration": config["default_duration"],
        "tariffs": config["tariffs"],
        "grids": grids,
    }

    target = ROOT / "data.json"
    target.write_text(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    total = sum(len(numbers) for grid in grids.values() for cells in grid.values() for numbers in cells.values())
    print(f"дней: {len(days)}, слотов во всех сетках: {total}, файл: {target.stat().st_size} байт")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except DikidiError as error:
        print(f"не собралось: {error}", file=sys.stderr)
        sys.exit(1)
