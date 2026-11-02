from datetime import timedelta

CLOSE_CHOICES = (0.05, 0.1, 0.25, 0.5, 1.0)
WEEKDAY_NAMES = ("週一", "週二", "週三", "週四", "週五", "週六", "週日")
DEFAULTS = {
    "neo_alert_enabled": False,
    "neo_alert_channel_ids": [],
    "neo_levels": [1, 2, 3, 4],
    "neo_close_ld": 0.1,
    "neo_mention_role_ids": [],
    "neo_mention_min_level": 4,
    "neo_weekly_enabled": False,
    "neo_weekly_channel_ids": [],
    "neo_weekly_day": 0,
    "neo_weekly_hour": 8,
}


def option(settings, name):
    value = settings.get(name, DEFAULTS[name])
    return DEFAULTS[name] if value is None else value


def alert_targets(all_settings, level):
    out = []
    seen = set()
    for settings in all_settings.values():
        if not option(settings, "neo_alert_enabled") or level not in option(settings, "neo_levels"):
            continue
        roles = [int(r) for r in option(settings, "neo_mention_role_ids") if str(r).isdigit()]
        minimum = int(option(settings, "neo_mention_min_level") or 0)
        roles = roles if minimum and level >= minimum else []
        for channel_id in option(settings, "neo_alert_channel_ids"):
            if int(channel_id) in seen:
                continue
            seen.add(int(channel_id))
            out.append({"channel_id": int(channel_id), "roles": roles, "close_ld": float(option(settings, "neo_close_ld"))})
    return out


def weekly_due(settings, now_local, last_sent):
    if not option(settings, "neo_weekly_enabled") or not option(settings, "neo_weekly_channel_ids"):
        return None
    if now_local.weekday() != int(option(settings, "neo_weekly_day")) or now_local.hour < int(option(settings, "neo_weekly_hour")):
        return None
    stamp = now_local.strftime("%Y%m%d")
    return None if last_sent == stamp else stamp


def week_start(now_local):
    today = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    return today - timedelta(days=7)
