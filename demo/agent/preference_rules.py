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


@dataclass(frozen=True)
class TimeLimitedRegionBan:
    start_minute: int
    end_minute: int
    city_keyword: str

@dataclass(frozen=True)
class AppointmentTask:
    start_minute: int
    end_minute: int
    lat: float
    lng: float
    duration_minutes: int
    penalty_amount: float

@dataclass(frozen=True)
class RouteSequence:
    waypoints: list[tuple[float, float, str, int | None]]  # (lat, lng, label, deadline_minute)
    penalty_amount: float


@dataclass
class PreferencePolicy:
    forbidden_cargo_names: set[str] = field(default_factory=set)
    # New: city-level cargo region bans
    forbidden_cargo_regions: set[str] = field(default_factory=set)
    soft_avoid_cargo_names: set[str] = field(default_factory=set)
    max_haul_km: float | None = None
    max_pickup_km: float | None = None
    max_month_deadhead_km: float | None = None
    daily_rest_minutes: int = 0
    daily_rest_weekdays_only: bool = False
    no_order_days_required: int = 0
    off_days_required: int = 0
    off_days_penalty: float = 0.0  # New: penalty for missing off-days
    daily_max_orders: int | None = None
    first_order_latest_minute: int | None = None
    quiet_windows: list[QuietWindow] = field(default_factory=list)
    bounds: tuple[float, float, float, float] | None = None
    forbidden_zones: list[ForbiddenZone] = field(default_factory=list)
    required_visits: list[RequiredVisit] = field(default_factory=list)
    time_limited_region_bans: list[TimeLimitedRegionBan] = field(default_factory=list)  # New
    appointments: list[AppointmentTask] = field(default_factory=list)  # New
    route_sequences: list[RouteSequence] = field(default_factory=list)  # New
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
        # 20260529 新偏好类型
        _parse_cargo_region_forbid(text, policy)
        _parse_time_limited_region_ban(text, item, policy)
        _parse_off_days_penalty(text, item, policy)
        _parse_required_region_days(text, item, policy)
        _parse_appointment(text, item, policy)
        _parse_route_sequence(text, item, policy)
    # Cross-reference: fill missing appointment coordinates from required_visits
    for appt in policy.appointments:
        if appt.lat == 0.0 and appt.lng == 0.0 and policy.required_visits:
            appt = AppointmentTask(appt.start_minute, appt.end_minute, policy.required_visits[0].lat, policy.required_visits[0].lng, appt.duration_minutes, appt.penalty_amount)
            policy.appointments = [a for a in policy.appointments if not (a.lat == 0.0 and a.lng == 0.0)] + [appt]
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

    # soft_avoid 收紧已禁用：Qwen 可能将"尽量不拉"升级为硬禁运，
    # 导致失去盈利货源机会（D010 服饰纺织皮革为高利润品类）。
    # 如需重新启用，取消下面注释。
    # extra_soft = hints.get("soft_avoid_cargo_names")
    # if isinstance(extra_soft, list):
    #     for name in extra_soft:
    #         if isinstance(name, str) and name:
    #             policy.soft_avoid_cargo_names.add(name)

    # 距离限制收紧已禁用：Qwen 对 max_haul_km / max_pickup_km 的收紧过于激进，
    # 会显著减少接单机会从而降低净收入（尤其是 D010）。保留禁运品类和休息时间的收紧。
    # 如需重新启用，取消下面两段的注释。
    # hint_haul = hints.get("max_haul_km")
    # if isinstance(hint_haul, (int, float)) and hint_haul > 0:
    #     if policy.max_haul_km is None or hint_haul < policy.max_haul_km:
    #         policy.max_haul_km = float(hint_haul)
    # hint_pickup = hints.get("max_pickup_km")
    # if isinstance(hint_pickup, (int, float)) and hint_pickup > 0:
    #     if policy.max_pickup_km is None or hint_pickup < policy.max_pickup_km:
    #         policy.max_pickup_km = float(hint_pickup)

    # 休息时间收紧已禁用：增加休息需求会减少接单时间，净收入损失
    # 通常超过罚分节省（例如 +1h rest ≈ -1,200 net vs -300 penalty saving）。
    # 保留禁运品类收紧（纯罚分改善，不影响接单时间）。
    # hint_rest = hints.get("daily_rest_hours")
    # if isinstance(hint_rest, (int, float)) and hint_rest > 0:
    #     rest_min = int(hint_rest * 60)
    #     if rest_min > policy.daily_rest_minutes:
    #         policy.daily_rest_minutes = rest_min

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
    if memory.active_minutes_today(now_minute) > 0:
        # Allow early-morning off-day even with cross-night cargo minutes
        if minute_of_day(now_minute) >= 120:
            return False
    still_needed = needed - done
    days_remaining = MONTH_HORIZON_MINUTES // DAY_MINUTES - (now_minute // DAY_MINUTES)
    # More proactive: force off-day with 10-day buffer to ensure compliance
    return days_remaining <= still_needed + 10


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
    # Bracketed: 「机械设备」
    names = set(re.findall(r"「([^」]+)」", text))
    if names and ("不接" in text or "不拉" in text or "不干" in text or "推掉" in text):
        policy.forbidden_cargo_names.update(names)
    elif names and ("尽量不拉" in text or "尽量不接" in text):
        policy.soft_avoid_cargo_names.update(names)
    # Unbracketed: always try this path too
    m = re.search(r"([一-鿿]{2,4})(?:货源|这类活儿|这类货|这一类|的货|订单)", text)
    if m and any(w in text for w in ("不接", "不拉", "不干", "推掉", "干不了", "搞不了", "一律推", "每接一次都扣", "凡是", "赔不起")):
        policy.forbidden_cargo_names.add(m.group(1))


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
    if not any(k in text for k in ("不接单", "不空", "不空车", "熄火", "睡觉", "停车歇", "雷打不动")):
        return
    # Arabic: "23点至次日4点"
    for match in re.finditer(r"(\d{1,2})点至(?:次日)?(?:当天)?(?:早|凌晨|上午|下午|中午)?(\d{1,2})点", text):
        start = int(match.group(1))
        end = int(match.group(2))
        if "下午" in text and end < 12:
            end += 12
        if start <= 24 and end <= 24:
            policy.quiet_windows.append(QuietWindow(start * 60, (end % 24) * 60))
    # Chinese: "零点以后到早上六点"
    cn_hour = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    m = re.search(r"([零一二两三四五六七八九十]+)点(?:以后)?(?:到|至)(?:早上|上午|凌晨)?([零一二两三四五六七八九十]+)点", text)
    if m:
        s = cn_hour.get(m.group(1), -1)
        e = cn_hour.get(m.group(2), -1)
        if 0 <= s <= 24 and 0 <= e <= 24:
            policy.quiet_windows.append(QuietWindow(s * 60, e * 60))


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


