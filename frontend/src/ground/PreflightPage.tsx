import { useEffect, useMemo, useState } from 'react';
import { API_URL } from '../api/client';
import { ui } from '../theme/tokens';
import { fmtMET, fmtNum } from './format';
import { RangeMap, type Zone } from './RangeMap';
import { StripChart } from './StripChart';
import { useGround } from './store';

interface Check {
  id: string;
  title: string;
  status: 'PASS' | 'FAIL' | 'NOT CHECKED' | 'WAIVED';
  evidence: Record<string, unknown>;
}
interface PreflightResp {
  readiness: string;
  counts: Record<string, number>;
  items: Check[];
  report: unknown;
  report_sha256: string;
}
interface ProfileSample {
  met: number;
  phase: string;
  ch: Record<string, number>;
  lo: Record<string, number>;
  hi: Record<string, number>;
}
interface Iip {
  case: string;
  lat: number;
  lon: number;
  inside: boolean;
}
interface ProfileResp {
  samples: ProfileSample[];
  track: { met: number; lat: number; lon: number; alt: number }[];
  basis: string;
  atmo_source: string;
  footprint: { regions: { level: number; polygon: [number, number][] }[]; basis: string };
  iips: Iip[];
  iip_model: string;
  release_point: { lat: number; lon: number; alt: number };
  range: { site: { lat: number; lon: number; name: string }; approved: { polygon: [number, number][] } | null; zones: Zone[]; label: string };
  range_check: { status: string; conflicts: { zone: string; what: string }[]; iip_outside_approved: string[]; label: string };
}

const STATUS_CLS: Record<string, string> = { PASS: 'tag-nom', FAIL: 'tag-alm', 'NOT CHECKED': 'tag-stale', WAIVED: 'tag-cau' };
const CH: { m: string; label: string; unit: string; dec: number }[] = [
  { m: 'GNSS_ALT', label: 'Altitude', unit: 'm', dec: 0 },
  { m: 'GNSS_VU', label: 'Ascent rate', unit: 'm/s', dec: 2 },
  { m: 'BARO_P', label: 'BARO_P', unit: 'hPa', dec: 1 },
  { m: 'AIR_T', label: 'AIR_T', unit: '°C', dec: 1 },
  { m: 'TANK_P', label: 'TANK_P', unit: 'bar', dec: 1 },
  { m: 'TANK_T', label: 'TANK_T', unit: '°C', dec: 1 },
  { m: 'MOTOR_T', label: 'MOTOR_T', unit: '°C', dec: 1 },
];

function evidenceText(c: Check): string {
  const e = c.evidence;
  if (Array.isArray(e.failures) && e.failures.length) return (e.failures as string[]).join(', ');
  if (typeof e.reason === 'string') return e.reason;
  if (c.id === 'agreement' && typeof e.chi2_gnss_navic_h === 'number')
    return `χ² H ${fmtNum(e.chi2_gnss_navic_h, 2)} V ${fmtNum(e.chi2_gnss_navic_v, 2)} baro ${fmtNum(e.chi2_gnss_baro_v, 2)} (N=${e.epochs})`;
  if (typeof e.rate_hz === 'number') return `${fmtNum(e.rate_hz, 2)} Hz · median ${fmtNum(e.median, 2)}${typeof e.variance === 'number' ? ` · var ${(e.variance as number).toExponential(1)}` : ''}`;
  if (c.id === 'range') return `${(e.zones_checked as number) ?? 0} zones · ${((e.conflicts as unknown[]) ?? []).length} conflicts`;
  if (c.id === 'profile') return `${e.samples} samples · ${e.atmo_source}`;
  return '';
}

function useJson<T>(path: string, period: number, dep?: unknown): T | null {
  const [data, setData] = useState<T | null>(null);
  useEffect(() => {
    let live = true;
    const pull = () =>
      fetch(`${API_URL}${path}`)
        .then((r) => (r.ok ? (r.json() as Promise<T>) : null))
        .then((d) => live && d && setData(d))
        .catch(() => undefined);
    void pull();
    const id = period > 0 ? setInterval(pull, period) : null;
    return () => {
      live = false;
      if (id) clearInterval(id);
    };
  }, [path, period, dep]);
  return data;
}

export function useProfile(): ProfileResp | null {
  return useJson<ProfileResp>('/api/ground/preflight/profile', 0);
}

export function profileMapProps(p: ProfileResp) {
  return {
    zones: p.range.zones,
    approved: p.range.approved?.polygon ?? null,
    center: [p.range.site?.lat ?? 13.7, p.range.site?.lon ?? 80.2] as [number, number],
    label: p.range.label,
  };
}

