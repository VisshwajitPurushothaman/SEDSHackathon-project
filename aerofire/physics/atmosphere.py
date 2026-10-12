"""Atmosphere: US Standard Atmosphere 1976 and live Open-Meteo profiles.

US Standard Atmosphere 1976 (USSA76, NOAA-S/T 76-1562), geopotential layers
0-84.852 km. Within a layer with lapse rate L (K/m) and base (h_b, T_b, p_b):

    T(h) = T_b + L (h - h_b)
    p(h) = p_b (T_b / T)^(g0 M / (R* L))            if L != 0
    p(h) = p_b exp(-g0 M (h - h_b) / (R* T_b))       if L == 0
    rho  = p / (R_air T)

Geometric altitude z and geopotential altitude h are related by
h = r0 z / (r0 + z), r0 = 6 356 766 m (USSA76 eq. 18). All public functions
take GEOMETRIC altitude in metres unless their name says otherwise.

Live profiles come from the Open-Meteo forecast API (pressure-level variables).
Between levels, ln(p), T and the wind components are interpolated linearly in
altitude. Above the highest live level the profile blends into ISA over
`blend_m`; above that it is pure ISA. Every sample carries a `source` label:
`open-meteo@<valid hour>`, `blend` or `isa-fallback`.
"""
from __future__ import annotations

import bisect
import datetime as dt
import json
import math
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

# --- USSA76 constants (duplicated from config/mission.yaml `constants` so the
# --- model is usable standalone; tests assert both agree).
G0 = 9.80665
R_STAR = 8.31432
M_AIR = 0.0289644
R_AIR = 287.053
R0_EARTH = 6356766.0
GMR = G0 * M_AIR / R_STAR  # 0.034163195 K/m

# (base geopotential altitude m, lapse rate K/m)
_LAYERS: list[tuple[float, float]] = [
    (0.0, -0.0065),
    (11000.0, 0.0),
    (20000.0, 0.0010),
    (32000.0, 0.0028),
    (47000.0, 0.0),
    (51000.0, -0.0028),
    (71000.0, -0.0020),
]
H_TOP = 84852.0  # m geopotential (86 km geometric)


def _build_bases() -> list[tuple[float, float, float, float]]:
    """(h_b, L, T_b, p_b) for every layer, integrated from sea level."""
    out = []
    T, p = 288.15, 101325.0
    for i, (hb, L) in enumerate(_LAYERS):
        out.append((hb, L, T, p))
        h_next = _LAYERS[i + 1][0] if i + 1 < len(_LAYERS) else H_TOP
        T, p = _layer_tp(h_next, hb, L, T, p)
    out.append((H_TOP, 0.0, T, p))  # isothermal extension above 84.852 km (USSA76 itself stops at 86 km geometric)
    return out


def _layer_tp(h: float, hb: float, L: float, Tb: float, pb: float) -> tuple[float, float]:
    if L == 0.0:
        return Tb, pb * math.exp(-GMR * (h - hb) / Tb)
    T = Tb + L * (h - hb)
    return T, pb * (Tb / T) ** (GMR / L)


_BASES = _build_bases()
_BASE_H = [b[0] for b in _BASES]


def geometric_to_geopotential(z: float) -> float:
    return R0_EARTH * z / (R0_EARTH + z)


def geopotential_to_geometric(h: float) -> float:
    return R0_EARTH * h / (R0_EARTH - h)


def gravity(z: float) -> float:
    """Gravity magnitude at geometric altitude z (inverse-square, USSA76 eq. 17)."""
    return G0 * (R0_EARTH / (R0_EARTH + z)) ** 2


def isa_geopotential(h: float) -> tuple[float, float, float]:
    """(T K, p Pa, rho kg/m^3) at geopotential altitude h (m). Extrapolates below 0 with layer 0."""
    i = max(0, bisect.bisect_right(_BASE_H, h) - 1)
    hb, L, Tb, pb = _BASES[i]
    T, p = _layer_tp(h, hb, L, Tb, pb)
    return T, p, p / (R_AIR * T)


