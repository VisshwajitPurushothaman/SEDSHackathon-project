// Smoke tests for the ground-station console. Start the API in playback mode first:
//   python backend.py --port 8001 --snapshot --playback data/demo_sessions/nominal.jsonl.gz --playback-speed 10
// and the UI on 5174 with VITE_API_URL=http://127.0.0.1:8001 (see README).
import { test, expect } from '@playwright/test';
import { errors } from './helpers.mjs';

const PAGES = [
  ['1', 'overview', 'Recent alarms', 'OVERVIEW'],
  ['2', 'preflight', 'Pre-flight checks', 'PRE-FLIGHT'],
  ['3', 'flight', 'Telemetry', 'FLIGHT'],
  ['4', 'trajectory', 'Trajectory', 'TRAJECTORY'],
  ['5', 'postflight', 'Post-flight', 'POST-FLIGHT'],
  ['6', 'events', 'Event log (append-only)', 'EVENTS'],
];

for (const [key, id, heading, nav] of PAGES) {
  test(`page ${key} ${id} renders playback telemetry without console errors`, async ({ page }) => {
    const found = errors(page);
    await page.goto(`/#/${id}`);
    await expect(page.getByRole('banner', { name: 'Mission status' }).getByText(/^PLAYBACK/)).toBeVisible();
    await expect(page.getByRole('banner', { name: 'Mission status' }).getByText('demo-nominal')).toBeVisible();
    await expect(page.getByRole('heading', { name: heading, exact: true })).toBeVisible();
    await expect(page.getByRole('navigation', { name: 'Pages' }).getByRole('link', { name: new RegExp(`^${nav}\\s*\\d$`) })).toHaveAttribute('aria-current', 'page');
    expect(found).toEqual([]);
  });
}

test('flight page: channels, limits, strip charts, keyboard navigation, poll and alarm acknowledgement', async ({ page }) => {
  const found = errors(page);
  await page.goto('/#/flight');
  const table = page.getByRole('table', { name: 'Telemetry channels' });
  await expect(table.getByRole('cell', { name: 'GNSS_ALT', exact: true })).toBeVisible();
  await expect(table.getByRole('cell', { name: 'TANK_P', exact: true })).toBeVisible();
  await expect(page.getByRole('figure', { name: 'Altitude strip chart' }).locator('canvas')).toBeVisible();
  await expect(page.getByRole('figure', { name: 'Tank pressure strip chart' }).locator('canvas')).toBeVisible();
  await page.getByRole('textbox', { name: 'Filter channels' }).fill('tank');
  await expect(table.getByRole('cell', { name: 'GNSS_ALT', exact: true })).toHaveCount(0);
  await page.getByRole('textbox', { name: 'Filter channels' }).fill('');

  await page.locator('#operator').fill('Smoke Operator');
  await page.getByRole('button', { name: 'POLL NO-GO' }).click();
  await expect(page.getByRole('list', { name: 'Recorded polls' })).toContainText('Smoke Operator');

  await page.locator('body').click({ position: { x: 700, y: 20 } });
  await page.keyboard.press('6');
  await expect(page).toHaveURL(/#\/events$/);
  await expect(page.getByRole('heading', { name: 'Alarms', exact: true })).toBeVisible();
  await expect.poll(() => page.getByRole('button', { name: 'ACK' }).count(), { timeout: 90000 }).toBeGreaterThan(0);
  await page.keyboard.press('a');
  await expect(page.getByRole('cell', { name: /^Smoke Operator \d\d:\d\d:\d\dZ$/ }).first()).toBeVisible();
  await page.keyboard.press('3');
  await expect(page).toHaveURL(/#\/flight$/);
  expect(found).toEqual([]);
});
