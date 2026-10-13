"""Audit A5: physics spot-checks against hand values (prints a markdown table; changes nothing).

    python tools/audit/physics_check.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aerofire.config import load_config, load_raw  # noqa: E402
from aerofire.physics import atmosphere as A  # noqa: E402
from aerofire.physics import balloon as Bl  # noqa: E402
from aerofire.physics import descent as Ds  # noqa: E402
from aerofire.physics import pendulum as Pd  # noqa: E402
from aerofire.physics import tank as Tk  # noqa: E402

G0 = 9.80665
rows = []


def check(name: str, got: float, want: float, rel_tol: float, source: str) -> None:
    ok = abs(got - want) <= rel_tol * abs(want)
    rows.append((name, got, want, rel_tol, "PASS" if ok else "FAIL", source))


# ISA (US Standard Atmosphere 1976 table values, geopotential altitudes)
for h, p, T in ((11000.0, 22632.06, 216.65), (20000.0, 5474.889, 216.65), (32000.0, 868.0187, 228.65)):
    Tm, pm, _ = A.isa_geopotential(h)
    check(f"ISA p @ {h / 1000:.0f} km (geopotential)", pm, p, 1e-4, "USSA-1976 table")
    check(f"ISA T @ {h / 1000:.0f} km (geopotential)", Tm, T, 1e-5, "USSA-1976 table")

# Terminal speed sqrt(2 m g / (rho Cd A)) at sea level, 10 kg, Cd 1.5, D 3 m
chute = Ds.ParachuteParams(1.5, 3.0)

T0, p0, rho0 = A.isa(0.0)
hand = math.sqrt(2 * 10.0 * G0 / (rho0 * 1.5 * math.pi * 1.5 ** 2))
sample = type("S", (), {"rho": rho0})()
check("terminal speed 10 kg, Cd 1.5, D 3 m, sea level", Ds.terminal_velocity(chute, 10.0, sample, 0.0), hand, 1e-3, "hand formula")

# Choked flow, ideal gas (B2 = 0): mdot = Cd A P sqrt(g/(R T)) (2/(g+1))^((g+1)/(2(g-1)))
tp = Tk.TankParams(V=0.01, B2=0.0)
P, T, A_h = 10e5, 293.15, Tk.diameter_to_area(100e-6)
g, R = tp.gamma, tp.R
hand = tp.Cd * A_h * P * math.sqrt(g / (R * T)) * (2 / (g + 1)) ** ((g + 1) / (2 * (g - 1)))
check("choked He flow, 100 um, 10 bar, ideal", Tk.orifice_mass_flow(tp, P, T, 1e5, A_h), hand, 1e-6, "isentropic nozzle formula")

# Pendulum small-angle period 2 pi sqrt(L/g)
cfg = load_config()
pp = Pd.PendulumParams(cfg.pendulum.length_m, 0.0)
check(f"pendulum period L = {pp.L} m", 2 * math.pi / pp.omega, 2 * math.pi * math.sqrt(pp.L / pp.g), 1e-9, "2 pi sqrt(L/g)")

# Float equilibrium: buoyancy = weight at the computed float altitude
raw = load_raw()
prof = A.load_snapshot(str(Path(__file__).resolve().parents[2] / "config" / "atmosphere_snapshot.json"), raw["atmosphere"]["levels_hpa"], raw["atmosphere"]["blend_m"])
b, r = cfg.balloon, cfg.rocket
params = Bl.BalloonParams(b.m_envelope_kg + b.m_gondola_kg + r.m_dry_kg + r.m_prop_kg, b.V_max_m3, b.Cd, R_gas=cfg.constants.R_He)
zf = Bl.float_altitude(params, prof.at, b.m_ballast_kg)
a = prof.at(zf)
lift = (a.rho - a.p / (params.R_gas * a.T)) * params.V_max
check(f"float equilibrium at z = {zf:.0f} m: net lift (kg) / load", lift / (params.m_fixed + b.m_ballast_kg), 1.0, 1e-4, "Archimedes")

print("| check | model | hand / table | rel. tol | result | reference |")
print("|---|---|---|---|---|---|")
for n, got, want, tol, res, src in rows:
    print(f"| {n} | {got:.6g} | {want:.6g} | {tol:g} | {res} | {src} |")
sys.exit(0 if all(r[4] == "PASS" for r in rows) else 1)
