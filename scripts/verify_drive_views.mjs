// Headless check of onboard / grandstand / helicopter cameras.
// Needs the viewer server (scripts/serve.py) and playwright-core plus Chrome.
//   python3 scripts/serve.py 8766
//   node scripts/verify_drive_views.mjs
//
// For layout A and the saved edit version-1: the onboard eye stays above the
// terrain for the whole lap, and the scrub slider moves the camera. Also
// checks the layout dropdown lists version-1 on a fresh load.

import { existsSync } from 'node:fs';
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

const editsRes = await fetch(`${base}/api/layouts/edits?site=nishiwaki_basin`);
if (!editsRes.ok) throw new Error('GET /api/layouts/edits failed: ' + editsRes.status);
const edits = await editsRes.json();
if (!edits.edits?.includes('version-1')) {
    throw new Error('API edits list missing version-1: ' + JSON.stringify(edits));
}
const indexRes = await fetch(`${base}/data/sites/nishiwaki_basin/layouts/edits/index.json`);
if (!indexRes.ok) throw new Error('edits/index.json failed: ' + indexRes.status);
const index = await indexRes.json();
if (!index.edits?.includes('version-1')) {
    throw new Error('index.json missing version-1: ' + JSON.stringify(index));
}
console.log('edits api', edits.edits.join(','));

const browser = await chromium.launch({
    channel: 'chrome',
    headless: true,
    args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-webgl', '--ignore-gpu-blocklist'],
});

const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const errors = [];
const missed = [];
page.on('pageerror', (err) => errors.push(String(err)));
page.on('console', (msg) => {
    if (msg.type() === 'error' && !/Failed to load resource/.test(msg.text())) errors.push(msg.text());
});
page.on('response', (res) => {
    if (res.status() >= 400 && !res.url().endsWith('/favicon.ico')) {
        missed.push(`${res.status()} ${res.url()}`);
    }
});

async function openLayout(layout) {
    const url = `${base}/site.html?site=nishiwaki_basin&layout=${layout}&capture=1`;
    await page.goto(url, { waitUntil: 'networkidle' });
    await page.waitForFunction(() => window.__ready && window.__tracklab?.getStats()?.lapLengthM, null, { timeout: 30000 });
    await page.waitForTimeout(400);
}

function assertClearance(name, report) {
    console.log(
        `${name}: min clearance ${report.minClearanceM.toFixed(3)} m `
        + `at ${report.minAtM.toFixed(0)} m, max ${report.maxClearanceM.toFixed(3)} m, `
        + `${report.samples} samples, lap ${report.lapLengthM.toFixed(1)} m, `
        + `crest ${report.crestDistanceM.toFixed(0)} m (prominence ${report.crestProminenceM.toFixed(1)} m)`,
    );
    if (!(report.samples > 100)) throw new Error(name + ' probe took too few samples');
    if (!(report.minClearanceM >= 1.0)) {
        throw new Error(`${name} onboard camera dips below 1.0 m clearance (${report.minClearanceM})`);
    }
    if (!(report.minClearanceM <= 2.0)) {
        throw new Error(`${name} clearance ${report.minClearanceM} is not driver eye height`);
    }
}

await openLayout('layout_a_straight_hairpin');

const options = await page.evaluate(() => window.__tracklab.layoutOptions());
console.log('dropdown', options.map(o => o.value).join(','));
if (!options.some(o => o.value === 'version-1')) {
    throw new Error('dropdown does not list version-1 after a fresh load');
}

await page.click('#btn-cam-onboard');
await page.waitForFunction(() => window.__tracklab.getStats().cameraMode === 'onboard');
const reportA = await page.evaluate(() => window.__tracklab.probeOnboardClearance(5));
assertClearance('layout A', reportA);

