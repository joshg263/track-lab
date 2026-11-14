// Headless check of the Nishiwaki layout editor.
// Needs the viewer server (scripts/serve.py) and playwright-core plus Chrome.
//   python3 scripts/serve.py 8765
//   node scripts/verify_layout_edit.mjs
//
// PLAYWRIGHT_CORE can point at playwright-core's package root. Chrome is the
// system browser (channel "chrome"); this script is not a viewer dependency.

import { spawnSync } from 'node:child_process';
import { existsSync, readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const port = process.env.PORT || '8765';
const base = `http://127.0.0.1:${port}`;
const variant = 'spike7_drag_test';

const pwRoot = process.env.PLAYWRIGHT_CORE || '/tmp/pw/node_modules/playwright-core';
const pw = await import(pathToFileURL(join(pwRoot, 'index.js')).href);
const chromium = pw.chromium || pw.default.chromium;

const browser = await chromium.launch({
    channel: 'chrome',
    headless: true,
    args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-webgl', '--ignore-gpu-blocklist'],
});

const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errors = [];
page.on('pageerror', (err) => errors.push(String(err)));
page.on('console', (msg) => {
    if (msg.type() === 'error') errors.push(msg.text());
});

const url = `${base}/site.html?site=nishiwaki_basin&layout=layout_a_straight_hairpin&capture=1`;
await page.goto(url, { waitUntil: 'networkidle' });
await page.waitForFunction(() => window.__ready && window.__tracklab?.getStats()?.lapLengthM, null, { timeout: 30000 });
await page.waitForTimeout(600);

await page.click('#btn-edit-mode');
await page.waitForFunction(() => window.__tracklab.getStats().editMode);

const before = await page.evaluate(() => window.__tracklab.getStats());
const points = await page.evaluate(() => {
    const n = window.__tracklab.getStats().pointCount;
    const out = [];
    for (let i = 0; i < n; i++) out.push({ i, ...window.__tracklab.screenPoint(i) });
    return out;
});
const target = points.find(p => p.x > 420 && p.x < 1100 && p.y > 160 && p.y < 640) || points[4];
console.log('drag point', target.i, 'at', target.x.toFixed(0), target.y.toFixed(0));
console.log('before', JSON.stringify(before));

await page.screenshot({ path: join(root, 'docs/edit_before_drag.png') });

await page.mouse.move(target.x, target.y);
await page.mouse.down();
await page.mouse.move(target.x + 140, target.y - 90, { steps: 12 });
await page.mouse.up();
await page.waitForFunction(() => {
    const s = window.__tracklab.getStats();
    return s.viewshedPct != null && !s.viewshedPending;
}, null, { timeout: 10000 });
await page.waitForTimeout(400);

const after = await page.evaluate(() => window.__tracklab.getStats());
const moved = await page.evaluate((i) => {
    const p = window.__tracklab.screenPoint(i);
    return p;
}, target.i);
console.log('after', JSON.stringify(after));
console.log('viewshed ms', after.viewshedMs, 'live pending was cleared');

const lapChanged = Math.abs(after.lapLengthM - before.lapLengthM) > 1;
const gradChanged = Math.abs(after.maxGradientPct - before.maxGradientPct) > 0.05;
const radiusChanged = Math.abs((after.minRadiusM ?? 0) - (before.minRadiusM ?? 0)) > 0.05;
const cornersChanged = after.numCorners !== before.numCorners;
if (!(lapChanged || gradChanged || radiusChanged || cornersChanged)) {
    throw new Error('stats did not change after drag: ' + JSON.stringify({ before, after }));
}
const screenShift = Math.hypot(moved.x - target.x, moved.y - target.y);
if (screenShift < 20) {
    throw new Error(`point did not move on screen (${screenShift.toFixed(1)} px)`);
}

await page.screenshot({ path: join(root, 'docs/edit_after_drag.png') });

await page.click('#btn-add-point');
await page.waitForFunction((n) => window.__tracklab.getStats().pointCount === n + 1, before.pointCount);
await page.click('#btn-undo');
await page.waitForFunction((n) => window.__tracklab.getStats().pointCount === n, before.pointCount);

await page.click('#btn-delete-point');
await page.waitForFunction((n) => window.__tracklab.getStats().pointCount === n - 1, before.pointCount);
await page.click('#btn-undo');
await page.waitForFunction((n) => window.__tracklab.getStats().pointCount === n, before.pointCount);

await page.click('#btn-reset');
await page.waitForFunction((lap) => Math.abs(window.__tracklab.getStats().lapLengthM - lap) < 0.01, before.lapLengthM);
await page.click('#btn-undo');
await page.waitForFunction((lap) => Math.abs(window.__tracklab.getStats().lapLengthM - lap) < 0.01, after.lapLengthM);

await page.fill('#variant-name', variant);
await page.fill('#session-note', 'dragged a point on layout A');
await page.click('#btn-add-note');
await page.click('#btn-save-layout');
await page.waitForFunction(() => document.getElementById('save-status').textContent.includes('edits/'), null, { timeout: 5000 });

const editPath = join(root, 'data/sites/nishiwaki_basin/layouts/edits', `${variant}.json`);
if (!existsSync(editPath)) throw new Error('saved layout missing: ' + editPath);
const saved = JSON.parse(readFileSync(editPath, 'utf8'));
if (!Array.isArray(saved.control_points) || saved.control_points.length < 3) {
    throw new Error('saved layout has no control points');
}
if (saved.coordinate_system !== 'local_m') throw new Error('saved layout schema');

const check = spawnSync('python3', ['scripts/layout_check.py', editPath, '--quiet'], {
    cwd: root,
    encoding: 'utf8',
});
if (check.status !== 0 && check.status !== 1) {
    throw new Error('layout_check crashed: ' + check.stderr);
}
const report = `${check.stdout}\n${check.stderr}`;
if (/Traceback|Error checking/.test(report)) {
    throw new Error('layout_check rejected the file:\n' + report);
}
if (!/PASS|FAIL/.test(report)) {
    throw new Error('layout_check did not produce a report:\n' + report);
}
console.log(report.trim());

const logPath = join(root, 'logs/session-events.jsonl');
const lines = readFileSync(logPath, 'utf8').trim().split('\n').map(l => JSON.parse(l));
const types = new Set(lines.map(e => e.type));
for (const type of ['edit_mode_on', 'drag', 'note', 'save']) {
    if (!types.has(type)) throw new Error('missing event type ' + type);
}
const drag = lines.find(e => e.type === 'drag');
if (typeof drag.point_index !== 'number' || !drag.before || !drag.after) {
    throw new Error('drag event missing point index / before / after');
}
if (!drag.ts || !drag.layout_id) throw new Error('event missing ts or layout_id');
const note = lines.find(e => e.type === 'note');
if (!note.text || !note.ts) throw new Error('note event incomplete');
console.log('events', lines.length, 'types', [...types].join(','));

await page.setViewportSize({ width: 390, height: 844 });
await page.waitForTimeout(400);
await page.screenshot({ path: join(root, 'docs/edit_iphone.png') });

if (errors.length) {
    console.log('page errors:', errors.join('\n'));
}

await browser.close();
console.log('layout edit check passed');
