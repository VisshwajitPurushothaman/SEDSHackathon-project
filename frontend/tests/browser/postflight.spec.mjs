// Claude's eleven Post-flight smoke checks, with condition-based waits and download verification.
import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import { flown, errors } from './helpers.mjs';

test.describe.serial('Post-flight continuation', () => {
  let page, id, consoleErrors;
  test.beforeAll(async ({ browser, request }, info) => {
    info.setTimeout(180000);
    id = await flown(request, { blackouts: [[6000, 6600]] });
    page = await browser.newPage({ viewport: { width: 1440, height: 900 }, acceptDownloads: true });
    consoleErrors = errors(page);
    await page.goto(`${process.env.TEST_BASE_URL ?? 'http://localhost:5174'}/#/postflight`);
    await expect(page.locator(`#pf-mission option[value="${id}"]`)).toBeAttached();
    await page.getByLabel('Mission', { exact: true }).selectOption(id);
    await page.getByLabel('Operator name', { exact: true }).fill('PF Operator');
    await expect(page.getByRole('heading', { name: 'Requirement verification', exact: true })).toBeVisible();
  });
  test.afterAll(async () => { await page?.close(); });

  test('1 requirements load before recovery', async () => {
    await expect(page.getByRole('button', { name: /^REQ-DATA-1 / })).toContainText('NOT TESTED');
    await expect(page.getByRole('button', { name: /^REQ-GATE-1 / })).toBeVisible();
  });
  test('2 recovery completes, chain verifies, zero lost', async () => {
    await page.getByRole('button', { name: 'Run recovery', exact: true }).click();
    await expect(page.getByText('verified end to end', { exact: true })).toBeVisible();
    await expect(page.getByRole('region', { name: 'Recover', exact: true })).toContainText(/lost records\s*0/);
  });
  test('3 rough picture stage reported', async () => {
    await expect(page.getByRole('region', { name: 'Recover', exact: true })).toContainText(/rough picture\s*[\d,]+ records/);
  });
  test('4 requirements re-evaluated after recovery', async () => {
    await expect(page.getByRole('button', { name: /^REQ-DATA-1 / })).toContainText('PASS');
    await expect(page.getByRole('button', { name: /^REQ-DATA-2 / })).toContainText('PASS');
  });
  test('5 injected settings hidden before reveal', async () => {
    await expect(page.getByText('injected (truth)', { exact: true })).toHaveCount(0);
    await expect(page.getByRole('heading', { name: 'Scorecard (grading)', exact: true })).toHaveCount(0);
  });
  test('6 reveal shows grading scorecard', async () => {
    await page.getByRole('button', { name: 'Reveal truth / grade', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Scorecard (grading)', exact: true })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Region coverage vs claimed', exact: true })).toBeVisible();
  });
  test('7 reveal shows injected faults and third history', async () => {
    await expect(page.getByText('injected (truth)', { exact: true })).toBeVisible();
    await expect(page.getByText('truth (grading)', { exact: true })).toBeVisible();
  });
  test('8 corrective action saved with operator', async () => {
    await page.getByLabel('Corrective action for Telemetry blackout', { exact: true }).fill('Re-test link budget at float altitude');
    await page.getByRole('button', { name: 'Save', exact: true }).click();
    await expect(page.getByText('Corrective action (PF Operator):', { exact: true })).toBeVisible();
  });
  test('9 closure saved with operator', async () => {
    await page.getByRole('button', { name: 'Mark done', exact: true }).first().click();
    await expect(page.getByRole('region', { name: 'Export & closure', exact: true })).toContainText(/Payload recovered\s*PF Operator/);
  });
  test('10 downloaded JSON retains canonical hash and excludes truth', async ({}, info) => {
    const pending = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Report + records JSON', exact: true }).click();
    const download = await pending;
    const file = info.outputPath('mission.json');
    await download.saveAs(file);
    const body = JSON.parse(await fs.readFile(file, 'utf8'));
    expect(body.report.truth_track).toBeUndefined();
    expect(body.report.scorecard).toBeUndefined();
    expect(body.mission.faults).toEqual({});
    expect(body.report.faults.every((f) => f.injected === null)).toBeTruthy();
    const python = process.env.TEST_PYTHON ?? path.resolve('..', '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
    const verified = execFileSync(python, ['-c', 'import json,hashlib,sys; x=json.load(open(sys.argv[1],encoding="utf-8")); c=json.dumps(x["report"],sort_keys=True,separators=(",",":"),default=str); assert hashlib.sha256(c.encode()).hexdigest()==x["report_sha256"]; print("verified")', file], { encoding: 'utf8' });
    expect(verified.trim()).toBe('verified');
    await expect(page.getByText(/report SHA-256:/)).toContainText(body.report_sha256);
    await page.screenshot({ path: info.outputPath('postflight.png') });
  });
  test('11 no browser or API errors', async () => { expect(consoleErrors).toEqual([]); });
});
