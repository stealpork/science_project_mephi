import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { webcrypto } from "node:crypto";
import { vi } from "vitest";
import { App } from "../App";
import { AuthProvider } from "../ui/auth";
import { api, session } from "../lib/api";
import type { Tokens } from "../types";
import { fixtureAPI, ids } from "../../tests/fixtures/api.mjs";
export { ids };
export const page = (
  items: unknown[],
  has_more = false,
  next_cursor: string | null = null,
) => ({ items, has_more, next_cursor });
export const response = (body: unknown, status = 200) =>
  new Response(status === 204 ? undefined : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
export function harness() {
  session.clear();
  const fixture = fixtureAPI();
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  const routes = new Map<
    string,
    (url: URL, options: RequestInit) => Response | Promise<Response>
  >();
  vi.stubGlobal("crypto", webcrypto);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, options: RequestInit = {}) => {
      const url = new URL(input, "http://localhost");
      const path = url.pathname.replace(/^\/api\/v1/, "");
      const handler = routes.get(`${options.method ?? "GET"} ${path}`);
      if (handler) return handler(url, options);
      const result = await fixture.handle(
        {
          method: options.method ?? "GET",
          headers: Object.fromEntries(
            Object.entries(options.headers ?? {}).map(([k, v]) => [
              k.toLowerCase(),
              String(v),
            ]),
          ),
        },
        url,
        typeof options.body === "string" ? JSON.parse(options.body) : {},
      );
      return response(result.body, result.status);
    }),
  );
  async function login(role: string) {
    const pair = await api<Tokens>("/auth/login", {
      method: "POST",
      anonymous: true,
      body: { email: `${role}@example.test`, password: "TestPassword123!" },
    });
    session.accept(pair);
  }
  async function mount(path: string, role?: string, waitForHeading = true) {
    if (role) await login(role);
    const view = render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[path]}>
          <AuthProvider>
            <App />
          </AuthProvider>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    if (waitForHeading) await screen.findByRole("heading", { level: 1 });
    return { ...view, user: userEvent.setup() };
  }
  function cleanup() {
    client.clear();
    session.clear();
    vi.unstubAllGlobals();
  }
  async function refresh() {
    await act(async () => {
      await client.invalidateQueries();
    });
  }
  return { fixture, client, routes, mount, login, cleanup, refresh };
}
