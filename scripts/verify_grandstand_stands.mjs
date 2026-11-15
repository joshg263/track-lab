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

function assertNearTrack(label, stands, maxM) {
    for (const s of stands) {
        if (s.preset) continue;
        if (!(s.distM <= maxM)) {
            throw new Error(`${label}: stand "${s.name}" is ${s.distM.toFixed(1)} m from track, want ≤${maxM}`);
        }
    }
}

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

await openLayout('layout_a_straight_hairpin');
const defaultsA = await page.evaluate(() => {
    const tl = window.__tracklab;
    const custom = tl.cameraState().customStands;
    return custom.map((s) => ({
        name: s.name,
        distM: tl.nearestTrackDistanceM(s.x, s.y),
        preset: false,
    }));
});
console.log('layout A default stands', JSON.stringify(defaultsA));
if (defaultsA.length < 2) throw new Error('layout A: expected 2 default stands');
if (/best/i.test(defaultsA[0]?.name || '')) {
    throw new Error('layout A: first stand should be track-side, not best viewpoint');
}
assertNearTrack('layout A', defaultsA, 150);

await openLayout('version-1');
const defaultsV1 = await page.evaluate(() => {
    const tl = window.__tracklab;
    return tl.cameraState().customStands.map((s) => ({
        name: s.name,
        distM: tl.nearestTrackDistanceM(s.x, s.y),
    }));
});
console.log('version-1 default stands', JSON.stringify(defaultsV1));
assertNearTrack('version-1', defaultsV1, 150);

await page.click('#btn-cam-grandstand');
await page.waitForFunction(() => window.__tracklab.getStats().cameraMode === 'grandstand');

const placed = await page.evaluate(() => {
    const tl = window.__tracklab;
    const base = tl.cameraState().customStands[0];
    return tl.placeStandAt(base.x + 35, base.y + 22);
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

await page.click('#btn-cam-grandstand');
await page.waitForTimeout(500);
await page.screenshot({ path: join(root, 'docs/grandstand_placed_stand.png') });

await openLayout('layout_a_straight_hairpin');
await page.click('#btn-cam-grandstand');
await page.waitForTimeout(500);
await page.screenshot({ path: join(root, 'docs/grandstand_default_stands.png') });

await browser.close();
console.log('grandstand stands check passed');