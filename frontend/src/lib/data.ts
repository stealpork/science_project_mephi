import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { api, query } from "./api";
import type { Params } from "./api";
import type { Dictionary, Page } from "../types";

export function useResource<T>(path: string, enabled = true) {
  return useQuery({
    queryKey: [path],
    queryFn: ({ signal }) => api<T>(path, { signal }),
    enabled,
  });
}
export function useList<T>(path: string, params: Params = {}, enabled = true) {
  return useInfiniteQuery({
    queryKey: [path, params],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam, signal }) =>
      api<Page<T>>(
        `${path}${query({ ...params, limit: 20, cursor: pageParam })}`,
        { signal },
      ),
    getNextPageParam: (page) => (page.has_more ? page.next_cursor : undefined),
    enabled,
  });
}
export function useDictionary(kind: string, parent?: string | null) {
  return useQuery({
    queryKey: ["dictionary", kind, parent],
    staleTime: 300_000,
    queryFn: async ({ signal }) => {
      const items: Dictionary[] = [];
      let cursor: string | null = null;
      for (let i = 0; i < 100; i++) {
        const page: Page<Dictionary> = await api(
          `/dictionaries/${kind}${query({
            limit: 100,
            cursor,
            university_id: kind === "faculties" ? parent : undefined,
            faculty_id: kind === "departments" ? parent : undefined,
          })}`,
          { signal },
        );
        items.push(...page.items);
        if (!page.has_more || !page.next_cursor) return items;
        cursor = page.next_cursor;
      }
      throw new Error(
        "Справочник слишком большой. Обратитесь к администратору.",
      );
    },
  });
}
