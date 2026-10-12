import { test, expect } from '@playwright/test';
import { API } from './helpers.mjs';

test('Pre-flight sealed checks, short-hold cancellation, hold/release and simulated commit', async ({ page }) => {
  const exceptions = [];
  page.on('pageerror', (e) => exceptions.push(e.message));
  await page.goto('/#/');
  await expect(page.getByText('GO RECOMMENDED', { exact: true })).toBeVisible();
  await expect(page.getByRole('group', { name: /^Pressurant tank integrity: PASS/ })).toBeVisible();
  await expect(page.getByRole('group', { name: /^Release altitude: PASS/ })).toBeVisible();
  await expect(page.getByRole('group', { name: /^Motor temperature: PASS/ })).toBeVisible();
  const commit = page.getByRole('button', { name: 'Hold for 1.5 seconds to commit ignition', exact: true });
  const box = await commit.boundingBox();
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.down();
  await page.waitForTimeout(250); // verify early release cancels the timed hold
  await page.mouse.up();
  await expect(page.getByText('GO RECOMMENDED', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Hold', exact: true }).click();
  await expect(page.getByText('OPERATOR HOLD', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Release hold', exact: true }).click();
  await expect(commit).toBeVisible();
  await commit.focus();
  await page.keyboard.down('Space');
  await page.waitForTimeout(1700); // hold duration is the behavior under test
  await page.keyboard.up('Space');
  await expect(page.getByText('IGNITION COMMITTED (simulation)', { exact: true })).toBeVisible();
  expect(exceptions).toEqual([]);
});

test('Pre-flight leak animation/checks and reverse scrub; stream does not restart', async ({ page }) => {
  const streams = [];
  page.on('request', (r) => { if (r.url().includes('/api/stream?')) streams.push(r.url()); });
  await page.goto('/#/');
  await expect(page.getByRole('radio', { name: 'Live stream', exact: true })).toBeEnabled();
  await page.getByRole('radio', { name: 'Live stream', exact: true }).click();
  await page.getByRole('button', { name: '100 µm', exact: true }).click();
  await page.getByRole('button', { name: 'Run live', exact: true }).click();
  await expect(page.getByText('MISSION ABORTED', { exact: true })).toBeVisible();
  await expect(page.getByRole('group', { name: /^Pressurant tank integrity: FAIL/ })).toBeVisible();
  await page.waitForTimeout(2000); // exceed normal EventSource reconnect delay
  expect(streams).toHaveLength(1);
  await page.getByRole('slider', { name: 'Mission time scrubber', exact: true }).fill('1');
  await expect(page.getByRole('group', { name: /^Pressurant tank integrity: PASS/ })).toBeVisible();
  await expect(page.getByText('MISSION ABORTED', { exact: true })).toHaveCount(0);
});

test('backend-down Pre-flight plays both recorded fixtures', async ({ page, context }) => {
  const exceptions = [];
  page.on('pageerror', (e) => exceptions.push(e.message));
  await context.route(`${API}/api/**`, (route) => route.abort('connectionrefused'));
  await page.goto('/#/');
  await expect(page.getByText('Backend offline · demo data', { exact: true })).toBeVisible();
  await expect(page.getByText('GO RECOMMENDED', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: '100 µm', exact: true }).click();
  await page.getByRole('button', { name: 'Run demo', exact: true }).click();
  await expect(page.getByText('MISSION ABORTED', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '300 µm', exact: true })).toBeDisabled();
  expect(exceptions).toEqual([]);
});

test('backend-down Flight retains its backend-required notice', async ({ page, context }) => {
  const exceptions = [];
  page.on('pageerror', (e) => exceptions.push(e.message));
  await context.route(`${API}/api/**`, (route) => route.abort('connectionrefused'));
  await page.goto('/#/flight');
  await expect(page.getByText(/The flight window needs the backend/)).toBeVisible();
  expect(exceptions).toEqual([]);
});
