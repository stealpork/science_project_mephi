import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { AuthProvider, RequireAuth } from "./auth";
import { ApiError, session } from "../lib/api";
import type { Tokens } from "../types";

let client: QueryClient;
const pair: Tokens = {
  access_token: "access",
  refresh_token: "refresh",
  token_type: "bearer",
  expires_in: 900,
  refresh_expires_in: 3600,
  account: {
    user_id: "u",
    email: "u@example.org",
    email_verified: false,
    global_roles: [],
    account_status: "active",
  },
};
beforeEach(() => {
  session.clear();
  client = new QueryClient();
});
afterEach(() => {
  client.clear();
  session.clear();
  vi.unstubAllGlobals();
});
function mount() {
  return render(
    <QueryClientProvider client={client}>
      <AuthProvider>
        <p>Приложение</p>
      </AuthProvider>
    </QueryClientProvider>,
  );
}
it("holds a protected route until the initial session check finishes", () => {
  render(
    <MemoryRouter>
      <RequireAuth>Приватное</RequireAuth>
    </MemoryRouter>,
  );
  expect(screen.getByRole("status")).toHaveTextContent("Восстановление");
});
it("offers retry and guest mode when session restoration has a temporary failure", async () => {
  session.accept(pair);
  vi.spyOn(session, "restore").mockRejectedValue(
    new ApiError(503, "DOWN", "Сервис недоступен"),
  );
  const reload = vi.fn();
  vi.stubGlobal(
    "window",
    new Proxy(window, {
      get: (target, key) =>
        key === "location" ? { reload } : Reflect.get(target, key),
    }),
  );
  mount();
  await screen.findByRole("alert");
  await userEvent.click(screen.getByText("Повторить"));
  expect(reload).toHaveBeenCalledOnce();
  await userEvent.click(screen.getByText("Продолжить без входа"));
  expect(screen.getByText("Приложение")).toBeVisible();
  expect(session.hasRefresh()).toBe(false);
});
it("does not show a recoverable-session error after credentials were revoked", async () => {
  session.accept(pair);
  vi.spyOn(session, "restore").mockImplementation(async () => {
    session.clear();
    throw new Error("revoked");
  });
  mount();
  await waitFor(() =>
    expect(screen.queryByRole("alert")).not.toBeInTheDocument(),
  );
  expect(screen.getByText("Приложение")).toBeVisible();
});
it.each([false, true])(
  "settles a restore after unmount without rendering stale state (failure=%s)",
  async (failure) => {
    let resolve!: () => void;
    let reject!: (reason: unknown) => void;
    vi.spyOn(session, "restore").mockImplementation(
      () =>
        new Promise((r, j) => {
          resolve = r;
          reject = j;
        }),
    );
    const view = mount();
    view.unmount();
    await act(async () => {
      if (failure) reject(new Error("offline"));
      else resolve();
    });
    expect(screen.queryByText("Приложение")).not.toBeInTheDocument();
  },
);
