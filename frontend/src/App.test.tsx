import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { App } from "./App";
import { AuthProvider } from "./ui/auth";
import { ApiError, api, errorMessage, session } from "./lib/api";
import { fixtureAPI, ids } from "../tests/fixtures/api.mjs";
import { nextPath } from "./pages/account";
import type { Tokens } from "./types";
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
        options.body ? JSON.parse(String(options.body)) : {},
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
async function mount(path: string, role?: string) {
  if (role) {
    const tokens = await api<Tokens>("/auth/login", {
      method: "POST",
      anonymous: true,
      body: { email: `${role}@example.test`, password: "TestPassword123!" },
    });
    session.accept(tokens);
  }
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <AuthProvider>
          <App />
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
describe("routes against explicit API fixtures", () => {
  it.each([
    ["/", "Обзор"],
    ["/projects", "Проекты"],
    ["/researchers", "Исследователи"],
    ["/papers", "Научные работы"],
    [`/projects/${ids.project}`, "Анализ научных публикаций"],
    [`/papers/${ids.paper}`, "Сравнение методов анализа научных текстов"],
    [`/researchers/${ids.owner}`, "Анна Соколова"],
    ["/login", "Вход"],
    ["/register", "Регистрация"],
    ["/forgot-password", "Восстановление пароля"],
    ["/reset-password", "Восстановление пароля"],
    ["/verify-email", "Подтверждение email"],
    ["/no-route", "Страница не найдена"],
  ])("guest %s renders %s", async (path, heading) => {
    await mount(path);
    expect(
      await screen.findByRole("heading", { level: 1, name: heading }),
    ).toBeVisible();
    expect(
      screen.queryByText("Не удалось открыть страницу"),
    ).not.toBeInTheDocument();
  });
  it.each([
    ["/workspace", "Мой раздел"],
    ["/me/profile", "Иван Петров"],
    ["/me/profile/edit", "Редактировать профиль"],
    ["/projects/new", "Создать проект"],
    ["/papers/new", "Добавить научную работу"],
    ["/me/projects", "Мои проекты"],
    ["/me/teams", "Мои команды"],
    ["/me/papers", "Мои работы"],
    ["/me/applications", "Мои заявки"],
    ["/me/invitations", "Приглашения"],
    ["/favorites", "Избранное"],
    ["/favorites?type=user", "Избранное"],
    ["/favorites?type=paper", "Избранное"],
    ["/notifications", "Уведомления"],
    ["/settings", "Настройки"],
    ["/dictionaries/suggest", "Предложить значение"],
  ])("authenticated %s renders %s", async (path, heading) => {
    await mount(path, "student");
    expect(
      await screen.findByRole("heading", { level: 1, name: heading }),
    ).toBeVisible();
  });
  it.each([
    `/projects/${ids.project}/edit`,
    `/projects/${ids.project}/manage`,
    `/papers/${ids.paper}/edit`,
  ])("owner can open %s", async (path) => {
    await mount(path, "owner");
    expect(await screen.findByRole("heading", { level: 1 })).toBeVisible();
  });
  it.each([
    "users",
    "projects",
    "papers",
    "reports",
    "dictionary-suggestions",
    "dictionaries",
    "settings",
    "audit",
  ])("admin tab %s", async (tab) => {
    await mount(`/admin?tab=${tab}`, "admin");
    expect(
      await screen.findByRole("heading", { level: 1, name: "Модерация" }),
    ).toBeVisible();
  });
  it("redirects unauthenticated visitors from protected pages", async () => {
    await mount("/settings");
    expect(await screen.findByRole("heading", { name: "Вход" })).toBeVisible();
  });
  it("denies non-admin access without even requesting admin APIs", async () => {
    await mount("/admin", "student");
    expect(
      await screen.findByRole("heading", { name: "Нет доступа" }),
    ).toBeVisible();
    expect(
      vi
        .mocked(fetch)
        .mock.calls.some(([path]) => String(path).includes("/admin/")),
    ).toBe(false);
  });
});
describe("interaction and recovery", () => {
  it("logs in via the form and clears prior cached private data", async () => {
    client.setQueryData(["old-private-data"], { secret: "old user" });
    await mount("/login");
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Email"), "student@example.test");
    await user.type(screen.getByLabelText("Пароль"), "TestPassword123!");
    await user.click(screen.getByRole("button", { name: "Войти" }));
    expect(
      await screen.findByRole("heading", { name: "Мой раздел" }),
    ).toBeVisible();
    expect(client.getQueryData(["old-private-data"])).toBeUndefined();
  });
  it("shows an invalid-login error without navigating away", async () => {
    await mount("/login");
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Email"), "missing@example.test");
    await user.type(screen.getByLabelText("Пароль"), "bad password");
    await user.click(screen.getByRole("button", { name: "Войти" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Неверный email или пароль",
    );
    expect(session.snapshot()).toBeNull();
  });
  it("retains an explicit unavailable state instead of inventing data", async () => {
    const original = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation(async (path, options) =>
      String(path).includes("/projects?")
        ? new Response(
            JSON.stringify({
              error: {
                code: "UNAVAILABLE",
                message: "Каталог недоступен",
                request_id: "request-test",
              },
            }),
            { status: 503 },
          )
        : original(path, options),
    );
    await mount("/projects");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Каталог недоступен",
    );
    expect(screen.getByText("Код обращения: request-test")).toBeVisible();
  });
  it("explains a missing reset token and sends forgot-password without disclosing accounts", async () => {
    await mount("/forgot-password");
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Email"), "unknown@example.test");
    await user.click(screen.getByRole("button", { name: "Отправить письмо" }));
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Если аккаунт существует",
    );
  });
  it("routes search to the selected category", async () => {
    await mount("/");
    const user = userEvent.setup();
    await user.click(screen.getByRole("tab", { name: "Исследователи" }));
    await user.type(
      screen.getByRole("textbox", { name: "Поисковый запрос" }),
      "Анна",
    );
    await user.click(screen.getByRole("button", { name: "Найти" }));
    expect(
      await screen.findByRole("heading", { level: 1, name: "Исследователи" }),
    ).toBeVisible();
    expect(
      await screen.findByRole("link", { name: "Анна Соколова" }),
    ).toBeVisible();
  });
});
it.each([
  null,
  "https://evil.test",
  "//evil.test",
  "/\\evil.test",
  "javascript:alert(1)",
])("rejects unsafe login redirect %s", (value) => {
  expect(nextPath(value)).toBe("/workspace");
});
it("keeps safe internal redirects and custom fallbacks", () => {
  expect(nextPath("/projects?id=x")).toBe("/projects?id=x");
  expect(nextPath(null, "/me/profile")).toBe("/me/profile");
  expect(errorMessage(new ApiError(403, "DENIED", "Denied"))).toBe("Denied");
});
