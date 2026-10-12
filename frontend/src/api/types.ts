/** Wire types for the AERO-FIRE FastAPI backend (backend.py). Do not diverge. */

export type GateStatus = 'ARMED' | 'NO-GO';
export type Verdict = 'IGNITION_CONFIRMED' | 'MISSION_ABORTED' | 'TERMINATED';

export interface TelemetryFrame {
  time_s: number;
  altitude_m: number;
  ext_temp_c: number;
  ext_pres_hpa: number;
  tank_pres_bar: number;
  motor_temp_c: number;
  mass_loss_g: number;
  tilt_deg: number;
  leak_detected: boolean;
  leak_status: string;
  gate_status: GateStatus;
  is_abort: boolean;
  is_ignition: boolean;
}

export interface SimulateSummary {
  verdict: Verdict;
  abort_reason: string | null;
  puncture_input_um: number;
  final_altitude_m: number;
  final_mass_loss_g: number;
  total_frames: number;
}

export interface SimulateResponse {
  summary: SimulateSummary;
  telemetry: TelemetryFrame[];
}