def isa(z: float) -> tuple[float, float, float]:
    """(T K, p Pa, rho kg/m^3) at geometric altitude z (m)."""
    return isa_geopotential(geometric_to_geopotential(z))


def isa_altitude_for_pressure(p: float) -> float:
    """Geometric altitude (m) at which ISA has pressure p (inverse of `isa`)."""
    h = _isa_geopotential_for_pressure(p)
    return geopotential_to_geometric(h)


def _isa_geopotential_for_pressure(p: float) -> float:
    for i in range(len(_BASES) - 1, -1, -1):
        hb, L, Tb, pb = _BASES[i]
        if p <= pb or i == 0:
            if L == 0.0:
                return hb - Tb * math.log(p / pb) / GMR
            return hb + (Tb * (p / pb) ** (-L / GMR) - Tb) / L
    raise AssertionError("unreachable")


# ---------------------------------------------------------------------------
# Atmosphere samples and profiles
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AtmoSample:
    T: float  # K
    p: float  # Pa
    rho: float  # kg/m^3
    u: float  # m/s, wind towards east
    v: float  # m/s, wind towards north
    source: str  # open-meteo@<valid hour> | blend | isa-fallback

    @property
    def wind_speed(self) -> float:
        return math.hypot(self.u, self.v)

    @property
    def wind_from_deg(self) -> float:
        """Meteorological convention: direction the wind blows FROM, degrees clockwise from north."""
        return math.degrees(math.atan2(-self.u, -self.v)) % 360.0


def wind_components(speed: float, from_deg: float) -> tuple[float, float]:
    """(u east, v north) from speed and meteorological 'from' direction."""
    rad = math.radians(from_deg)
    return -speed * math.sin(rad), -speed * math.cos(rad)


@dataclass
class AtmosphereProfile:
    """Vertical profile. Empty `z` means ISA only (no live data, zero wind)."""

    z: list[float] = field(default_factory=list)  # geometric m, ascending
    lnp: list[float] = field(default_factory=list)
    T: list[float] = field(default_factory=list)
    u: list[float] = field(default_factory=list)
    v: list[float] = field(default_factory=list)
    label: str = "isa-fallback"  # e.g. "open-meteo@2026-10-12T15:00Z"
    blend_m: float = 2000.0
    note: str = ""  # why this profile was chosen (e.g. fetch error); never silent
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def is_live(self) -> bool:
        return len(self.z) >= 2

    @property
    def z_top(self) -> float:
        return self.z[-1] if self.z else float("-inf")

    def at(self, z: float) -> AtmoSample:
        if not self.is_live:
            T, p, rho = isa(z)
            return AtmoSample(T, p, rho, 0.0, 0.0, "isa-fallback")

        zs = self.z
        if z <= zs[-1]:
            if z <= zs[0]:
                # Below the lowest level: hydrostatic extrapolation at the lowest level's temperature.
                T = self.T[0]
                p = math.exp(self.lnp[0]) * math.exp(-gravity(z) * (z - zs[0]) / (R_AIR * T))
                return AtmoSample(T, p, p / (R_AIR * T), self.u[0], self.v[0], self.label)
            i = bisect.bisect_right(zs, z) - 1
            k = (z - zs[i]) / (zs[i + 1] - zs[i])
            T = self.T[i] + k * (self.T[i + 1] - self.T[i])
            p = math.exp(self.lnp[i] + k * (self.lnp[i + 1] - self.lnp[i]))
            u = self.u[i] + k * (self.u[i + 1] - self.u[i])
            v = self.v[i] + k * (self.v[i + 1] - self.v[i])
            return AtmoSample(T, p, p / (R_AIR * T), u, v, self.label)

        # Above the top live level: offset ISA to match the top level, fade the offset out over blend_m.
        T_isa_top, p_isa_top, _ = isa(zs[-1])
        dT = self.T[-1] - T_isa_top
        dlnp = self.lnp[-1] - math.log(p_isa_top)
        w = min(1.0, (z - zs[-1]) / self.blend_m) if self.blend_m > 0 else 1.0
        T_isa, p_isa, _ = isa(z)
        T = T_isa + dT * (1.0 - w)
        p = p_isa * math.exp(dlnp * (1.0 - w))
        # Wind above the top level is held at the top-level value (no ISA wind exists).
        src = "blend" if w < 1.0 else "isa-fallback"
        return AtmoSample(T, p, p / (R_AIR * T), self.u[-1], self.v[-1], src)

    def altitude_for_pressure(self, p: float) -> float:
        """Invert the profile: geometric altitude with pressure p (monotone p(z)).

        Inside the live levels: exact inverse of the ln(p)-linear interpolation. Elsewhere: bisection.
        """
        if self.is_live and self.lnp[-1] <= math.log(p) <= self.lnp[0]:
            lp = math.log(p)
            neg = [-v for v in self.lnp]  # ascending
            i = min(max(bisect.bisect_right(neg, -lp) - 1, 0), len(self.z) - 2)
            k = (lp - self.lnp[i]) / (self.lnp[i + 1] - self.lnp[i])
            return self.z[i] + k * (self.z[i + 1] - self.z[i])
        if not self.is_live:
            return isa_altitude_for_pressure(p)
        lo, hi = -500.0, 90000.0
        for _ in range(60):
            mid = 0.5 * (lo + hi)
            if self.at(mid).p > p:
                lo = mid
            else:
                hi = mid
        return 0.5 * (lo + hi)

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "note": self.note,
            "levels": [
                {"z_m": z, "p_pa": math.exp(lp), "T_k": T, "u_ms": u, "v_ms": v}
                for z, lp, T, u, v in zip(self.z, self.lnp, self.T, self.u, self.v)
            ],
            "blend_m": self.blend_m,
            "meta": self.meta,
        }


