import { LayoutGroup, motion } from 'framer-motion';
import { ListChecks } from 'lucide-react';
import { useMemo } from 'react';
import { CHECK_ORDER, deriveChecks, summarizeChecks } from '../lib/checks';
import { selectUiFrame, useMission } from '../store/useMission';
import { CheckCard } from './CheckCard';

/** "0101000": which checks are FAIL, in CHECK_ORDER. A string so the selector is cheap to compare. */
const selectFailKey = (s: Parameters<typeof selectUiFrame>[0]) => {
  const c = deriveChecks(selectUiFrame(s));
  return CHECK_ORDER.map((id) => (c[id].state === 'FAIL' ? '1' : '0')).join('');
};

const selectSummary = (s: Parameters<typeof selectUiFrame>[0]) => {
  const x = summarizeChecks(deriveChecks(selectUiFrame(s)));
  return `${x.active}|${x.total}|${x.passing}|${x.failed}`;
};

function Summary() {
  const [active, total, passing, failed] = useMission(selectSummary).split('|').map(Number);
  return (
    <div className="flex items-center gap-2 px-4 pt-3 text-[12.5px]" aria-live="polite">
      <span className="num text-ink">
        {active} of {total}
      </span>
      <span className="text-muted">monitoring</span>
      <span className="text-faint">·</span>
      <span className="num text-pass">{passing}</span>
      <span className="text-muted">passing</span>
      <span className="text-faint">·</span>
      <span className={`num ${failed ? 'text-fail' : 'text-ink'}`}>{failed}</span>
      <span className="text-muted">failed</span>
    </div>
  );
}

/** Left column: one card per pre-flight check; FAILs move to the top. */
export function ChecklistPanel() {
  const failKey = useMission(selectFailKey);
  const order = useMemo(
    () => [...CHECK_ORDER].sort((a, b) => Number(failKey[CHECK_ORDER.indexOf(b)]) - Number(failKey[CHECK_ORDER.indexOf(a)])),
    [failKey],
  );

  return (
    <section className="panel flex min-h-0 flex-col" aria-labelledby="checklist-title">
      <div className="flex items-center gap-2 border-b border-line px-4 py-3">
        <ListChecks size={15} className="text-muted" aria-hidden />
        <h2 id="checklist-title" className="panel-title">
          Pre-flight checklist
        </h2>
      </div>
      <Summary />
      <LayoutGroup>
        <ol className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto p-3 pt-2.5">
          {order.map((id) => (
            <motion.li key={id} layout="position" transition={{ layout: { duration: 0.45, ease: [0.16, 1, 0.3, 1] } }} className="list-none">
              <CheckCard id={id} />
            </motion.li>
          ))}
        </ol>
      </LayoutGroup>
      <p className="border-t border-line px-4 py-2 text-[10.5px] leading-snug text-faint">
        States derive from the playhead frame. <span className="text-muted">≈</span> = interpolated between backend frames. Limits come from
        config/mission.yaml; GO/ABORT is the ground station's recommendation. The 5 g caution is display only.
      </p>
    </section>
  );
}
