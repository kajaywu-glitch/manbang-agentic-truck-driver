"""Generic preference parsing and candidate checks.

The parser intentionally reads only runtime ``preferences`` text from
``get_driver_status``. It does not inspect driver ids or raw data files.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from agent.geo import (
    DAY_MINUTES,
    MONTH_HORIZON_MINUTES,
    day_end,
    day_index,
    haversine_km,
    interval_overlap,
    minute_of_day,
    point_in_bounds,
    wall_time_to_minutes,
)
from agent.state_tracker import DriverMemory


@dataclass(frozen=True)
class QuietWindow:
    start_minute: int
    end_minute: int


@dataclass(frozen=True)
class ForbiddenZone:
    lat: float
    lng: float
    radius_km: float


@dataclass(frozen=True)
class RequiredVisit:
    lat: float
    lng: float
    radius_km: float
    days_required: int


@dataclass(frozen=True)
class HomeNightRule:
    lat: float
    lng: float
    radius_km: float
    deadline_minute_of_day: int
    quiet_start_minute: int
    quiet_end_minute: int


@dataclass(frozen=True)
class FamilyTask:
    start_minute: int
    pickup_lat: float
    pickup_lng: float
    pickup_wait_minutes: int
    home_lat: float
    home_lng: float
    home_deadline_minute: int
    stay_until_minute: int
    radius_km: float = 1.0


@dataclass(frozen=True)
class RequiredCargo:
    cargo_id: str
    target_lat: float | None
    target_lng: float | None
    start_minute: int | None
    end_minute: int | None


@dataclass
class PreferencePolicy:
    forbidden_cargo_names: set[str] = field(default_factory=set)
    soft_avoid_cargo_names: set[str] = field(default_factory=set)
    max_haul_km: float | None = None
    max_pickup_km: float | None = None
    max_month_deadhead_km: float | None = None
    daily_rest_minutes: int = 0
    daily_rest_weekdays_only: bool = False
    no_order_days_required: int = 0
    off_days_required: int = 0
    daily_max_orders: int | None = None
    first_order_latest_minute: int | None = None
    quiet_windows: list[QuietWindow] = field(default_factory=list)
    bounds: tuple[float, float, float, float] | None = None
    forbidden_zones: list[ForbiddenZone] = field(default_factory=list)
    required_visits: list[RequiredVisit] = field(default_factory=list)
    home_night: HomeNightRule | None = None
    family_task: FamilyTask | None = None
    required_cargo: RequiredCargo | None = None

    def active_interval_blocked(self, start_minute: int, end_minute: int) -> bool:
        return quiet_overlap(self.quiet_windows, start_minute, end_minute)

    def point_allowed(self, lat: float, lng: float) -> bool:
        if self.bounds is not None and not point_in_bounds(lat, lng, self.bounds):
            return False
        for zone in self.forbidden_zones:
            if haversine_km(lat, lng, zone.lat, zone.lng) <= zone.radius_km:
                return False
        return True


def parse_preferences(preferences: list[Any]) -> PreferencePolicy:
    policy = PreferencePolicy()
    for item in preferences or []:
        text = _preference_text(item)
        if not text:
            continue
        _parse_cargo_names(text, policy)
        _parse_rest(text, policy)
        _parse_quiet_window(text, policy)
        _parse_distance_limits(text, policy)
        _parse_day_count_rules(text, policy)
        _parse_geo_rules(text, policy)
        _parse_required_cargo(text, item, policy)
        _parse_family_task(text, item, policy)
    return policy


def apply_qwen_hints(policy: PreferencePolicy, hints: dict[str, Any]) -> PreferencePolicy:
    """Apply model-generated preference hints. Only tightens constraints, never relaxes.

    Args:
        policy: Existing parsed policy.
        hints: Dict from QwenFlashHelper.preference_hints().

    Returns:
        Updated policy (modified in-place and returned).
    """
    if not hints:
        return policy

    # 禁运品类：只能新增，不能移除
    extra_forbidden = hints.get("forbidden_cargo_names")
    if isinstance(extra_forbidden, list):
        for name in extra_forbidden:
            if isinstance(name, str) and name:
                policy.forbidden_cargo_names.add(name)

    extra_soft = hints.get("soft_avoid_cargo_names")
    if isinstance(extra_soft, list):
        for name in extra_soft:
            if isinstance(name, str) and name:
                policy.soft_avoid_cargo_names.add(name)

    # 距离限制：只能收紧
    hint_haul = hints.get("max_haul_km")
    if isinstance(hint_haul, (int, float)) and hint_haul > 0:
        if policy.max_haul_km is None or hint_haul < policy.max_haul_km:
            policy.max_haul_km = float(hint_haul)

    hint_pickup = hints.get("max_pickup_km")
    if isinstance(hint_pickup, (int, float)) and hint_pickup > 0:
        if policy.max_pickup_km is None or hint_pickup < policy.max_pickup_km:
            policy.max_pickup_km = float(hint_pickup)

    # 休息时间：只能增加
    hint_rest = hints.get("daily_rest_hours")
    if isinstance(hint_rest, (int, float)) and hint_rest > 0:
        rest_min = int(hint_rest * 60)
        if rest_min > policy.daily_rest_minutes:
            policy.daily_rest_minutes = rest_min

    return policy


def quiet_overlap(windows: list[QuietWindow], start_minute: int, end_minute: int) -> bool:
    if end_minute <= start_minute:
        return False
    first_day = day_index(start_minute) - 1
    last_day = day_index(end_minute) + 1
    for day in range(max(0, first_day), last_day + 1):
        base = day * DAY_MINUTES
        for window in windows:
            if window.end_minute > window.start_minute:
                if interval_overlap(start_minute, end_minute, base + window.start_minute, base + window.end_minute):
                    return True
            else:
                if interval_overlap(start_minute, end_minute, base + window.start_minute, base + DAY_MINUTES):
                    return True
                if interval_overlap(start_minute, end_minute, base + DAY_MINUTES, base + DAY_MINUTES + window.end_minute):
                    return True
    return False


def quiet_window_end_if_inside(windows: list[QuietWindow], now_minute: int) -> int | None:
    mod = minute_of_day(now_minute)
    base = now_minute - mod
    for window in windows:
        if window.end_minute > window.start_minute:
            if window.start_minute <= mod < window.end_minute:
                return base + window.end_minute
        else:
            if mod >= window.start_minute:
                return base + DAY_MINUTES + window.end_minute
            if mod < window.end_minute:
                return base + window.end_minute
    return None


def needs_rest_today(policy: PreferencePolicy, memory: DriverMemory, now_minute: int) -> int:
    required = int(policy.daily_rest_minutes or 0)
    if required <= 0:
        return 0
    if policy.daily_rest_weekdays_only and _weekday(day_index(now_minute)) >= 5:
        return 0
    longest = memory.longest_rest_today(now_minute)
    return max(0, required - longest)


def should_preserve_off_day(policy: PreferencePolicy, memory: DriverMemory, now_minute: int) -> bool:
    needed = int(policy.off_days_required or 0)
    if needed <= 0:
        return False
    done = memory.completed_off_days(now_minute)
    if done >= needed:
        return False
    return memory.active_minutes_today(now_minute) == 0


def should_preserve_no_order_day(policy: PreferencePolicy, memory: DriverMemory, now_minute: int) -> bool:
    needed = int(policy.no_order_days_required or 0)
    if needed <= 0:
        return False
    done = memory.completed_no_order_days(now_minute)
    if done >= needed:
        return False
    return memory.accepted_orders_today(now_minute) == 0


def must_rest_today_proactive(policy: PreferencePolicy, memory: DriverMemory, now_minute: int) -> str | None:
    """月度前瞻：检查今天是否必须作为休息日。
    返回 "off_day"（不能有任何活动）或 "no_order_day"（不能接单）或 None。"""
    current_day = now_minute // DAY_MINUTES
    month_days = MONTH_HORIZON_MINUTES // DAY_MINUTES  # 31
    days_remaining = month_days - current_day

    # off-day 检查：active_minutes == 0 才能算完全不出车。
    off_needed = int(policy.off_days_required or 0)
    if off_needed > 0:
        off_done = memory.completed_off_days(now_minute)
        off_still_needed = off_needed - off_done
        if off_still_needed > 0 and days_remaining <= off_still_needed:
            # 今天必须是 off-day
            if memory.active_minutes_today(now_minute) == 0:
                return "off_day"
            # 今天已有活动，无法作为 off-day，往后推

    # no-order-day 检查（D002/D007：无 accepted take_order）
    no_order_needed = int(policy.no_order_days_required or 0)
    if no_order_needed > 0:
        no_order_done = memory.completed_no_order_days(now_minute)
        no_order_still_needed = no_order_needed - no_order_done
        if no_order_still_needed > 0 and days_remaining <= no_order_still_needed:
            if memory.accepted_orders_today(now_minute) == 0:
                return "no_order_day"

    return None


def _preference_text(item: Any) -> str:
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        return str(item.get("content") or item.get("text") or "").strip()
    return ""


def _parse_cargo_names(text: str, policy: PreferencePolicy) -> None:
    if "货源品类" not in text:
        return
    names = set(re.findall(r"「([^」]+)」", text))
    if not names:
        return
    if "不接" in text:
        policy.forbidden_cargo_names.update(names)
    elif "尽量不拉" in text or "尽量不接" in text:
        policy.soft_avoid_cargo_names.update(names)


def _parse_rest(text: str, policy: PreferencePolicy) -> None:
    if not any(k in text for k in ("连续", "连着")) or not any(k in text for k in ("休息", "停车", "歇")):
        return
    match = re.search(r"(?:满|至少)(\d+)小时|(\d+)小时", text)
    if not match:
        return
    hours = int(match.group(1) or match.group(2))
    policy.daily_rest_minutes = max(policy.daily_rest_minutes, hours * 60)
    if "平日" in text:
        policy.daily_rest_weekdays_only = True


def _parse_quiet_window(text: str, policy: PreferencePolicy) -> None:
    if not any(k in text for k in ("不接单", "不空", "不空车")):
        return
    for match in re.finditer(r"(\d{1,2})点至(?:次日)?(?:当天)?(?:早|凌晨|上午|下午|中午)?(\d{1,2})点", text):
        start = int(match.group(1))
        end = int(match.group(2))
        if "下午" in text and end < 12:
            end += 12
        if "中午" in text and start == 12 and end <= 2:
            end += 12
        if start <= 24 and end <= 24:
            policy.quiet_windows.append(QuietWindow(start * 60, (end % 24) * 60))


def _parse_distance_limits(text: str, policy: PreferencePolicy) -> None:
    if "装货点至卸货点" in text or "装卸距离" in text:
        value = _first_number_before_km(text)
        if value is not None:
            policy.max_haul_km = value if policy.max_haul_km is None else min(policy.max_haul_km, value)
    if "赴装货点" in text and "空驶" in text:
        value = _first_number_before_km(text)
        if value is not None:
            policy.max_pickup_km = value if policy.max_pickup_km is None else min(policy.max_pickup_km, value)
    if "空驶" in text and "总和" in text:
        value = _first_number_before_km(text)
        if value is not None:
            policy.max_month_deadhead_km = value


def _parse_day_count_rules(text: str, policy: PreferencePolicy) -> None:
    if "自然月" in text and ("整天" in text or "完全歇着" in text or "完全" in text) and ("不接单" in text or "歇着" in text):
        count = _first_int(text) or 1
        if "不空" in text or "完全" in text or "歇着" in text or "不外跑" in text:
            policy.off_days_required = max(policy.off_days_required, count)
        else:
            policy.no_order_days_required = max(policy.no_order_days_required, count)
    if "放空一整天不接单" in text:
        policy.no_order_days_required = max(policy.no_order_days_required, 1)
    if "同一天接单不得超过" in text:
        count = _first_int(text)
        if count:
            policy.daily_max_orders = count
    if "首单开工不得晚于" in text:
        policy.first_order_latest_minute = 12 * 60


def _parse_geo_rules(text: str, policy: PreferencePolicy) -> None:
    bounds_match = re.search(r"北纬\s*([0-9.]+)至([0-9.]+).*?东经\s*([0-9.]+)至([0-9.]+)", text)
    if bounds_match:
        policy.bounds = (
            float(bounds_match.group(1)),
            float(bounds_match.group(2)),
            float(bounds_match.group(3)),
            float(bounds_match.group(4)),
        )
    zone_match = re.search(r"以[（(]\s*([0-9.]+)\s*[，,]\s*([0-9.]+)\s*[）)].*?半径\s*([0-9.]+)\s*公里", text)
    if zone_match and any(k in text for k in ("不得进入", "禁止进入", "不得驶入", "不允许进入", "不可进入")):
        policy.forbidden_zones.append(
            ForbiddenZone(float(zone_match.group(1)), float(zone_match.group(2)), float(zone_match.group(3)))
        )
    visit_match = re.search(r"至少\s*(\d+).*?自然日到过[（(]\s*([0-9.]+)\s*[，,]\s*([0-9.]+)\s*[）)].*?([0-9一二两三四五六七八九十]+)公里", text)
    if visit_match:
        policy.required_visits.append(
            RequiredVisit(
                lat=float(visit_match.group(2)),
                lng=float(visit_match.group(3)),
                radius_km=float(_chinese_or_int(visit_match.group(4))),
                days_required=int(visit_match.group(1)),
            )
        )
    if "自家位置" in text and "23点前" in text:
        coords = _coords(text)
        if coords:
            policy.home_night = HomeNightRule(coords[0][0], coords[0][1], 1.0, 23 * 60, 23 * 60, 8 * 60)
            policy.quiet_windows.append(QuietWindow(23 * 60, 8 * 60))


def _parse_required_cargo(text: str, item: Any, policy: PreferencePolicy) -> None:
    if "指定熟货源编号" not in text:
        return
    cid_match = re.search(r"指定熟货源编号\s*(\d+)", text)
    coords = _coords(text)
    start = None
    if "上架时间" in text:
        time_match = re.search(r"上架时间[:：]\s*([0-9:-]+\s+[0-9:]+)", text)
        if time_match:
            start = wall_time_to_minutes(time_match.group(1))
    end = None
    if isinstance(item, dict):
        end = wall_time_to_minutes(str(item.get("end_time", "")))
    if cid_match:
        policy.required_cargo = RequiredCargo(
            cargo_id=cid_match.group(1),
            target_lat=coords[0][0] if coords else None,
            target_lng=coords[0][1] if coords else None,
            start_minute=start,
            end_minute=end,
        )


def _parse_family_task(text: str, item: Any, policy: PreferencePolicy) -> None:
    if "家中急事" not in text or "配偶" not in text:
        return
    coords = _coords(text)
    if len(coords) < 2:
        return
    start = wall_time_to_minutes(str(item.get("start_time", ""))) if isinstance(item, dict) else None
    deadline_match = re.search(r"须在([0-9]{4}年[0-9]{1,2}月[0-9]{1,2}日[0-9]{1,2}:00)前", text)
    stay_match = re.search(r"至少待到([0-9]{4}年[0-9]{1,2}月[0-9]{1,2}日[0-9]{1,2}:00)", text)
    deadline = _cn_wall_time_to_minutes(deadline_match.group(1)) if deadline_match else None
    stay_until = _cn_wall_time_to_minutes(stay_match.group(1)) if stay_match else None
    if start is None or deadline is None or stay_until is None:
        return
    policy.family_task = FamilyTask(
        start_minute=start,
        pickup_lat=coords[0][0],
        pickup_lng=coords[0][1],
        pickup_wait_minutes=10,
        home_lat=coords[1][0],
        home_lng=coords[1][1],
        home_deadline_minute=deadline,
        stay_until_minute=stay_until,
    )


def _coords(text: str) -> list[tuple[float, float]]:
    return [(float(a), float(b)) for a, b in re.findall(r"[（(]\s*([0-9.]+)\s*[，,]\s*([0-9.]+)\s*[）)]", text)]


def _first_number_before_km(text: str) -> float | None:
    match = re.search(r"([0-9.]+)\s*公里", text)
    return float(match.group(1)) if match else None


def _first_int(text: str) -> int | None:
    match = re.search(r"(\d+)", text)
    if match:
        return int(match.group(1))
    for token in ("一", "二", "两", "三", "四", "五", "六", "七", "八", "九", "十"):
        if token in text:
            return _chinese_or_int(token)
    return None


def _chinese_or_int(text: str) -> int:
    raw = str(text)
    if raw.isdigit():
        return int(raw)
    table = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    return table.get(raw, 1)


def _cn_wall_time_to_minutes(text: str) -> int | None:
    raw = str(text).replace("年", "-").replace("月", "-").replace("日", " ")
    if len(raw.split(":")) == 2:
        raw += ":00"
    return wall_time_to_minutes(raw)


def _weekday(day: int) -> int:
    # 2026-03-01 is Sunday.
    return (6 + int(day)) % 7