function Waive({ id }: { id: string }) {
  const operator = useGround((s) => s.operator);
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState('');
  const [err, setErr] = useState<string | null>(null);
  if (!open)
    return (
      <button type="button" className="btn px-2 py-0.5 text-[11px]" onClick={() => setOpen(true)} disabled={!operator.trim()} title={operator.trim() ? '' : 'Enter the operator name first'}>
        WAIVE
      </button>
    );
  return (
    <span className="flex items-center gap-1">
      <input className="field w-40 py-0.5 text-[11px]" placeholder="reason (required)" value={reason} onChange={(e) => setReason(e.target.value)} aria-label={`Waiver reason for ${id}`} />
      <button
        type="button"
        className="btn px-2 py-0.5 text-[11px]"
        disabled={!reason.trim()}
        onClick={() =>
          void fetch(`${API_URL}/api/ground/preflight/${encodeURIComponent(id)}/waive`, {
            method: 'POST',
            headers: { 'content-type': 'application/json' },
            body: JSON.stringify({ operator: operator.trim(), reason: reason.trim() }),
          }).then((r) => (r.ok ? setOpen(false) : r.text().then(setErr)))
        }
      >
        OK
      </button>
      {err && <span className="text-[11px] text-fail">{err}</span>}
    </span>
  );
}

