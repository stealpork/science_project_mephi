import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type {
  InfiniteData,
  UseInfiniteQueryResult,
} from "@tanstack/react-query";
import { MemoryRouter, useLocation } from "react-router-dom";
import type { ReactNode } from "react";
import { ApiError } from "../lib/api";
import { uploadFile } from "../lib/upload";
import { useDictionary } from "../lib/data";
import { useAuth } from "./auth";
import {
  CommandButton,
  DictName,
  DictSelect,
  DictTags,
  ErrorNotice,
  FavoriteButton,
  FileInput,
  FormActions,
  ListView,
  Modal,
  ReportButton,
  label,
  formText,
  prevent,
} from "./common";
import type { Page, UploadCreated } from "../types";

vi.mock("../lib/data", () => ({ useDictionary: vi.fn() }));
vi.mock("../lib/upload", () => ({ uploadFile: vi.fn() }));
vi.mock("./auth", () => ({ useAuth: vi.fn() }));
let client: QueryClient;
const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status });
beforeEach(() => {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  vi.mocked(useDictionary).mockReturnValue({
    data: [],
    isPending: false,
    error: null,
  } as unknown as ReturnType<typeof useDictionary>);
  vi.mocked(useAuth).mockReturnValue({ account: null, ready: true });
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json({})));
});
afterEach(() => {
  client.clear();
  vi.unstubAllGlobals();
});
function Location() {
  return <div data-testid="location">{useLocation().pathname}</div>;
}
function mount(node: ReactNode) {
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        {node}
        <Location />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
function authenticated() {
  vi.mocked(useAuth).mockReturnValue({
    ready: true,
    account: {
      user_id: "user",
      email: "u@example.org",
      email_verified: true,
      global_roles: [],
      account_status: "active",
    },
  });
}
const list = (extra: object) =>
  ({
    isPending: false,
    data: undefined,
    error: null,
    hasNextPage: false,
    refetch: vi.fn(),
    fetchNextPage: vi.fn(),
    ...extra,
  }) as unknown as UseInfiniteQueryResult<
    InfiniteData<Page<string>, unknown>,
    Error
  >;

it("renders validation fields with translated and server-defined names", () => {
  const form = new FormData();
  form.set("q", "данные");
  expect(formText(form, "q")).toBe("данные");
  expect(formText(form, "absent")).toBe("");
  mount(
    <ErrorNotice
      error={
        new ApiError(422, "INVALID", "Проверьте поля", "request", [
          { path: "email", code: "bad", message: "Неверный адрес" },
          { path: "custom", code: "bad", message: "Не подходит" },
        ])
      }
    />,
  );
  expect(screen.getByText("Email: Неверный адрес")).toBeVisible();
  expect(screen.getByText("custom: Не подходит")).toBeVisible();
  expect(label("future-status")).toBe("future-status");
});
it("retries a failed list and fetches the next page without losing its existing rows", async () => {
  const result = list({
    error: new Error("offline"),
    data: {
      pages: [{ items: ["Первая"], has_more: true }],
      pageParams: [null],
    },
    hasNextPage: true,
  });
  const view = mount(
    <ListView result={result}>{(item) => <p key={item}>{item}</p>}</ListView>,
  );
  await userEvent.click(screen.getByText("Повторить"));
  expect(result.refetch).toHaveBeenCalledOnce();
  await userEvent.click(screen.getByText("Показать ещё"));
  expect(result.fetchNextPage).toHaveBeenCalledOnce();
  view.rerender(
    <ListView result={{ ...result, isFetchingNextPage: true }}>
      {(item) => <p key={item}>{item}</p>}
    </ListView>,
  );
  expect(screen.getByRole("button", { name: "Загрузка…" })).toBeDisabled();
});
it("does not show an empty-state message when an initial list request fails", () => {
  mount(
    <ListView result={list({ error: new Error("offline") })}>
      {(item) => item}
    </ListView>,
  );
  expect(screen.getByRole("alert")).toBeVisible();
  expect(screen.queryByText("Пока ничего нет.")).not.toBeInTheDocument();
});
it("allows dictionary retries and distinguishes absent, loading and unknown values", async () => {
  const refetch = vi.fn();
  vi.mocked(useDictionary).mockReturnValue({
    data: undefined,
    isPending: false,
    error: new Error("offline"),
    refetch,
  } as unknown as ReturnType<typeof useDictionary>);
  const view = mount(
    <>
      <DictSelect kind="skills" value="" onChange={vi.fn()} />
      <DictName kind="skills" id={null} />
      <DictName kind="skills" id="unknown" />
      <DictTags ids={["unknown"]} kind="skills" />
    </>,
  );
  await userEvent.click(screen.getByText("Повторить"));
  expect(refetch).toHaveBeenCalledOnce();
  expect(screen.getByText("Не указано")).toBeVisible();
  vi.mocked(useDictionary).mockReturnValue({
    data: undefined,
    isPending: true,
    error: null,
  } as ReturnType<typeof useDictionary>);
  view.rerender(<DictName kind="skills" id="unknown" />);
  expect(screen.getByText("…")).toBeVisible();
});
it("preserves an idempotency key on a failed retry, and supports cancelling a confirmation", async () => {
  const confirm = vi
    .spyOn(window, "confirm")
    .mockReturnValueOnce(false)
    .mockReturnValue(true);
  vi.mocked(fetch)
    .mockResolvedValueOnce(
      json({ error: { message: "Попробуйте снова" } }, 503),
    )
    .mockResolvedValue(json({}));
  const done = vi.fn();
  mount(
    <CommandButton
      path="/test"
      confirm="Продолжить?"
      body={{ value: 1 }}
      onDone={done}
    >
      Команда
    </CommandButton>,
  );
  await userEvent.click(screen.getByText("Команда"));
  expect(confirm).toHaveBeenCalledOnce();
  expect(fetch).not.toHaveBeenCalled();
  await userEvent.click(screen.getByText("Команда"));
  await screen.findByText("Попробуйте снова");
  await userEvent.click(screen.getByText("Команда"));
  await waitFor(() => expect(done).toHaveBeenCalledOnce());
  expect(vi.mocked(fetch).mock.calls[0][1]?.headers).toEqual(
    vi.mocked(fetch).mock.calls[1][1]?.headers,
  );
});
it("disables a command while its response is outstanding", async () => {
  let resolve!: (value: Response) => void;
  vi.mocked(fetch).mockImplementation(
    () =>
      new Promise((r) => {
        resolve = r;
      }),
  );
  mount(<CommandButton path="/test">Команда</CommandButton>);
  await userEvent.click(screen.getByText("Команда"));
  expect(await screen.findByText("Выполняется…")).toBeDisabled();
  await act(async () => resolve(json({})));
});
it("takes a guest to login, and toggles saved resources for an authenticated user", async () => {
  const view = mount(
    <FavoriteButton type="project" id="project" saved={false} />,
  );
  await userEvent.click(screen.getByLabelText("Сохранить в избранное"));
  expect(screen.getByText("/login")).toBeVisible();
  authenticated();
  view.rerender(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <FavoriteButton type="paper" id="paper" saved={null} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  expect(screen.getByRole("button")).toBeDisabled();
  view.rerender(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <FavoriteButton type="paper" id="paper" saved={true} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await userEvent.click(screen.getByLabelText("Удалить из избранного"));
  await waitFor(() =>
    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/favorites/paper/paper",
      expect.objectContaining({ method: "DELETE" }),
    ),
  );
  view.rerender(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <FavoriteButton type="paper" id="paper" saved={false} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await userEvent.click(screen.getByLabelText("Сохранить в избранное"));
  await waitFor(() =>
    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/favorites/paper/paper",
      expect.objectContaining({ method: "PUT" }),
    ),
  );
});
it("intercepts dialog Escape and closes a report without submitting it", async () => {
  const close = vi.fn();
  const view = mount(
    <Modal title="Диалог" close={close}>
      Текст
    </Modal>,
  );
  fireEvent(
    screen.getByRole("dialog"),
    new Event("cancel", { cancelable: true }),
  );
  expect(close).toHaveBeenCalledOnce();
  view.unmount();
  authenticated();
  mount(<ReportButton type="project" id="project" />);
  await userEvent.click(screen.getByText("Пожаловаться"));
  await userEvent.click(screen.getByLabelText("Закрыть"));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(fetch).not.toHaveBeenCalled();
});
it.each([
  new ApiError(400, "BAD", "Файл отклонён"),
  new Error("Неверный PDF"),
  "unknown",
])("shows safe upload errors: %s", async (error) => {
  vi.mocked(uploadFile).mockRejectedValue(error);
  mount(<FileInput purpose="avatar" resourceId="user" onUploaded={vi.fn()} />);
  fireEvent.change(screen.getByLabelText(/^Фотография/), {
    target: { files: [new File(["data"], "photo.png", { type: "image/png" })] },
  });
  expect(await screen.findByRole("alert")).toHaveTextContent(
    error instanceof Error ? error.message : "Не удалось загрузить файл.",
  );
  expect(screen.getByLabelText(/^Фотография/)).not.toBeDisabled();
});
it("ignores empty and duplicate selections, exposes progress and cancels an outstanding upload", async () => {
  let reject!: (error: unknown) => void;
  vi.mocked(uploadFile).mockImplementation(
    async (_file, _purpose, _resource, signal, progress) => {
      progress?.("Проверка файла");
      return new Promise((_resolve, r) => {
        reject = r;
        signal.addEventListener("abort", () => r(new Error("Отменено")));
      });
    },
  );
  mount(
    <FileInput
      purpose="project_pdf"
      resourceId="project"
      onUploaded={vi.fn()}
    />,
  );
  const input = screen.getByLabelText(/^PDF-файл/);
  fireEvent.change(input, { target: { files: null } });
  fireEvent.change(input, { target: { files: [] } });
  expect(uploadFile).not.toHaveBeenCalled();
  const files = [new File(["pdf"], "paper.pdf", { type: "application/pdf" })];
  fireEvent.change(input, { target: { files } });
  expect(await screen.findByRole("status")).toHaveTextContent("Проверка файла");
  fireEvent.change(input, { target: { files } });
  expect(uploadFile).toHaveBeenCalledOnce();
  await userEvent.click(screen.getByText("Отменить загрузку"));
  await screen.findByText("Отменено");
  expect(reject).toBeTypeOf("function");
});
it("aborts on unmount and never accesses a detached file input", async () => {
  let resolve!: (upload: UploadCreated) => void;
  let signal!: AbortSignal;
  vi.mocked(uploadFile).mockImplementation(async (_f, _p, _r, s) => {
    signal = s;
    return new Promise((r) => {
      resolve = r;
    });
  });
  const done = vi.fn();
  const view = mount(
    <FileInput purpose="paper_pdf" resourceId="paper" onUploaded={done} />,
  );
  fireEvent.change(screen.getByLabelText(/^PDF-файл/), {
    target: { files: [new File(["pdf"], "paper.pdf")] },
  });
  view.unmount();
  expect(signal.aborted).toBe(true);
  const upload = { upload_id: "upload", file_id: "file" } as UploadCreated;
  await act(async () => resolve(upload));
  expect(done).toHaveBeenCalledWith(upload);
});
it("renders pending form actions and prevents native form submission", () => {
  const callback = vi.fn();
  mount(
    <form onSubmit={prevent(callback)}>
      <FormActions busy={true} error={null} back="/projects" />
    </form>,
  );
  expect(screen.getByText("Сохранение…")).toBeDisabled();
  fireEvent.submit(screen.getByText("Сохранение…").closest("form")!);
  expect(callback).toHaveBeenCalledOnce();
});
