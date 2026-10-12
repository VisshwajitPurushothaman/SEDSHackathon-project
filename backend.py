#!/usr/bin/env python3
"""
AERO-FIRE: Backend API for Rockoon Autonomy & Leak Detection
------------------------------------------------------------
FastAPI service exposing live atmospheric ingestion,
temperature-compensated mass balance, and launch safety gates.
"""

import math
import time
import json
import asyncio
import urllib.request
from typing import Dict, Any, List
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

app = FastAPI(title="AERO-FIRE Flight Director API", version="2.0")

# Enable CORS for all local frontend ports (Vite 5173, React 3000, etc.)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==============================================================================
# 1. LIVE ATMOSPHERE SOUNDING ENGINE
# ==============================================================================

class LiveAtmosphereProfile:
    def __init__(self, lat: float = 13.72, lon: float = 80.23):
        self.lat = lat
        self.lon = lon
        self.levels = [1000, 925, 850, 700, 500, 300, 250, 200, 150, 100, 70, 50, 30, 20, 10]
        self.profile = self.fetch_live_sounding()

    def fetch_live_sounding(self) -> List[Dict[str, float]]:
        query_vars = []
        for lvl in self.levels:
            query_vars.extend([
                f"temperature_{lvl}hPa",
                f"windspeed_{lvl}hPa",
                f"geopotential_height_{lvl}hPa"
            ])
        
        url = (
            f"https://api.open-meteo.com/v1/forecast?latitude={self.lat}&longitude={self.lon}"
            f"&hourly={','.join(query_vars)}&forecast_days=1"
        )
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "AERO-FIRE/2.0"})
            with urllib.request.urlopen(req, timeout=4) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                hourly = data["hourly"]
                profile = []
                for lvl in self.levels:
                    profile.append({
                        "pressure_pa": lvl * 100.0,
                        "altitude_m": float(hourly[f"geopotential_height_{lvl}hPa"][0]),
                        "temp_c": float(hourly[f"temperature_{lvl}hPa"][0]),
                        "wind_speed_ms": float(hourly[f"windspeed_{lvl}hPa"][0]) / 3.6
                    })
                profile.sort(key=lambda x: x["altitude_m"])
                return profile
        except Exception:
            return self._fallback_standard_atmosphere()

    def _fallback_standard_atmosphere(self) -> List[Dict[str, float]]:
        profile = []
        for lvl in self.levels:
            p_pa = lvl * 100.0
            z = 44330.0 * (1.0 - (p_pa / 101325.0) ** 0.1903)
            if z < 11000:
                t = 28.0 - (0.0065 * z)
            elif z < 20000:
                t = -68.0
            else:
                t = -68.0 + (0.0015 * (z - 20000))
            ws = 6.0 + 28.0 * math.exp(-((z - 14000) / 3500) ** 2)
            profile.append({"pressure_pa": p_pa, "altitude_m": z, "temp_c": t, "wind_speed_ms": ws})
        profile.sort(key=lambda x: x["altitude_m"])
        return profile

    def interpolate(self, z: float):
        alts = [d["altitude_m"] for d in self.profile]
        pressures = [d["pressure_pa"] for d in self.profile]
        temps = [d["temp_c"] for d in self.profile]
        winds = [d["wind_speed_ms"] for d in self.profile]

        if z <= alts[0]:
            return pressures[0], temps[0], winds[0]
        if z >= alts[-1]:
            return pressures[-1], temps[-1], winds[-1]

        for i in range(len(alts) - 1):
            if alts[i] <= z <= alts[i + 1]:
                factor = (z - alts[i]) / (alts[i + 1] - alts[i])
                p = pressures[i] + factor * (pressures[i + 1] - pressures[i])
                t = temps[i] + factor * (temps[i + 1] - temps[i])
                w = winds[i] + factor * (winds[i + 1] - winds[i])
                return p, t, w
        return pressures[0], temps[0], winds[0]


# ==============================================================================
# 2. CORE SIMULATION GENERATOR
# ==============================================================================