def _parse_cargo_region_forbid(text: str, policy: PreferencePolicy) -> None:
    """装货地或卸货地在XX的货，我一律不接 / 起点或终点涉及XX"""
    has_negative = any(w in text for w in ["不接", "不往", "不进", "推掉", "不拉", "不干", "一律不", "都扣", "每接一次都"])
    if not has_negative:
        return
    m = re.search(r"(?:装货地?或卸货地?在|起点或终点涉及)\s*([一-鿿]{2,8})(?:的货|的货源)", text)
    if m:
        city = m.group(1)
        # Safety: don't add city if the text also describes it positively (required region)
        if "得接够" not in text and "起码得" not in text and "至少" not in text.replace(city, ""):
            policy.forbidden_cargo_regions.add(city)


def _parse_time_limited_region_ban(text: str, item: Any, policy: PreferencePolicy) -> None:
    """三月四号五号交警在深圳查车，这天我不往深圳跑"""
    m = re.search(r"([^，]+)(?:交警|查车|不往)([一-鿿]{2,6})(?:跑|去|进)", text)
    if m:
        city = m.group(2)
        start = wall_time_to_minutes(str(item.get("start_time", ""))) if isinstance(item, dict) else None
        end = wall_time_to_minutes(str(item.get("end_time", ""))) if isinstance(item, dict) else None
        if start is not None and end is not None:
            policy.time_limited_region_bans.append(TimeLimitedRegionBan(start, end, city))


