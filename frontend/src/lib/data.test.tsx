import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { useDictionary, useList, useResource } from "./data";
let client: QueryClient;
const wrapper = ({ children }: { children: ReactNode }) => (
  <QueryClientProvider client={client}>{children}</QueryClientProvider>
);
beforeEach(() => {
  client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  vi.stubGlobal("fetch", vi.fn());
});
afterEach(() => {
  client.clear();
  vi.unstubAllGlobals();
});
const json = (value: unknown) => new Response(JSON.stringify(value));
it("loads resource data and honors disabled queries", async () => {
  vi.mocked(fetch).mockResolvedValueOnce(json({ title: "resource" }));
  const disabled = renderHook(() => useResource("/unused", false), { wrapper });
  expect(fetch).not.toHaveBeenCalled();
  disabled.unmount();
  const { result } = renderHook(() => useResource("/resource"), { wrapper });
  await waitFor(() =>
    expect(result.current.data).toEqual({ title: "resource" }),
  );
});
it("follows opaque cursors with the original filters", async () => {
  vi.mocked(fetch)
    .mockResolvedValueOnce(
      json({ items: [1], has_more: true, next_cursor: "opaque" }),
    )
    .mockResolvedValueOnce(
      json({ items: [2], has_more: false, next_cursor: null }),
    );
  const { result } = renderHook(
    () => useList<number>("/projects", { q: "topic" }),
    { wrapper },
  );
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(result.current.hasNextPage).toBe(true);
  await result.current.fetchNextPage();
  await waitFor(() => expect(result.current.data?.pages).toHaveLength(2));
  expect(result.current.hasNextPage).toBe(false);
  expect(vi.mocked(fetch).mock.calls[1][0]).toContain(
    "q=topic&limit=20&cursor=opaque",
  );
});
it("does not fetch a disabled list", () => {
  renderHook(() => useList("/private", {}, false), { wrapper });
  expect(fetch).not.toHaveBeenCalled();
});
it.each([
  ["faculties", "university_id"],
  ["departments", "faculty_id"],
  ["skills", null],
])("loads all dictionary pages with parent scoping: %s", async (kind, key) => {
  vi.mocked(fetch)
    .mockResolvedValueOnce(
      json({ items: [{ id: "one" }], has_more: true, next_cursor: "next" }),
    )
    .mockResolvedValueOnce(
      json({ items: [{ id: "two" }], has_more: false, next_cursor: null }),
    );
  const { result } = renderHook(() => useDictionary(kind, "parent"), {
    wrapper,
  });
  await waitFor(() => expect(result.current.data).toHaveLength(2));
  if (key) expect(vi.mocked(fetch).mock.calls[0][0]).toContain(`${key}=parent`);
  else expect(vi.mocked(fetch).mock.calls[0][0]).not.toContain("parent");
});
it("finishes a dictionary with a missing cursor instead of looping", async () => {
  vi.mocked(fetch).mockResolvedValueOnce(
    json({ items: [], has_more: true, next_cursor: null }),
  );
  const { result } = renderHook(() => useDictionary("cities"), { wrapper });
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(fetch).toHaveBeenCalledTimes(1);
});
it("caps a malformed endless cursor sequence", async () => {
  vi.mocked(fetch).mockImplementation(async () =>
    json({ items: [], has_more: true, next_cursor: "same" }),
  );
  const { result } = renderHook(() => useDictionary("cities"), { wrapper });
  await waitFor(() => expect(result.current.isError).toBe(true));
  expect(result.current.error?.message).toContain("слишком большой");
  expect(fetch).toHaveBeenCalledTimes(100);
});
