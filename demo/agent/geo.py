"""Small geography and simulation-time helpers for the deterministic agent."""

from __future__ import annotations

import math
from datetime import datetime, timedelta

SIMULATION_EPOCH = datetime(2026, 3, 1, 0, 0, 0)
DAY_MINUTES = 24 * 60
MONTH_HORIZON_MINUTES = 31 * DAY_MINUTES


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    radius_km = 6371.0
    p1 = math.radians(float(lat1))
    l1 = math.radians(float(lng1))
    p2 = math.radians(float(lat2))
    l2 = math.radians(float(lng2))
    dp = p2 - p1
    dl = l2 - l1
    h = math.sin(dp * 0.5) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl * 0.5) ** 2
    h = min(1.0, max(0.0, h))
    return 2.0 * radius_km * math.asin(math.sqrt(h))


def distance_to_minutes(distance_km: float, speed_km_per_hour: float = 60.0) -> int:
    if distance_km <= 0:
        return 1
    return max(1, int(math.ceil((distance_km / speed_km_per_hour) * 60.0)))


def wall_time_to_minutes(text: str) -> int | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.strptime(raw, fmt)
            return int((dt - SIMULATION_EPOCH).total_seconds() // 60)
        except ValueError:
            pass
    return None


def minutes_to_wall_time(minutes: int) -> str:
    return (SIMULATION_EPOCH + timedelta(minutes=int(minutes))).strftime("%Y-%m-%d %H:%M:%S")


def day_index(minutes: int) -> int:
    return max(0, int(minutes) // DAY_MINUTES)


def minute_of_day(minutes: int) -> int:
    return int(minutes) % DAY_MINUTES


def day_start(minutes_or_day: int, *, is_day_index: bool = False) -> int:
    day = int(minutes_or_day) if is_day_index else day_index(minutes_or_day)
    return day * DAY_MINUTES


def day_end(minutes_or_day: int, *, is_day_index: bool = False) -> int:
    return day_start(minutes_or_day, is_day_index=is_day_index) + DAY_MINUTES


def interval_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return max(int(a_start), int(b_start)) < min(int(a_end), int(b_end))


def iter_day_segments(start_minute: int, end_minute: int) -> list[tuple[int, int, int]]:
    """Return ``(day, segment_start, segment_end)`` pieces for an interval."""
    if end_minute <= start_minute:
        return []
    out: list[tuple[int, int, int]] = []
    cur = int(start_minute)
    while cur < end_minute:
        d = day_index(cur)
        seg_end = min(day_end(d, is_day_index=True), int(end_minute))
        out.append((d, cur, seg_end))
        cur = seg_end
    return out


def merged_longest_span(intervals: list[tuple[int, int]]) -> int:
    if not intervals:
        return 0
    ordered = sorted((int(s), int(e)) for s, e in intervals if e > s)
    if not ordered:
        return 0
    merged: list[tuple[int, int]] = []
    for start, end in ordered:
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return max(end - start for start, end in merged)


def point_in_bounds(lat: float, lng: float, bounds: tuple[float, float, float, float]) -> bool:
    lat_min, lat_max, lng_min, lng_max = bounds
    return lat_min <= float(lat) <= lat_max and lng_min <= float(lng) <= lng_max
