#!/usr/bin/env python3
"""Собирает data.json со свободными слотами кортов из открытого API онлайн-записи.

Запускается по расписанию из GitHub Actions. Страница на Pages читает только
готовый data.json и никуда наружу не ходит.

Источников несколько: теннисные корты и падел заведены как разные компании,
и ведут себя они по-разному — см. STRATEGY ниже.
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
WORKERS = 10

ROOT = Path(__file__).resolve().parent.parent

# Как добывать слоты для источника:
#
#   derive       — хватает одной 30-минутной услуги на день. Тарифное окно услуги
#                  на выдачу не влияет, а час и полтора выводятся из получасовок:
#                  час свободен там, где свободны две смежные, полтора — три.
#                  Проверено на теннисных кортах, совпадает с ответами API.
#
#   per_duration — так нельзя. У падел-корта сетка стартов своя: бывает, что час
#                  с 07:00 свободен, а получасовки 07:30 в выдаче нет. Проверка
#                  на пяти днях не совпала ни разу, поэтому каждая длительность
#                  запрашивается своей услугой.


class DikidiError(RuntimeError):
    pass


def fetch_day(company_id: int, services: list[int], day: str) -> dict[int, list[str]]:
    """Свободные слоты всех кортов компании за одну дату: master_id -> ['07:00', ...].

    Без master_id ответ содержит сразу все корты, поэтому на день хватает
    одного обращения. Услуг может быть несколько: сервис складывает их
    длительности, так у падела набираются полтора часа из часа и получаса.
    """
    query = urllib.parse.urlencode(
        [("company_id", company_id)]
        + [("service_id[]", s) for s in services]
        + [("day_month", day), ("with_first", 1)]
    )
    request = urllib.request.Request(
        f"{ENDPOINT}?{query}",
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )

    last_error = "причина неизвестна"
    for _ in range(ATTEMPTS):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                payload = json.loads(response.read().decode("utf-8"))
            break
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            last_error = str(error)
    else:
        raise DikidiError(f"{day}: {last_error}")

    if payload.get("error", {}).get("code") not in (0, "0"):
        raise DikidiError(f"{day}: сервис вернул {payload.get('error')}")

    slots: dict[int, list[str]] = {}
    for master_id, stamps in (payload.get("data", {}).get("times") or {}).items():
        # Ключ 0 — «любой специалист», объединение остальных.
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
    """Начала слотов нужной длительности из получасовок."""
    if duration % step:
        raise ValueError(f"длительность {duration} не кратна шагу {step}")

    needed = duration // step
    free = {to_minutes(s) for s in slots}
    starts = [m for m in sorted(free) if all(m + i * step in free for i in range(1, needed))]

    return [to_clock(m) for m in starts]


def is_day_off(day: str, holidays: list[str]) -> bool:
    return day in holidays or date.fromisoformat(day).isoweekday() >= 6


def on_grid(time: str, start: str, end: str, step: int, duration: int) -> bool:
    """Попадает ли старт в сетку, которую рисует страница.

    Сервис записи иногда отдаёт «ближайшее доступное время» от текущего момента:
    сегодня у падела это были 15:05 и 16:05. На получасовую ось такой старт не
    ложится и не отрисуется никогда, поэтому в data.json ему делать нечего —
    иначе файл говорит одно, а страница показывает другое.
    """
    at = to_minutes(time)
    return (
        at >= to_minutes(start)
        and at + duration <= to_minutes(end)
        and (at - to_minutes(start)) % step == 0
    )


def normalize_tariffs(tariffs: dict) -> dict:
    """Приводит окна к одному виду: всегда список услуг.

    У тенниса на окно приходится одна услуга, у падела на полтора часа — две.
    Странице проще, когда поле одно и всегда список.
    """
    out: dict = {}
    for duration, kinds in tariffs.items():
        out[duration] = {}
        for kind, windows in kinds.items():
            out[duration][kind] = [
                {
                    "from": w["from"],
                    "to": w["to"],
                    "service_ids": w.get("service_ids") or [w["service_id"]],
                    "price": w["price"],
                }
                for w in windows
            ]
    return out


def plan(sources: list[dict], days: list[str]) -> list[tuple]:
    """Какие запросы нужны: (ключ источника, компания, услуга, дата, длительность).

    Для derive длительность None — один запрос обслуживает все длительности.
    """
    tasks = []
    for source in sources:
        for day in days:
            if source["strategy"] == "derive":
                tasks.append((source["key"], source["company_id"], [source["probe_service_id"]], day, None))
            else:
                for duration in source["durations"]:
                    services = source["services"][str(duration)]
                    tasks.append((source["key"], source["company_id"], services, day, duration))
    return tasks


def main() -> int:
    config = json.loads((ROOT / "build" / "config.json").read_text(encoding="utf-8"))
    sources = config["sources"]
    step = config["slot_step"]

    # Часовой пояс центра: день, с которого начинается таблица, считается по нему,
    # а не по UTC, в котором живёт раннер.
    offset = timezone(timedelta(hours=3))
    today = datetime.now(offset).date()
    days = [(today + timedelta(days=i)).isoformat() for i in range(config["days"])]

    tasks = plan(sources, days)
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        answers = list(pool.map(lambda t: fetch_day(t[1], t[2], t[3]), tasks))

    raw = {(t[0], t[3], t[4]): a for t, a in zip(tasks, answers)}

    # Порядок чипов в ячейке задаётся порядком кортов в конфиге.
    order = [c["code"] for s in sources for c in s["courts"]]

    grids: dict[str, dict[str, dict[str, list[str]]]] = {
        str(d): {day: {} for day in days} for d in config["durations"]
    }
    dropped: list[str] = []

    for source in sources:
        for duration in source["durations"]:
            grid = grids[str(duration)]
            for day in days:
                for court in source["courts"]:
                    master = court["master_id"]
                    if source["strategy"] == "derive":
                        slots = derive(raw[(source["key"], day, None)].get(master, []), duration, step)
                    else:
                        slots = raw[(source["key"], day, duration)].get(master, [])
                    for time in slots:
                        if not on_grid(time, config["grid_from"], config["grid_to"], step, duration):
                            dropped.append(f'{source["key"]} {day} {time} ({duration} мин)')
                            continue
                        grid[day].setdefault(time, []).append(court["code"])

    for grid in grids.values():
        for day, cells in grid.items():
            grid[day] = {
                time: sorted(cells[time], key=order.index) for time in sorted(cells)
            }

    courts = [
        {
            "code": court["code"],
            "label": court["label"],
            "title": court["title"],
            "source": source["key"],
            "master_id": court["master_id"],
            "booking_base": f"https://dikidi.net/ru/record/{source['company_id']}",
        }
        for source in sources
        for court in source["courts"]
    ]

    data = {
        "generated_at": datetime.now(offset).isoformat(timespec="seconds"),
        "venue": {"name": config["venue_name"], "city": config["venue_city"]},
        "courts": courts,
        "days": days,
        "day_off": {day: is_day_off(day, config["holidays"]) for day in days},
        "grid_from": config["grid_from"],
        "grid_to": config["grid_to"],
        "slot_step": step,
        "durations": config["durations"],
        "default_duration": config["default_duration"],
        "tariffs": {source["key"]: normalize_tariffs(source["tariffs"]) for source in sources},
        "grids": grids,
    }

    target = ROOT / "data.json"
    target.write_text(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )

    total = sum(len(v) for g in grids.values() for cells in g.values() for v in cells.values())
    print(
        f"запросов: {len(tasks)}, дней: {len(days)}, кортов: {len(courts)}, "
        f"слотов во всех сетках: {total}, файл: {target.stat().st_size} байт"
    )
    if dropped:
        print(f"не легли на сетку и отброшены ({len(dropped)}): {', '.join(dropped[:8])}")

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except DikidiError as error:
        print(f"не собралось: {error}", file=sys.stderr)
        sys.exit(1)
