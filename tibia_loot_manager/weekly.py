"""Weekly Task tracker: this week's Delivery Tasks, with required, collected and remaining amounts.

Weekly tasks reset at the Monday server save, 10:00 German time (CET in
winter, CEST in summer). The daylight-saving switch is computed here because
Python on Windows has no time-zone database without an extra package.
"""

from datetime import date, datetime, timedelta, timezone

from .i18n import _

ARCHIVE_WEEKS = 12
SERVER_SAVE_HOUR = 10  # local German time


def _last_sunday(year: int, month: int) -> date:
    d = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    return d - timedelta(days=(d.weekday() + 1) % 7)


def german_utc_offset(moment: datetime) -> timedelta:
    """+2 h during EU summer time (last Sunday of March 01:00 UTC to last Sunday of October 01:00 UTC), else +1 h."""
    year = moment.year
    start = datetime.combine(_last_sunday(year, 3), datetime.min.time(), timezone.utc) + timedelta(hours=1)
    end = datetime.combine(_last_sunday(year, 10), datetime.min.time(), timezone.utc) + timedelta(hours=1)
    return timedelta(hours=2 if start <= moment < end else 1)


def server_save(day: date) -> datetime:
    """The server save on ``day`` as a UTC datetime."""
    local = datetime.combine(day, datetime.min.time(), timezone.utc) + timedelta(hours=SERVER_SAVE_HOUR)
    return local - german_utc_offset(local - timedelta(hours=1))


def week_start(now: datetime | None = None) -> datetime:
    """The Monday server save that started the current task week."""
    now = now or datetime.now(timezone.utc)
    monday = (now - timedelta(days=now.weekday())).date()
    start = server_save(monday)
    if now < start:
        start = server_save(monday - timedelta(days=7))
    return start


def next_reset(now: datetime | None = None) -> datetime:
    return server_save((week_start(now) + timedelta(days=7)).date())


def empty_week(now: datetime | None = None) -> dict:
    return {"week_start": week_start(now).isoformat(), "tasks": []}


def valid_week(data) -> bool:
    return (isinstance(data, dict) and isinstance(data.get("week_start", ""), str)
            and isinstance(data.get("tasks", []), list) and all(valid_task(t) for t in data.get("tasks", [])))


def valid_task(t) -> bool:
    return (isinstance(t, dict) and isinstance(t.get("key"), str) and isinstance(t.get("name"), str)
            and all(isinstance(t.get(k), int) and not isinstance(t.get(k), bool) and t.get(k) >= 0
                    for k in ("required", "collected")))


def roll_over(week: dict, archive: list, now: datetime | None = None) -> tuple[dict, list, bool]:
    """Start a new week if the stored one has ended; the old one is summarised in the archive."""
    current = week_start(now).isoformat()
    if week and week.get("week_start") == current:
        return week, archive, False
    if week and week.get("tasks"):
        tasks = week["tasks"]
        archive = ([{"week_start": week.get("week_start"), "tasks": len(tasks),
                     "done": sum(t["collected"] >= t["required"] for t in tasks),
                     "items": [{"name": t["name"], "required": t["required"], "collected": t["collected"]}
                               for t in tasks]}] + list(archive))[:ARCHIVE_WEEKS]
    return {"week_start": current, "tasks": []}, archive, bool(week and week.get("week_start"))


def clamp_required(required: int, low: int | None, high: int | None) -> int:
    required = max(1, int(required))
    if low and required < low:
        raise ValueError(_("This task asks for at least {n}.").format(n=low))
    if high and required > high:
        raise ValueError(_("This task asks for at most {n}.").format(n=high))
    return required
