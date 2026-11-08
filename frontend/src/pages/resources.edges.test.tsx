import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { harness, ids, response } from "../test/harness";
let h: ReturnType<typeof harness>;
beforeEach(() => {
  h = harness();
});
afterEach(() => h.cleanup());
const button = (name: string) => screen.getByRole("button", { name });
const input = (name: string) => screen.getByLabelText(name, { exact: true });
const change = (name: string, value: string) =>
  fireEvent.change(input(name), { target: { value } });
const pdf = () => {
  const file = new File(["%PDF-1.4\ntest"], "paper.pdf", {
    type: "application/pdf",
  });
  Object.defineProperty(file, "arrayBuffer", {
    value: async () => new TextEncoder().encode("%PDF-1.4\ntest").buffer,
  });
  return file;
};
it.each(["projects", "papers"])(
  "retries a failed %s detail request",
  async (type) => {
    const id = type === "projects" ? ids.project : ids.paper;
    h.routes.set(`GET /${type}/${id}`, () =>
      response({ error: { message: "Материал недоступен" } }, 503),
    );
    const { user } = await h.mount(`/${type}/${id}`, undefined, false);
    await screen.findByText("Материал недоступен");
    h.routes.delete(`GET /${type}/${id}`);
    await user.click(button("Повторить"));
    expect(await screen.findByRole("heading", { level: 1 })).toBeVisible();
  },
);
it.each(["projects", "papers"])(
  "does not allow an unrelated account to edit %s",
  async (type) => {
    const id = type === "projects" ? ids.project : ids.paper;
    await h.mount(`/${type}/${id}/edit`, "student", false);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      type === "projects"
        ? "Редактирование доступно руководителю проекта."
        : "Редактирование доступно только явно добавленным соавторам.",
    );
  },
);
it.each(["projects", "papers"])(
  "shows a failed edit request for %s without rendering the editor",
  async (type) => {
    const id = type === "projects" ? ids.project : ids.paper;
    h.routes.set(`GET /${type}/${id}`, () =>
      response({ error: { message: "Нет доступа к материалу" } }, 404),
    );
    await h.mount(`/${type}/${id}/edit`, "owner", false);
    expect(await screen.findByText("Нет доступа к материалу")).toBeVisible();
    expect(screen.queryByLabelText("Название")).not.toBeInTheDocument();
  },
);
it("shows sparse project fields, safe result links and unavailable team accounts", async () => {
  Object.assign(h.fixture.projects.get(ids.project)!, {
    description: "",
    research_goal: "",
    required_roles: [],
    results: [
      { url: "https://example.org/result", title: "Результат" },
      { url: "https://example.org/code" },
      { url: "javascript:alert(1)", title: "Небезопасный адрес" },
      { title: "Текстовый результат" },
    ],
  });
  h.routes.set(`GET /users/${ids.owner}`, () =>
    response({ error: { message: "Удалён" } }, 404),
  );
  await h.mount(`/projects/${ids.project}`);
  expect(await screen.findByText("Аккаунт недоступен")).toBeVisible();
  expect(screen.getByText("Не указана.")).toBeVisible();
  expect(screen.getByText("Роли не ограничены")).toBeVisible();
  expect(screen.getByRole("link", { name: "Результат" })).toHaveAttribute(
    "href",
    "https://example.org/result",
  );
  expect(
    screen.queryByRole("link", { name: "Небезопасный адрес" }),
  ).not.toBeInTheDocument();
  expect(screen.getByText("Текстовый результат")).toBeVisible();
});
it("lets a non-owner member leave a team", async () => {
  h.fixture.members.push({
    id: "member",
    project_id: ids.project,
    user_id: ids.student,
    role: "researcher",
  });
  const { user } = await h.mount(`/projects/${ids.project}`, "student");
  expect(await screen.findByText("Вы участник команды.")).toBeVisible();
  await user.click(button("Выйти из проекта"));
  expect(await screen.findByText("Подать заявку")).toBeVisible();
});
it.each(["public_open", "public_invite"])(
  "explains why joining an unavailable %s project is not allowed",
  async (access_type) => {
    Object.assign(h.fixture.projects.get(ids.project)!, {
      status: "completed",
      access_type,
    });
    const { user } = await h.mount(`/projects/${ids.project}`, "student");
    expect(
      await screen.findByText(
        access_type === "public_open"
          ? "Сейчас нельзя подать заявку."
          : "Участие только по приглашению.",
      ),
    ).toBeVisible();
    await user.click(screen.getByText("К содержимому"));
  },
);
it("shows invitation navigation and an outstanding application together", async () => {
  h.routes.set(`GET /projects/${ids.project}`, async () => {
    const p = h.fixture.projects.get(ids.project)!;
    return response({
      ...p,
      members_count: 1,
      free_slots: 4,
      is_favorited: false,
      viewer: {
        can_manage: false,
        is_member: false,
        can_apply: false,
        application_status: "pending",
        invitation_status: "pending",
      },
    });
  });
  await h.mount(`/projects/${ids.project}`, "student");
  expect(screen.getByText("Ваша заявка на рассмотрении.")).toBeVisible();
  expect(screen.getByRole("link", { name: "Мои заявки" })).toBeVisible();
  expect(
    screen.getByRole("link", { name: "Открыть приглашение" }),
  ).toBeVisible();
});
it("retries the project files list and distinguishes attachment and publication sources", async () => {
  h.routes.set(`GET /projects/${ids.project}/files`, () =>
    response({ error: { message: "Материалы недоступны" } }, 503),
  );
  const { user } = await h.mount(`/projects/${ids.project}`, "owner");
  await screen.findByText("Материалы недоступны");
  h.routes.set(`GET /projects/${ids.project}/files`, () =>
    response({
      items: [
        {
          id: "a",
          file_id: "a",
          title: "Названный материал",
          origin: "attachment",
        },
        {
          id: "b",
          file_id: "b",
          title: "",
          filename: "report.pdf",
          origin: "paper",
          paper_id: ids.paper,
        },
        { id: "c", file_id: "c", title: "", origin: "attachment" },
      ],
      limit: 5,
      used_slots: 3,
      reserved_slots: 1,
      can_upload: false,
    }),
  );
  await user.click(button("Повторить"));
  expect(await screen.findByText("Названный материал")).toBeVisible();
  expect(screen.getByText("report.pdf")).toBeVisible();
  expect(screen.getByText("Материал проекта")).toBeVisible();
  expect(screen.getByText(/Загружается: 1/)).toBeVisible();
  expect(screen.getAllByRole("button", { name: "Удалить файл" })).toHaveLength(
    2,
  );
  expect(
    screen.getByRole("link", { name: "К научной работе" }),
  ).toHaveAttribute("href", `/papers/${ids.paper}`);
  await user.click(screen.getAllByRole("button", { name: "Удалить файл" })[0]);
  await waitFor(() =>
    expect(fetch).toHaveBeenCalledWith(
      `/api/v1/projects/${ids.project}/files/a`,
      expect.objectContaining({ method: "DELETE" }),
    ),
  );
});
it.each([false, true])(
  "attaches a project PDF with an existing reservation: %s",
  async (reserved) => {
    if (!reserved) {
      h.routes.set("POST /media/uploads", () =>
        response({
          upload_id: "upload",
          file_id: "file",
          reservation_id: null,
          upload_method: "PUT",
          upload_url: "/test-s3/file",
          required_headers: {},
        }),
      );
      h.routes.set("POST /media/uploads/upload/complete", () => response({}));
      h.routes.set("GET /media/uploads/upload", () =>
        response({ status: "ready" }),
      );
      h.routes.set(`POST /projects/${ids.project}/file-reservations`, () =>
        response({ id: "reservation" }),
      );
    }
    const { user } = await h.mount(`/projects/${ids.project}`, "owner");
    await user.upload(await screen.findByLabelText(/^PDF-файл/), pdf());
    await waitFor(() =>
      expect(h.fixture.projects.get(ids.project)!.files).toHaveLength(1),
    );
    expect(h.fixture.projects.get(ids.project)!.files[0]).toMatchObject({
      title: "Материал проекта",
    });
  },
);
it("rejects an unsafe signed file URL and requests a fresh URL after following a valid link", async () => {
  h.routes.set(`GET /papers/${ids.paper}/file-url`, () =>
    response({ url: "javascript:alert(1)" }),
  );
  const { user } = await h.mount(`/papers/${ids.paper}`);
  await user.click(button("Подготовить: открыть pdf"));
  expect(await screen.findByRole("alert")).toBeVisible();
  expect(
    screen.queryByRole("link", { name: "Открыть PDF" }),
  ).not.toBeInTheDocument();
  h.routes.delete(`GET /papers/${ids.paper}/file-url`);
  await user.click(button("Подготовить: открыть pdf"));
  await user.click(await screen.findByRole("link", { name: "Открыть PDF" }));
  expect(
    await screen.findByRole("button", { name: "Подготовить: открыть pdf" }),
  ).toBeVisible();
});
it("serializes an optional deadline, courses and cleared recommendation criteria", async () => {
  const p = h.fixture.projects.get(ids.project)!;
  Object.assign(p, {
    deadline: null,
    min_completed_projects: 2,
    preferred_specialty_ids: [ids.specialty],
  });
  const { user } = await h.mount(`/projects/${ids.project}/edit`, "owner");
  await screen.findByRole("option", { name: "МГУ" });
  await user.selectOptions(input("Университет"), "");
  change("Дедлайн", "2027-03-01T12:00");
  await user.click(screen.getByText("Дополнительные критерии рекомендаций"));
  change("Минимум завершённых проектов", "");
  fireEvent.change(screen.getByLabelText(/^Курсы через запятую/), {
    target: { value: "" },
  });
  await user.click(input("Разработчик"));
  await user.click(button("Сохранить изменения"));
  await screen.findByRole("heading", { name: p.title });
  expect(h.fixture.projects.get(ids.project)).toMatchObject({
    university_id: null,
    min_completed_projects: null,
    eligible_courses: [],
    deadline: new Date("2027-03-01T12:00").toISOString(),
  });
});
it("shows manager fetch errors instead of controls", async () => {
  h.routes.set(`GET /projects/${ids.project}`, () =>
    response({ error: { message: "Управление недоступно" } }, 503),
  );
  await h.mount(`/projects/${ids.project}/manage`, "owner", false);
  expect(await screen.findByText("Управление недоступно")).toBeVisible();
});
it("does not offer publication actions for an archived project", async () => {
  h.fixture.projects.get(ids.project)!.status = "archived";
  await h.mount(`/projects/${ids.project}/manage`, "owner");
  expect(
    screen.queryByRole("button", { name: "В архив" }),
  ).not.toBeInTheDocument();
});
it("renders missing publication metadata and filters unsafe external URLs", async () => {
  const p = h.fixture.papers.get(ids.paper)!;
  Object.assign(p, {
    file_id: null,
    abstract: "",
    keywords: [],
    project_id: ids.project,
    external_links: ["https://doi.org/10.1234/test", "javascript:alert(1)"],
  });
  p.authors.push({
    ...p.authors[0],
    author_id: "external",
    user_id: null,
    display_name: "Внешний Автор",
  });
  await h.mount(`/papers/${ids.paper}`);
  expect(screen.getByText("PDF ещё не загружен.")).toBeVisible();
  expect(screen.getByText("Аннотация пока не добавлена.")).toBeVisible();
  expect(screen.getByText("Не указаны.")).toBeVisible();
  expect(
    screen.getByRole("link", { name: "Связанный проект" }),
  ).toHaveAttribute("href", `/projects/${ids.project}`);
  expect(
    screen.getByRole("link", { name: "https://doi.org/10.1234/test" }),
  ).toBeVisible();
  expect(screen.queryByText("javascript:alert(1)")).not.toBeInTheDocument();
  expect(screen.getByText("Внешний Автор")).toBeVisible();
});
it("confirms publication visibility expansion when unlinking a project", async () => {
  const p = h.fixture.papers.get(ids.paper)!;
  p.project_id = ids.private;
  p.university_id = null;
  const { user } = await h.mount(`/papers/${ids.paper}/edit`, "owner");
  await screen.findByRole("option", { name: "МГУ" });
  await user.selectOptions(input("Университет"), ids.university);
  await user.selectOptions(input("Университет"), "");
  await user.click(screen.getByText("Связь с проектом и внешние ссылки"));
  fireEvent.change(screen.getByLabelText(/^ID проекта/), {
    target: { value: "" },
  });
  change("Ключевые слова через запятую", "");
  change("Ссылки, каждая с новой строки", "");
  vi.mocked(window.confirm).mockReturnValueOnce(false);
  await user.click(button("Сохранить описание"));
  expect(h.fixture.papers.get(ids.paper)!.project_id).toBe(ids.private);
  await user.click(button("Сохранить описание"));
  await waitFor(() =>
    expect(h.fixture.papers.get(ids.paper)!.project_id).toBeNull(),
  );
});
it("edits an external coauthor and explicitly grants and revokes account access", async () => {
  const p = h.fixture.papers.get(ids.paper)!;
  p.authors.push({
    ...p.authors[0],
    author_id: "external",
    user_id: null,
    display_name: "Внешний Автор",
    position: 2,
    affiliation: "",
  });
  const { user } = await h.mount(`/papers/${ids.paper}/edit`, "owner");
  let author = (await screen.findByText("Внешний Автор")).closest("article")!;
  await user.click(within(author).getByText("Изменить соавтора"));
  fireEvent.change(within(author).getByLabelText("ФИО"), {
    target: { value: "Изменённый Автор" },
  });
  fireEvent.change(within(author).getByLabelText("Порядок в списке"), {
    target: { value: "3" },
  });
  await user.click(within(author).getByText("Сохранить соавтора"));
  await screen.findByText("Изменённый Автор");
  author = screen.getByText("Изменённый Автор").closest("article")!;
  await user.click(within(author).getByText("Изменить соавтора"));
  const grant = within(author).getByLabelText("ID аккаунта для выдачи доступа");
  fireEvent.change(grant, { target: { value: ids.student } });
  fireEvent.submit(grant.closest("form")!);
  await user.click(within(author).getByText("Выдать доступ соавтору"));
  await waitFor(() =>
    expect(h.fixture.papers.get(ids.paper)!.authors[1].user_id).toBe(
      ids.student,
    ),
  );
  await user.click(await screen.findByText("Отозвать доступ"));
  await waitFor(() =>
    expect(h.fixture.papers.get(ids.paper)!.authors[1].user_id).toBeNull(),
  );
});
it.each(["projects", "papers"])(
  "normalizes empty %s metadata and displays server validation errors",
  async (type) => {
    const id = type === "projects" ? ids.project : ids.paper;
    const { user } = await h.mount(`/${type}/${id}/edit`, "owner");
    await screen.findByRole("option", { name: "Информатика" });
    await user.selectOptions(input("Направление"), "");
    if (type === "papers") change("Год", "");
    h.routes.set(`PATCH /${type}/${id}`, (_url, options) => {
      const data = JSON.parse(String(options.body));
      expect(data.area_id).toBeNull();
      if (type === "papers") expect(data.year).toBeNull();
      return response({ error: { message: "Укажите направление и год" } }, 422);
    });
    fireEvent.submit(input("Название").closest("form")!);
    expect(await screen.findByText("Укажите направление и год")).toBeVisible();
  },
);
it("publishes an existing project draft from its management page", async () => {
  h.fixture.projects.get(ids.project)!.status = "draft";
  const { user } = await h.mount(`/projects/${ids.project}/manage`, "owner");
  await user.click(button("Опубликовать проект"));
  expect(
    await screen.findByRole("button", { name: "Начать исследование" }),
  ).toBeVisible();
});
