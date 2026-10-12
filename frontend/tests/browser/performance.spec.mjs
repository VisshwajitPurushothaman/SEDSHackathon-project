import { test, expect } from '@playwright/test';

test('Flight renderer pauses while hidden and resumes; records actual render rate', async ({ page }, info) => {
  let fiberUrl;
  page.on('request', (r) => { if (r.url().includes('/@react-three_fiber.js')) fiberUrl = r.url(); });
  await page.goto('/#/flight');
  await expect(page.getByRole('region', { name: '3D flight view', exact: true }).locator('canvas')).toBeVisible();
  await expect.poll(() => Boolean(fiberUrl)).toBeTruthy();
  const state = () => page.evaluate(async (url) => {
    const { _roots } = await import(url);
    const canvas = document.querySelector('[aria-label="3D flight view"] canvas');
    const s = _roots.get(canvas).store.getState();
    const gl = s.gl.getContext();
    const ext = gl.getExtension('WEBGL_debug_renderer_info');
    return { frame: s.gl.info.render.frame, time: performance.now(), loop: s.frameloop, dpr: s.viewport.dpr,
      renderer: ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : 'unavailable' };
  }, fiberUrl);
  await expect.poll(async () => (await state()).frame).toBeGreaterThan(10);
  const before = await state();
  await page.waitForTimeout(2000); // a measurement interval, not an application readiness wait
  const after = await state();
  const metrics = { ...after, sampled_fps: 1000 * (after.frame - before.frame) / (after.time - before.time) };
  await info.attach('render-performance', { body: JSON.stringify(metrics, null, 2), contentType: 'application/json' });
  console.log('RENDER_METRICS', JSON.stringify(metrics));
  expect(after.dpr).toBeLessThanOrEqual(1.75);
  await page.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await page.waitForTimeout(300);
  const hidden = await state();
  expect(hidden.loop).toBe('never');
  await page.waitForTimeout(300);
  expect((await state()).frame).toBe(hidden.frame);
  await page.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  await expect.poll(async () => (await state()).frame).toBeGreaterThan(hidden.frame);
});

test('reduced motion changes Flight camera without an animated transition', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  let fiberUrl;
  page.on('request', (r) => { if (r.url().includes('/@react-three_fiber.js')) fiberUrl = r.url(); });
  await page.goto('/#/flight');
  await expect(page.getByRole('region', { name: '3D flight view', exact: true }).locator('canvas')).toBeVisible();
  await page.getByRole('button', { name: 'Top-down', exact: true }).click();
  const y = await page.evaluate(async (url) => {
    const { _roots } = await import(url);
    const s = _roots.get(document.querySelector('[aria-label="3D flight view"] canvas')).store.getState();
    await new Promise(requestAnimationFrame);
    return s.camera.position.y;
  }, fiberUrl);
  expect(y).toBeCloseTo(160, 3);
});
