import asyncio
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import aiohttp

CAD_URL = "https://ssd-api.jpl.nasa.gov/cad.api"
SENTRY_URL = "https://ssd-api.jpl.nasa.gov/sentry.api"
SCOUT_URL = "https://ssd-api.jpl.nasa.gov/scout.api"
HORIZONS_URL = "https://ssd.jpl.nasa.gov/api/horizons.api"
SBDB_PAGE = "https://ssd.jpl.nasa.gov/tools/sbdb_lookup.html#/?sstr={}"
SCOUT_PAGE = "https://cneos.jpl.nasa.gov/scout/#/object/{}"

AU_KM = 149597870.7
LD_KM = 384400.0
LD_AU = LD_KM / AU_KM
EARTH_KM = 6371.0
TAIPEI = timezone(timedelta(hours=8))
TIMEOUT = aiohttp.ClientTimeout(total=40)
MONTHS = {m: i for i, m in enumerate(("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)}

LEVELS = {
    1: {"name": "近距離飛掠", "en": "CLOSE FLYBY", "color": 0x2D5BD3},
    2: {"name": "大型近距離飛掠", "en": "LARGE CLOSE FLYBY", "color": 0xE39B20},
    3: {"name": "撞擊風險上升", "en": "IMPACT RISK", "color": 0xE8552A},
    4: {"name": "可能即將撞擊", "en": "POSSIBLE IMPACT", "color": 0xC21A2B},
}


@dataclass
class Approach:
    des: str
    name: str
    time: datetime
    sigma: str
    dist_au: float
    v_rel: float
    h: float = None
    dia_min: float = None
    dia_max: float = None

    @property
    def dist_km(self):
        return self.dist_au * AU_KM

    @property
    def dist_ld(self):
        return self.dist_au / LD_AU

    @property
    def local_time(self):
        return self.time.astimezone(TAIPEI)

    @property
    def key(self):
        return f"{self.des}|{self.time:%Y%m%d}"

    @property
    def is_pha_size(self):
        if self.h is not None:
            return self.h <= 22.0
        return self.dia_max is not None and self.dia_max >= 0.14


@dataclass
class Risk:
    des: str
    name: str
    ip: float
    ts: int
    ps: float
    dia: float
    years: str
    n_imp: int
    last_obs: str
    h: float = None


@dataclass
class Candidate:
    name: str
    neo: int
    geo: int
    rating: int
    ca_dist: float
    h: float
    arc: float
    n_obs: int
    last_run: str
    extra: dict = field(default_factory=dict)


@dataclass
class Track:
    times: list
    xyz: list


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def parse_cd(text):
    date, clock = text.strip().split(" ")
    y, mon, d = date.split("-")
    hh, mm = clock.split(":")[:2]
    return datetime(int(y), MONTHS[mon], int(d), int(hh), int(mm), tzinfo=timezone.utc)


def diameter_range(h, diameter=None, sigma=None):
    if diameter is not None:
        s = sigma or 0.0
        return max(diameter - s, 0.0), diameter + s
    if h is None:
        return None, None
    base = 10 ** (-h / 5)
    return 1329 / math.sqrt(0.25) * base, 1329 / math.sqrt(0.05) * base


def level_of(a, close_ld=1.0, large_ld=5.0):
    if a.is_pha_size and a.dist_ld < large_ld:
        return 2
    if a.dist_ld < close_ld:
        return 1
    return 0


def risk_level(r, ts_min=1):
    if r.ts is not None and r.ts >= 5:
        return 4
    if r.ts is not None and r.ts >= ts_min:
        return 3
    return 0


def candidate_level(c, rating_min=3):
    if c.rating is not None and c.rating >= rating_min:
        return 4
    return 0


async def _get_json(session, url, params):
    async with session.get(url, params=params, timeout=TIMEOUT) as r:
        r.raise_for_status()
        return await r.json(content_type=None)


def _utc_param(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def parse_cad(payload):
    fields = payload.get("fields") or []
    out = []
    for row in payload.get("data") or []:
        d = dict(zip(fields, row))
        h = _float(d.get("h"))
        lo, hi = diameter_range(h, _float(d.get("diameter")), _float(d.get("diameter_sigma")))
        name = (d.get("fullname") or d["des"]).strip()
        out.append(Approach(
            des=d["des"],
            name=name.strip("()") if name.startswith("(") and name.endswith(")") else name,
            time=parse_cd(d["cd"]),
            sigma=(d.get("t_sigma_f") or "").strip(),
            dist_au=float(d["dist"]),
            v_rel=_float(d.get("v_rel")) or 0.0,
            h=h,
            dia_min=lo,
            dia_max=hi,
        ))
    return out


async def close_approaches(session, start, end, dist_ld=10.0):
    payload = await _get_json(session, CAD_URL, {
        "date-min": _utc_param(start),
        "date-max": _utc_param(end),
        "dist-max": f"{dist_ld:g}LD",
        "diameter": "true",
        "fullname": "true",
    })
    return parse_cad(payload)


def parse_sentry(payload):
    out = []
    for d in payload.get("data") or []:
        name = (d.get("fullname") or d.get("des") or "").strip()
        out.append(Risk(
            des=d.get("des", ""),
            name=name.strip("()") if name.startswith("(") and name.endswith(")") else name,
            ip=_float(d.get("ip")) or 0.0,
            ts=_int(d.get("ts_max")) or 0,
            ps=_float(d.get("ps_max")),
            dia=_float(d.get("diameter")),
            years=d.get("range", ""),
            n_imp=_int(d.get("n_imp")) or 0,
            last_obs=d.get("last_obs", ""),
            h=_float(d.get("h")),
        ))
    return out


async def sentry_list(session):
    return parse_sentry(await _get_json(session, SENTRY_URL, {}))


def parse_scout(payload):
    out = []
    for d in payload.get("data") or []:
        out.append(Candidate(
            name=d.get("objectName", ""),
            neo=_int(d.get("neoScore")) or 0,
            geo=_int(d.get("geocentricScore")) or 0,
            rating=_int(d.get("rating")),
            ca_dist=_float(d.get("caDist")),
            h=_float(d.get("H")),
            arc=_float(d.get("arc")),
            n_obs=_int(d.get("nObs")) or 0,
            last_run=d.get("lastRun", ""),
            extra={k: d.get(k) for k in ("moid", "vInf", "unc", "Vmag")},
        ))
    return out


async def scout_list(session):
    return parse_scout(await _get_json(session, SCOUT_URL, {}))


def parse_vectors(text):
    if "$$SOE" not in text:
        return None
    body = text.split("$$SOE", 1)[1].split("$$EOE", 1)[0]
    times, xyz = [], []
    for line in body.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 5:
            continue
        jd = float(parts[0])
        times.append(datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(days=jd - 2440587.5))
        xyz.append((float(parts[2]), float(parts[3]), float(parts[4])))
    return Track(times, xyz) if times else None


async def _vectors(session, command, start, end, step):
    payload = await _get_json(session, HORIZONS_URL, {
        "format": "json",
        "COMMAND": f"'{command}'",
        "OBJ_DATA": "'NO'",
        "MAKE_EPHEM": "'YES'",
        "EPHEM_TYPE": "'VECTORS'",
        "CENTER": "'500@399'",
        "START_TIME": f"'{start.astimezone(timezone.utc):%Y-%m-%d %H:%M}'",
        "STOP_TIME": f"'{end.astimezone(timezone.utc):%Y-%m-%d %H:%M}'",
        "STEP_SIZE": f"'{step}'",
        "VEC_TABLE": "'1'",
        "REF_PLANE": "'ECLIPTIC'",
        "OUT_UNITS": "'KM-S'",
        "CSV_FORMAT": "'YES'",
    })
    return parse_vectors(payload.get("result", ""))


async def flyby_tracks(session, a, hours=48, step="20m"):
    start = a.time - timedelta(hours=hours)
    end = a.time + timedelta(hours=hours)
    body, moon = await asyncio.gather(
        _vectors(session, f"DES={a.des};", start, end, step),
        _vectors(session, "301", start, end, step),
    )
    return body, moon
