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

const STAND_MARGIN_M = 15;

async function footprintReportOnPage(page) {
    return page.evaluate(() => {
        const tl = window.__tracklab;
        const rows = tl.standFootprintClearances();
        const min = tl.layoutMinFootprintClearanceM();
        return { rows, minClearanceM: min };
    });
}

function assertFootprints(label, report) {
    console.log(
        `${label} footprint min clearance ${report.minClearanceM?.toFixed?.(3) ?? 'n/a'} m`,
        JSON.stringify(report.rows),
    );
    for (const r of report.rows) {
        if (r.overlapsRoad) {
            throw new Error(`${label}: stand "${r.name}" footprint overlaps road`);
        }
        if (r.footprintClearanceM == null || r.footprintClearanceM < STAND_MARGIN_M - 0.35) {
            throw new Error(
                `${label}: stand "${r.name}" footprint clearance `
                    + `${r.footprintClearanceM?.toFixed(2) ?? 'n/a'} m < ${STAND_MARGIN_M} m`,
            );
        }
    }
}

async function defaultStandsOnPage(page) {
    return footprintReportOnPage(page);
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
    await page.click('#btn-cam-grandstand');
    await page.waitForTimeout(250);
    const report = await defaultStandsOnPage(page);
    if (report.rows.length < 2) throw new Error(`${layout}: expected 2 default stands`);
    if (/best/i.test(report.rows[0]?.name || '')) {
        throw new Error(`${layout}: first stand should be track-side, not best viewpoint`);
    }
    assertFootprints(layout, report);
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

await page.evaluate(() => window.__tracklab.setPlaceStandActive(true));
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
const reloaded = await page.evaluate((expectedCount) => {
    const stands = window.__tracklab.cameraState().customStands;
  return {
        count: stands.length,
        names: stands.map((s) => s.name),
        expectedCount,
    };
}, customBefore.length);
console.log('reloaded stands', JSON.stringify(reloaded));
if (reloaded.count !== customBefore.length) throw new Error('stand count changed after reload');
if (!reloaded.names.includes('Stand 3')) throw new Error('placed stand missing after reload');

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

await openLayout('version-1');
await page.click('#btn-cam-grandstand');
await page.waitForFunction(() => window.__tracklab.isPlaceStandActive() === false);
await page.click('#btn-place-stand');
await page.waitForFunction(() => window.__tracklab.isPlaceStandActive() === true);
await page.waitForTimeout(200);

const placeDrag = await page.evaluate(() => {
    const tl = window.__tracklab;
    const before = {
        count: tl.cameraState().customStands.length,
        cam: tl.cameraPosition(),
    };
    return before;
});

const canvas = await page.$('#canvas-container canvas');
const box = await canvas.boundingBox();
const x0 = box.x + box.width * 0.55;
const y0 = box.y + box.height * 0.5;
await page.mouse.move(x0, y0);
await page.mouse.down();
await page.mouse.move(x0 + 120, y0 + 40, { steps: 8 });
await page.waitForTimeout(80);
await page.mouse.up();
await page.waitForTimeout(200);

const afterDrag = await page.evaluate((camBefore) => {
    const tl = window.__tracklab;
    const cam = tl.cameraPosition();
    const shift = Math.hypot(cam.x - camBefore.x, cam.y - camBefore.y, cam.z - camBefore.z);
    return {
        count: tl.cameraState().customStands.length,
        shift,
    };
}, placeDrag.cam);
console.log('place drag', JSON.stringify(afterDrag));
if (afterDrag.count !== placeDrag.count) {
    throw new Error('drag in place mode added a stand');
}
if (!(afterDrag.shift > 2)) {
    throw new Error('drag in place mode did not move the camera');
}

const beforeClick = await page.evaluate(() => window.__tracklab.cameraState().customStands.length);
const x1 = box.x + box.width * 0.48;
const y1 = box.y + box.height * 0.52;
await page.mouse.move(x1, y1);
await page.mouse.down();
await page.mouse.up();
await page.waitForTimeout(200);
const afterClick = await page.evaluate(() => window.__tracklab.cameraState().customStands.length);
console.log('place click counts', beforeClick, '->', afterClick);
if (afterClick !== beforeClick + 1) {
    throw new Error('click in place mode should add exactly one stand');
}

await page.keyboard.press('Escape');
await page.waitForFunction(() => window.__tracklab.isPlaceStandActive() === false);
console.log('place mode camera interaction check passed');

const version2 = JSON.parse(readFileSync(fixturePath, 'utf8'));
await page.evaluate((layout) => window.__tracklab.previewLayout(layout), version2);
await page.waitForFunction(() => window.__tracklab.getStats().layoutId === 'version-2', null, { timeout: 15000 });
await page.click('#btn-cam-grandstand');
await page.waitForFunction(() => window.__tracklab.getStats().cameraMode === 'grandstand');
await page.waitForTimeout(300);

const grandstandEntry = await page.evaluate(() => ({
    seat: window.__tracklab.isStandSeatView(),
    listSel: window.__tracklab.listSelectionIndex(),
    controls: window.__tracklab.controlsEnabled(),
    cam: window.__tracklab.cameraPosition(),
    customCount: window.__tracklab.cameraState().customStands.length,
    markerCount: window.__tracklab.cameraState().customStands.length,
}));
console.log('version-2 grandstand entry', JSON.stringify(grandstandEntry));
const v2PreviewDefaults = await footprintReportOnPage(page);
assertFootprints('version-2 preview defaults', v2PreviewDefaults);
if (grandstandEntry.seat) throw new Error('Grandstand entry must not start in seat view');
if (grandstandEntry.listSel !== -1) throw new Error('Grandstand entry must select no stand');
if (!grandstandEntry.controls) throw new Error('Grandstand entry must keep orbit controls enabled');
if (!(grandstandEntry.cam?.y > 80)) {
    throw new Error('Grandstand entry camera should be overhead (high Y)');
}

const canvasV2 = await page.$('#canvas-container canvas');
const boxV2 = await canvasV2.boundingBox();
const orbitFrom = { x: boxV2.x + boxV2.width * 0.72, y: boxV2.y + boxV2.height * 0.38 };
await page.mouse.move(orbitFrom.x, orbitFrom.y);
await page.mouse.down();
await page.mouse.move(orbitFrom.x + 95, orbitFrom.y + 55, { steps: 10 });
await page.mouse.up();
await page.waitForTimeout(150);
const orbitShift = await page.evaluate((cam0) => {
    const cam1 = window.__tracklab.cameraPosition();
    return Math.hypot(cam1.x - cam0.x, cam1.y - cam0.y, cam1.z - cam0.z);
}, grandstandEntry.cam);
console.log('version-2 overhead canvas drag shift', orbitShift.toFixed(2));
if (!(orbitShift > 2)) throw new Error('empty canvas drag in Grandstand should move the camera');

const placeButtonOnly = await page.evaluate(() => {
    const n0 = window.__tracklab.cameraState().customStands.length;
    document.getElementById('btn-place-stand').click();
    document.getElementById('btn-place-stand').click();
    return { n0, n1: window.__tracklab.cameraState().customStands.length };
});
if (placeButtonOnly.n1 !== placeButtonOnly.n0) {
    throw new Error('Place stand button alone must not add stands');
}

await page.evaluate(() => window.__tracklab.deleteAllCustomStands());
await page.click('#btn-place-stand');
await page.waitForFunction(() => window.__tracklab.isPlaceStandActive());
const corners = await page.evaluate(() => window.__tracklab.cornerList());
const sorted = [...corners].sort((a, b) => a.radiusM - b.radiusM);
const tightCornerM = sorted[0]?.positionM ?? 250;
const hairpinM = sorted[Math.min(13, sorted.length - 1)]?.positionM ?? 900;
const beforePlace = await page.evaluate(() => window.__tracklab.cameraState().customStands.length);
const placeTargets = [tightCornerM, hairpinM];
const placementRows = [];
for (const distM of placeTargets) {
    const pt = await page.evaluate(
        (d) => window.__tracklab.clientPointBesideCentrelineM(d, 30),
        distM,
    );
    if (!pt) throw new Error(`no screen point beside centreline at ${distM} m`);
    await page.mouse.move(pt.x, pt.y);
    await page.mouse.down();
    await page.mouse.up();
    await page.waitForTimeout(220);
    const after = await page.evaluate(() => window.__tracklab.cameraState().customStands.length);
    placementRows.push({ distM, before: beforePlace + placementRows.length, after });
}
const placedCount = (await page.evaluate(() => window.__tracklab.cameraState().customStands.length)) - beforePlace;
if (placedCount < placeTargets.length) {
    throw new Error(`pointer place near corners added ${placedCount} stands, wanted ${placeTargets.length}`);
}
const v2Footprint = await footprintReportOnPage(page);
console.log('version-2 pointer placements', JSON.stringify({ placedCount, placementRows, v2Footprint }));
assertFootprints('version-2 placed', v2Footprint);

await page.evaluate(() => window.__tracklab.setPlaceStandActive(false));

const moveStart = await page.evaluate(() => {
    const tl = window.__tracklab;
    const n = tl.cameraState().customStands.length;
    const cam = tl.cameraPosition();
    for (let i = 0; i < n; i++) {
        const screen = tl.standMarkerScreen(i);
        if (!screen) continue;
        if (tl.pickStandMarkerAt(screen.x, screen.y) !== i) continue;
        return { index: i, s0: tl.cameraState().customStands[i], screen, cam };
    }
    return null;
});
if (!moveStart?.s0 || !moveStart?.screen) {
    throw new Error('no stand marker on screen with a reliable hit target for drag move');
}
const dragDx = 70;
const dragDy = 42;
const sx = Math.round(moveStart.screen.x);
const sy = Math.round(moveStart.screen.y);
await page.evaluate(
    ({ x, y, dx, dy }) => {
        const canvas = document.querySelector('#canvas-container canvas');
        const path = [
            ['pointerdown', x, y, 1],
            ['pointermove', x + dx * 0.35, y + dy * 0.35, 1],
            ['pointermove', x + dx, y + dy, 1],
            ['pointerup', x + dx, y + dy, 0],
        ];
        for (const [type, cx, cy, buttons] of path) {
            canvas.dispatchEvent(
                new PointerEvent(type, {
                    clientX: cx,
                    clientY: cy,
                    pointerId: 41,
                    isPrimary: true,
                    button: 0,
                    buttons,
                    bubbles: true,
                    cancelable: true,
                }),
            );
        }
    },
    { x: sx, y: sy, dx: dragDx, dy: dragDy },
);
await page.waitForTimeout(200);
const moved = await page.evaluate((before) => {
    const tl = window.__tracklab;
    const s1 = tl.cameraState().customStands[before.index];
    return {
        shiftM: Math.hypot(before.x - s1.x, before.y - s1.y),
        footprintClearanceM: tl.standFootprintClearances().find((r, i) => i === before.index)?.footprintClearanceM,
        camShift: Math.hypot(
            tl.cameraPosition().x - before.camX,
            tl.cameraPosition().y - before.camY,
            tl.cameraPosition().z - before.camZ,
        ),
    };
}, {
    ...moveStart.s0,
    index: moveStart.index,
    camX: moveStart.cam.x,
    camY: moveStart.cam.y,
    camZ: moveStart.cam.z,
});
console.log('version-2 stand marker drag move', JSON.stringify(moved));
if (!(moved.shiftM > 1)) throw new Error('pointer drag on stand marker should move the stand');
if (moved.footprintClearanceM < STAND_MARGIN_M - 0.5) throw new Error('moved stand footprint on/near tarmac');
if (moved.camShift > 35) throw new Error('moving a stand should not move the overhead camera');

await page.click('#stand-list button:first-child');
await page.waitForFunction(() => window.__tracklab.listSelectionIndex() >= 0);
await page.click('#btn-view-stand');
await page.waitForFunction(() => window.__tracklab.isStandSeatView());
const seatEnter = await page.evaluate(() => ({
    seat: window.__tracklab.isStandSeatView(),
    controls: window.__tracklab.controlsEnabled(),
    cam: window.__tracklab.cameraPosition(),
}));
console.log('version-2 seat enter', JSON.stringify(seatEnter));
if (!seatEnter.seat || !seatEnter.controls) throw new Error('seat view must enable controls');

const seatCam0 = seatEnter.cam;
const seatDragOrigin = { x: boxV2.x + boxV2.width * 0.5, y: boxV2.y + boxV2.height * 0.45 };
await page.mouse.move(seatDragOrigin.x, seatDragOrigin.y);
await page.mouse.down();
await page.mouse.move(seatDragOrigin.x + 110, seatDragOrigin.y - 40, { steps: 10 });
await page.mouse.up();
await page.waitForTimeout(150);
const seatRotate = await page.evaluate((c0) => {
    const c1 = window.__tracklab.cameraPosition();
    return Math.hypot(c1.x - c0.x, c1.y - c0.y, c1.z - c0.z);
}, seatCam0);
console.log('version-2 seat view drag shift', seatRotate.toFixed(2));
if (!(seatRotate > 0.5)) throw new Error('drag in seat view should rotate / move the camera');

await page.keyboard.press('Escape');
await page.waitForFunction(() => !window.__tracklab.isStandSeatView());
const afterEsc = await page.evaluate(() => ({
    seat: window.__tracklab.isStandSeatView(),
    controls: window.__tracklab.controlsEnabled(),
    listSel: window.__tracklab.listSelectionIndex(),
}));
console.log('version-2 Esc overhead', JSON.stringify(afterEsc));
if (afterEsc.seat) throw new Error('Esc should return to overhead Grandstand');
if (!afterEsc.controls) throw new Error('overhead Grandstand must keep controls enabled');

await page.click('#btn-cam-grandstand');
await page.waitForFunction(
    () => !window.__tracklab.isStandSeatView() && window.__tracklab.controlsEnabled(),
);
await page.waitForTimeout(400);
await page.screenshot({ path: join(root, 'docs/grandstand_t1_hairpin_version2.png') });
await page.screenshot({ path: join(root, 'docs/grandstand_sf_version2.png') });

await browser.close();
console.log('grandstand stands check passed');