export default function PreflightPage() {
  const missionId = useGround((s) => s.missionId);
  const pf = useJson<PreflightResp>('/api/ground/preflight', 2000, missionId);
  const prof = useProfile();
  const [onlyIssues, setOnlyIssues] = useState(false);
  const items = (pf?.items ?? []).filter((i) => !onlyIssues || i.status !== 'PASS');
  const ready = pf?.readiness === 'READY FOR DIRECTOR REVIEW';

  const x = useMemo(() => prof?.samples.map((s) => s.met) ?? [], [prof]);
  const milestones = useMemo(() => {
    if (!prof) return [];
    const s = prof.samples;
    const fl = s.find((r) => r.phase === 'FLOAT');
    const pick = [s[0], ...[600, 1800, 3600].map((t) => s.find((r) => r.met >= t)), fl, fl && s.find((r) => r.met >= fl.met + 1800)];
    return pick.filter((r, i, a): r is ProfileSample => !!r && a.findIndex((q) => q?.met === r.met) === i);
  }, [prof]);

  const download = () => {
    if (!pf) return;
    const blob = new Blob([JSON.stringify({ report: pf.report, report_sha256: pf.report_sha256 }, null, 1)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `readiness-${missionId ?? 'no-mission'}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  return (
    <div className="grid min-h-[760px] grid-cols-[minmax(520px,1fr)_minmax(460px,1fr)] gap-2 p-2">
      <div className="flex min-h-0 flex-col gap-2">
        <section className={`panel flex items-center gap-3 px-3 py-2 ${ready ? 'border-pass/60' : 'border-hold/60'}`} aria-labelledby="pf-ready">
          <h2 id="pf-ready" className="panel-title">
            Readiness
          </h2>
          <span className={`tag ${ready ? 'tag-nom' : 'tag-cau'} text-[14px]`}>{pf?.readiness ?? '---'}</span>
          <span className="num text-[11px] text-muted">
            {Object.entries(pf?.counts ?? {})
              .map(([k, v]) => `${k} ${v}`)
              .join(' · ')}
          </span>
          <button type="button" className="btn ml-auto px-2 py-1 text-[11px]" onClick={download} disabled={!pf}>
            Report JSON
          </button>
        </section>
        <p className="num -mt-1 truncate px-1 text-[10.5px] text-muted" title="SHA-256 of the canonical report JSON">
          report SHA-256 {pf?.report_sha256 ?? '---'}
        </p>
        <section className="panel flex min-h-0 flex-1 flex-col" aria-labelledby="pf-checks">
          <div className="panel-head">
            <h2 id="pf-checks" className="panel-title">
              Pre-flight checks
            </h2>
            <span className="text-[10.5px] text-muted">pad telemetry · freeze at release</span>
            <label className="ml-auto flex items-center gap-1 text-[11.5px] text-muted">
              <input type="checkbox" checked={onlyIssues} onChange={(e) => setOnlyIssues(e.target.checked)} /> issues only
            </label>
          </div>
          <div className="min-h-0 flex-1 overflow-auto">
            <table className="tbl" aria-label="Pre-flight checks">
              <thead>
                <tr>
                  <th>Check</th>
                  <th>Status</th>
                  <th>Evidence</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {items.map((c) => (
                  <tr key={c.id} className={c.status === 'FAIL' ? 'bg-fail/10' : ''}>
                    <td className="max-w-[260px] truncate text-[12px]" title={c.title}>
                      <span className="mn mr-1.5 text-ink">{c.id.replace('ch:', '')}</span>
                      <span className="text-muted">{c.id.startsWith('ch:') ? '' : c.title}</span>
                    </td>
                    <td>
                      <span className={`tag ${STATUS_CLS[c.status]}`}>{c.status}</span>
                    </td>
                    <td className="num max-w-[240px] truncate text-[11px] text-muted" title={JSON.stringify(c.evidence)}>
                      {c.status === 'WAIVED' ? `waived by ${(c.evidence.waiver as { by: string })?.by}: ${(c.evidence.waiver as { reason: string })?.reason}` : evidenceText(c)}
                    </td>
                    <td className="text-right">{(c.status === 'FAIL' || c.status === 'NOT CHECKED') && missionId && <Waive id={c.id} />}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>

      <div className="flex min-h-0 flex-col gap-2">
        <section className="panel" aria-labelledby="pf-profile">
          <div className="panel-head">
            <h2 id="pf-profile" className="panel-title">
              Predicted telemetry profile
            </h2>
            <span className="truncate text-[10.5px] text-muted" title={prof?.basis}>
              {prof?.atmo_source ?? ''}
            </span>
          </div>
          <div className="overflow-auto">
            <table className="tbl" aria-label="Predicted telemetry profile">
              <thead>
                <tr>
                  <th>MET</th>
                  <th>Phase</th>
                  {CH.map((c) => (
                    <th key={c.m} className="text-right">
                      {c.label}
                      <span className="unit ml-1 normal-case">{c.unit}</span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {milestones.map((r) => (
                  <tr key={r.met}>
                    <td className="num">{fmtMET(r.met)}</td>
                    <td className="text-[11px]">{r.phase}</td>
                    {CH.map((c) => (
                      <td key={c.m} className="num text-right text-[11.5px]" title={`band ${fmtNum(r.lo[c.m], c.dec)} … ${fmtNum(r.hi[c.m], c.dec)}`}>
                        {fmtNum(r.ch[c.m], c.dec)}
                        <div className="text-[10px] text-pred">
                          {fmtNum(r.lo[c.m], c.dec)}…{fmtNum(r.hi[c.m], c.dec)}
                        </div>
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
        <div className="grid grid-cols-2 gap-2">
          {prof &&
            CH.slice(0, 6).map((c) => (
              <StripChart
                key={c.m}
                title={c.label}
                unit={c.unit}
                x={x}
                series={[{ label: 'nominal', values: prof.samples.map((s) => s.ch[c.m]), color: ui.info, dash: [4, 3] }]}
                band={{ lo: prof.samples.map((s) => s.lo[c.m]), hi: prof.samples.map((s) => s.hi[c.m]) }}
                decimals={c.dec}
                height={92}
                legend={false}
              />
            ))}
        </div>
        <section className="panel flex min-h-[300px] flex-1 flex-col" aria-labelledby="pf-range">
          <div className="panel-head">
            <h2 id="pf-range" className="panel-title">
              Range and airspace safety
            </h2>
            {prof && <span className={`tag ${prof.range_check.status === 'PASS' ? 'tag-nom' : 'tag-alm'}`}>{prof.range_check.status}</span>}
            <span className="ml-auto truncate text-[10.5px] text-muted">{prof ? `IIP: ${prof.iip_model}` : ''}</span>
          </div>
          <div className="min-h-0 flex-1 p-1.5">
            {prof && (
              <RangeMap
                {...profileMapProps(prof)}
                zoom={8}
                lines={[{ id: 'pred', coords: prof.track.map((t) => [t.lat, t.lon]), color: ui.info, dashed: true }]}
                polys={prof.footprint.regions.map((r) => ({ id: `fp${r.level}`, polygon: r.polygon, color: ui.info, opacity: r.level === 50 ? 0.25 : 0.1 }))}
                points={[
                  ...prof.iips.map((p) => ({ id: p.case, lat: p.lat, lon: p.lon, color: p.inside ? ui.white : ui.fail, label: `IIP ${p.case}` })),
                  { id: 'site', lat: prof.range.site.lat, lon: prof.range.site.lon, color: ui.pass, label: prof.range.site.name },
                ]}
              />
            )}
          </div>
          {prof && prof.range_check.conflicts.length > 0 && (
            <ul className="border-t border-line px-3 py-1 text-[11.5px] text-fail">
              {prof.range_check.conflicts.map((c, i) => (
                <li key={i}>
                  {c.what} enters {c.zone}
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
}
