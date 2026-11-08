import http from 'node:http';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import puppeteer from 'puppeteer-core';
import { fixtureAPI } from '../tests/fixtures/api.mjs';

const root = path.resolve(fileURLToPath(new URL('../dist/', import.meta.url)));
const types = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css', '.png': 'image/png', '.svg': 'image/svg+xml' };
export async function harness({ liveTarget } = {}) {
  const fixture = liveTarget ? undefined : fixtureAPI(); const errors = []; const requests = [];
  const server = http.createServer(async (req, res) => {
    try {
      const url = new URL(req.url, 'http://localhost');
      if (url.pathname.startsWith('/api/v1/') || url.pathname.startsWith('/test-s3/')) {
        if (liveTarget) {
          const chunks = []; for await (const chunk of req) chunks.push(chunk);
          const response = await fetch(new URL(req.url, liveTarget), { method: req.method, headers: { ...req.headers, host: new URL(liveTarget).host }, body: ['GET', 'HEAD'].includes(req.method) ? undefined : Buffer.concat(chunks), redirect: 'manual' });
          res.writeHead(response.status, { 'Content-Type': response.headers.get('content-type') ?? 'application/json' }); res.end(Buffer.from(await response.arrayBuffer())); return;
        }
        const chunks = []; for await (const chunk of req) chunks.push(chunk);
        const input = Buffer.concat(chunks).toString();
        const result = await fixture.handle(req, url, req.headers['content-type']?.includes('application/json') && input ? JSON.parse(input) : {});
        requests.push({ method: req.method, path: url.pathname, status: result.status });
        res.writeHead(result.status, { 'Content-Type': result.type ?? 'application/json' }); res.end(result.raw ?? (result.body === undefined ? '' : JSON.stringify(result.body))); return;
      }
      const filename = path.resolve(root, `.${decodeURIComponent(url.pathname)}`);
      if (!filename.startsWith(`${root}${path.sep}`) && filename !== root) { res.writeHead(403); res.end(); return; }
      const asset = path.extname(filename) ? filename : path.join(root, 'index.html');
      const content = await readFile(asset);
      res.writeHead(200, { 'Content-Type': types[path.extname(asset)] ?? 'application/octet-stream' }); res.end(content);
    } catch (error) { if (!res.headersSent) res.writeHead(error.code === 'ENOENT' ? 404 : 500); res.end('Test server error'); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try { browser = await puppeteer.launch({ executablePath: process.env.CHROMIUM_PATH ?? '/usr/sbin/chromium', headless: true, args: ['--disable-dev-shm-usage', '--disable-gpu', '--renderer-process-limit=2'] }); }
  catch (error) { await new Promise(resolve => server.close(resolve)); throw error; }
  const base = `http://127.0.0.1:${server.address().port}`;
  return { base, browser, fixture, errors, requests,
    async page(width = 1440) { const context = await browser.createBrowserContext(); const page = await context.newPage(); await page.setViewport({ width, height: 900, deviceScaleFactor: 1 }); page.setDefaultTimeout(12_000); page.on('pageerror', error => errors.push(error.message)); page.on('console', message => { if (message.type() === 'error' && !message.text().startsWith('Failed to load resource')) errors.push(message.text()); }); return { page, context }; },
    async close() { await browser.close(); server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); },
  };
}
export async function go(page, base, route) { await page.goto(`${base}${route}`, { waitUntil: 'networkidle0' }); try { await page.waitForSelector('main h1, main [role="alert"]'); } catch (error) { throw new Error(`Could not open ${route}: ${await page.title()}`, { cause: error }); } }
export async function fill(page, label, value) {
  const input = await page.evaluateHandle(label => { const field = [...document.querySelectorAll('label.field')].find(node => node.querySelector('span')?.textContent === label); return field?.querySelector('input, textarea, select'); }, label);
  const element = input.asElement(); if (!element) throw new Error(`Field not found: ${label}`);
  if (await element.evaluate(node => node.tagName === 'SELECT')) await element.select(...(Array.isArray(value) ? value : [value]));
  else { await element.click({ clickCount: 3 }); await page.keyboard.press('Backspace'); await element.type(String(value)); }
  await input.dispose();
}
export async function click(page, text) {
  const handle = await page.waitForFunction(text => ['button', 'a', 'summary'].flatMap(tag => [...document.querySelectorAll(tag)]).find(element => element.textContent.trim() === text && element.getClientRects().length && !element.disabled), {}, text);
  try { await handle.asElement().click(); } finally { await handle.dispose(); }
}
export async function login(page, base, user = 'student') { await go(page, base, '/login'); await fill(page, 'Email', `${user}@example.test`); await fill(page, 'Пароль', 'TestPassword123!'); await page.locator('form button[type="submit"], form .button-primary').click(); await page.waitForFunction(() => location.pathname === '/workspace'); }
