// Headless screenshots of the run-off zones on Nishiwaki.
// Needs the viewer server and playwright-core plus Chrome.
//   python3 scripts/serve.py 8766
//   PLAYWRIGHT_CORE=/tmp/pw/node_modules/playwright-core node scripts/screenshot_runoff.mjs

import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const port = process.env.PORT || '8766';
const base = `http://127.0.0.1:${port}`;
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
    if (msg.type() === 'error' && !/Failed to load resource/.test(msg.text())) errors.push(msg.text());
});
page.on('response', (res) => {
    if (res.status() >= 400 && !res.url().endsWith('/favicon.ico')) {
        errors.push(`${res.status()} ${res.url()}`);
    }
});

async function openLayout(layout) {
    const url = `${base}/site.html?site=nishiwaki_basin&layout=${layout}&capture=1`;
    await page.goto(url, { waitUntil: 'networkidle' });
    await page.waitForFunction(
        () => window.__ready && window.__tracklab?.runoffSummary()?.cornerCount > 0,
        null,
        { timeout: 30000 },
    );
    await page.waitForTimeout(500);
}

async function revealReasons() {
    await page.evaluate(() => {
        document.getElementById('runoff-reasons-wrap')?.scrollIntoView({ block: 'center' });
    });
}

async function shoot(name) {
    await page.evaluate(() => {
        window.__tracklab.frameTrack(1.55);
        document.querySelector('[data-check="runoff"]')?.scrollIntoView({ block: 'nearest' });
    });
    await revealReasons();
    await page.waitForTimeout(400);
    const summary = await page.evaluate(() => window.__tracklab.runoffSummary());
    if (!summary || summary.zoneCount !== summary.cornerCount) {
        throw new Error(`${name} drew ${summary && summary.zoneCount} zones for ${summary && summary.cornerCount} corners`);
    }
    if (summary.reasonRows !== summary.cornerCount) {
        throw new Error(`${name} listed ${summary.reasonRows} reasons for ${summary.cornerCount} corners`);
    }
    if (!summary.line.includes(summary.summary)) {
        throw new Error(`${name} metrics line ${summary.line} does not include ${summary.summary}`);
    }
    await page.screenshot({ path: join(root, 'docs', name) });
    console.log(name, summary.summary, 'zones', summary.zoneCount, 'line', summary.line.trim());
    return summary;
}

async function shootCorner(name, index) {
    const ok = await page.evaluate((i) => window.__tracklab.focusRunoff(i), index);
    if (!ok) throw new Error(`${name} could not focus corner ${index}`);
    await revealReasons();
    await page.evaluate((i) => {
        document.querySelector(`#runoff-reasons tbody tr[data-index="${i}"]`)
            ?.scrollIntoView({ block: 'center' });
    }, index);
    await page.waitForTimeout(400);
    const summary = await page.evaluate(() => window.__tracklab.runoffSummary());
    if (summary.highlight !== index) {
        throw new Error(`${name} highlight is ${summary.highlight}, expected ${index}`);
    }
    await page.screenshot({ path: join(root, 'docs', name) });
    const corner = summary.corners[index];
    console.log(
        name,
        `corner ${index + 1}`,
        `${corner.speedKmh.toFixed(0)} km/h`,
        `R ${corner.radiusM.toFixed(0)}`,
        `need ${corner.depthM.toFixed(0)} have ${corner.availableM.toFixed(0)}`,
        corner.reasons.join(', ') || 'pass',
    );
}

await openLayout('layout_a_straight_hairpin');
const layoutA = await shoot('runoff_layout_a.png');

await page.click('#show-runoff');
await page.waitForTimeout(200);
const hidden = await page.evaluate(() => window.__tracklab.runoffSummary().zoneCount);
if (hidden !== 0) throw new Error('run-off toggle left ' + hidden + ' zones visible');
await page.click('#show-runoff');
await page.waitForTimeout(200);
const shown = await page.evaluate(() => window.__tracklab.runoffSummary().zoneCount);
if (shown !== layoutA.cornerCount) throw new Error('run-off toggle did not restore the zones');
console.log('toggle hides and restores the zones');

await page.selectOption('#layout-select', 'version-1');
await page.waitForFunction(
    () => window.__tracklab.getStats().layoutId === 'version-1'
        && window.__tracklab.runoffSummary()?.cornerCount > 0,
    null,
    { timeout: 30000 },
);
await shoot('runoff_version1.png');

const fixture = JSON.parse(readFileSync(join(root, 'scripts/fixtures/version-2.json'), 'utf8'));
await page.evaluate((layout) => window.__tracklab.previewLayout(layout), fixture);
await page.waitForFunction(
    () => window.__tracklab.getStats().layoutId === 'version-2'
        && window.__tracklab.runoffSummary()?.cornerCount > 0,
    null,
    { timeout: 30000 },
);
const version2 = await shoot('runoff_version2.png');
let fast = version2.corners[0];
let hairpin = version2.corners[0];
for (const corner of version2.corners) {
    if (corner.speedKmh > fast.speedKmh) fast = corner;
    if (corner.radiusM < hairpin.radiusM) hairpin = corner;
}
if (fast.index === hairpin.index) {
    hairpin = version2.corners.filter((c) => c.index !== fast.index)
        .sort((a, b) => a.radiusM - b.radiusM)[0];
}
await shootCorner('runoff_version2_fast.png', fast.index);
await shootCorner('runoff_version2_hairpin.png', hairpin.index);

if (errors.length) {
    console.error(errors.slice(0, 8).join('\n'));
    throw new Error('page errors during run-off screenshots');
}
await browser.close();
console.log('run-off screenshots written');
