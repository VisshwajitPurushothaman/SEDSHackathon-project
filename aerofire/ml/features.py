"""ML features for the altitude/position forecast correction. Ground-side inputs only.

Shared by the offline dataset builder and the runtime predictor so both compute exactly the
same features. Inputs: the fused state (ground estimator), the physics forecast and the forecast
atmosphere. No simulator truth. (The filter's altitude sigma is not a feature: it is nearly constant in
training and only made the runtime range check fragile.)
"""
from __future__ import annotations

import math

FEATURES = (
    "is_float", "t_in_phase", "z", "vz", "vz_mean", "ve", "vn", "u_fc", "v_fc", "dve", "dvn",
    "phys_dz", "phys_de", "phys_dn", "z_float_gap",
)
TARGETS = ("z", "east", "north")
PHASES = ("ASCENT", "FLOAT")


def build(state: dict[str, float], phase: str, t_in_phase: float, phys_h: dict[str, float], wind: tuple[float, float],
          z_float: float) -> list[float]:
    hd = math.radians(state["heading"])
    ve, vn = state["gs"] * math.sin(hd), state["gs"] * math.cos(hd)
    u, v = wind
    f = {
        "is_float": 1.0 if phase == "FLOAT" else 0.0, "t_in_phase": t_in_phase, "z": state["z"], "vz": state["vz"],
        "vz_mean": state["vz_mean"], "ve": ve, "vn": vn, "u_fc": u, "v_fc": v,
        "dve": ve - u, "dvn": vn - v, "phys_dz": phys_h["z"] - state["z"], "phys_de": phys_h["east"] - state["east"],
        "phys_dn": phys_h["north"] - state["north"], "z_float_gap": z_float - state["z"],
    }
    return [float(f[k]) for k in FEATURES]
