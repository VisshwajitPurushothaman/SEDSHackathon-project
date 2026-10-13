// Audit A2 layer 3 (read-only): record what the UI renders once a second.
//   node tools/audit/ui_tap.mjs [url] [seconds]
// Writes data/audit/ui_values.jsonl: {utc, values: {MNEMONIC: number}} from every rendered table row whose first
// cell is an upper-case mnemonic and second cell is numeric, plus counts of open WebSocket connections.
import { chromium } from 'playwright-core';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');
const url = process.argv[2] ?? 'http://localhost:5173/#/flight';
const seconds = Number(process.argv[3] ?? 120);
const out = path.join(ROOT, 'data', 'audit', 'ui_values.jsonl');
fs.mkdirSync(path.dirname(out), { recursive: true });
const fh = fs.openSync(out, 'w');

const b = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', args: ['--headless=new'] });
const p = await b.newPage({ viewport: { width: 1440, height: 900 } });
let sockets = 0;
const errors = [];
p.on('websocket', (ws) => { sockets += 1; ws.on('close', () => { sockets -= 1; }); });
p.on('pageerror', (e) => errors.push(e.message));
p.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
await p.goto(url);
await p.waitForTimeout(3000);
const grid = p.getByRole('button', { name: /telemetry/i }).first();
if (await grid.count()) await grid.click().catch(() => {});
for (let i = 0; i < seconds; i++) {
  const values = await p.evaluate(() => {
    const v = {};
    for (const tr of document.querySelectorAll('tr')) {
      const td = tr.querySelectorAll('td');
      if (td.length < 2) continue;
      const k = td[0].innerText.trim();
      const n = Number(td[1].innerText.trim().replace(/[^0-9eE+\-.]/g, ''));
      if (/^[A-Z][A-Z0-9_]{2,}$/.test(k) && td[1].innerText.trim() !== '' && Number.isFinite(n)) v[k] = n;
    }
    return v;
  });
  fs.writeSync(fh, JSON.stringify({ utc: Date.now() / 1000, sockets, values }) + '\n');
  await p.waitForTimeout(1000);
}
fs.closeSync(fh);
console.log(`ui samples ${seconds}, open websockets at end ${sockets}, console errors ${errors.length}: ${errors.slice(0, 3).join(' | ')}`);
await b.close();
