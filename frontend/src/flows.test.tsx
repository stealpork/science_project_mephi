import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { webcrypto } from "node:crypto";
import { App } from "./App";
import { AuthProvider } from "./ui/auth";
import { api, command, session } from "./lib/api";
import { fixtureAPI, ids } from "../tests/fixtures/api.mjs";
import type { Application, Tokens } from "./types";

let fixture: ReturnType<typeof fixtureAPI>;
let client: QueryClient;
beforeEach(() => {
  session.clear();
  fixture = fixtureAPI();
  client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  vi.stubGlobal("crypto", webcrypto);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, options: RequestInit = {}) => {
      const result = await fixture.handle(
        {
          method: options.method ?? "GET",
          headers: Object.fromEntries(
            Object.entries(options.headers ?? {}).map(([key, value]) => [
              key.toLowerCase(),
              String(value),
            ]),
          ),
        },
        new URL(input, "http://localhost"),
        typeof options.body === "string" ? JSON.parse(options.body) : {},
      );
      return new Response(
        result.status === 204 ? undefined : JSON.stringify(result.body),
        {
          status: result.status,
          headers: { "Content-Type": "application/json" },
        },
      );
    }),
  );
});
afterEach(() => {
  client.clear();
  session.clear();
  vi.unstubAllGlobals();
});
async function asRole(role: string) {
  const pair = await api<Tokens>("/auth/login", {
    method: "POST",
    anonymous: true,
    body: { email: `${role}@example.test`, password: "TestPassword123!" },
  });
  session.accept(pair);
}
async function mount(path: string, role = "owner") {
  await asRole(role);
  const view = render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <AuthProvider>
          <App />
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await screen.findByRole("heading", { level: 1 });
  return { ...view, user: userEvent.setup() };
}
const button = (name: string) => screen.getByRole("button", { name });
const input = (name: string) => screen.getByLabelText(name, { exact: true });

it("creates a project with specialist roles and recommendation criteria", async () => {
  const { user } = await mount("/projects/new");
  await user.type(input("Название"), "Проверка проекта");
  await user.type(input("Описание"), "Описание исследования");
  await user.type(input("Цель исследования"), "Проверить воспроизводимость");
  await user.selectOptions(input("Направление"), ids.area);
  await user.selectOptions(input("Университет"), ids.university);
  await user.selectOptions(screen.getByLabelText(/^Требуемые навыки/), [
    ids.skill,
  ]);
  await user.selectOptions(input("Научные интересы"), [ids.interest]);
  await user.click(screen.getByLabelText("Разработчик"));
  await user.selectOptions(input("Доступ"), "private_invite");
  await user.click(screen.getByText("Дополнительные критерии рекомендаций"));
  await user.selectOptions(
    input("Предпочтительные специальности"),
    ids.specialty,
  );
  await user.type(screen.getByLabelText(/^Курсы через запятую/), "2, 3");
  await user.type(input("Минимум завершённых проектов"), "1");
  await user.click(button("Сохранить черновик"));
  expect(
    await screen.findByRole("heading", { level: 1, name: "Проверка проекта" }),
  ).toBeVisible();
  expect(screen.getByText("Черновик")).toBeVisible();
  const call = vi
    .mocked(fetch)
    .mock.calls.find(
      ([path, options]) =>
        path === "/api/v1/projects" && options?.method === "POST",
    );
  expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({
    access_type: "private_invite",
    required_roles: ["developer"],
    eligible_courses: [2, 3],
    min_completed_projects: 1,
    required_skill_ids: [ids.skill],
  });
});
it("edits project text and confirms visibility expansion explicitly", async () => {
  const { user } = await mount(`/projects/${ids.private}/edit`);
  await user.clear(input("Название"));
  await user.type(input("Название"), "Раскрытый проект");
  await user.selectOptions(input("Доступ"), "public_open");
  vi.mocked(window.confirm).mockReturnValueOnce(false);
  await user.click(button("Сохранить изменения"));
  expect(
    screen.getByRole("heading", { name: "Редактировать проект" }),
  ).toBeVisible();
  await user.click(button("Сохранить изменения"));
  expect(
    await screen.findByRole("heading", { level: 1, name: "Раскрытый проект" }),
  ).toBeVisible();
  expect(window.confirm).toHaveBeenCalledTimes(2);
});
it("manages project lifecycle through independent commands", async () => {
  const { user } = await mount(`/projects/${ids.project}/manage`);
  await user.click(button("Начать исследование"));
  await screen.findByRole("button", { name: "Завершить проект" });
  await user.click(button("Завершить проект"));
  await screen.findByRole("button", { name: "Открыть набор" });
  await user.click(button("Открыть набор"));
  expect(
    await screen.findByRole("button", { name: "Начать исследование" }),
  ).toBeVisible();
});
for (const decision of ["Принять заявку", "Отклонить заявку"])
  it(`owner decides application: ${decision}`, async () => {
    await asRole("student");
    await command(`/projects/${ids.project}/applications`, {
      requested_role: "analyst",
      message: "Пишу Python и анализирую экспериментальные данные.",
    });
    const { user } = await mount(`/projects/${ids.project}/manage`);
    await screen.findByText(
      "Пишу Python и анализирую экспериментальные данные.",
    );
    await user.selectOptions(input("Роль в команде"), "developer");
    await user.type(
      input("Комментарий к решению"),
      "Рассмотрено руководителем",
    );
    await user.click(button(decision));
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: decision }),
      ).not.toBeInTheDocument(),
    );
  });
