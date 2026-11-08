import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  api,
  ApiError,
  command,
  date,
  errorMessage,
  query,
  safeURL,
  session,
} from "./api";
import type { Tokens } from "../types";
export const pair = (access = "access", refresh = "refresh"): Tokens => ({
  access_token: access,
  refresh_token: refresh,
  token_type: "bearer",
  expires_in: 900,
  refresh_expires_in: 86400,
  account: {
    user_id: "user",
    email: "user@example.test",
    email_verified: true,
    global_roles: [],
    account_status: "active",
  },
});
const json = (body: unknown, status = 200) =>
  new Response(body === undefined ? undefined : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
beforeEach(() => {
  session.clear();
  vi.stubGlobal("fetch", vi.fn());
});
describe("query serialization and output safety", () => {
  it("omits absent values, repeats arrays, preserves false and zero", () => {
    expect(query()).toBe("");
    expect(
      query({
        q: "a & б",
        keyword: ["one", "two"],
        empty: "",
        nil: null,
        absent: undefined,
        count: 0,
        flag: false,
      }),
    ).toBe("?q=a+%26+%D0%B1&keyword=one&keyword=two&count=0&flag=false");
  });
  it.each([
    "javascript:alert(1)",
    "data:text/html,test",
    "file:///etc/passwd",
    "http://[invalid",
  ])("rejects unsafe URL %s", (value) => {
    expect(safeURL(value)).toBeUndefined();
  });
  it("permits HTTP(S) and resolves same-origin relative URLs", () => {
    expect(safeURL("https://example.test/paper")).toBe(
      "https://example.test/paper",
    );
    expect(safeURL("/paper")).toMatch(/\/paper$/);
  });
  it("formats dates and empty dates", () => {
    expect(date(null)).toBe("Не указан");
    expect(date(undefined)).toBe("Не указан");
    expect(date("2026-01-15T12:00:00Z")).toContain("2026");
  });
  it("shows conflicts, request errors, cancellation, and network fallback", () => {
    expect(errorMessage(new ApiError(409, "CONFLICT", "Изменено"))).toContain(
      "Обновите",
    );
    expect(errorMessage(new ApiError(403, "FORBIDDEN", "Нет доступа"))).toBe(
      "Нет доступа",
    );
    expect(errorMessage(new DOMException("aborted", "AbortError"))).toBe(
      "Действие отменено.",
    );
    expect(errorMessage(null)).toContain("Нет связи");
  });
});
describe("API transport", () => {
  it("GET sends an Accept header and a cancellation signal without content-type", async () => {
    vi.mocked(fetch).mockResolvedValue(json({ items: [] }));
    const controller = new AbortController();
    expect(await api("/users", { signal: controller.signal })).toEqual({
      items: [],
    });
    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/users",
      expect.objectContaining({
        method: "GET",
        signal: controller.signal,
        headers: { Accept: "application/json" },
      }),
    );
  });
  it("commands serialize a body and preserve an explicit idempotency key", async () => {
    vi.mocked(fetch).mockResolvedValue(json({ id: "created" }));
    await command("/projects", { title: "A" }, "POST", "stable-key");
    expect(fetch).toHaveBeenCalledWith(
      "/api/v1/projects",
      expect.objectContaining({
        body: '{"title":"A"}',
        headers: expect.objectContaining({
          "Idempotency-Key": "stable-key",
          "Content-Type": "application/json",
        }),
      }),
    );
  });
  it("supports bodyless DELETE and no-content responses", async () => {
    vi.mocked(fetch).mockResolvedValue(json(undefined, 204));
    expect(
      await command("/favorites/project/id", undefined, "DELETE"),
    ).toBeUndefined();
  });
  it("attaches access only to authenticated API calls", async () => {
    session.accept(pair());
    vi.mocked(fetch).mockImplementation(async () => json({}));
    await api("/account/me");
    expect(vi.mocked(fetch).mock.calls[0][1]?.headers).toHaveProperty(
      "Authorization",
      "Bearer access",
    );
    await api("/auth/login", { anonymous: true });
    expect(vi.mocked(fetch).mock.calls[1][1]?.headers).not.toHaveProperty(
      "Authorization",
    );
  });
  it("reads structured API errors including request id", async () => {
    vi.mocked(fetch).mockResolvedValue(
      json(
        {
          error: {
            code: "DENIED",
            message: "Нет доступа",
            request_id: "req-123",
          },
        },
        403,
      ),
    );
    await expect(api("/projects")).rejects.toMatchObject({
      status: 403,
      code: "DENIED",
      message: "Нет доступа",
      requestId: "req-123",
    });
  });
  it("preserves sanitized field validation errors", async () => {
    const fields = [
      { path: "email", code: "value_error", message: "Invalid email address" },
    ];
    vi.mocked(fetch).mockResolvedValue(
      json(
        {
          error: {
            code: "VALIDATION_ERROR",
            message: "Проверьте поля запроса",
            details: { fields },
          },
        },
        422,
      ),
    );
    await expect(api("/auth/register")).rejects.toMatchObject({ fields });
  });
  it.each([413, 502])("handles non-JSON proxy response %d", async (status) => {
    vi.mocked(fetch).mockResolvedValue(
      new Response("<html>upstream</html>", { status }),
    );
    await expect(api("/projects")).rejects.toMatchObject({
      status,
      code: "HTTP_ERROR",
      message:
        status === 413
          ? "Файл или запрос слишком большой."
          : "Сервис временно недоступен. Попробуйте ещё раз.",
    });
  });
  it("handles an empty error object", async () => {
    vi.mocked(fetch).mockResolvedValue(json({}, 500));
    await expect(api("/projects")).rejects.toMatchObject({
      code: "HTTP_ERROR",
    });
  });
  it("does not refresh anonymous login failures", async () => {
    session.accept(pair());
    vi.mocked(fetch).mockResolvedValue(
      json({ error: { message: "Неверный пароль" } }, 401),
    );
    await expect(api("/auth/login", { anonymous: true })).rejects.toThrow(
      "Неверный пароль",
    );
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(session.snapshot()).not.toBeNull();
  });
  it("clears a session on an unauthenticated response without refresh", async () => {
    vi.mocked(fetch).mockResolvedValue(json({}, 401));
    await expect(api("/account/me")).rejects.toBeInstanceOf(ApiError);
    expect(session.snapshot()).toBeNull();
  });
});
describe("JWT session and refresh rotation", () => {
  it("notifies subscribers and stores only the refresh token in sessionStorage", () => {
    const listener = vi.fn();
    const unsubscribe = session.subscribe(listener);
    session.accept(pair());
    expect(session.snapshot()?.user_id).toBe("user");
    expect(sessionStorage.getItem("science.refresh.v1")).toBe("refresh");
    expect(JSON.stringify(sessionStorage)).not.toContain("access");
    session.clear();
    expect(listener).toHaveBeenCalledTimes(2);
    unsubscribe();
    session.clear();
    expect(listener).toHaveBeenCalledTimes(2);
  });
  it("does not request a refresh when no persisted session exists", async () => {
    await session.restore();
    expect(fetch).not.toHaveBeenCalled();
  });
  it("restores a persisted session after page reload", async () => {
    sessionStorage.setItem("science.refresh.v1", "persisted");
    vi.mocked(fetch).mockResolvedValue(json(pair("new-access", "rotated")));
    await session.restore();
    expect(session.snapshot()?.user_id).toBe("user");
    expect(sessionStorage.getItem("science.refresh.v1")).toBe("rotated");
  });
  it("retries once after 401 using a new access token and the original mutation key", async () => {
    session.accept(pair());
    vi.mocked(fetch)
      .mockResolvedValueOnce(json({}, 401))
      .mockResolvedValueOnce(json(pair("new-access", "new-refresh")))
      .mockResolvedValueOnce(json({ id: "result" }));
    expect(
      await command("/projects", { title: "x" }, "POST", "same-key"),
    ).toEqual({ id: "result" });
    expect(vi.mocked(fetch).mock.calls[2][1]?.headers).toMatchObject({
      Authorization: "Bearer new-access",
      "Idempotency-Key": "same-key",
    });
  });
  it("shares one refresh for concurrent expired requests", async () => {
    session.accept(pair());
    let refreshCount = 0;
    vi.mocked(fetch).mockImplementation(async (url, init) => {
      if (url === "/api/v1/auth/refresh") {
        refreshCount++;
        await Promise.resolve();
        return json(pair("renewed"));
      }
      return (init?.headers as Record<string, string>).Authorization ===
        "Bearer renewed"
        ? json({ ok: true })
        : json({}, 401);
    });
    await Promise.all([api("/projects"), api("/papers"), api("/users")]);
    expect(refreshCount).toBe(1);
  });
  it.each([401, 403])(
    "removes invalid refresh credentials on %d",
    async (status) => {
      session.accept(pair());
      vi.mocked(fetch)
        .mockResolvedValueOnce(json({}, 401))
        .mockResolvedValueOnce(json({}, status));
      await expect(api("/account/me")).rejects.toBeInstanceOf(ApiError);
      expect(session.hasRefresh()).toBe(false);
    },
  );
  it("keeps refresh credentials on transient server errors", async () => {
    session.accept(pair());
    vi.mocked(fetch)
      .mockResolvedValueOnce(json({}, 401))
      .mockResolvedValueOnce(json({}, 503));
    await expect(api("/account/me")).rejects.toMatchObject({ status: 503 });
    expect(session.hasRefresh()).toBe(true);
  });
  it("never loops when the retried access token is rejected", async () => {
    session.accept(pair());
    vi.mocked(fetch)
      .mockResolvedValueOnce(json({}, 401))
      .mockResolvedValueOnce(json(pair()))
      .mockResolvedValueOnce(json({}, 401));
    await expect(api("/account/me")).rejects.toBeInstanceOf(ApiError);
    expect(fetch).toHaveBeenCalledTimes(3);
    expect(session.snapshot()).toBeNull();
  });
  it("does not resurrect a session cleared during in-flight refresh", async () => {
    session.accept(pair());
    let resolve!: (response: Response) => void;
    vi.mocked(fetch).mockReturnValue(
      new Promise<Response>((r) => {
        resolve = r;
      }),
    );
    const restoring = session.restore();
    session.clear();
    resolve(json(pair()));
    await restoring;
    expect(session.snapshot()).toBeNull();
    expect(session.hasRefresh()).toBe(false);
  });
  it("retains a newer login when an older refresh is rejected", async () => {
    session.accept(pair());
    let resolve!: (response: Response) => void;
    vi.mocked(fetch).mockReturnValue(
      new Promise<Response>((r) => {
        resolve = r;
      }),
    );
    const restoring = session.restore();
    session.accept(pair("newer", "newer-refresh"));
    resolve(json({}, 401));
    await expect(restoring).rejects.toBeInstanceOf(ApiError);
    expect(session.hasRefresh()).toBe(true);
    expect(sessionStorage.getItem("science.refresh.v1")).toBe("newer-refresh");
  });
  it("works in memory when browser storage is disabled", async () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    session.accept(pair());
    expect(session.hasRefresh()).toBe(true);
    session.clear();
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(session.hasRefresh()).toBe(false);
  });
  it("handles storage disappearing between detection and restoration", async () => {
    vi.spyOn(Storage.prototype, "getItem")
      .mockReturnValueOnce("old")
      .mockReturnValue(null);
    await expect(session.restore()).rejects.toMatchObject({
      code: "AUTH_REQUIRED",
    });
    expect(fetch).not.toHaveBeenCalled();
  });
  it("rejects an old identity response after logout", async () => {
    session.accept(pair());
    let resolve!: (response: Response) => void;
    vi.mocked(fetch).mockReturnValue(
      new Promise<Response>((r) => {
        resolve = r;
      }),
    );
    const pending = api("/account/me");
    session.clear();
    resolve(json({ private: true }));
    await expect(pending).rejects.toMatchObject({ code: "SESSION_CHANGED" });
  });
  it("never replays a command as a different user after refresh", async () => {
    session.accept(pair());
    vi.mocked(fetch)
      .mockResolvedValueOnce(json({}, 401))
      .mockImplementationOnce(async () => {
        session.accept({
          ...pair("other", "other-refresh"),
          account: { ...pair().account, user_id: "other-user" },
        });
        return json(pair());
      });
    await expect(
      command("/projects", { title: "private" }),
    ).rejects.toMatchObject({ code: "SESSION_CHANGED" });
    expect(fetch).toHaveBeenCalledTimes(2);
  });
});
