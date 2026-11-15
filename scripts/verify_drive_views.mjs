// Headless check of onboard / grandstand / helicopter cameras.
// Needs the viewer server (scripts/serve.py) and playwright-core plus Chrome.
//   python3 scripts/serve.py 8766
//   node scripts/verify_drive_views.mjs
//
// For layout A and the saved edit version-1: the onboard eye stays above the
// terrain for the whole lap, and the scrub slider moves the camera. Also
// checks the layout dropdown lists version-1 on a fresh load.

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

const indexSave = await fetch(`${base}/api/layouts/edits`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
        name: 'index',
        coordinate_system: 'local_m',
        control_points: [{ x: 0, y: 0 }, { x: 10, y: 0 }, { x: 0, y: 10 }],
    }),
});
if (indexSave.status !== 400) {
    throw new Error('saving a variant named index should be rejected, got ' + indexSave.status);
}
const indexAfter = await (await fetch(`${base}/data/sites/nishiwaki_basin/layouts/edits/index.json`)).json();
if (!Array.isArray(indexAfter.edits) || indexAfter.edits.includes('index') || indexAfter.control_points) {
    throw new Error('index.json was overwritten by a layout save: ' + JSON.stringify(indexAfter));
}
if (!indexAfter.edits.includes('version-1')) {
    throw new Error('index.json lost version-1: ' + JSON.stringify(indexAfter));
}
console.log('reserved name index rejected');

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
const summaryA = await page.evaluate(() => window.__tracklab.profileSummary());
console.log('layout A profile', JSON.stringify(summaryA));
if (!summaryA || !(summaryA.maxSpeedKmh - summaryA.minSpeedKmh > 20)) {
    throw new Error('layout A speed profile is missing or flat');
}
const readoutA = await page.$eval('#drive-readout', (el) => el.innerText);
if (!/km\/h/.test(readoutA) || !/throttle|brake|coast/.test(readoutA)) {
    throw new Error('speed readout missing km/h or throttle/brake/coast: ' + readoutA);
}
const estimateA = await page.$eval('#lap-estimate', (el) => el.textContent);
if (!/estimate/i.test(estimateA)) throw new Error('lap time is not labelled estimate: ' + estimateA);
console.log('readout', readoutA.replace(/\n/g, ' | '), '|', estimateA);

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

await page.evaluate((d) => window.__tracklab.setDriveDistance(d, true), Math.round(reportA.lapLengthM * 0.22));
await page.evaluate(() => document.getElementById('speed-trace').scrollIntoView({ block: 'center' }));
await page.waitForTimeout(350);
await page.screenshot({ path: join(root, 'docs/onboard_speed.png') });

await page.fill('#car-vmax', '180');
await page.dispatchEvent('#car-vmax', 'change');
const capped = await page.evaluate(() => window.__tracklab.profileSummary());
console.log('capped top', capped.maxSpeedKmh.toFixed(1));
if (!(capped.maxSpeedKmh <= 180.5)) throw new Error('top speed setting did not cap the profile');
await page.fill('#car-vmax', '250');
await page.dispatchEvent('#car-vmax', 'change');
await page.click('#show-racing-line');
await page.click('#show-racing-line');
await page.click('#horizon-steady');
await page.click('#horizon-steady');

await page.selectOption('#layout-select', 'version-1');
await page.waitForFunction(() => window.__tracklab.getStats().layoutId === 'version-1', null, { timeout: 15000 });
await page.click('#btn-cam-onboard');
await page.waitForFunction(() => window.__tracklab.getStats().cameraMode === 'onboard');
const reportV = await page.evaluate(() => window.__tracklab.probeOnboardClearance(5));
assertClearance('version-1', reportV);

const summaryV = await page.evaluate(() => window.__tracklab.profileSummary());
console.log('version-1 profile', JSON.stringify(summaryV));
if (!summaryV || !(summaryV.maxSpeedKmh - summaryV.minSpeedKmh > 20)) {
    throw new Error('version-1 speed profile is missing or flat');
}

const crestV = (reportV.crestDistanceM - 70 + reportV.lapLengthM) % reportV.lapLengthM;
await page.evaluate((d) => window.__tracklab.setDriveDistance(d, true), crestV);
await page.evaluate(() => document.getElementById('speed-trace').scrollIntoView({ block: 'center' }));
await page.waitForTimeout(500);
await page.screenshot({ path: join(root, 'docs/onboard_crest.png') });
await page.evaluate((d) => window.__tracklab.setDriveDistance(d, true), Math.round(reportV.lapLengthM * 0.45));
await page.evaluate(() => document.getElementById('speed-trace').scrollIntoView({ block: 'center' }));
await page.waitForTimeout(300);
await page.screenshot({ path: join(root, 'docs/speed_trace_version1.png') });

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
await page.evaluate(() => {
    document.getElementById('layout-select').removeAttribute('size');
});

const version2 = JSON.parse(readFileSync(join(root, 'scripts/fixtures/version-2.json'), 'utf8'));
const previewed = await page.evaluate((layout) => window.__tracklab.previewLayout(layout), version2);
if (!previewed) throw new Error('version-2 preview failed');
await page.waitForFunction(() => window.__tracklab.getStats().layoutId === 'version-2', null, { timeout: 15000 });
const summary2 = await page.evaluate(() => window.__tracklab.profileSummary());
console.log('version-2 profile', JSON.stringify(summary2));
if (!summary2 || !(summary2.maxSpeedKmh > summary2.minSpeedKmh + 20)) {
    throw new Error('version-2 speed profile is missing or flat');
}
await page.click('#btn-cam-orbit');
await page.waitForFunction(() => window.__tracklab.getStats().cameraMode === 'orbit');
await page.evaluate(() => {
    const box = document.getElementById('show-racing-line');
    if (!box.checked) {
        box.checked = true;
        box.dispatchEvent(new Event('change', { bubbles: true }));
    }
    window.__tracklab.frameTrack();
});
await page.waitForTimeout(500);
await page.screenshot({ path: join(root, 'docs/version2_racing_line.png') });

await page.click('#btn-cam-onboard');
await page.waitForFunction(() => window.__tracklab.getStats().cameraMode === 'onboard');
const report2 = await page.evaluate(() => window.__tracklab.probeOnboardClearance(5));
assertClearance('version-2', report2);
await page.evaluate((d) => window.__tracklab.setDriveDistance(d, true), Math.round(report2.lapLengthM * 0.28));
await page.evaluate(() => document.getElementById('speed-trace').scrollIntoView({ block: 'center' }));
await page.waitForTimeout(450);
await page.screenshot({ path: join(root, 'docs/version2_onboard.png') });

if (missed.length) console.log('http misses:\n' + missed.join('\n'));
if (errors.length) {
    console.log('page errors:', errors.join('\n'));
    throw new Error('page errors during drive-view check');
}

await browser.close();
console.log('drive view check passed');
