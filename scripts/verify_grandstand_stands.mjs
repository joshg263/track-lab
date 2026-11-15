// Grandstand stand placement: defaults near track, save/reload via /api/layouts/edits.
//   python3 scripts/serve.py 8766
//   node scripts/verify_grandstand_stands.mjs

import { existsSync, readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const port = process.env.PORT || '8766';
const base = `http://127.0.0.1:${port}`;

const pwRoot = process.env.PLAYWRIGHT_CORE || '/tmp/pw/node_modules/playwright-core';
if (!existsSync(join(pwRoot, 'index.js'))) {
    throw new Error('playwright-core not found at ' + pwRoot);
}
const pw = await import(pathToFileURL(join(pwRoot, 'index.js')).href);
const chromium = pw.chromium || pw.default.chromium;

const fixturePath = join(root, 'scripts/fixtures/version-2.json');
const fixture = JSON.parse(readFileSync(fixturePath, 'utf8'));
const testName = `stands-test-${Date.now()}`;
const emptyName = `stands-empty-${Date.now()}`;
const v2Name = `version-2-verify-${Date.now()}`;

function assertEdgeClearanceRange(label, stands, minM = 40, maxM = 120) {
    for (const s of stands) {
        const d = s.distEdgeM;
        if (!(d >= minM - 0.5 && d <= maxM + 0.5)) {
            throw new Error(
                `${label}: stand "${s.name}" outside-edge clearance ${d.toFixed(1)} m, want ${minM}–${maxM}`,
            );
        }
    }
}

async function defaultStandsOnPage(page) {
    return page.evaluate(() => {
        const tl = window.__tracklab;
        return tl.cameraState().customStands.map((s) => ({
            name: s.name,
            distEdgeM: tl.trackEdgeClearanceM(s.x, s.y),
        }));
    });
}

const nanRes = await fetch(`${base}/api/layouts/edits`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
        name: `nan-test-${Date.now()}`,
        control_points: [{ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 0, y: 10 }],
        stands: [{ x: Number.NaN, y: 1 }],
    }),
});
if (nanRes.status !== 400) {
    throw new Error('NaN stand x should be rejected, got ' + nanRes.status);
}
console.log('NaN stand coords rejected');

const browser = await chromium.launch({
    channel: 'chrome',
    headless: true,
    args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-webgl', '--ignore-gpu-blocklist'],
});
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

async function openLayout(layout) {
    const url = `${base}/site.html?site=nishiwaki_basin&layout=${layout}&capture=1`;
    await page.goto(url, { waitUntil: 'networkidle' });
    await page.waitForFunction(
        () => window.__ready && window.__tracklab?.getStats()?.lapLengthM,
        null,
        { timeout: 30000 },
    );
    await page.waitForTimeout(400);
}

async function checkDefaults(layout) {
    await openLayout(layout);
    const defaults = await defaultStandsOnPage(page);
    console.log(`${layout} default stands`, JSON.stringify(defaults));
    if (defaults.length < 2) throw new Error(`${layout}: expected 2 default stands`);
    if (/best/i.test(defaults[0]?.name || '')) {
        throw new Error(`${layout}: first stand should be track-side, not best viewpoint`);
    }
    assertEdgeClearanceRange(layout, defaults);
}

await checkDefaults('layout_a_straight_hairpin');
await checkDefaults('version-1');

const v2Payload = {
    ...fixture,
    name: v2Name,
    edited_from: 'version-1',
};
delete v2Payload.stands;
const v2Save = await fetch(`${base}/api/layouts/edits`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(v2Payload),
});
if (!v2Save.ok) throw new Error('version-2 fixture save failed: ' + v2Save.status);
await checkDefaults(v2Name);

await openLayout('version-1');
await page.click('#btn-cam-grandstand');
await page.waitForFunction(() => window.__tracklab.getStats().cameraMode === 'grandstand');

const placed = await page.evaluate(() => {
    const tl = window.__tracklab;
    const base0 = tl.cameraState().customStands[0];
    return tl.placeStandAt(base0.x + 35, base0.y + 22);
});
if (!placed) throw new Error('placeStandAt failed');

const customBefore = await page.evaluate(() => window.__tracklab.cameraState().customStands);
const newStand = customBefore.find((s) => s.name === 'Stand 3') || customBefore[customBefore.length - 1];
if (!newStand) throw new Error('no placed stand in state');

const payload = {
    ...fixture,
    name: testName,
    edited_from: 'version-1',
    stands: customBefore.map((s) => ({
        name: s.name,
        x: Math.round(s.x * 100) / 100,
        y: Math.round(s.y * 100) / 100,
        eye_elev_m: Math.round(s.eyeElev * 10) / 10,
    })),
};

const saveRes = await fetch(`${base}/api/layouts/edits`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
});
if (!saveRes.ok) throw new Error('save failed: ' + saveRes.status + ' ' + (await saveRes.text()));

await openLayout(testName);
const reloaded = await page.evaluate((target) => {
    const stands = window.__tracklab.cameraState().customStands;
    const hit = stands.find((s) => Math.hypot(s.x - target.x, s.y - target.y) < 2);
    return { count: stands.length, found: !!hit, stands };
}, newStand);
console.log('reloaded stands', JSON.stringify({ count: reloaded.count, found: reloaded.found }));
if (!reloaded.found) throw new Error('placed stand missing after reload');

const emptySave = await fetch(`${base}/api/layouts/edits`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
        ...fixture,
        name: emptyName,
        edited_from: 'version-1',
        stands: [],
    }),
});
if (!emptySave.ok) throw new Error('empty stands save failed');

await openLayout(emptyName);
const emptyReload = await page.evaluate(() => window.__tracklab.cameraState().customStands);
console.log('empty stands reload', emptyReload.length);
if (emptyReload.length !== 0) {
    throw new Error('stands: [] should load with no custom stands (no auto-defaults)');
}
const presetsOnly = await page.evaluate(() =>
    window.__tracklab.cameraState().stands.filter((s) => s.preset).length,
);
if (presetsOnly < 2) throw new Error('presets missing after empty stands load');

await page.click('#btn-cam-grandstand');
await page.waitForTimeout(500);
await page.screenshot({ path: join(root, 'docs/grandstand_placed_stand.png') });

await openLayout('layout_a_straight_hairpin');
await page.click('#btn-cam-grandstand');
await page.waitForTimeout(500);
await page.screenshot({ path: join(root, 'docs/grandstand_default_stands.png') });

await browser.close();
console.log('grandstand stands check passed');