/** Wire types for the AERO-FIRE backend's legacy pre-flight endpoints (aerofire/api/legacy.py). Do not diverge. */

export type GateStatus = 'ARMED' | 'NO-GO';
/** The software only ever RECOMMENDS; ignition needs the operator's commit. */
export type Verdict = 'GO_RECOMMENDED' | 'ABORT_RECOMMENDED' | 'NO_GO';
export type Recommendation = Verdict;

export interface TelemetryFrame {
  time_s: number;
  altitude_m: number;
  ext_temp_c: number;
  ext_pres_hpa: number;
  tank_pres_bar: number;
  motor_temp_c: number;
  /** Ground estimate: mass lost from the first estimate (noisy measurements). */
  mass_loss_g: number;
  tilt_deg: number;
  leak_detected: boolean;
  leak_status: string;
  /** "ARMED" = the ground recommends GO (5 gates pass, no leak). */
  gate_status: GateStatus;
  is_abort: boolean;
  /** Always false: software never confirms ignition. Kept for wire compatibility. */
  is_ignition: boolean;
  // --- fields added by the new backend (optional so old recordings still parse)
  go_recommended?: boolean;
  recommendation?: Recommendation;
  phase?: string;
  vertical_speed_m_s?: number;
  swing_amplitude_deg?: number;
  balloon_volume_m3?: number;
  balloon_vent_rate_kg_s?: number;
  balloon_venting?: boolean;
  atmo_source?: string;
  gates?: Record<string, boolean | null>;
}

export interface SimulateSummary {
  verdict: Verdict;
  abort_reason: string | null;
  puncture_input_um: number;
  final_altitude_m: number;
  final_mass_loss_g: number;
  total_frames: number;
  atmo_source?: string;
  atmo_note?: string;
}

export interface SimulateResponse {
  summary: SimulateSummary;
  telemetry: TelemetryFrame[];
}
