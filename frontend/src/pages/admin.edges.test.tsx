import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { harness, ids, page, response } from "../test/harness";
let h: ReturnType<typeof harness>;
beforeEach(() => {
  h = harness();
});
afterEach(() => {
  vi.useRealTimers();
  h.cleanup();
});
const button = (name: string) => screen.getByRole("button", { name });
const change = (name: string, value: string) =>
  fireEvent.change(screen.getByLabelText(name), { target: { value } });
it.each(["", "?tab=invalid"])(
  "uses reports as the default moderation tab (%s)",
  async (suffix) => {
    h.routes.set("GET /admin/reports", () =>
      response(
        page([
          {
            id: "report",
            version: 1,
            status: "open",
            description: "Проверить публикацию",
            target_type: "paper",
            target_id: ids.paper,
            reason_code: "spam",
          },
        ]),
      ),
    );
    const { user } = await h.mount(`/admin${suffix}`, "admin");
    await screen.findByText("Проверить публикацию");
    change("Решение по жалобе", "Проверено модератором");
    fireEvent.change(screen.getByLabelText("Статус жалобы"), {
      target: { value: "resolved" },
    });
    await user.click(button("Сохранить решение"));
    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        "/api/v1/admin/reports/report",
        expect.objectContaining({
          method: "PATCH",
          body: JSON.stringify({
            version: 1,
            status: "resolved",
            resolution: "Проверено модератором",
          }),
        }),
      ),
    );
  },
);
it("keeps an existing report decision and shows audit metadata", async () => {
  h.routes.set("GET /admin/reports", () =>
    response(
      page([
        {
          id: "report",
          version: 2,
          status: "resolved",
          description: "Решено",
          resolution: "Проверено",
          name: "Обращение",
        },
      ]),
    ),
  );
  h.routes.set("GET /admin/audit", () =>
    response(
      page([
        {
          id: "audit",
          action: "user.block",
          created_at: "2026-01-01",
          actor_id: ids.admin,
          target_type: "user",
          target_id: ids.student,
        },
      ]),
    ),
  );
  const { user } = await h.mount("/admin", "admin");
  expect(await screen.findByLabelText("Статус жалобы")).toHaveValue("resolved");
  await user.click(button("Аудит"));
  expect(await screen.findByText("user.block")).toBeVisible();
  expect(screen.getByText(/Инициатор:/)).toHaveTextContent(ids.admin);
});
it.each(["approve", "merge", "reject"])(
  "decides a dictionary suggestion: %s",
  async (decision) => {
    h.routes.set("GET /admin/dictionary-suggestions", () =>
      response(
        page([
          {
            id: "suggestion",
            name: "Новый навык",
            status: "pending",
            kind: "skills",
            comment: "Полезное значение",
          },
          { id: "done", status: "approved", name: "Уже решено" },
        ]),
      ),
    );
    const { user } = await h.mount(
      "/admin?tab=dictionary-suggestions",
      "admin",
    );
    await screen.findByText("Полезное значение");
    await user.selectOptions(
      screen.getByLabelText("Решение по предложению"),
      decision,
    );
    if (decision === "merge") change("ID существующего значения", ids.skill);
    change("Комментарий модератора", "Решение принято");
    await user.click(button("Применить решение"));
    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        `/api/v1/admin/dictionary-suggestions/suggestion/${decision}`,
        expect.objectContaining({
          body: JSON.stringify({
            reason: "Решение принято",
            ...(decision === "merge" ? { target_id: ids.skill } : {}),
          }),
        }),
      ),
    );
  },
);
it.each(["settings", "dictionaries"])(
  "does not expose administrator-only %s to a moderator",
  async (tab) => {
    h.fixture.accounts.get(ids.admin)!.global_roles = ["moderator"];
    await h.mount(`/admin?tab=${tab}`, "admin");
    expect(screen.getByText("Требуются права администратора.")).toBeVisible();
    expect(
      screen.queryByRole("button", {
        name: tab === "settings" ? "Настройки" : "Справочники",
      }),
    ).not.toBeInTheDocument();
  },
);
it.each(["users", "projects", "papers"])(
  "searches %s moderation and restores blocked or hidden content",
  async (tab) => {
    h.routes.set(`GET /admin/${tab}`, () =>
      response(
        page([
          {
            id: ids.student,
            title: tab === "users" ? undefined : "Материал",
            email: tab === "users" ? "blocked@example.org" : undefined,
            status: "blocked",
            moderation_status: tab === "users" ? undefined : "hidden",
          },
        ]),
      ),
    );
    const { user } = await h.mount(`/admin?tab=${tab}`, "admin");
    change("Поиск в модерации", "запрос");
    await user.click(button("Найти"));
    await user.click(
      await screen.findByRole("button", {
        name: tab === "users" ? "Разблокировать" : "Восстановить",
      }),
    );
    change("Обоснование", "Возвращаем доступ");
    await user.click(button("Подтвердить решение"));
    expect(await screen.findByText("Решение выполнено.")).toBeVisible();
    expect(
      vi
        .mocked(fetch)
        .mock.calls.some(([url]) =>
          String(url).includes(
            `/${tab}/${ids.student}/${tab === "users" ? "unblock" : "restore"}`,
          ),
        ),
    ).toBe(true);
  },
);
it.each(["id", "none"])(
  "handles a moderation response with %s action identifier",
  async (kind) => {
    h.routes.set("GET /admin/users", () =>
      response(page([{ id: ids.student, status: "active", email: 42 }])),
    );
    h.routes.set(`POST /admin/users/${ids.student}/block`, () =>
      response(
        kind === "id"
          ? { id: "action", status: "pending" }
          : { status: "succeeded" },
        202,
      ),
    );
    const { user } = await h.mount("/admin?tab=users", "admin");
    await user.click(await screen.findByText("Заблокировать"));
    change("Обоснование", "Проверка обращения");
    await user.click(button("Подтвердить решение"));
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );
    if (kind === "id")
      expect(await screen.findByText("Решение выполнено.")).toBeVisible();
    else expect(screen.queryByText("Проверить статус")).not.toBeInTheDocument();
  },
);
it.each(["failed", "queued", "error"])(
  "shows asynchronous moderation status %s",
  async (status) => {
    h.routes.set("GET /admin/actions/action", () =>
      status === "error"
        ? response({ error: { message: "Статус недоступен" } }, 503)
        : response({ status }),
    );
    h.routes.set(`POST /admin/users/${ids.owner}/block`, () =>
      response({ action_id: "action", status: "pending" }, 202),
    );
    const { user } = await h.mount("/admin?tab=users", "admin");
    await user.click(
      (await screen.findAllByRole("button", { name: "Заблокировать" }))[0],
    );
    change("Обоснование", "Проверяем блокировку");
    await user.click(button("Подтвердить решение"));
    expect(
      await screen.findByText(
        status === "failed"
          ? "Не удалось выполнить решение. Проверьте аудит."
          : "Решение в очереди. Обновите статус позже.",
      ),
    ).toBeVisible();
    if (status === "error")
      expect(screen.getByText("Статус недоступен")).toBeVisible();
    await user.click(button("Проверить статус"));
  },
);
it("limits polling of a pending moderation command to thirty responses", async () => {
  let resolve!: (r: Response) => void;
  let calls = 0;
  h.routes.set("GET /admin/actions/action", () => {
    calls++;
    if (calls === 1)
      return new Promise((r) => {
        resolve = r;
      });
    return response({ status: "pending" });
  });
  h.routes.set(`POST /admin/users/${ids.owner}/block`, () =>
    response({ action_id: "action", status: "pending" }, 202),
  );
  const { user } = await h.mount("/admin?tab=users", "admin");
  await user.click(
    (await screen.findAllByRole("button", { name: "Заблокировать" }))[0],
  );
  change("Обоснование", "Проверка очереди");
  await user.click(button("Подтвердить решение"));
  await screen.findByText("Решение принято, проверяем исполнение…");
  vi.useFakeTimers();
  await act(async () => resolve(response({ status: "pending" })));
  for (let i = 0; i < 32; i++)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2001);
    });
  expect(calls).toBe(30);
});
it("reports administrator settings failure without exposing a writable form", async () => {
  h.routes.set("GET /admin/settings/project-files", () =>
    response({ error: { message: "Настройки недоступны" } }, 503),
  );
  await h.mount("/admin?tab=settings", "admin");
  expect(await screen.findByText("Настройки недоступны")).toBeVisible();
  expect(
    screen.queryByLabelText("Максимум файлов на проект"),
  ).not.toBeInTheDocument();
});
it("creates a non-skill dictionary entry and clears its previous success on kind change", async () => {
  const { user } = await h.mount("/admin?tab=dictionaries", "admin");
  await user.selectOptions(screen.getByLabelText("Справочник"), "universities");
  change("Название нового значения", "Новый университет");
  change("ID родительского значения", ids.university);
  await user.click(button("Создать значение"));
  await waitFor(() =>
    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/admin/dictionaries/universities",
      expect.objectContaining({
        body: JSON.stringify({
          name: "Новый университет",
          parent_id: ids.university,
          skill_kind: null,
        }),
      }),
    ),
  );
});
it("allows creating a skill without assigning its optional kind", async () => {
  const { user } = await h.mount("/admin?tab=dictionaries", "admin");
  await user.selectOptions(screen.getByLabelText("Тип навыка"), "");
  change("Название нового значения", "Навык без типа");
  await user.click(button("Создать значение"));
  expect(await screen.findByText("Значение создано.")).toBeVisible();
});
