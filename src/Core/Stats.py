"""
Core/Stats.py
Numbers for the statistics screen, from the launcher's state file:

    state["play"]      {game key: [seconds, last played epoch]}   all time
    state["sessions"]  [[game key, title, start epoch, seconds, players], ...]
                       one entry per game run, newest last, at most
                       MAX_SESSIONS (older ones are dropped; the all-time
                       totals above are not affected)

Sessions have been recorded since the statistics screen exists, so the
"last 7 days" and "longest session" start empty on an older installation.
Pure functions: no UI, no files.
"""

import time

MAX_SESSIONS = 600
DAY_S = 86400


def add_session(sessions, key, title, start, seconds, players):
    """`sessions` with one more run, trimmed to MAX_SESSIONS."""
    return (list(sessions or []) + [[key, title, int(start), int(seconds), int(players)]])[-MAX_SESSIONS:]


def _midnight(epoch):
    local = time.localtime(epoch)
    return time.mktime((local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1))


def summarize(state, titles=None, now=None, days=7):
    """
    Args:
        state  (dict): the launcher's state.
        titles (dict): {game key: title} of the games in the library, for
                       games that have play time but no recorded session.

    Returns:
        dict: {"total": seconds, "games": games played,
               "week": seconds in the last `days` days, "week_sessions": runs in them,
               "sessions": runs recorded,
               "longest": (title, seconds, start epoch) | None,
               "top": [(title, seconds)] most played first,
               "days": [(start-of-day epoch, seconds)] oldest first, `days` entries,
               "players": average players per run (0 when unknown)}
    """
    now = time.time() if now is None else now
    titles = dict(titles or {})
    sessions = [s for s in (state.get("sessions") or []) if isinstance(s, list) and len(s) >= 5]
    for key, title, *_ in sessions:
        titles.setdefault(key, title)
    played = {key: (list(value or []) + [0])[0] for key, value in (state.get("play") or {}).items()}
    played = {key: seconds for key, seconds in played.items() if seconds > 0}

    today = _midnight(now)
    starts = [_midnight(today - i * DAY_S + DAY_S / 2) for i in range(days - 1, -1, -1)]
    per_day = {start: 0 for start in starts}
    week = week_sessions = 0
    for _, _, start, seconds, _ in sessions:
        day = _midnight(start)
        if day in per_day:
            per_day[day] += seconds
            week += seconds
            week_sessions += 1

    longest = max(sessions, key=lambda s: s[3], default=None)
    counted = [s[4] for s in sessions if s[4] > 0]
    top = sorted(played.items(), key=lambda item: -item[1])
    return {
        "total": sum(played.values()),
        "games": len(played),
        "week": week,
        "week_sessions": week_sessions,
        "sessions": len(sessions),
        "longest": (longest[1], longest[3], longest[2]) if longest else None,
        "top": [(titles.get(key) or key, seconds) for key, seconds in top],
        "days": [(start, per_day[start]) for start in starts],
        "players": sum(counted) / len(counted) if counted else 0,
    }
