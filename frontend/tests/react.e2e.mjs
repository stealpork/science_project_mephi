import { before, after, test } from 'node:test';
import assert from 'node:assert/strict';
import { harness, go, fill, click, login } from '../tools/browser-harness.mjs';
import { ids } from './fixtures/api.mjs';
let app;
before(async () => { app = await harness(); });
after(async () => { await app.close(); assert.deepEqual(app.errors, [], 'Browser runtime errors'); });

for (const width of [1440, 390, 320]) {
  test(`public navigation, search, keyboard and layout at ${width}px`, async () => {
    const { page, context } = await app.page(width);
    try {
      for (const route of ['/', '/projects', '/researchers', '/papers', `/projects/${ids.project}`, `/papers/${ids.paper}`, `/researchers/${ids.owner}`, '/login', '/register', '/forgot-password', '/missing']) {
        await go(page, app.base, route); assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, route);
        assert.equal(await page.title(), 'Stepork Science');
        assert.equal(await page.$$eval('main h1', nodes => nodes.length), 1, route);
      }
      const favicon = await page.evaluate(async () => { const icon = document.querySelector('link[rel="icon"]'); const response = await fetch(icon.href); return {type:response.headers.get('content-type'), body:await response.text(), status:response.status}; });
      assert.equal(favicon.status, 200); assert.match(favicon.type, /image\/svg\+xml/); assert.match(favicon.body, /<svg/);
      await go(page, app.base, '/'); await page.locator('#search-projects').click(); await page.keyboard.press('End'); assert.equal(await page.$eval('#search-papers', node => node.getAttribute('aria-selected')), 'true');
      await page.keyboard.press('Home'); await page.locator('[name="q"]').fill('Анализ'); await click(page, 'Найти'); await page.waitForFunction(() => location.pathname === '/projects'); await page.waitForSelector('.project-card');
      await page.locator('[name="q"]').fill('No-match-987654'); await click(page, 'Найти'); await page.waitForFunction(() => document.querySelector('main')?.textContent.includes('Проекты не найдены.'));
      await go(page, app.base, '/projects/new'); await page.waitForFunction(() => location.pathname === '/login');
    } finally { await context.close(); }
  });
  test(`profile, favorites, refresh, notifications, settings at ${width}px`, async () => {
    const { page, context } = await app.page(width);
    try {
      await login(page, app.base); await go(page, app.base, '/me/profile/edit'); await fill(page, 'О себе', `Обновлённый профиль ${width}`); await click(page, 'Сохранить'); await page.waitForFunction(() => location.pathname === '/me/profile');
      await page.waitForFunction(value => document.querySelector('main')?.textContent.includes(value), {}, `Обновлённый профиль ${width}`);
      await go(page, app.base, `/projects/${ids.project}`); const save = await page.$('main button[aria-label="Сохранить в избранное"]'); if (save) await save.click();
      await go(page, app.base, '/favorites'); await page.waitForSelector('.project-card'); await page.locator('main button[aria-label="Удалить из избранного"]').click(); await page.waitForFunction(() => document.querySelector('main')?.textContent.includes('Пока ничего нет.'));
      await page.reload({ waitUntil: 'networkidle0' }); await page.waitForSelector('.workspace-link, main h1'); assert.equal(new URL(page.url()).pathname, '/favorites');
      await go(page, app.base, '/notifications'); assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      await go(page, app.base, '/settings'); assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      await go(page, app.base, '/admin'); await page.waitForFunction(() => document.querySelector('main')?.textContent.includes('Нет доступа'));
    } finally { await context.close(); }
  });
}
test('owner creates and publishes a project; student applies; owner accepts; team updates', async () => {
  const owner = await app.page(); const student = await app.page(390);
  try {
    await login(owner.page, app.base, 'owner'); await go(owner.page, app.base, '/projects/new');
    await fill(owner.page, 'Название', 'Новый проект из React'); await fill(owner.page, 'Описание', 'Изучаем совместную работу и воспроизводимость данных.'); await fill(owner.page, 'Цель исследования', 'Проверить главный сценарий взаимодействия.'); await fill(owner.page, 'Направление', ids.area);
    await click(owner.page, 'Сохранить черновик'); await owner.page.waitForFunction(() => /\/projects\/[0-9a-f-]{36}$/.test(location.pathname)); const route = new URL(owner.page.url()).pathname;
    await click(owner.page, 'Управление проектом'); await click(owner.page, 'Опубликовать проект'); await owner.page.waitForFunction(() => document.querySelector('main')?.textContent.includes('Начать исследование'));
    await login(student.page, app.base); await go(student.page, app.base, route); await click(student.page, 'Подать заявку'); await fill(student.page, 'Опыт и польза для проекта', 'Есть опыт Python и анализа данных, помогу с обработкой.'); await click(student.page, 'Отправить заявку'); await student.page.waitForSelector('dialog', { hidden: true }); await student.page.waitForFunction(() => document.querySelector('main')?.textContent.includes('Ваша заявка на рассмотрении.'));
    await owner.page.reload({ waitUntil: 'networkidle0' }); await owner.page.waitForFunction(() => document.querySelector('main')?.textContent.includes('Есть опыт Python')); await click(owner.page, 'Принять заявку'); await owner.page.waitForFunction(() => !document.querySelector('main')?.textContent.includes('Принять заявку'));
    await go(student.page, app.base, route); await student.page.waitForFunction(() => document.querySelector('main')?.textContent.includes('Вы участник команды.'));
    assert.ok(app.fixture.calls.filter(c => c.method === 'POST' && c.path.startsWith('/projects/')).every(c => c.idempotency));
  } finally { await owner.context.close(); await student.context.close(); }
});
test('invitation exposes a narrow preview; accepting unlocks a private project', async () => {
  const { page, context } = await app.page();
  try { await login(page, app.base); await go(page, app.base, `/projects/${ids.private}`); await page.waitForSelector('[role="alert"]'); await go(page, app.base, '/me/invitations'); await click(page, 'Принять приглашение'); await page.waitForFunction(() => document.querySelector('main')?.textContent.includes('Принято')); await click(page, 'Открыть проект'); await page.waitForFunction(() => document.querySelector('main')?.textContent.includes('Вы участник команды.')); } finally { await context.close(); }
});
test('paper draft, client file validation, upload, publication and explicit external author', async () => {
  const { page, context } = await app.page();
  try { await login(page, app.base, 'owner'); await go(page, app.base, '/papers/new'); await fill(page, 'Название', 'Результаты проверки React'); await fill(page, 'Аннотация', 'Проверка публикации и совместного авторства через браузер.'); await fill(page, 'Направление', ids.area); await click(page, 'Сохранить и добавить PDF'); await page.waitForFunction(() => /\/papers\/[\w-]+\/edit$/.test(location.pathname));
    await page.waitForSelector('input[type="file"]'); await page.locator('::-p-text(Добавить соавтора)').filter(element => element.tagName === 'SUMMARY').click(); await fill(page, 'ФИО соавтора', 'Внешний Исследователь'); await click(page, 'Добавить соавтора'); await page.waitForFunction(() => document.querySelector('main')?.textContent.includes('Внешний соавтор · Нет доступа'));
    await page.evaluate(() => { const file = new File(['not PDF'], 'data.txt', { type: 'text/plain' }); const transfer = new DataTransfer(); transfer.items.add(file); const input = document.querySelector('input[type="file"]'); input.files = transfer.files; input.dispatchEvent(new Event('change', { bubbles: true })); }); await page.waitForFunction(() => document.querySelector('[role="alert"]')?.textContent.includes('PDF'));
    await page.evaluate(() => { const file = new File(['%PDF-1.4\nBrowser fixture\n%%EOF'], 'research.pdf', { type: 'application/pdf' }); const transfer = new DataTransfer(); transfer.items.add(file); const input = document.querySelector('input[type="file"]'); input.files = transfer.files; input.dispatchEvent(new Event('change', { bubbles: true })); }); await page.waitForFunction(() => document.querySelector('main')?.textContent.toLowerCase().includes('текущий pdf'));
    await click(page, 'Опубликовать работу'); await page.waitForFunction(() => document.querySelector('main')?.textContent.includes('Статус: Опубликована'));
    await click(page, 'Открыть страницу работы'); await page.waitForFunction(() => !location.pathname.endsWith('/edit')); await click(page, 'Подготовить: открыть pdf'); await page.waitForSelector('a[href^="http"][target="_blank"]');
  } finally { await context.close(); }
});
test('registration, login failure, sign out and protected routes', async () => {
  const { page, context } = await app.page();
  try { await go(page, app.base, '/login'); await fill(page, 'Email', 'missing@example.test'); await fill(page, 'Пароль', 'incorrect'); await page.locator('form .button-primary').click(); await page.waitForSelector('[role="alert"]');
    await go(page, app.base, '/register'); await fill(page, 'Имя', 'Новый'); await fill(page, 'Фамилия', 'Участник'); await fill(page, 'Email', `new-${Date.now()}@example.test`); await fill(page, 'Пароль', 'TestPassword123!'); await click(page, 'Зарегистрироваться'); await page.waitForFunction(() => location.pathname === '/me/profile/edit'); await page.waitForSelector('form');
    await go(page, app.base, '/settings'); await click(page, 'Выйти из аккаунта'); await page.waitForFunction(() => location.pathname === '/login'); assert.equal(await page.evaluate(() => sessionStorage.getItem('science.refresh.v1')), null);
  } finally { await context.close(); }
});
test('admin pages and real TOTP setup contract are rendered without overflow', async () => {
  const { page, context } = await app.page(320);
  try { await login(page, app.base, 'admin'); for (const tab of ['users', 'projects', 'papers', 'reports', 'dictionary-suggestions', 'dictionaries', 'settings', 'audit']) { await go(page, app.base, `/admin?tab=${tab}`); assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, tab); }
    await go(page, app.base, '/settings'); await fill(page, 'Пароль для настройки 2FA', 'TestPassword123!'); await click(page, 'Настроить 2FA'); await page.waitForFunction(() => document.querySelector('.secret')?.textContent === 'JBSWY3DPEHPK3PXP');
  } finally { await context.close(); }
});
