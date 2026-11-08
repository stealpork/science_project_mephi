import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { harness, ids, page, response } from "../test/harness";
import { api } from "../lib/api";
import type { Paper, Profile, Project } from "../types";
let h: ReturnType<typeof harness>;
beforeEach(() => {
  h = harness();
});
afterEach(() => h.cleanup());
const button = (name: string) => screen.getByRole("button", { name });
const field = (name: string) => screen.getByLabelText(name, { exact: true });
it("makes the home search keyboard navigable and focuses the main content", async () => {
  const { user } = await h.mount("/");
  await user.click(screen.getByText("К содержимому"));
  expect(document.activeElement).toHaveAttribute("id", "main");
  const keys = [
    ["End", "Научные работы"],
    ["Home", "Проекты"],
    ["ArrowLeft", "Научные работы"],
    ["ArrowRight", "Проекты"],
    ["Escape", "Проекты"],
  ];
  for (const [key, name] of keys) {
    fireEvent.keyDown(screen.getByRole("tab", { selected: true }), { key });
    expect(screen.getByRole("tab", { selected: true })).toHaveTextContent(name);
  }
  await user.click(button("Найти"));
  expect(
    await screen.findByRole("heading", { level: 1, name: "Проекты" }),
  ).toBeVisible();
});
it.each([undefined, "student"])(
  "renders empty home sections for %s",
  async (role) => {
    for (const path of ["/users", "/papers", "/projects"])
      h.routes.set(`GET ${path}`, () => response(page([])));
    h.routes.set("GET /recommendations/projects", () =>
      response({ ...page([]), personalized: true, fallback_reason: null }),
    );
    await h.mount("/", role);
    expect(
      await screen.findByText(
        role
          ? "Подходящих проектов пока нет. Заполните интересы в профиле или откройте каталог."
          : "Пока нет проектов с открытым набором.",
      ),
    ).toBeVisible();
    expect(
      await screen.findByText("Работы ещё не опубликованы."),
    ).toBeVisible();
  },
);
it("retries personalized recommendations and accepts structured explanations and an unknown score", async () => {
  h.routes.set("GET /recommendations/projects", () =>
    response({ error: { message: "Рекомендации недоступны" } }, 503),
  );
  const { user } = await h.mount("/", "student");
  await screen.findByText("Рекомендации недоступны");
  const p = await api<Project>(`/projects/${ids.project}`);
  p.summary = "";
  h.routes.set("GET /recommendations/projects", () =>
    response({
      ...page([
        {
          project: p,
          score: null,
          reasons: [{ text: "Навыки" }, { label: "Интересы" }, {}],
        },
      ]),
      personalized: true,
      fallback_reason: null,
    }),
  );
  await user.click(button("Повторить"));
  expect(await screen.findByText("Навыки · Интересы ·")).toBeVisible();
  expect(screen.queryByText(/% совпадения/)).not.toBeInTheDocument();
});
it.each(["/researchers", "/papers", "/projects"])(
  "sets and clears catalog filters at %s",
  async (path) => {
    const { user } = await h.mount(path);
    await screen.findByRole("option", { name: "МГУ" });
    await user.click(screen.getByText("Фильтры"));
    for (const [name, id] of [
      ["Направление", ids.area],
      ["Университет", ids.university],
      ...(path === "/papers" ? [] : [["Навык", ids.skill]]),
    ]) {
      await user.selectOptions(field(name), id);
      await user.selectOptions(field(name), "");
    }
    if (path === "/researchers") {
      for (const [name, id] of [
        ["Специальность", ids.specialty],
        ["Город", ids.city],
      ]) {
        await user.selectOptions(field(name), id);
        await user.selectOptions(field(name), "");
      }
      fireEvent.change(field("Курс"), { target: { value: "3" } });
      fireEvent.change(field("Курс"), { target: { value: "" } });
      await user.click(field("С опытом проектов"));
      await user.click(field("С опытом проектов"));
    } else if (path === "/papers") {
      for (const [name, value] of [
        ["Год от", "2020"],
        ["Год до", "2026"],
        ["Автор", "Соколова"],
        ["Ключевое слово", "данные"],
      ]) {
        fireEvent.change(field(name), { target: { value } });
        fireEvent.change(field(name), { target: { value: "" } });
      }
    } else {
      await user.click(field("Есть свободные места"));
      await user.click(field("Есть свободные места"));
    }
    await user.click(button("Найти"));
    expect(
      vi
        .mocked(fetch)
        .mock.calls.some(([url]) =>
          String(url).startsWith(
            `/api/v1${path === "/researchers" ? "/users" : path}?`,
          ),
        ),
    ).toBe(true);
  },
);
it("renders a populated paper catalog with no publication year", async () => {
  h.fixture.papers.get(ids.paper)!.year = null;
  await h.mount("/papers");
  expect(
    await screen.findByText("Сравнение методов анализа научных текстов"),
  ).toBeVisible();
});
it("shows sparse profile details and safe avatar URLs", async () => {
  Object.assign(h.fixture.profiles.get(ids.student)!, {
    avatar_file_id: ids.paper,
    bio: "",
    course: null,
    skills: [],
    interests: [],
    contacts: [
      { type: "email", value: "contact@example.org", visibility: "public" },
    ],
    experience: [
      { title: "Платформенный опыт", source: "platform", completed: true },
      { title: "Свой опыт", source: "self_reported", description: "Вручную" },
    ],
  });
  h.routes.set(`GET /users/${ids.student}/avatar-url`, () =>
    response({ url: "javascript:alert(1)" }),
  );
  const { container } = await h.mount(`/researchers/${ids.student}`);
  expect(await screen.findByText("Описание пока не добавлено.")).toBeVisible();
  expect(screen.getByText("email: contact@example.org")).toBeVisible();
  expect(screen.getByText("Участие на платформе · завершено")).toBeVisible();
  expect(screen.getByText("Указано пользователем")).toBeVisible();
  expect(screen.getAllByText("Не указаны")).toHaveLength(2);
  expect(container.querySelector("img")).toBeNull();
  h.routes.set(`GET /users/${ids.student}/avatar-url`, () =>
    response({ url: "/test-s3/avatar" }),
  );
  await h.refresh();
  await waitFor(() =>
    expect(container.querySelector("img")).toHaveAttribute(
      "src",
      "/test-s3/avatar",
    ),
  );
});
it("paginates owner projects in a profile invitation dialog", async () => {
  await h.login("owner");
  const p = await api<Project>(`/projects/${ids.project}`);
  let calls = 0;
  h.routes.set("GET /me/projects", (url) => {
    calls++;
    return response(
      page(
        url.searchParams.has("cursor")
          ? [{ ...p, id: ids.private, title: "Второй проект" }]
          : [p],
        !url.searchParams.has("cursor"),
        url.searchParams.has("cursor") ? null : "next",
      ),
    );
  });
  const { user } = await h.mount(`/researchers/${ids.student}`);
  await user.click(button("Пригласить в проект"));
  await user.click(await screen.findByText("Ещё проекты"));
  expect(
    await screen.findByRole("option", { name: "Второй проект" }),
  ).toBeVisible();
  expect(calls).toBe(2);
  await user.click(screen.getByLabelText("Закрыть"));
});
it("shows the moderation entry in the administrator workspace", async () => {
  await h.mount("/workspace", "admin");
  expect(screen.getByRole("link", { name: "Модерация" })).toHaveAttribute(
    "href",
    "/admin",
  );
});
it.each(["projects", "teams", "papers"])(
  "renders populated personal %s",
  async (type) => {
    await h.mount(`/me/${type}`, "owner");
    expect(
      await screen.findByText(
        type === "papers"
          ? "Сравнение методов анализа научных текстов"
          : "Анализ научных публикаций",
      ),
    ).toBeVisible();
  },
);
it("shows decisions and expired, accepted or unavailable invitations", async () => {
  h.routes.set("GET /me/invitations", () =>
    response(
      page([
        {
          id: "accepted",
          project_id: ids.project,
          status: "accepted",
          role: "analyst",
          expires_at: "2099-01-01",
          message: "Принято",
          project: { title: "Принятый проект", summary: "Команда" },
        },
        {
          id: "expired",
          project_id: ids.project,
          status: "pending",
          role: "analyst",
          expires_at: "2000-01-01",
          message: "Истёкшее",
          project: null,
        },
        {
          id: "declined",
          project_id: ids.project,
          status: "declined",
          role: "analyst",
          expires_at: "2099-01-01",
          message: "Отклонено",
          project: null,
        },
      ]),
    ),
  );
  await h.mount("/me/invitations", "student");
  expect(
    await screen.findByRole("link", { name: "Открыть проект" }),
  ).toHaveAttribute("href", `/projects/${ids.project}`);
  expect(
    screen.queryByRole("button", { name: "Принять приглашение" }),
  ).not.toBeInTheDocument();
});
it("shows a rejected application decision", async () => {
  h.routes.set("GET /me/applications", () =>
    response(
      page([
        {
          id: "app",
          project_id: ids.project,
          status: "rejected",
          requested_role: "analyst",
          message: "Опыт",
          decision_reason: "Набор завершён",
        },
      ]),
    ),
  );
  await h.mount("/me/applications", "student");
  expect(await screen.findByText("Ответ: Набор завершён")).toBeVisible();
});
it("renders every favorite type and removes unavailable materials", async () => {
  await h.login("owner");
  const cards = {
    project: await api<Project>(`/projects/${ids.project}`),
    paper: await api<Paper>(`/papers/${ids.paper}`),
    user: await api<Profile>(`/users/${ids.student}`),
  };
  h.routes.set("GET /favorites", (url) => {
    const type = url.searchParams.get("type") as keyof typeof cards;
    return response(
      page([
        {
          type,
          target_id: "available",
          availability: "available",
          card: cards[type],
        },
        { type, target_id: "hidden", availability: "unavailable", card: null },
        {
          type,
          target_id: "temporary",
          availability: "temporarily_unavailable",
          card: null,
        },
        { type, target_id: "missing", availability: "available", card: null },
      ]),
    );
  });
  const { user } = await h.mount("/favorites");
  await screen.findByText("Анализ научных публикаций");
  await user.click(button("Люди"));
  await screen.findByText("Иван Петров");
  await user.click(button("Работы"));
  await screen.findByText("Сравнение методов анализа научных текстов");
  expect(screen.getByText("Материал временно недоступен.")).toBeVisible();
  await user.click(
    screen.getAllByRole("button", { name: "Убрать из избранного" })[0],
  );
  await waitFor(() =>
    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/favorites/paper/hidden",
      expect.objectContaining({ method: "DELETE" }),
    ),
  );
});
it("routes notifications by safe target and marks only unread records", async () => {
  h.routes.set("GET /notifications", () =>
    response(
      page([
        {
          id: "one",
          title: "",
          body: "Проект",
          target: { type: "project", id: ids.project },
          read_at: null,
          created_at: "2026-01-01",
        },
        {
          id: "two",
          title: "Работа",
          target: { type: "paper", id: ids.paper },
          read_at: "2026-01-02",
          created_at: "2026-01-01",
        },
        {
          id: "three",
          title: "Скрыто",
          target: { type: "project", id: ids.project },
          action: "unavailable",
          read_at: null,
        },
        {
          id: "four",
          title: "Недоступно",
          target: { type: "paper", id: ids.paper },
          action: "temporarily_unavailable",
          read_at: null,
        },
        { id: "five", title: "Без цели", read_at: null },
        {
          id: "six",
          title: "Другая цель",
          target: { type: "user", id: ids.student },
          read_at: null,
        },
      ]),
    ),
  );
  const { user } = await h.mount("/notifications", "student");
  await screen.findByText("Обновление");
  const rows = screen.getAllByRole("article");
  expect(
    within(rows[0]).getByRole("link", { name: "Открыть" }),
  ).toHaveAttribute("href", `/projects/${ids.project}`);
  expect(
    within(rows[1]).getByRole("link", { name: "Открыть" }),
  ).toHaveAttribute("href", `/papers/${ids.paper}`);
  expect(
    within(rows[1]).queryByRole("button", { name: "Прочитать" }),
  ).not.toBeInTheDocument();
  for (const row of rows.slice(2))
    expect(within(row).queryByRole("link")).not.toBeInTheDocument();
  await user.click(field("Только непрочитанные"));
  await user.click(field("Только непрочитанные"));
});
it("catches a malformed page render without displaying private errors", async () => {
  h.routes.set("GET /projects", () =>
    response(page([{ id: "bad", required_skill_ids: null }])),
  );
  vi.spyOn(console, "error").mockImplementation(() => undefined);
  const reload = vi.fn();
  vi.stubGlobal(
    "window",
    new Proxy(window, {
      get: (target, key) =>
        key === "location" ? { reload } : Reflect.get(target, key),
    }),
  );
  const { user } = await h.mount("/projects");
  expect(
    await screen.findByRole("heading", { name: "Не удалось открыть страницу" }),
  ).toBeVisible();
  await user.click(button("Обновить"));
  expect(reload).toHaveBeenCalledOnce();
});
it("renders positive and zero matching scores with string explanations", async () => {
  await h.login("student");
  const p = await api<Project>(`/projects/${ids.project}`);
  h.routes.set("GET /recommendations/projects", () =>
    response({
      ...page([
        { project: p, score: 0, reasons: ["Пока нет совпадений"] },
        { project: { ...p, id: "second" }, score: 100, reasons: [] },
      ]),
      personalized: true,
    }),
  );
  await h.mount("/");
  expect(await screen.findByText("0% совпадения")).toBeVisible();
  expect(screen.getByText("Пока нет совпадений")).toBeVisible();
});
it("renders the portfolio project and paper lists after they load", async () => {
  await h.mount(`/researchers/${ids.owner}`);
  expect(await screen.findByText("Анализ научных публикаций")).toBeVisible();
  expect(
    await screen.findByText("Сравнение методов анализа научных текстов"),
  ).toBeVisible();
});
it("does not append a course label for an unspecified researcher course", async () => {
  h.fixture.profiles.get(ids.student)!.course = null;
  await h.mount("/researchers");
  await screen.findByText("Иван Петров");
  const row = screen.getByText("Иван Петров").closest("article")!;
  expect(row).not.toHaveTextContent("курс");
});