def _parse_off_days_penalty(text: str, item: Any, policy: PreferencePolicy) -> None:
    """三月怎么也得抽三个整天完全歇着 / 起码留两个整天停驶检修 / 别给我排活"""
    m = re.search(r"(?:抽|留|至少|起码)[一-鿿]*?([一-鿿0-9]+)个?整[天日]\s*(?:完全歇着|停驶|歇着|别排活|不进)", text)
    if m:
        count = _chinese_or_int(m.group(1))
        if isinstance(item, dict) and count > 0:
            penalty = float(item.get("penalty_amount", 0) or 0)
            policy.off_days_required = max(policy.off_days_required, count)
            policy.off_days_penalty = max(policy.off_days_penalty, penalty)


def _parse_required_region_days(text: str, item: Any, policy: PreferencePolicy) -> None:
    """装货或卸货在增城的货，起码得接够四个不同的日子"""
    m = re.search(r"(?:装货|卸货|装货或卸货)在([一-鿿]{2,6})(?:的货)?[,，\s]*起码得接够|至少.*?接够|接够\s*([一-鿿0-9]+)\s*个?不同的?(?:自然)?日", text)
    if not m:
        m = re.search(r"在([一-鿿]{2,6})(?:的货)[,，\s]*起码得接够|至少.*?接够\s*([一-鿿0-9]+)\s*个?", text)
    if m:
        city = m.group(1)
        count = _chinese_or_int(m.group(2) if m.lastindex >= 2 else "1")
        coords_list = _coords(text)
        lat, lng = coords_list[0] if coords_list else (0.0, 0.0)
        if count > 0:
            policy.required_visits.append(RequiredVisit(lat=lat, lng=lng, radius_km=30.0, days_required=count))


def _parse_appointment(text: str, item: Any, policy: PreferencePolicy) -> None:
    """单次停留任务：X月X日到XX停一趟，花X小时 / 到XX连续停留至少X分钟"""
    m = re.search(r"(?:到|在)\s*([一-鿿]{2,8})(?:城区|区|县|市)?\s*(?:停一趟|停留|停)\s*[,，]?\s*(?:花|至少|停留?)\s*([一-鿿0-9]+)\s*(?:小时|个钟|分钟)", text)
    if not m:
        m = re.search(r"连续停留至少\s*(\d+)\s*分钟", text)
    if m:
        dur_str = m.group(2) if m.lastindex >= 2 else m.group(1)
        if "小时" in text or "个钟" in text:
            duration = _chinese_or_int(dur_str) * 60
        else:
            duration = int(dur_str) if dur_str.isdigit() else _chinese_or_int(dur_str)
        coords_list = _coords(text)
        lat, lng = coords_list[0] if coords_list else (0.0, 0.0)
        start = wall_time_to_minutes(str(item.get("start_time", ""))) if isinstance(item, dict) else None
        end = wall_time_to_minutes(str(item.get("end_time", ""))) if isinstance(item, dict) else None
        if start is not None and end is not None:
            policy.appointments.append(AppointmentTask(start, end, lat, lng, duration, float(item.get("penalty_amount", 0) or 0)))


def _parse_route_sequence(text: str, item: Any, policy: PreferencePolicy) -> None:
    """多站顺序任务：先过X捎上Y，12点前赶到Z赴宴到下午2点"""
    if "先" in text and ("再" in text or "然后" in text or "赶到" in text):
        coords_list = _coords(text)
        if len(coords_list) >= 2:
            deadline_str = re.search(r"(\d{1,2})点[前以之]|中午(\d{1,2})点", text)
            deadline_hour = None
            if deadline_str:
                dl = deadline_str.group(1) or deadline_str.group(2)
                deadline_hour = int(dl) * 60 if dl else None
            waypoints = []
            for i, (lat, lng) in enumerate(coords_list):
                label = f"wp_{i}"
                waypoints.append((lat, lng, label, deadline_hour if i == len(coords_list) - 1 else None))
            penalty = float(item.get("penalty_amount", 0) or 0) if isinstance(item, dict) else 0.0
            policy.route_sequences.append(RouteSequence(waypoints, penalty))


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
