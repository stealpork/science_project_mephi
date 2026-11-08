import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { harness, ids, page, response } from "../test/harness";
import { session } from "../lib/api";
let h: ReturnType<typeof harness>;
beforeEach(() => {
  h = harness();
});
afterEach(() => h.cleanup());
const input = (name: string) => screen.getByLabelText(name, { exact: true });
const change = (name: string, value: string) =>
  fireEvent.change(input(name), { target: { value } });
const button = (name: string) => screen.getByRole("button", { name });

it.each([false, true])(
  "completes MFA login using %s recovery mode",
  async (recovery) => {
    const original = h.fixture.handle.bind(h.fixture);
    let challenged = false;
    h.routes.set("POST /auth/login", async (url) => {
      if (!challenged) {
        challenged = true;
        return response({
          requires_2fa: true,
          challenge_token: "challenge",
          expires_in: 300,
        });
      }
      const result = await original({ method: "POST", headers: {} }, url, {
        email: "student@example.test",
        password: "TestPassword123!",
      });
      return response(result.body);
    });
    h.routes.set("POST /auth/2fa/challenge", async (_url, options) => {
      expect(JSON.parse(String(options.body))).toEqual({
        challenge_token: "challenge",
        [recovery ? "recovery_code" : "code"]: recovery
          ? "backup-code"
          : "123456",
      });
      const result = await original(
        { method: "POST", headers: {} },
        new URL("http://localhost/api/v1/auth/login"),
        { email: "student@example.test", password: "TestPassword123!" },
      );
      return response(result.body);
    });
    const { user } = await h.mount("/login");
    change("Email", "student@example.test");
    change("Пароль", "TestPassword123!");
    await user.click(button("Войти"));
    await screen.findByRole("heading", { name: "Подтверждение входа" });
    if (recovery) await user.click(input("Использовать код восстановления"));
    change(
      recovery ? "Код восстановления" : "Код из приложения-аутентификатора",
      recovery ? "backup-code" : "123456",
    );
    await user.click(button("Войти"));
    expect(
      await screen.findByRole("heading", { name: "Мой раздел" }),
    ).toBeVisible();
  },
);
it("offers an authenticated account a safe continuation link", async () => {
  await h.mount("/login?next=%2Fme%2Fpapers", "student");
  expect(screen.getByRole("link", { name: "Продолжить" })).toHaveAttribute(
    "href",
    "/me/papers",
  );
});
it.each([false, true])(
  "registers with %s optional education data",
  async (full) => {
    h.fixture.dictionaries.faculties = [
      {
        ...h.fixture.dictionaries.universities[0],
        id: ids.area,
        name: "Факультет",
      },
    ];
    const { user } = await h.mount("/register");
    change("Имя", "Пётр");
    change("Фамилия", "Сидоров");
    change("Email", "new@example.org");
    fireEvent.change(screen.getByLabelText(/^Пароль/), {
      target: { value: "TestPassword123!" },
    });
    if (full) {
      await screen.findByRole("option", { name: "МГУ" });
      await user.selectOptions(input("Университет"), ids.university);
      await user.selectOptions(input("Факультет / институт"), ids.area);
      await user.selectOptions(input("Специальность"), ids.specialty);
      change("Курс", "2");
      await user.selectOptions(input("Университет"), "");
      await user.selectOptions(input("Факультет / институт"), "");
      await user.selectOptions(input("Специальность"), "");
      await user.selectOptions(input("Университет"), ids.university);
      await user.selectOptions(input("Факультет / институт"), ids.area);
      await user.selectOptions(input("Специальность"), ids.specialty);
    }
    await user.click(button("Зарегистрироваться"));
    expect(
      await screen.findByRole("heading", { name: "Редактировать профиль" }),
    ).toBeVisible();
    const options = vi
      .mocked(fetch)
      .mock.calls.find(([url]) => url === "/api/v1/auth/register")![1];
    expect(JSON.parse(String(options?.body))).toMatchObject({
      university_id: full ? ids.university : null,
      faculty_id: full ? ids.area : null,
      specialty_id: full ? ids.specialty : null,
      course: full ? 2 : null,
    });
  },
);
it("does not mistake a registration MFA challenge for an authenticated session", async () => {
  h.routes.set("POST /auth/login", () =>
    response({
      requires_2fa: true,
      challenge_token: "challenge",
      expires_in: 300,
    }),
  );
  const { user } = await h.mount("/register");
  change("Имя", "Пётр");
  change("Фамилия", "Сидоров");
  change("Email", "new@example.org");
  fireEvent.change(screen.getByLabelText(/^Пароль/), {
    target: { value: "TestPassword123!" },
  });
  await user.click(button("Зарегистрироваться"));
  expect(await screen.findByRole("heading", { name: "Вход" })).toBeVisible();
  expect(session.snapshot()).toBeNull();
});
it.each(["reset", "verify"])(
  "submits the email link token for %s",
  async (mode) => {
    const { user } = await h.mount(
      mode === "reset"
        ? "/reset-password?token=valid-token"
        : "/verify-email?token=valid-token",
    );
    if (mode === "reset") change("Новый пароль", "ChangedPassword123!");
    await user.click(button("Подтвердить"));
    expect(await screen.findByRole("status")).toHaveTextContent(
      mode === "reset" ? "Пароль изменён" : "Email подтверждён",
    );
  },
);
it.each(["/me/profile", "/me/profile/edit"])(
  "retries an initializing profile at %s",
  async (path) => {
    h.routes.set("GET /me/profile", () =>
      response({ status: "initializing" }, 202),
    );
    const { user } = await h.mount(path, "student");
    expect(
      screen.getByRole("heading", { name: "Профиль создаётся" }),
    ).toBeVisible();
    h.routes.delete("GET /me/profile");
    await user.click(button("Проверить готовность"));
    expect(
      await screen.findByRole("heading", {
        name: path.endsWith("edit") ? "Редактировать профиль" : "Иван Петров",
      }),
    ).toBeVisible();
  },
);
it.each(["/me/profile", "/me/profile/edit"])(
  "retries a profile transport error at %s",
  async (path) => {
    h.routes.set("GET /me/profile", () =>
      response({ error: { message: "Профиль недоступен" } }, 503),
    );
    const { user } = await h.mount(path, "student", false);
    await screen.findByText("Профиль недоступен");
    h.routes.delete("GET /me/profile");
    await user.click(button("Повторить"));
    expect(
      await screen.findByRole("heading", {
        name: path.endsWith("edit") ? "Редактировать профиль" : "Иван Петров",
      }),
    ).toBeVisible();
  },
);
it("edits independent contacts and experience rows without changing their neighbours", async () => {
  const p = h.fixture.profiles.get(ids.student)!;
  Object.assign(p, {
    university_id: null,
    specialty_id: null,
    city_id: null,
    course: null,
    contacts: [
      { type: "email", value: "first@example.org", visibility: "private" },
      { type: "email", value: "second@example.org", visibility: "public" },
    ],
    experience: [
      { title: "Первый опыт", source: "self_reported" },
      {
        title: "Второй опыт",
        description: "Сохранить",
        source: "self_reported",
      },
      { title: "Не редактировать", source: "platform" },
    ],
  });
  h.fixture.dictionaries.faculties = [
    {
      ...h.fixture.dictionaries.universities[0],
      id: ids.area,
      name: "Факультет",
    },
  ];
  h.fixture.dictionaries.departments = [
    {
      ...h.fixture.dictionaries.universities[0],
      id: ids.skill,
      name: "Кафедра",
    },
  ];
  const { user } = await h.mount("/me/profile/edit", "student");
  change("Имя", "Илья");
  change("Фамилия", "Павлов");
  await user.selectOptions(input("Университет"), ids.university);
  await user.selectOptions(input("Факультет / институт"), ids.area);
  await user.selectOptions(input("Кафедра"), ids.skill);
  await user.selectOptions(input("Кафедра"), "");
  await user.selectOptions(input("Факультет / институт"), "");
  const contacts = screen
    .getAllByRole("group")
    .filter((g) => g.textContent?.startsWith("Контакт"));
  await user.selectOptions(
    within(contacts[0]).getByLabelText("Тип"),
    "telegram",
  );
  fireEvent.change(within(contacts[0]).getByLabelText("Значение"), {
    target: { value: "@researcher" },
  });
  await user.selectOptions(
    within(contacts[0]).getByLabelText("Кто видит"),
    "authenticated",
  );
  const experience = screen
    .getAllByRole("group")
    .filter((g) => g.textContent?.startsWith("Опыт"));
  fireEvent.change(within(experience[0]).getByLabelText("Название"), {
    target: { value: "Новый опыт" },
  });
  fireEvent.change(within(experience[0]).getByLabelText("Описание"), {
    target: { value: "Вручную" },
  });
  await user.click(within(experience[0]).getByLabelText("Завершён"));
  await user.click(button("Сохранить"));
  await screen.findByRole("heading", { name: "Илья Павлов" });
  expect(h.fixture.profiles.get(ids.student)!.contacts[1].value).toBe(
    "second@example.org",
  );
  expect(h.fixture.profiles.get(ids.student)!.experience).toEqual([
    {
      title: "Новый опыт",
      description: "Вручную",
      completed: true,
      source: "self_reported",
    },
    { title: "Второй опыт", description: "Сохранить", source: "self_reported" },
  ]);
});
it("supports an entirely optional education profile", async () => {
  const p = h.fixture.profiles.get(ids.student)!;
  Object.assign(p, {
    university_id: null,
    faculty_id: null,
    department_id: null,
    specialty_id: null,
    city_id: null,
    course: null,
  });
  const { user } = await h.mount("/me/profile/edit", "student");
  await user.click(button("Сохранить"));
  await screen.findByRole("heading", { name: "Иван Петров" });
  expect(h.fixture.profiles.get(ids.student)).toMatchObject({
    university_id: null,
    faculty_id: null,
    department_id: null,
    specialty_id: null,
    city_id: null,
    course: null,
  });
});
it("removes a current avatar and attaches a newly scanned avatar", async () => {
  h.fixture.profiles.get(ids.student)!.avatar_file_id = ids.paper;
  const { user } = await h.mount("/me/profile/edit", "student");
  await user.click(button("Удалить фотографию"));
  await waitFor(() =>
    expect(h.fixture.profiles.get(ids.student)!.avatar_file_id).toBeNull(),
  );
  const file = new File(["image"], "photo.png", { type: "image/png" });
  Object.defineProperty(file, "arrayBuffer", {
    value: async () => new TextEncoder().encode("image").buffer,
  });
  await user.upload(screen.getByLabelText(/^Фотография/), file);
  await waitFor(() =>
    expect(h.fixture.profiles.get(ids.student)!.avatar_file_id).not.toBeNull(),
  );
});
it("resends verification and handles a session with no user agent", async () => {
  h.fixture.accounts.get(ids.student)!.email_verified = false;
  h.routes.set("GET /account/sessions", () =>
    response(
      page([{ id: "session", user_agent: "", ip_address: "127.0.0.1" }]),
    ),
  );
  h.routes.set("POST /auth/resend-verification", () =>
    response({ status: "ok" }),
  );
  const { user } = await h.mount("/settings", "student");
  await user.click(button("Отправить письмо"));
  expect(
    await screen.findByText("Письмо для подтверждения отправлено."),
  ).toBeVisible();
  expect(screen.getByText(/Браузер ·/)).toBeVisible();
});
it.each([false, true])(
  "logs out even if the server is unavailable: %s",
  async (failure) => {
    if (failure)
      h.routes.set("POST /auth/logout", () =>
        response({ error: { message: "Нет связи с сервером" } }, 503),
      );
    const { user } = await h.mount("/settings", "student");
    await user.click(button("Выйти из аккаунта"));
    if (failure) {
      await screen.findByText("Нет связи с сервером");
      await user.click(button("Забыть сессию на этом устройстве"));
    }
    expect(await screen.findByRole("heading", { name: "Вход" })).toBeVisible();
    expect(session.snapshot()).toBeNull();
  },
);
it("requires explicit confirmation before deleting an account", async () => {
  const assign = vi.fn();
  vi.stubGlobal(
    "window",
    new Proxy(window, {
      get: (target, key) =>
        key === "location"
          ? { assign, pathname: "/settings" }
          : Reflect.get(target, key),
    }),
  );
  const { user } = await h.mount("/settings", "student");
  await user.click(screen.getByText("Удаление аккаунта"));
  change("Пароль для удаления аккаунта", "TestPassword123!");
  vi.mocked(window.confirm).mockReturnValueOnce(false);
  await user.click(button("Удалить аккаунт"));
  expect(assign).not.toHaveBeenCalled();
  await user.click(button("Удалить аккаунт"));
  await waitFor(() => expect(assign).toHaveBeenCalledWith("/login"));
  expect(session.snapshot()).toBeNull();
});
it("displays existing dictionary proposals", async () => {
  h.routes.set("GET /me/dictionary-suggestions", () =>
    response(
      page([{ id: "suggestion", name: "Биоинформатика", status: "pending" }]),
    ),
  );
  await h.mount("/dictionaries/suggest", "student");
  expect(await screen.findByText("Биоинформатика · pending")).toBeVisible();
});
it("clears every optional profile dictionary selection", async () => {
  const { user } = await h.mount("/me/profile/edit", "student");
  await screen.findByRole("option", { name: "МГУ" });
  for (const name of ["Университет", "Специальность", "Город"])
    await user.selectOptions(input(name), "");
  await user.click(button("Сохранить"));
  await screen.findByRole("heading", { name: "Иван Петров" });
  expect(h.fixture.profiles.get(ids.student)).toMatchObject({
    university_id: null,
    specialty_id: null,
    city_id: null,
  });
});