const moved = await page.evaluate(() => {
    window.__tracklab.setDriveDistance(0, true);
    const a = window.__tracklab.cameraState();
    const lap = window.__tracklab.getStats().lapLengthM;
    const scrub = document.getElementById('drive-scrub');
    const target = Math.round(lap * 0.37);
    scrub.value = String(target);
    scrub.dispatchEvent(new Event('input', { bubbles: true }));
    const b = window.__tracklab.cameraState();
    const clearance = window.__tracklab.clearanceUnderCamera();
    return {
        shift: Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z),
        distanceM: b.distanceM,
        target,
        clearance,
    };
});
console.log('scrub', JSON.stringify(moved));
if (!(moved.shift > 20)) throw new Error('scrub slider did not move the onboard camera');
if (Math.abs(moved.distanceM - moved.target) > 2) {
    throw new Error('scrub did not seek to the requested distance');
}
if (!(moved.clearance >= 1.0)) throw new Error('camera under the scrubbed view is below the terrain');

await page.selectOption('#layout-select', 'version-1');
await page.waitForFunction(() => window.__tracklab.getStats().layoutId === 'version-1', null, { timeout: 15000 });
await page.click('#btn-cam-onboard');
await page.waitForFunction(() => window.__tracklab.getStats().cameraMode === 'onboard');
const reportV = await page.evaluate(() => window.__tracklab.probeOnboardClearance(5));
assertClearance('version-1', reportV);

const crestV = (reportV.crestDistanceM - 70 + reportV.lapLengthM) % reportV.lapLengthM;
await page.evaluate((d) => window.__tracklab.setDriveDistance(d, true), crestV);
await page.waitForTimeout(500);
await page.screenshot({ path: join(root, 'docs/onboard_crest.png') });

const movedV = await page.evaluate(() => {
    const lap = window.__tracklab.getStats().lapLengthM;
    window.__tracklab.setDriveDistance(0, true);
    const a = window.__tracklab.cameraState();
    const scrub = document.getElementById('drive-scrub');
    scrub.value = String(Math.round(lap * 0.62));
    scrub.dispatchEvent(new Event('input', { bubbles: true }));
    const b = window.__tracklab.cameraState();
    return Math.hypot(a.x - b.x, a.y - b.y, a.z - b.z);
});
if (!(movedV > 20)) throw new Error('scrub slider did not move the camera on version-1');
console.log('version-1 scrub shift', movedV.toFixed(1), 'm');

const viewAt = Math.round(reportV.lapLengthM * 0.22);
await page.evaluate((d) => window.__tracklab.setDriveDistance(d, true), viewAt);
await page.click('#btn-cam-grandstand');
await page.waitForFunction(() => window.__tracklab.getStats().cameraMode === 'grandstand');
await page.waitForTimeout(400);
const stands = await page.evaluate(() => window.__tracklab.cameraState().stands);
console.log('stands', JSON.stringify(stands));
if (!stands.some(s => /best/i.test(s.name))) throw new Error('missing best-viewpoint grandstand');
if (stands.length < 2) throw new Error('expected a second grandstand');
await page.screenshot({ path: join(root, 'docs/grandstand.png') });

await page.click('#btn-cam-helicopter');
await page.waitForFunction(() => window.__tracklab.getStats().cameraMode === 'helicopter');
await page.waitForTimeout(400);
const heli = await page.evaluate(() => {
    const cam = window.__tracklab.cameraState();
    const marker = window.__tracklab.markerWorld();
    const above = cam.y - marker.y;
    const behind = Math.hypot(cam.x - marker.x, cam.z - marker.z);
    return { above, behind, visible: marker.visible };
});
console.log('helicopter', JSON.stringify(heli));
if (!(heli.above >= 60 && heli.above <= 120)) {
    throw new Error(`helicopter is ${heli.above.toFixed(1)} m above the car, want 60–120`);
}
if (!(heli.behind >= 40 && heli.behind <= 140)) {
    throw new Error(`helicopter is ${heli.behind.toFixed(1)} m behind the car`);
}
if (!heli.visible) throw new Error('car marker hidden in helicopter view');
await page.screenshot({ path: join(root, 'docs/helicopter.png') });

await page.evaluate(() => {
    const s = document.getElementById('layout-select');
    s.size = s.options.length;
});
await page.waitForTimeout(200);
await page.screenshot({ path: join(root, 'docs/layout_dropdown_version1.png') });

if (missed.length) console.log('http misses:\n' + missed.join('\n'));
if (errors.length) {
    console.log('page errors:', errors.join('\n'));
    throw new Error('page errors during drive-view check');
}

await browser.close();
console.log('drive view check passed');
