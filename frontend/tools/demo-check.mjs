import assert from 'node:assert/strict';
import puppeteer from 'puppeteer-core';
import { go, fill, click } from './browser-harness.mjs';

const base = 'http://localhost:18080';
const password = 'Stepork-Demo-2026!';
const errors = [];
const browser = await puppeteer.launch({ executablePath: process.env.CHROMIUM_PATH ?? '/usr/sbin/chromium', headless: true, args: ['--disable-dev-shm-usage', '--disable-gpu', '--renderer-process-limit=2'] });
async function api(path, token, options = {}) {
  const response = await fetch(`${base}/api/v1${path}`, { ...options, headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...options.headers } });
  assert.equal(response.status, 200, `Demo API: ${path}`);
  return response.json();
}
async function page(width = 1440) {
  const context = await browser.createBrowserContext();
  const result = await context.newPage();
  await result.setViewport({ width, height: 1000 }); result.setDefaultTimeout(20_000);
  result.on('pageerror', error => errors.push(error.message));
  return result;
}
async function login(target, email, secret) {
  await go(target, base, '/login'); await fill(target, 'Email', email); await fill(target, 'Пароль', secret); await click(target, 'Войти');
  await target.waitForFunction(() => location.pathname === '/workspace');
}
try {
  const universities = await api('/dictionaries/universities?limit=100');
  assert.ok(universities.items.length >= 3);
  assert.ok(universities.items.every(item => !/^[0-9a-f]{32}$/i.test(item.name)));
  const profiles = await api('/users?limit=100');
  const anna = profiles.items.find(item => item.first_name === 'Анна' && item.last_name === 'Соколова');
  const mikhail = profiles.items.find(item => item.first_name === 'Михаил' && item.last_name === 'Орлов');
  assert.ok(anna?.avatar_file_id && mikhail?.avatar_file_id);
  const projects = await api('/projects?limit=100');
  assert.equal(projects.items.length, 2);
  const papers = await api('/papers?limit=100');
  assert.equal(papers.items.length, 2);
  const guest = await page();
  await go(guest, base, '/researchers');
  await guest.waitForFunction(() => document.querySelector('main')?.textContent.includes('Соколова'));
  for (const person of [anna, mikhail]) {
    await go(guest, base, `/researchers/${person.id}`);
    await guest.waitForFunction(() => [...document.querySelectorAll('main img')].some(image => image.complete && image.naturalWidth > 0));
  }
  await go(guest, base, `/papers/${papers.items[0].id}`);
  await click(guest, 'Подготовить: открыть pdf');
  await guest.waitForSelector('main a[target="_blank"]');
  const url = await guest.$eval('.file-link a', element => element.href);
  const pdf = await fetch(url); assert.equal(pdf.status, 200);
  assert.equal(Buffer.from(await pdf.arrayBuffer()).subarray(0, 5).toString(), '%PDF-');
  const researcher = await page(390);
  await login(researcher, 'mikhail@demo.example.org', password);
  await go(researcher, base, '/favorites?type=project');
  await researcher.waitForFunction(() => document.querySelector('main')?.textContent.includes('Смысловой поиск'));
  await go(researcher, base, '/me/teams');
  await researcher.waitForFunction(() => document.querySelector('main')?.textContent.includes('Открытая наука'));
  assert.equal(await researcher.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  const owner = await page();
  await login(owner, 'anna@demo.example.org', password);
  await go(owner, base, '/me/invitations');
  await owner.waitForFunction(() => document.querySelector('main')?.textContent.includes('Качество воздуха'));
  const admin = await page();
  await login(admin, 'admin@demo.example.org', 'Stepork-Admin-2026!');
  await go(admin, base, '/admin?tab=dictionaries');
  await fill(admin, 'Справочник', 'universities');
  await admin.waitForFunction(() => [...document.querySelectorAll('input')].some(input => input.value === 'Университет ИТМО'));
  await go(admin, base, '/admin?tab=settings');
  await admin.waitForFunction(() => document.querySelector('input[type="number"]')?.value === '5');
  const loginResult = await api('/auth/login', null, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email: 'anna@demo.example.org', password }) });
  const ownProjects = await api('/me/projects?limit=100', loginResult.access_token);
  assert.equal(ownProjects.items.length, 2);
  const privateProject = ownProjects.items.find(project => project.access_type === 'private_invite');
  assert.ok(privateProject);
  assert.equal((await fetch(`${base}/api/v1/projects/${privateProject.id}`)).status, 404);
  for (const project of ownProjects.items) {
    const files = await api(`/projects/${project.id}/files`, loginResult.access_token);
    assert.equal(files.items.length, 1);
    const link = await api(`/projects/${project.id}/files/${files.items[0].file_id}/file-url?mode=inline`, loginResult.access_token);
    const result = await fetch(link.url); assert.equal(result.status, 200);
    assert.equal(Buffer.from(await result.arrayBuffer()).subarray(0, 5).toString(), '%PDF-');
  }
  for (const paper of papers.items) {
    const link = await api(`/papers/${paper.id}/file-url?mode=inline`);
    const result = await fetch(link.url); assert.equal(result.status, 200);
    assert.equal(Buffer.from(await result.arrayBuffer()).subarray(0, 5).toString(), '%PDF-');
  }
  assert.deepEqual(errors, []);
  console.log('Demo browser checks passed: 3 logins, avatars, clean dictionaries, projects, team, pending invitation, favorites, private access, settings and real PDF downloads.');
} finally {
  await browser.close();
}
