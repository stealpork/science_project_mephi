import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { randomBytes, randomUUID } from 'node:crypto';
import { mkdir, mkdtemp, writeFile, unlink, rmdir } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import puppeteer from 'puppeteer-core';
import { go, fill, click } from './browser-harness.mjs';

const root = fileURLToPath(new URL('../../', import.meta.url));
const base = process.env.TEST_BASE_URL ?? 'http://localhost:18081';
const guard = spawnSync('uv', ['run', '--directory', path.join(root, 'backend'), '--frozen', 'python', '-c',
  'import sys; from tests.conftest import validate_live_target, verify_live_runtime; verify_live_runtime(validate_live_target(base_url=sys.argv[1]))', base], { cwd: path.join(root, 'backend'), encoding: 'utf8' });
if (guard.status !== 0) throw new Error('Live browser tests require the isolated science-test stack and its declared loopback port.');
const output = path.join(root, 'test-results', `frontend-live-${new Date().toISOString().replaceAll(':', '-')}`);
await mkdir(output, { recursive: true });
const temporary = await mkdtemp(path.join(tmpdir(), 'science-web-pdf-'));
const pdf = path.join(temporary, 'research.pdf');
const fixture = spawnSync('uv', ['run', '--directory', path.join(root, 'backend'), '--frozen', 'python', '-c', 'import sys; from tests.files import pdf_bytes; sys.stdout.buffer.write(pdf_bytes())'], { cwd: path.join(root, 'backend') });
if (fixture.status !== 0) throw new Error('Unable to generate the PDF test fixture.');
await writeFile(pdf, fixture.stdout);
const browser = await puppeteer.launch({ executablePath: process.env.CHROMIUM_PATH ?? '/usr/sbin/chromium', headless: true, args: ['--disable-dev-shm-usage', '--disable-gpu', '--renderer-process-limit=2'] });
const errors = []; const steps = []; const contexts = [];
const password = `Test-${randomBytes(20).toString('base64url')}!`;
const suffix = `${Date.now()}-${randomUUID().slice(0, 8)}`;
async function register(name, width) {
  const context = await browser.createBrowserContext(); contexts.push(context); const page = await context.newPage(); await page.setViewport({ width, height: 900 }); page.setDefaultTimeout(20_000);
  page.on('pageerror', error => errors.push(error.message));
  await go(page, base, '/register'); await fill(page, 'Имя', name); await fill(page, 'Фамилия', 'Тестирование'); await fill(page, 'Email', `${name === 'Руководитель' ? 'owner' : 'student'}-${suffix}@example.org`); await fill(page, 'Пароль', password); await click(page, 'Зарегистрироваться');
  await page.waitForFunction(() => location.pathname === '/me/profile/edit');
  for (let attempt = 0; attempt < 10; attempt++) {
    if (await page.$('textarea')) break;
    const retry = await page.$('main button'); if (retry) await retry.click();
    await page.waitForNetworkIdle({ idleTime: 500 });
  }
  await page.waitForSelector('textarea'); await fill(page, 'О себе', 'Проверка настоящего React с FastAPI, очередью событий и S3.'); await click(page, 'Сохранить'); await page.waitForFunction(() => location.pathname === '/me/profile');
  await page.waitForNetworkIdle({ idleTime: 500 }); steps.push(`registered and saved profile: ${name}`); return page;
}
try {
  const owner = await register('Руководитель', 1440); const student = await register('Исследователь', 390);
  const areas = await fetch(`${base}/api/v1/dictionaries/research_areas`).then(response => response.json()); assert.ok(areas.items.length);
  await go(owner, base, '/projects/new'); await fill(owner, 'Название', `Browser research ${suffix}`); await fill(owner, 'Описание', 'Проверяем совместную работу на настоящих сервисах.'); await fill(owner, 'Цель исследования', 'Убедиться, что регистрация, команда и публикация работают вместе.'); await fill(owner, 'Направление', areas.items[0].id); await click(owner, 'Сохранить черновик'); await owner.waitForFunction(() => /^\/projects\/[0-9a-f-]{36}$/.test(location.pathname));
  const projectPath = new URL(owner.url()).pathname;
  await click(owner, 'Управление проектом'); await click(owner, 'Опубликовать проект'); await owner.waitForFunction(() => document.querySelector('main')?.textContent.includes('Начать исследование')); steps.push('created and published project through UI');
  await go(student, base, projectPath); await click(student, 'Подать заявку'); await fill(student, 'Опыт и польза для проекта', 'Умею анализировать данные и писать тесты. Помогу проверить гипотезу.'); await click(student, 'Отправить заявку'); await student.waitForSelector('dialog', { hidden: true }); await student.waitForFunction(() => document.querySelector('main')?.textContent.includes('Ваша заявка на рассмотрении.')); steps.push('student submitted application through UI');
  await owner.reload({ waitUntil: 'networkidle0' }); await owner.waitForFunction(() => document.querySelector('main')?.textContent.includes('Умею анализировать')); await click(owner, 'Принять заявку'); await owner.waitForFunction(() => !document.querySelector('main')?.textContent.includes('Принять заявку')); steps.push('owner accepted application');
  await go(student, base, projectPath); await student.waitForFunction(() => document.querySelector('main')?.textContent.includes('Вы участник команды.')); steps.push('membership and team visible to student');
  await go(owner, base, '/papers/new'); await fill(owner, 'Название', `Browser publication ${suffix}`); await fill(owner, 'Аннотация', 'Проверяется загрузка PDF в MinIO, антивирусная проверка и публикация результата исследования.'); await fill(owner, 'Направление', areas.items[0].id); await click(owner, 'Сохранить и добавить PDF'); await owner.waitForFunction(() => /^\/papers\/[\w-]+\/edit$/.test(location.pathname));
  const input = await owner.waitForSelector('input[type="file"]'); await input.uploadFile(pdf); await owner.waitForFunction(() => document.querySelector('main')?.textContent.toLowerCase().includes('текущий pdf'), { timeout: 180_000 }); steps.push('PDF uploaded to real MinIO and passed real security scan');
  await click(owner, 'Опубликовать работу'); await owner.waitForFunction(() => document.querySelector('main')?.textContent.includes('Статус: Опубликована')); await click(owner, 'Открыть страницу работы'); await owner.waitForFunction(() => !location.pathname.endsWith('/edit')); const paperPath = new URL(owner.url()).pathname; steps.push('published paper through UI');
  const guestContext = await browser.createBrowserContext(); contexts.push(guestContext); const guest = await guestContext.newPage(); await guest.setViewport({ width: 320, height: 900 }); await go(guest, base, paperPath); await click(guest, 'Подготовить: открыть pdf'); await guest.waitForSelector('main a[target="_blank"]');
  const download = await guest.$eval('main a[target="_blank"]', element => element.href); const result = await fetch(download); assert.equal(result.status, 200); assert.ok(Buffer.from(await result.arrayBuffer()).subarray(0, 5).equals(Buffer.from('%PDF-'))); steps.push('guest can open and download actual published PDF');
  assert.equal(await guest.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
  await go(student, base, paperPath); await student.locator('main button[aria-label="Сохранить в избранное"]').click(); await go(student, base, '/favorites?type=paper'); await student.waitForFunction(() => document.querySelector('main')?.textContent.includes('Browser publication')); await student.reload({ waitUntil: 'networkidle0' }); await student.waitForFunction(() => document.querySelector('main')?.textContent.includes('Browser publication')); steps.push('favorites persist across a real JWT refresh');
  assert.deepEqual(errors, []);
  await writeFile(path.join(output, 'result.json'), JSON.stringify({ status: 'passed', base, steps, errors }, null, 2)); console.log(JSON.stringify({ status: 'passed', output, steps }));
} catch (error) {
  await writeFile(path.join(output, 'result.json'), JSON.stringify({ status: 'failed', steps, errors, failure: error.name }, null, 2));
  throw error;
} finally {
  try { await browser.close(); await unlink(pdf); await rmdir(temporary); }
  finally {
    const cleanup = spawnSync('uv', ['run', '--directory', path.join(root, 'backend'), '--frozen', 'python', '-m', 'tests.cleanup'], { cwd: path.join(root, 'backend'), encoding: 'utf8', timeout: 360_000 });
    if (cleanup.status !== 0) throw new Error('Browser test data cleanup failed; run: cd backend && uv run python -m tests.cleanup');
    console.log(cleanup.stdout.trim());
  }
}