# ---------------------------------------------------------------------------
# Open-Meteo
# ---------------------------------------------------------------------------


def open_meteo_url(base_url: str, lat: float, lon: float, levels_hpa: Sequence[int]) -> str:
    variables = []
    for lvl in levels_hpa:
        variables += [
            f"temperature_{lvl}hPa",
            f"wind_speed_{lvl}hPa",
            f"wind_direction_{lvl}hPa",
            f"geopotential_height_{lvl}hPa",
        ]
    q = {
        "latitude": f"{lat:.4f}",
        "longitude": f"{lon:.4f}",
        "hourly": ",".join(variables),
        "wind_speed_unit": "ms",
        "timezone": "UTC",
        "past_days": 1,
        "forecast_days": 2,
    }
    return f"{base_url}?{urllib.parse.urlencode(q)}"


def _nearest_hour_index(times: Sequence[str], now: dt.datetime) -> int:
    best, best_err = 0, float("inf")
    for i, t in enumerate(times):
        stamp = dt.datetime.fromisoformat(t).replace(tzinfo=dt.timezone.utc)
        err = abs((stamp - now).total_seconds())
        if err < best_err:
            best, best_err = i, err
    return best


def profile_from_open_meteo(
    payload: Mapping[str, Any],
    levels_hpa: Sequence[int],
    now: dt.datetime | None = None,
    blend_m: float = 2000.0,
    label_suffix: str = "",
) -> AtmosphereProfile:
    """Build a profile from an Open-Meteo response. Uses only levels actually served (non-null).

    Raises ValueError when fewer than 3 usable levels remain.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    hourly = payload["hourly"]
    times = hourly["time"]
    idx = _nearest_hour_index(times, now)
    rows = []
    dropped = []
    for lvl in levels_hpa:
        keys = (f"geopotential_height_{lvl}hPa", f"temperature_{lvl}hPa", f"wind_speed_{lvl}hPa", f"wind_direction_{lvl}hPa")
        vals = [hourly.get(k, [None] * len(times))[idx] if k in hourly else None for k in keys]
        if any(v is None for v in vals):
            dropped.append(lvl)
            continue
        zgp, t_c, ws, wd = (float(v) for v in vals)
        u, v = wind_components(ws, wd)
        rows.append((geopotential_to_geometric(zgp), math.log(lvl * 100.0), t_c + 273.15, u, v))
    rows.sort()
    # Pressure must fall with altitude; drop any level that breaks monotonicity.
    clean = []
    for r in rows:
        if not clean or (r[0] > clean[-1][0] and r[1] < clean[-1][1]):
            clean.append(r)
        else:
            dropped.append(round(math.exp(r[1]) / 100))
    if len(clean) < 3:
        raise ValueError(f"only {len(clean)} usable pressure levels in Open-Meteo response")
    valid = times[idx]
    prof = AtmosphereProfile(
        z=[r[0] for r in clean],
        lnp=[r[1] for r in clean],
        T=[r[2] for r in clean],
        u=[r[3] for r in clean],
        v=[r[4] for r in clean],
        label=f"open-meteo@{valid}Z{label_suffix}",
        blend_m=blend_m,
        note=f"{len(clean)} levels used" + (f"; levels not served/invalid: {sorted(set(dropped))} hPa" if dropped else ""),
        meta={"valid_time_utc": valid, "levels_used_hpa": [round(math.exp(r[1]) / 100) for r in clean]},
    )
    return prof


_cache_lock = threading.Lock()
_cache: dict[tuple[float, float], tuple[float, AtmosphereProfile]] = {}


def _get_json(url: str, timeout: float, retries: int = 1) -> Any:
    """GET JSON; retries once on HTTP 5xx (Open-Meteo returns transient 503s)."""
    import urllib.error

    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "AERO-FIRE/3.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code < 500 or attempt == retries:
                raise
            time.sleep(1.0)
    raise AssertionError("unreachable")


def fetch_profile(cfg: Any, lat: float, lon: float, now: dt.datetime | None = None, allow_network: bool = True) -> AtmosphereProfile:
    """Live profile for (lat, lon), cached for cfg.atmosphere.cache_s. Falls back to ISA with an explicit note.

    Blocking (urllib); call it from a worker thread in async code.
    """
    acfg = cfg.atmosphere
    key = (round(lat, 2), round(lon, 2))
    with _cache_lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < acfg.cache_s:
            return hit[1]
    if not allow_network:
        return AtmosphereProfile(blend_m=acfg.blend_m, note="network disabled: ISA 1976, zero wind")
    url = open_meteo_url(acfg.open_meteo_url, lat, lon, acfg.levels_hpa)
    try:
        payload = _get_json(url, acfg.timeout_s)
        prof = profile_from_open_meteo(payload, acfg.levels_hpa, now=now, blend_m=acfg.blend_m)
        prof.meta["fetched_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    except Exception as exc:  # noqa: BLE001 - any failure -> labelled fallback
        prof = AtmosphereProfile(blend_m=acfg.blend_m, note=f"Open-Meteo unavailable ({type(exc).__name__}: {exc}); ISA 1976, zero wind")
    with _cache_lock:
        _cache[key] = (time.time(), prof)
    return prof


def load_snapshot(path: str, levels_hpa: Sequence[int], blend_m: float = 2000.0) -> AtmosphereProfile:
    """Profile from a recorded Open-Meteo response (deterministic tests and offline demos)."""
    with open(path, encoding="utf-8") as fh:
        snap = json.load(fh)
    now = dt.datetime.fromisoformat(snap["recorded_at_utc"])
    prof = profile_from_open_meteo(snap["payload"], levels_hpa, now=now, blend_m=blend_m, label_suffix=" (recorded)")
    prof.meta["recorded_at_utc"] = snap["recorded_at_utc"]
    return prof
