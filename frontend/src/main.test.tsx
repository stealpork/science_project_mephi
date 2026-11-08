import { expect, it, vi } from "vitest";
import type { ReactElement } from "react";
import type { QueryClient } from "@tanstack/react-query";
import { ApiError } from "./lib/api";

const render = vi.hoisted(() => vi.fn());
vi.mock("react-dom/client", () => ({
  default: { createRoot: vi.fn(() => ({ render })) },
}));
it("mounts the application and retries only transient read failures once", async () => {
  document.body.innerHTML = '<div id="root"></div>';
  await import("./main");
  const tree = render.mock.calls[0][0] as ReactElement<{
    children: ReactElement<{ client: QueryClient }>;
  }>;
  const client = tree.props.children.props.client;
  const options = client.getDefaultOptions();
  const retry = options.queries!.retry as (
    count: number,
    error: Error,
  ) => boolean;
  expect(retry(0, new Error("offline"))).toBe(true);
  expect(retry(1, new Error("offline"))).toBe(false);
  expect(retry(0, new ApiError(400, "INVALID", "Invalid"))).toBe(false);
  expect(retry(0, new ApiError(503, "DOWN", "Down"))).toBe(true);
  expect(options.mutations!.retry).toBe(false);
  expect(options.queries!.refetchOnWindowFocus).toBe(false);
  client.clear();
});
