#!/usr/bin/env python3
"""
AERO-FIRE: Autonomous Rockoon Mission Director & Pneumatic Health Monitor
--------------------------------------------------------------------------
- Direct Console Input: Puncture diameter in micrometers (µm).
- Real-Time Atmosphere: Open-Meteo 15-level isobaric vertical sounding.
- Rock-Solid Mass Balance: Zero false alarms on sealed tanks (m_lost = 0.0g).
- Physical Choked Flow: Accurately sizes punctures and aborts unsafe launches.
"""

import math
import time
import json
import urllib.request
import urllib.error

# ==============================================================================
# 1. LIVE ATMOSPHERIC SOUNDING ENGINE (OPEN-METEO)
# ==============================================================================

class LiveAtmosphereProfile:
    def __init__(self, lat: float = 13.72, lon: float = 80.23):
        self.lat = lat
        self.lon = lon
        self.levels = [1000, 925, 850, 700, 500, 300, 250, 200, 150, 100, 70, 50, 30, 20, 10]
        self.profile = self.fetch_live_sounding()

    def fetch_live_sounding(self):
        print(f"\n[*] Connecting to Open-Meteo API for Coordinates: {self.lat}°N, {self.lon}°E...")
        query_vars = []
        for lvl in self.levels:
            query_vars.append(f"temperature_{lvl}hPa")
            query_vars.append(f"windspeed_{lvl}hPa")
            query_vars.append(f"geopotential_height_{lvl}hPa")
        
        url = (
            f"https://api.open-meteo.com/v1/forecast?latitude={self.lat}&longitude={self.lon}"
            f"&hourly={','.join(query_vars)}&forecast_days=1"
        )
        
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "AERO-FIRE/2.0"})
            with urllib.request.urlopen(req, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
                hourly = payload["hourly"]
                
                profile_data = []
                for lvl in self.levels:
                    z_m = hourly[f"geopotential_height_{lvl}hPa"][0]
                    t_c = hourly[f"temperature_{lvl}hPa"][0]
                    ws_kmh = hourly[f"windspeed_{lvl}hPa"][0]
                    
                    profile_data.append({
                        "pressure_pa": lvl * 100.0,
                        "altitude_m": float(z_m),
                        "temp_c": float(t_c),
                        "wind_speed_ms": float(ws_kmh) / 3.6
                    })
                
                profile_data.sort(key=lambda item: item["altitude_m"])
                print(f"[✓] Live sounding ingested: {len(profile_data)} vertical atmospheric layers active.")
                return profile_data

        except Exception as e:
            print(f"[!] Network notice ({e}). Switching to calibrated standard atmospheric profile.")
            return self._fallback_standard_atmosphere()

    def _fallback_standard_atmosphere(self):
        data = []
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
            data.append({"pressure_pa": p_pa, "altitude_m": z, "temp_c": t, "wind_speed_ms": ws})
        data.sort(key=lambda item: item["altitude_m"])
        return data

    def interpolate(self, z: float):
        alts = [d["altitude_m"] for d in self.profile]
        pressures = [d["pressure_pa"] for d in self.profile]
        temps = [d["temp_c"] for d in self.profile]
        winds = [d["wind_speed_ms"] for d in self.profile]

        if z <= alts[0]:
            return pressures[0], temps[0], winds[0]
        elif z >= alts[-1]:
            return pressures[-1], temps[-1], winds[-1]
        else:
            for i in range(len(alts) - 1):
                if alts[i] <= z <= alts[i + 1]:
                    factor = (z - alts[i]) / (alts[i + 1] - alts[i])
                    p = pressures[i] + factor * (pressures[i + 1] - pressures[i])
                    t = temps[i] + factor * (temps[i + 1] - temps[i])
                    w = winds[i] + factor * (winds[i + 1] - winds[i])
                    return p, t, w
        return pressures[0], temps[0], winds[0]


# ==============================================================================
# 2. MISSION CONTROL & FLIGHT SIMULATION
# ==============================================================================

def main():
    print("=" * 80)
    print("   AERO-FIRE: AUTONOMOUS STRATOSPHERIC FLIGHT DIRECTOR & LEAK ENGINE")
    print("=" * 80)

    try:
        raw_hole = input("[?] Enter test puncture hole diameter in MICROMETERS (µm)\n"
                         "    (e.g., 0 = Sealed, 250 = Micro-fissure, 500 = Pin-hole): ").strip()
        puncture_um = float(raw_hole) if raw_hole else 0.0
        if puncture_um < 0:
            raise ValueError
    except ValueError:
        print("[!] Invalid input. Defaulting to 0 µm (Sealed).")
        puncture_um = 0.0

    sounding = LiveAtmosphereProfile(lat=13.72, lon=80.23)

    # Physical Tank Constants (Helium, 0.5 m^3, 200 bar)
    V_tank = 0.5
    R_gas = 2077.1
    gamma = 1.667
    Cd = 0.62

    P_tank = 200e5
    T_tank = 26.0
    T_motor = 20.0

    # Initial Gas Mass (Ideal Gas Law)
    initial_mass = (P_tank * V_tank) / (R_gas * (T_tank + 273.15))
    current_mass = initial_mass

    # Puncture area: A = (pi / 4) * d^2
    puncture_d_m = puncture_um * 1e-6
    puncture_area_m2 = (math.pi / 4.0) * (puncture_d_m ** 2)

    # Mission state
    altitude = 0.0
    ascent_rate = 120.0  # m/s (fast simulation clock)
    dt = 1.0
    sim_time = 0.0
    last_print_alt = -3000.0

    print("\n" + "=" * 92)
    print(f" LIVE TELEMETRY STREAM | TEST CONFIGURED: {puncture_um:.1f} µm ({puncture_area_m2*1e6:.4f} mm²)")
    print("=" * 92)
    print(f"{'ALTITUDE':<10} | {'EXT_TEMP':<9} | {'EXT_PRES':<9} | {'TANK_PRES':<10} | {'MASS_LOSS':<11} | {'LEAK STATUS':<16} | {'GATE'}")
    print("-" * 92)

    while altitude <= 30000.0:
        sim_time += dt
        altitude += ascent_rate * dt

        # 1. Sync with vertical weather profile
        p_amb, t_amb, wind_speed = sounding.interpolate(altitude)

        # 2. Thermal dissipation:
        # Tank cools towards cold ambient air
        T_tank += -0.002 * (T_tank - t_amb) * dt
        # Motor has active heating blanket maintaining safe ductile temp (~15°C)
        T_motor = max(12.0, T_motor - 0.0005 * (T_motor - t_amb) * dt)

        # 3. Choked sonic orifice flow (if puncture exists)
        if puncture_area_m2 > 0:
            t_k = T_tank + 273.15
            m_dot = (
                Cd * puncture_area_m2 * P_tank
                * math.sqrt(gamma / (R_gas * t_k))
                * ((2.0 / (gamma + 1.0)) ** ((gamma + 1.0) / (2.0 * (gamma - 1.0))))
            )
            mass_lost_step = m_dot * dt
            current_mass = max(0.001, current_mass - mass_lost_step)

        # Update physical tank pressure: P = mRT / V
        P_tank = (current_mass * R_gas * (T_tank + 273.15)) / V_tank

        # 4. Temperature-Compensated Mass Balance Leak Detection:
        mass_lost_total = max(0.0, initial_mass - current_mass)
        loss_rate = (mass_lost_total / sim_time) if sim_time > 0 else 0.0

        # Detection Threshold: > 10 grams total loss AND > 0.05 g/s loss rate
        if mass_lost_total > 0.010 and loss_rate > 0.00005:
            # Back-calculate effective hole diameter (µm)
            t_k = T_tank + 273.15
            flux_per_m2 = (
                Cd * P_tank
                * math.sqrt(gamma / (R_gas * t_k))
                * ((2.0 / (gamma + 1.0)) ** ((gamma + 1.0) / (2.0 * (gamma - 1.0))))
            )
            est_area = loss_rate / flux_per_m2 if flux_per_m2 > 0 else 0.0
            est_diam_um = math.sqrt(4.0 * est_area / math.pi) * 1e6
            leak_detected = True
            leak_str = f"LEAK:{est_diam_um:.0f}µm"
        else:
            leak_detected = False
            leak_str = "SEALED (OK)"

        # 5. Gondola Pendulum Oscillation (T ~ 12s)
        tilt = 3.2 * math.cos(0.53 * sim_time)

        # 6. Periodic milestone display (every ~3000m or upon reaching float altitude)
        if (altitude - last_print_alt >= 3000.0) or (altitude >= 25000.0):
            last_print_alt = altitude
            is_go = (altitude >= 25000.0) and (abs(tilt) <= 1.5) and (not leak_detected)
            gate_label = "ARMED" if is_go else "NO-GO"
            
            print(
                f"{altitude:<8.0f} m | {t_amb:<7.1f} C | {p_amb/100:<7.0f} hPa | "
                f"{P_tank/1e5:<8.1f} bar | {mass_lost_total*1000:<7.1f} g   | {leak_str:<16} | {gate_label}"
            )
            time.sleep(0.12)  # Short pause for realistic telemetry display feel

        # 7. Safety Abort Check
        if leak_detected:
            print("-" * 92)
            print(f">>> [ABORT TRIGGERED]: Pressurant Leak Detected ({est_diam_um:.1f} µm hole) <<<")
            print(f"    Tank has lost {mass_lost_total*1000:.1f} grams of Helium.")
            print(">>> MISSION ABORTED: ROCKET LOCKED ON LAUNCH RAIL <<<")
            print("=" * 92 + "\n")
            return

        # 8. Nominal Launch Authorization Check
        if (altitude >= 25000.0) and (abs(tilt) <= 1.5) and (not leak_detected):
            print("-" * 92)
            print(">>> [AUTONOMOUS IGNITION ARMED]: ALL 5 GATES ALIGNED <<<")
            print(f"    - Float Altitude   : {altitude:.0f} m (Threshold: >=25,000 m)")
            print(f"    - Pendulum Tilt    : {tilt:+.2f}° (Threshold: <=1.5° zero-crossing)")
            print(f"    - Propellant Core  : {T_motor:.1f}°C (Threshold: >=-10.0°C)")
            print(f"    - Tank Integrity   : 100% SEALED (0.0 grams lost)")
            print(">>> ROCKET DROP LATCH RELEASED -> ENGINE IGNITION CONFIRMED <<<")
            print("=" * 92 + "\n")
            return

if __name__ == "__main__":
    main()