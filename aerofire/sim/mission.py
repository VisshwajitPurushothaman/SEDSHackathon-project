"""TRUTH mission simulator: the vehicle and the world.

Integrates the balloon, pressurant tank, motor thermal state and gondola
pendulum on a fixed step, runs the vehicle's phase machine and injects
faults. It produces (a) a truth frame per step, for grading only, and (b) raw
sensor records via `aerofire.telemetry.sensors`, the only thing the ground
station is allowed to see.

The simulator never decides to launch, release or cut down on its own: those
happen only through `commit()` and `terminate()`, which the API calls on an
explicit operator action. (Ballast drops for float keeping are onboard flight
control, see float_control.py.)
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from ..physics import atmosphere as A
from ..physics import balloon as Bl
from ..physics import descent as Ds
from ..physics import geo
from ..physics import motor_thermal as Mt
from ..physics import pendulum as Pd
from ..physics import rocket as Rk
from ..physics import tank as Tk
from .float_control import BallastController

PHASES = ["PAD", "ASCENT", "FLOAT", "IGNITION", "CUTDOWN", "DESCENT", "LANDED"]
ROCKET_PHASES = ["ABOARD", "BURN", "COAST", "IMPACT"]


@dataclass
class Faults:
    tank_leak_um: float = 0.0
    tank_leak_onset_s: float = 0.0
    balloon_leak_mm: float = 0.0  # envelope tear diameter
    balloon_leak_onset_s: float = 0.0
    gnss_spoof_start_s: float | None = None
    gnss_spoof_drift_m_s: tuple[float, float] = (2.0, 0.0)  # east, north drift of the spoofed fix
    motor_heater_failed: bool = False
    blackouts: list[tuple[float, float]] = field(default_factory=list)  # link only (aerofire.telemetry.link)
    packet_loss: float | None = None  # override config link loss
    latency_s: float | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> "Faults":
        d = dict(d or {})
        if "gnss_spoof_drift_m_s" in d:
            d["gnss_spoof_drift_m_s"] = tuple(d["gnss_spoof_drift_m_s"])
        if "blackouts" in d:
            d["blackouts"] = [tuple(b) for b in d["blackouts"]]
        return cls(**d)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TruthFrame:
    t: float
    phase: str
    rocket_phase: str
    lat: float
    lon: float
    z: float
    vz: float
    east: float
    north: float
    atmo: dict[str, Any]
    balloon: dict[str, Any]
    tank: dict[str, Any]
    motor: dict[str, Any]
    swing: dict[str, Any]
    rocket: dict[str, Any] | None
    events: list[str]


class MissionSim:
    def __init__(self, cfg, atmosphere: A.AtmosphereProfile, faults: Faults | None = None, seed: int = 0):
        self.cfg = cfg
        self.prof = atmosphere
        self.atmo = atmosphere.at
        self.faults = faults or Faults()
        self.rng = np.random.default_rng(seed)
        self.dt = cfg.sim.dt_s
        self.t = 0.0
        self.phase = "PAD"
        self.rocket_phase = "ABOARD"
        self.lat0, self.lon0 = cfg.site.lat_deg, cfg.site.lon_deg
        self.ground_z = cfg.site.elevation_m
        self.pad_hold_s = cfg.sim.pad_hold_s

        b, r = cfg.balloon, cfg.rocket
        self.m_rocket = r.m_dry_kg + r.m_prop_kg
        self.bparams = Bl.BalloonParams(b.m_envelope_kg + b.m_gondola_kg + self.m_rocket, b.V_max_m3, b.Cd,
                                        R_gas=cfg.constants.R_He, added_mass_coeff=b.added_mass_coeff,
                                        leak_dp_pa=b.envelope_leak_dp_pa, leak_cd=b.envelope_leak_cd)
        a0 = self.atmo(self.ground_z)
        self.bstate = Bl.BalloonState(self.ground_z, 0.0, Bl.launch_gas_mass(self.bparams, a0, b.free_lift_fraction, b.m_ballast_kg), b.m_ballast_kg)
        self.bout = Bl.BalloonOutputs(Bl.gas_volume(self.bstate.m_gas, a0.T, a0.p, self.bparams.R_gas), 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        self.ballast_ctl = BallastController.from_config(b)

        self.tparams = Tk.TankParams.from_config(cfg)
        self.tstate = Tk.initial_state(self.tparams, cfg.tank.P0_bar * 1e5, cfg.tank.T0_c + 273.15)
        self.tank_mdot = 0.0

        self.mparams = Mt.MotorParams.from_config(cfg, heater_failed=self.faults.motor_heater_failed)
        self.motor_T = cfg.motor.T0_c + 273.15
        self.heater_on = False

        self.pparams = Pd.PendulumParams(cfg.pendulum.length_m, cfg.pendulum.damping_ratio)
        self.swing = [0.0, 0.0, 0.0, 0.0]  # theta_e, rate_e, theta_n, rate_n (rad, rad/s)
        self._prev_wind = (a0.u, a0.v)

        self.chute = Ds.ParachuteParams(cfg.descent.parachute_cd, cfg.descent.parachute_diameter_m)
        self.rparams = Rk.RocketParams.from_config(cfg)
        self.rocket_result: Rk.RocketResult | None = None
        self.t_release: float | None = None
        self.release_point: tuple[float, float, float] | None = None  # lat, lon, z
        self.t_float: float | None = None
        self.t_cutdown: float | None = None
        self.descent_mass: float | None = None
        self.vz = 0.0
        self.events: list[str] = []
        self.event_log: list[tuple[float, str]] = []
        self._pending_commit = False
        self._pending_terminate = False

    # ------------------------------------------------------------------ operator commands

    def commit(self) -> None:
        """Operator committed: start the ignition countdown (only valid at float)."""
        if self.phase != "FLOAT" or self._pending_commit:
            raise ValueError(f"cannot commit in phase {self.phase}")
        self._pending_commit = True

    def terminate(self) -> None:
        """Operator terminated the flight without launch: cut down with the rocket aboard."""
        if self.phase not in ("ASCENT", "FLOAT"):
            raise ValueError(f"cannot terminate in phase {self.phase}")
        self._pending_terminate = True

    @property
    def done(self) -> bool:
        rocket_done = self.rocket_result is None or self.rocket_phase == "IMPACT"
        return self.phase == "LANDED" and rocket_done

    # ------------------------------------------------------------------ stepping

    def _event(self, msg: str) -> None:
        self.events.append(msg)
        self.event_log.append((self.t, msg))

    def step(self) -> TruthFrame:
        self.events = []
        dt = self.dt
        f = self.faults

        if self.phase == "PAD":
            if self.t >= self.pad_hold_s:
                self.phase = "ASCENT"
                self._event("balloon released from pad")
        if self._pending_terminate:
            self._pending_terminate = False
            self._cutdown(with_rocket=True)
        if self._pending_commit and self.phase == "FLOAT":
            self.phase = "IGNITION"
            self._ignite_at = self.t + self.cfg.sim.commit_to_ignition_s
            self._event("OPERATOR COMMIT received: ignition countdown started")
        if self.phase == "IGNITION" and self.rocket_result is None and self.t >= self._ignite_at:
            self._pending_commit = False
            self._ignite()

        flying = self.phase in ("ASCENT", "FLOAT", "IGNITION")
        if flying:
            leak_area = Tk.diameter_to_area(f.balloon_leak_mm * 1e-3) if (f.balloon_leak_mm > 0 and self.t >= f.balloon_leak_onset_s) else 0.0
            a_now = self.atmo(self.bstate.z)
            drop = self.ballast_ctl.command(self.t, self.bstate.z, self.bstate.vz, self.bout.vent_rate_kg_s > 0, a_now, dt)
            self.bstate, self.bout = Bl.step(self.bparams, self.bstate, self.atmo, dt, leak_area_m2=leak_area,
                                             ground_z=self.ground_z, ballast_drop_kg_s=drop)
            self.vz = self.bstate.vz
            if self.phase == "ASCENT" and self.bout.vent_rate_kg_s > 0:
                self.phase = "FLOAT"
                self.t_float = self.t + dt
                self._event("balloon at full volume: venting through base ducts (float)")
            self._step_swing(dt)
        elif self.phase == "DESCENT":
            z, vz, e, n = Ds.step_dynamic(self.chute, self.descent_mass, self.bstate.z, self.vz, self.bstate.east, self.bstate.north, self.atmo, dt)
            if z <= self.ground_z:
                z, vz = self.ground_z, 0.0
                self.phase = "LANDED"
                self._event("gondola landed")
            self.bstate = Bl.BalloonState(z, vz, 0.0, 0.0, e, n)
            self.vz = vz
        elif self.phase == "CUTDOWN":
            self.phase = "DESCENT"

        a = self.atmo(max(self.bstate.z, 0.0))

        # Tank: rides with the rocket. After release it is no longer observable in this model.
        if self.rocket_phase == "ABOARD":
            area = Tk.diameter_to_area(f.tank_leak_um * 1e-6) if (f.tank_leak_um > 0 and self.t >= f.tank_leak_onset_s) else 0.0
            self.tstate, self.tank_mdot = Tk.step(self.tparams, self.tstate, a.T, a.p, a.rho, dt, area)
            self.heater_on = Mt.heater_command(self.mparams, self.motor_T, self.heater_on)
            self.motor_T = Mt.step(self.mparams, self.motor_T, a.T, a.rho, self.heater_on, dt)

        self._step_rocket()
        self.t += dt
        return self.frame(a)

    def _step_swing(self, dt: float) -> None:
        a = self.atmo(self.bstate.z)
        sig = self.cfg.pendulum.gust_accel_sigma_m_s2
        acc_e = (a.u - self._prev_wind[0]) / dt + self.rng.normal(0.0, sig)
        acc_n = (a.v - self._prev_wind[1]) / dt + self.rng.normal(0.0, sig)
        self._prev_wind = (a.u, a.v)
        if self.phase == "PAD":
            return
        sub = self.cfg.pendulum.substeps
        self.swing[0], self.swing[1] = Pd.step(self.pparams, self.swing[0], self.swing[1], acc_e, dt, sub)
        self.swing[2], self.swing[3] = Pd.step(self.pparams, self.swing[2], self.swing[3], acc_n, dt, sub)

    def _ignite(self) -> None:
        self.t_release = self.t
        lat, lon = self.latlon()
        self.release_point = (lat, lon, self.bstate.z)
        # Rocket flight (illustrative model), air-relative; ground track adds the release-altitude wind.
        a = self.atmo(self.bstate.z)
        self.release_wind = (a.u, a.v)
        self.rocket_result = Rk.simulate(self.rparams, self.bstate.z, self.atmo, ignite=True, case="truth")
        self.rocket_phase = "BURN"
        self._event("ignition: rocket released from rail")
        # The committed sequence includes the gondola cut-down after the rocket has cleared.
        self._cutdown_at = self.t + self.cfg.sim.cutdown_delay_s

    def _cutdown(self, with_rocket: bool) -> None:
        self.phase = "CUTDOWN"
        self.t_cutdown = self.t
        b = self.cfg.balloon
        self.descent_mass = b.m_gondola_kg + self.bstate.m_ballast + (self.m_rocket if with_rocket else 0.0)
        self._event("cut-down: gondola separated from balloon, parachute descent" + (" (rocket aboard, no launch)" if with_rocket else ""))

    def _step_rocket(self) -> None:
        if self.rocket_result is None or self.t_release is None:
            return
        tr = self.t - self.t_release
        if self.rocket_phase == "BURN" and tr >= self.rparams.burn_time:
            self.rocket_phase = "COAST"
            self._event("rocket burnout")
        if self.rocket_phase in ("BURN", "COAST") and tr >= self.rocket_result.impact_t_s:
            self.rocket_phase = "IMPACT"
            self._event("rocket impact (illustrative model)")
        if self.phase == "IGNITION" and self.t >= self._cutdown_at:
            self._cutdown(with_rocket=False)

    # ------------------------------------------------------------------ outputs

    def latlon(self) -> tuple[float, float]:
        return geo.enu_to_latlon(self.bstate.east, self.bstate.north, self.lat0, self.lon0)

    def rocket_position(self) -> dict[str, Any] | None:
        if self.rocket_result is None or self.t_release is None:
            return None
        tr = self.t - self.t_release
        s = self.rocket_result.samples
        i = min(range(len(s)), key=lambda k: abs(s[k][0] - tr))
        ts, x, z, v = s[i]
        lat, lon = Rk.position_latlon(self.rparams, x, ts, self.release_point[0], self.release_point[1], *self.release_wind)
        return {"t_since_release": tr, "downrange": x, "z": z, "speed": v, "lat": lat, "lon": lon, "phase": self.rocket_phase}

    def frame(self, a: A.AtmoSample) -> TruthFrame:
        lat, lon = self.latlon()
        P = Tk.pressure(self.tparams, self.tstate.m, self.tstate.T)
        amp_e = Pd.amplitude(self.pparams, self.swing[0], self.swing[1])
        amp_n = Pd.amplitude(self.pparams, self.swing[2], self.swing[3])
        return TruthFrame(
            t=self.t,
            phase=self.phase,
            rocket_phase=self.rocket_phase,
            lat=lat,
            lon=lon,
            z=self.bstate.z,
            vz=self.vz,
            east=self.bstate.east,
            north=self.bstate.north,
            atmo={"T": a.T, "p": a.p, "rho": a.rho, "u": a.u, "v": a.v, "source": a.source},
            balloon={"volume": self.bout.volume_m3, "vent_rate": self.bout.vent_rate_kg_s, "leak_rate": self.bout.leak_rate_kg_s,
                     "m_gas": self.bstate.m_gas, "ballast": self.bstate.m_ballast, "ballast_drop": self.bout.ballast_drop_kg_s,
                     "free_lift": self.bout.free_lift_n},
            tank={"m": self.tstate.m, "T": self.tstate.T, "P": P, "mdot": self.tank_mdot},
            motor={"T": self.motor_T, "heater_on": self.heater_on},
            swing={"theta_e": self.swing[0], "rate_e": self.swing[1], "theta_n": self.swing[2], "rate_n": self.swing[3],
                   "amplitude_deg": math.degrees(math.hypot(amp_e, amp_n))},
            rocket=self.rocket_position(),
            events=list(self.events),
        )
