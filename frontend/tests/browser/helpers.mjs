import { expect } from '@playwright/test';

export const API = process.env.TEST_API_URL ?? 'http://127.0.0.1:8001';
export const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
export async function api(request, path, data) {
  const response = data === undefined ? await request.get(`${API}${path}`) : await request.post(`${API}${path}`, { data });
  expect(response.ok(), `${path}: HTTP ${response.status()} ${await response.text()}`).toBeTruthy();
  return response.json();
}

export async function flown(request, faults = {}, name = 'Browser blackout') {
  const { mission_id: id } = await api(request, '/api/missions', { name, seed: 11, time_scale: 600, atmosphere: 'snapshot', faults });
  let firstGo = null;
  const deadline = Date.now() + 150000;
  while (Date.now() < deadline) {
    const summary = await api(request, `/api/missions/${id}`);
    if (summary.recommendation === 'GO_RECOMMENDED' && firstGo === null) firstGo = summary.t;
    // Delayed commit exercises the multiple-GO regression which the original suite missed.
    if (firstGo !== null && summary.t >= firstGo + 30 && summary.recommendation === 'GO_RECOMMENDED' && summary.operator.state === 'none') {
      await api(request, `/api/missions/${id}/commit`, { operator: 'Script Operator' });
    }
    if (summary.state === 'finished') {
      expect(summary.end_reason).toBe('landed');
      return id;
    }
    await pause(150);
  }
  throw new Error(`Mission ${id} did not land before the test deadline`);
}

export function errors(page) {
  const found = [];
  page.on('pageerror', (e) => found.push(e.message));
  page.on('console', (m) => { if (m.type() === 'error') found.push(m.text()); });
  page.on('response', (r) => { if (r.url().startsWith(API) && r.status() >= 400) found.push(`HTTP ${r.status()} ${r.url()}`); });
  return found;
}
