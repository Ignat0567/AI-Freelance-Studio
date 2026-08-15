import { chromium } from 'playwright';
const url = process.env.SHOT_URL;
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 900 }, deviceScaleFactor: 2 });
await page.goto(url, { waitUntil: 'load', timeout: 20000 });
await page.waitForTimeout(1500);
await page.screenshot({ path: '/out/focus-timer-deployed.png', fullPage: false });
await browser.close();
console.log('screenshot written');