def execute_flight_engine(puncture_um: float = 0.0):
    sounding = LiveAtmosphereProfile(lat=13.72, lon=80.23)

    V_tank = 0.5
    R_gas = 2077.1
    gamma = 1.667
    Cd = 0.62

    P_tank = 200e5
    T_tank = 26.0
    T_motor = 20.0

    initial_mass = (P_tank * V_tank) / (R_gas * (T_tank + 273.15))
    current_mass = initial_mass

    puncture_d_m = puncture_um * 1e-6
    puncture_area_m2 = (math.pi / 4.0) * (puncture_d_m ** 2)

    altitude = 0.0
    ascent_rate = 120.0
    dt = 1.0
    sim_time = 0.0
    last_emit_alt = -3000.0

    while altitude <= 30000.0:
        sim_time += dt
        altitude += ascent_rate * dt

        p_amb, t_amb, wind_speed = sounding.interpolate(altitude)

        # Thermal dissipation
        T_tank += -0.002 * (T_tank - t_amb) * dt
        T_motor = max(12.0, T_motor - 0.0005 * (T_motor - t_amb) * dt)

        # Choked sonic orifice flow
        if puncture_area_m2 > 0:
            t_k = T_tank + 273.15
            m_dot = (
                Cd * puncture_area_m2 * P_tank
                * math.sqrt(gamma / (R_gas * t_k))
                * ((2.0 / (gamma + 1.0)) ** ((gamma + 1.0) / (2.0 * (gamma - 1.0))))
            )
            current_mass = max(0.001, current_mass - (m_dot * dt))

        P_tank = (current_mass * R_gas * (T_tank + 273.15)) / V_tank

        # Temperature-compensated mass balance
        mass_lost_total = max(0.0, initial_mass - current_mass)
        loss_rate = (mass_lost_total / sim_time) if sim_time > 0 else 0.0

        leak_detected = False
        est_diam_um = 0.0

        if mass_lost_total > 0.010 and loss_rate > 0.00005:
            t_k = T_tank + 273.15
            flux_per_m2 = (
                Cd * P_tank
                * math.sqrt(gamma / (R_gas * t_k))
                * ((2.0 / (gamma + 1.0)) ** ((gamma + 1.0) / (2.0 * (gamma - 1.0))))
            )
            est_area = loss_rate / flux_per_m2 if flux_per_m2 > 0 else 0.0
            est_diam_um = math.sqrt(4.0 * est_area / math.pi) * 1e6
            leak_detected = True

        tilt = 3.2 * math.cos(0.53 * sim_time)

        is_emit_checkpoint = (altitude - last_emit_alt >= 3000.0) or (altitude >= 25000.0)
        
        if is_emit_checkpoint or leak_detected:
            last_emit_alt = altitude
            is_armed = (altitude >= 25000.0) and (abs(tilt) <= 1.5) and (not leak_detected)
            
            frame = {
                "time_s": round(sim_time, 1),
                "altitude_m": round(altitude, 1),
                "ext_temp_c": round(t_amb, 1),
                "ext_pres_hpa": round(p_amb / 100.0, 1),
                "tank_pres_bar": round(P_tank / 1e5, 2),
                "motor_temp_c": round(T_motor, 1),
                "mass_loss_g": round(mass_lost_total * 1000.0, 2),
                "tilt_deg": round(tilt, 2),
                "leak_detected": leak_detected,
                "leak_status": f"LEAK:{est_diam_um:.0f}µm" if leak_detected else "SEALED (OK)",
                "gate_status": "ARMED" if is_armed else "NO-GO",
                "is_abort": leak_detected,
                "is_ignition": is_armed
            }
            yield frame

            if leak_detected or is_armed:
                break


# ==============================================================================
# 3. REST & STREAMING ENDPOINTS
# ==============================================================================

class SimulationRequest(BaseModel):
    puncture_um: float = 0.0

@app.post("/api/simulate")
def run_simulation_batch(req: SimulationRequest) -> Dict[str, Any]:
    """
    Returns full telemetry array in one JSON response for graphs/tables.
    """
    telemetry_frames = []
    final_verdict = "TERMINATED"
    abort_reason = None

    for frame in execute_flight_engine(puncture_um=req.puncture_um):
        telemetry_frames.append(frame)
        if frame["is_ignition"]:
            final_verdict = "IGNITION_CONFIRMED"
        elif frame["is_abort"]:
            final_verdict = "MISSION_ABORTED"
            abort_reason = f"Pneumatic Depletion Detected: {frame['leak_status']}"

    return {
        "summary": {
            "verdict": final_verdict,
            "abort_reason": abort_reason,
            "puncture_input_um": req.puncture_um,
            "final_altitude_m": telemetry_frames[-1]["altitude_m"] if telemetry_frames else 0.0,
            "final_mass_loss_g": telemetry_frames[-1]["mass_loss_g"] if telemetry_frames else 0.0,
            "total_frames": len(telemetry_frames)
        },
        "telemetry": telemetry_frames
    }

@app.get("/api/stream")
async def stream_telemetry(puncture_um: float = Query(0.0)):
    """
    Server-Sent Events (SSE) endpoint to stream live telemetry frame-by-frame.
    """
    async def event_generator():
        for frame in execute_flight_engine(puncture_um=puncture_um):
            yield f"data: {json.dumps(frame)}\n\n"
            await asyncio.sleep(0.15)  # Pace frames for realistic live telemetry feel
            
    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.get("/api/health")
def health_check():
    return {"status": "AERO-FIRE Backend Operational"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend:app", host="0.0.0.0", port=8000, reload=True)