it("owner sends and revokes an invitation", async () => {
  const { user } = await mount(`/projects/${ids.project}/manage`);
  await user.click(button("Приглашения"));
  await user.type(screen.getByLabelText(/^ID пользователя/), ids.student);
  await user.selectOptions(input("Роль"), "analyst");
  await user.type(input("Сообщение"), "Приглашаем исследовать данные");
  await user.click(button("Отправить приглашение"));
  expect(await screen.findByRole("status")).toHaveTextContent(
    "Приглашение отправлено",
  );
  await user.click(
    await screen.findByRole("button", { name: "Отозвать приглашение" }),
  );
  await waitFor(() =>
    expect(
      screen.queryByRole("button", { name: "Отозвать приглашение" }),
    ).not.toBeInTheDocument(),
  );
});
it("student can submit and withdraw an application", async () => {
  const { user } = await mount(`/projects/${ids.project}`, "student");
  await user.click(button("Подать заявку"));
  await user.selectOptions(input("Роль"), "analyst");
  await user.type(
    input("Опыт и польза для проекта"),
    "Знаком с анализом экспериментальных данных.",
  );
  await user.click(button("Отправить заявку"));
  await waitFor(() =>
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
  );
  await user.click(await screen.findByRole("link", { name: "Мои заявки" }));
  await user.click(
    await screen.findByRole("button", { name: "Отозвать заявку" }),
  );
  await screen.findByText(/Отозвано/);
});
it("student declines a private invitation without gaining project access", async () => {
  const { user } = await mount("/me/invitations", "student");
  await user.click(
    await screen.findByRole("button", { name: "Отклонить приглашение" }),
  );
  expect(await screen.findByText(/Отклонено/)).toBeVisible();
  expect(
    screen.queryByRole("link", { name: "Открыть проект" }),
  ).not.toBeInTheDocument();
});
it("owner changes a member role and transfers ownership", async () => {
  await asRole("student");
  const app = await command<Application>(
    `/projects/${ids.project}/applications`,
    {
      requested_role: "researcher",
      message: "Готов участвовать в исследовании.",
    },
  );
  await asRole("owner");
  await command(`/projects/${ids.project}/applications/${app.id}/accept`, {
    role: "researcher",
    reason: "",
  });
  const { user } = await mount(`/projects/${ids.project}/manage`);
  await user.click(button("Команда"));
  await screen.findByRole("button", { name: "Изменить роль" });
  await user.selectOptions(input("Новая роль"), "analyst");
  await user.click(button("Изменить роль"));
  await user.click(button("Передать владение"));
  expect(await screen.findByRole("alert")).toHaveTextContent(
    "Управление доступно руководителю",
  );
});
it("edits a portfolio with multi-selects, contact visibility, and self-reported experience", async () => {
  const { user } = await mount("/me/profile/edit", "student");
  await user.clear(input("Имя"));
  await user.type(input("Имя"), "Пётр");
  await user.clear(input("О себе"));
  await user.type(input("О себе"), "Исследую проверяемые системы.");
  await user.selectOptions(input("Университет"), ids.university);
  await user.selectOptions(input("Специальность"), ids.specialty);
  await user.clear(input("Курс"));
  await user.type(input("Курс"), "4");
  await user.selectOptions(input("Город"), ids.city);
  await user.selectOptions(screen.getByLabelText(/^Научные интересы/), [
    ids.interest,
  ]);
  await user.selectOptions(screen.getByLabelText(/^Навыки и технологии/), [
    ids.skill,
  ]);
  await user.click(button("Добавить контакт"));
  await user.selectOptions(input("Тип"), "telegram");
  await user.type(input("Значение"), "@researcher");
  await user.selectOptions(input("Кто видит"), "authenticated");
  await user.click(button("Добавить опыт"));
  await user.type(input("Название"), "Анализ данных");
  await user.type(input("Описание"), "Исследовательский практикум");
  await user.click(screen.getByLabelText("Завершён"));
  await user.click(button("Сохранить"));
  expect(
    await screen.findByRole("heading", { level: 1, name: "Пётр Петров" }),
  ).toBeVisible();
  expect(screen.getByText("telegram: @researcher")).toBeVisible();
});
it("removes unsaved contacts and experience without a server mutation", async () => {
  const { user } = await mount("/me/profile/edit");
  await user.click(button("Добавить контакт"));
  await user.click(button("Удалить контакт"));
  await user.click(button("Добавить опыт"));
  await user.click(button("Удалить опыт"));
  expect(screen.queryByLabelText("Значение")).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Название")).not.toBeInTheDocument();
});
it("reports a publication through an accessible dialog", async () => {
  const { user } = await mount(`/papers/${ids.paper}`, "student");
  await user.click(button("Пожаловаться"));
  expect(screen.getByRole("dialog", { name: "Жалоба" })).toBeVisible();
  await user.selectOptions(input("Причина"), "plagiarism");
  await user.type(
    input("Описание"),
    "Прошу проверить происхождение этого текста.",
  );
  await user.click(button("Отправить жалобу"));
  await waitFor(() =>
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
  );
});
it("invites a researcher from their portfolio", async () => {
  const { user } = await mount(`/researchers/${ids.student}`);
  await user.click(button("Пригласить в проект"));
  await user.selectOptions(input("Проект"), ids.project);
  await user.selectOptions(input("Роль"), "developer");
  await user.type(input("Сообщение"), "Будем рады совместной работе");
  await user.click(button("Пригласить"));
  await waitFor(() =>
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
  );
});
it("creates and edits paper metadata, then explicitly adds, links and unlinks a coauthor", async () => {
  const { user } = await mount(`/papers/${ids.paper}/edit`);
  await user.clear(input("Название"));
  await user.type(input("Название"), "Уточнённая публикация");
  await user.type(input("Ключевые слова через запятую"), ", тестирование");
  await user.click(screen.getByText("Связь с проектом и внешние ссылки"));
  await user.type(
    input("Ссылки, каждая с новой строки"),
    "https://example.org/research",
  );
  await user.click(button("Сохранить описание"));
  await waitFor(() =>
    expect(
      vi
        .mocked(fetch)
        .mock.calls.some(
          ([p, o]) =>
            String(p).endsWith(`/papers/${ids.paper}`) && o?.method === "PATCH",
        ),
    ).toBe(true),
  );
  await user.click(
    screen.getByText("Добавить соавтора", { selector: "summary" }),
  );
  await user.type(input("ФИО соавтора"), "Внешний Автор");
  await user.type(input("Организация"), "Лаборатория");
  await user.click(button("Добавить соавтора"));
  await screen.findByText(/Внешний Автор/);
  const articles = screen.getAllByRole("article");
  const external = articles.find((node) =>
    node.textContent?.includes("Внешний Автор"),
  )!;
  await user.click(
    within(external).getByRole("button", { name: "Изменить соавтора" }),
  );
  await user.type(
    within(external).getByLabelText("ID аккаунта для выдачи доступа"),
    ids.student,
  );
  await user.click(
    within(external).getByRole("button", { name: "Выдать доступ соавтору" }),
  );
  await within(external).findByRole("button", { name: "Отозвать доступ" });
  await user.click(
    within(external).getByRole("button", { name: "Отозвать доступ" }),
  );
  await within(external).findByRole("button", {
    name: "Выдать доступ соавтору",
  });
  await user.click(
    within(external).getByRole("button", { name: "Удалить соавтора" }),
  );
  await waitFor(() =>
    expect(screen.queryByText(/Внешний Автор/)).not.toBeInTheDocument(),
  );
});
it("creates a paper, validates an invalid file, uploads a ready PDF and publishes", async () => {
  const { user } = await mount("/papers/new");
  await user.type(input("Название"), "Новая работа");
  await user.type(
    input("Аннотация"),
    "Сравнение методов верификации результатов.",
  );
  await user.selectOptions(input("Направление"), ids.area);
  await user.click(button("Сохранить и добавить PDF"));
  const fileInput = await screen.findByLabelText(/^PDF-файл/);
  const file = new File(["%PDF-1.4\ntest"], "research.pdf", {
    type: "application/pdf",
  });
  Object.defineProperty(file, "arrayBuffer", {
    value: async () => new TextEncoder().encode("%PDF-1.4\ntest").buffer,
  });
  await user.upload(fileInput, file);
  await screen.findByText("Файл готов.");
  await user.click(button("Опубликовать работу"));
  expect(await screen.findByText("Статус: Опубликована")).toBeVisible();
});
it("requests a fresh paper download link and never navigates to arbitrary schemes", async () => {
  const { user } = await mount(`/papers/${ids.paper}`);
  await user.click(button("Подготовить: открыть pdf"));
  const link = await screen.findByRole("link", { name: "Открыть PDF" });
  expect(link).toHaveAttribute("rel", "noopener noreferrer");
  expect(link.getAttribute("href")).toMatch(/^http/);
});
it("changes the password and completes the actual TOTP setup contract", async () => {
  const { user } = await mount("/settings");
  await user.type(input("Текущий пароль"), "TestPassword123!");
  await user.type(input("Новый пароль"), "ChangedPassword123!");
  await user.click(button("Изменить пароль"));
  expect(await screen.findByRole("status")).toHaveTextContent("Пароль изменён");
  await user.type(input("Пароль для настройки 2FA"), "TestPassword123!");
  await user.click(button("Настроить 2FA"));
  expect(await screen.findByText("JBSWY3DPEHPK3PXP")).toBeVisible();
  await user.type(input("Код подтверждения 2FA"), "123456");
  await user.click(button("Включить 2FA"));
  await screen.findByText("fixture-backup-code");
  await user.click(button("Я сохранил коды"));
  expect(screen.queryByText("fixture-backup-code")).not.toBeInTheDocument();
  await user.click(screen.getByText("Отключить 2FA", { selector: "summary" }));
  await user.type(input("Пароль для отключения 2FA"), "TestPassword123!");
  await user.type(input("Код 2FA"), "123456");
  await user.click(button("Отключить 2FA"));
  expect(await screen.findByRole("status")).toHaveTextContent("2FA отключена");
});
it("submits a dictionary suggestion without treating it as immediately approved", async () => {
  const { user } = await mount("/dictionaries/suggest", "student");
  await user.selectOptions(input("Справочник"), "research_interests");
  await user.type(input("Название"), "Новый научный интерес");
  await user.type(input("Комментарий"), "Обоснование для модератора");
  await user.click(button("Предложить"));
  expect(await screen.findByRole("status")).toHaveTextContent(
    "Предложение отправлено на рассмотрение",
  );
});
it("changes catalog filters and resets them", async () => {
  const { user } = await mount("/projects", "student");
  await user.click(screen.getByText("Фильтры", { selector: "summary" }));
  await user.selectOptions(input("Направление"), ids.area);
  await user.selectOptions(input("Университет"), ids.university);
  await user.selectOptions(input("Навык"), ids.skill);
  await user.selectOptions(input("Статус"), "active");
  await user.type(input("Команда от"), "2");
  await user.type(input("Команда до"), "6");
  await user.click(screen.getByLabelText("Есть свободные места"));
  await user.selectOptions(input("Сортировка"), "deadline");
  await user.click(button("Сбросить фильтры"));
  expect(input("Статус")).toHaveValue("");
  expect(screen.getByLabelText("Есть свободные места")).not.toBeChecked();
});
it("marks notifications read and filters unread items", async () => {
  const { user } = await mount("/notifications", "student");
  await user.click(screen.getByLabelText("Только непрочитанные"));
  await user.click(button("Прочитать"));
  await screen.findByText("Пока ничего нет.");
  await user.click(button("Прочитать все"));
  expect(
    vi
      .mocked(fetch)
      .mock.calls.some(([path]) => path === "/api/v1/notifications/read-all"),
  ).toBe(true);
});
it("admin submits moderation with an audit reason and waits for execution", async () => {
  const { user } = await mount("/admin?tab=projects", "admin");
  await user.click(
    (await screen.findAllByRole("button", { name: "Скрыть" }))[0],
  );
  await user.type(input("Обоснование"), "Нарушение правил публикации");
  await user.click(button("Подтвердить решение"));
  expect(await screen.findByText("Решение выполнено.")).toBeVisible();
  await user.click(button("Проверить статус"));
});
it("admin changes project file limits with version and reason", async () => {
  const { user } = await mount("/admin?tab=settings", "admin");
  await user.clear(await screen.findByLabelText("Максимум файлов на проект"));
  await user.type(input("Максимум файлов на проект"), "8");
  await user.type(input("Причина изменения"), "Расширяем хранилище команды");
  await user.click(button("Сохранить"));
  await waitFor(() =>
    expect(
      vi
        .mocked(fetch)
        .mock.calls.some(
          ([path, options]) =>
            path === "/api/v1/admin/settings/project-files" &&
            options?.method === "PATCH",
        ),
    ).toBe(true),
  );
});
it("admin creates and edits a dictionary entry", async () => {
  const { user } = await mount("/admin?tab=dictionaries", "admin");
  await user.type(input("Название нового значения"), "Статистический анализ");
  await user.selectOptions(input("Тип навыка"), "technology");
  await user.click(button("Создать значение"));
  await screen.findByText("Значение создано.");
  await user.clear(input("Название значения"));
  await user.type(input("Название значения"), "Python 3");
  await user.click(screen.getByLabelText("Активно"));
  await user.click(button("Сохранить"));
  await waitFor(() =>
    expect(
      vi
        .mocked(fetch)
        .mock.calls.some(
          ([path, options]) =>
            String(path).includes(`/admin/dictionaries/skills/${ids.skill}`) &&
            options?.method === "PATCH",
        ),
    ).toBe(true),
  );
});
