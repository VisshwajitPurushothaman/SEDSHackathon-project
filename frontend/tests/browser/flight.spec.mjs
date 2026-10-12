import { test, expect } from '@playwright/test';
import { API, api, errors, pause } from './helpers.mjs';

async function snapshot(page) {
  return page.evaluate(async () => {
    const { useFlight } = await import('/src/flight/store.ts');
    const s = useFlight.getState();
    return {
      id: s.missionId, ended: s.ended, live: s.live, cursor: s.cursor,
      ids: s.frames.map((f) => f.id), phases: [...new Set(s.frames.map((f) => f.phase))],
      volumes: s.frames.map((f) => f.estimates.balloon_volume?.value).filter((v) => typeof v === 'number'),
      rocket: s.frames.map((f) => f.estimates.rocket_altitude?.value).filter((v) => typeof v === 'number'),
      venting: s.frames.some((f) => f.estimates.balloon_venting?.value === true),
      leak: s.frames.some((f) => f.estimates.tank_leak_detected?.value === true),
      predictions: s.frames.filter((f) => f.predicting).map((f) => ({
        status: f.estimates.altitude.status, levels: f.estimates.prediction?.regions.map((r) => r.level),
        pressure: f.estimates.pressure?.status, latency: f.estimates.link_latency?.status,
      })),
      trusts: s.frames.some((f) => f.trust.gnss.state === 'SUSPECT' && f.trust.navic.state === 'SUSPECT'),
    };
  });
}

const scenarios = [
  ['nominal', 'Nominal'], ['leak', '100 µm tank leak'], ['spoof', 'GNSS spoofed (ascent)'],
  ['blackout', '10-min blackout (float)'], ['cold', 'Motor too cold'],
];

for (const [preset, label] of scenarios) {
  test(`Flight preset end to end: ${label}`, async ({ page, request }, info) => {
    const consoleErrors = errors(page);
    await page.goto('/#/flight');
    await page.getByLabel('Scenario', { exact: true }).selectOption(preset);
    await page.getByLabel('Operator name', { exact: true }).fill('Browser Operator');
    await page.getByRole('group', { name: 'Time scale', exact: true }).getByRole('button', { name: '600×', exact: true }).click();
    const created = page.waitForResponse((r) => r.url() === `${API}/api/missions` && r.request().method() === 'POST');
    await page.getByRole('button', { name: 'Start mission', exact: true }).click();
    const response = await created;
    expect(response.status()).toBe(201);
    const { mission_id: id } = await response.json();
    await expect.poll(async () => (await snapshot(page)).id).toBe(id);
    await expect(page.getByRole('region', { name: '3D flight view', exact: true }).locator('canvas')).toBeVisible();
    await expect(page.getByRole('region', { name: 'Map', exact: true }).locator('canvas')).toBeVisible();
    await page.getByRole('button', { name: 'Hold', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Release hold', exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Release hold', exact: true }).click();

    const deadline = Date.now() + 120000;
    let terminal = false;
    while (Date.now() < deadline) {
      const summary = await api(request, `/api/missions/${id}`);
      if (summary.state === 'finished') { terminal = true; expect(summary.end_reason).toBe('landed'); break; }
      if (summary.operator.state === 'none') {
        if (['nominal', 'blackout'].includes(preset)) {
          if (summary.recommendation === 'GO_RECOMMENDED' && (preset !== 'blackout' || summary.t > 6600)) {
            // Explicit simulated test operator. UI hold/release and pre-flight hold duration are checked separately.
            await api(request, `/api/missions/${id}/commit`, { operator: 'Browser Operator' });
          }
        } else {
          const f = await api(request, `/api/missions/${id}/frame`);
          const observed = preset === 'leak' ? f.recommendation === 'ABORT_RECOMMENDED'
            : preset === 'spoof' ? f.alerts.some((a) => a.key === 'nav_integrity')
            : f.vehicle_phase === 'FLOAT' && f.gates.motor.passed === false;
          if (observed) {
            const terminate = page.getByRole('button', { name: 'Hold for 1.5 seconds to terminate: cut down without launch', exact: true });
            await expect(terminate).toBeEnabled();
            await terminate.focus();
            await page.keyboard.down('Space');
            await page.waitForTimeout(1700);
            await page.keyboard.up('Space');
          }
        }
      }
      await pause(200);
    }
    expect(terminal, 'operator completion lands the mission before the deadline').toBeTruthy();
    await expect.poll(async () => (await snapshot(page)).ended).toBeTruthy();
    const s = await snapshot(page);
    expect(s.ids).toEqual(Array.from({ length: s.ids.length }, (_, i) => i + 1));
    expect(s.phases).toContain('Ascent');
    expect(s.phases).toContain('Descent');
    expect(s.phases).toContain('Landed');
    if (['nominal', 'blackout'].includes(preset)) {
      expect(s.phases).toEqual(expect.arrayContaining(['Float', 'Gates', 'Commit', 'Rocket burn', 'Coast', 'Cut-down']));
      expect(Math.max(...s.volumes)).toBeGreaterThan(Math.min(...s.volumes) * 20);
      expect(Math.max(...s.rocket)).toBeGreaterThan(30000);
      expect(s.venting).toBeTruthy();
    }
    if (preset === 'leak') expect(s.leak).toBeTruthy();
    if (preset === 'spoof') expect(s.trusts).toBeTruthy();
    if (preset === 'blackout') {
      expect(s.predictions.length).toBeGreaterThan(3);
      expect(s.predictions.every((p) => p.status === 'PREDICTED' && p.pressure === 'UNKNOWN' && p.latency === 'UNKNOWN')).toBeTruthy();
      expect(s.predictions[0].levels).toEqual([50, 90, 99]);
    }

    await page.getByRole('slider', { name: 'Scrub through received frames', exact: true }).fill(String(Math.floor(s.ids.length / 2)));
    await expect(page.getByText('Replaying the past: go live to act.', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Replay from cursor', exact: true }).click();
    await expect(page.getByRole('button', { name: 'Pause replay', exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Pause replay', exact: true }).click();
    await page.getByRole('button', { name: 'Top-down', exact: true }).click();
    await page.getByRole('button', { name: 'Live', exact: true }).click();
    await expect.poll(async () => (await snapshot(page)).live).toBeTruthy();
    const ack = page.getByRole('button', { name: 'Acknowledge', exact: true }).first();
    await ack.click();
    await expect(page.getByRole('region', { name: 'Alerts', exact: true })).toContainText('Browser Operator');
    await page.getByRole('link', { name: 'Post-flight', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Requirement verification', exact: true })).toBeVisible();
    await expect(page.getByLabel('Mission', { exact: true })).toHaveValue(id);
    await expect(page.getByRole('button', { name: 'Reveal truth / grade', exact: true })).toBeVisible();
    await page.screenshot({ path: info.outputPath(`${preset}.png`) });
    expect(consoleErrors).toEqual([]);
  });
}
