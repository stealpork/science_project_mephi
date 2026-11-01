import type { Account, Tokens } from "../types";

export interface ValidationField {
  path: string;
  code: string;
  message: string;
}
export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public requestId?: string,
    public fields: ValidationField[] = [],
  ) {
    super(message);
    this.name = "ApiError";
  }
}
export type Params = Record<
  string,
  string | number | boolean | null | undefined | string[]
>;
export function query(params: Params = {}) {
  const result = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    for (const item of Array.isArray(value) ? value : [value])
      result.append(key, String(item));
  }
  return result.size ? `?${result}` : "";
}
const STORAGE_KEY = "science.refresh.v1";
let access: string | null = null;
let account: Account | null = null;
let refreshing: Promise<void> | null = null;
let revision = 0;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((listener) => listener());
const storedRefresh = () => {
  try {
    return sessionStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
};
function persist(value: string | null) {
  try {
    if (value) sessionStorage.setItem(STORAGE_KEY, value);
    else sessionStorage.removeItem(STORAGE_KEY);
  } catch {}
}
let memoryRefresh: string | null = null;
export const session = {
  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  },
  snapshot: () => account,
  hasRefresh: () => !!(memoryRefresh ?? storedRefresh()),
  accept(tokens: Tokens) {
    revision++;
    access = tokens.access_token;
    memoryRefresh = tokens.refresh_token;
    account = tokens.account;
    persist(tokens.refresh_token);
    emit();
  },
  clear() {
    revision++;
    access = null;
    memoryRefresh = null;
    account = null;
    persist(null);
    emit();
  },
  async restore() {
    if (session.hasRefresh()) await refresh();
  },
};
async function responseError(response: Response) {
  let body: {
    error?: {
      code?: string;
      message?: string;
      request_id?: string;
      details?: { fields?: ValidationField[] };
    };
  } = {};
  try {
    body = await response.json();
  } catch {}
  return new ApiError(
    response.status,
    body.error?.code ?? "HTTP_ERROR",
    body.error?.message ??
      (response.status === 413
        ? "Файл или запрос слишком большой."
        : "Сервис временно недоступен. Попробуйте ещё раз."),
    body.error?.request_id,
    Array.isArray(body.error?.details?.fields) ? body.error.details.fields : [],
  );
}
async function refresh() {
  if (refreshing) return refreshing;
  const currentRevision = revision;
  refreshing = (async () => {
    const token = memoryRefresh ?? storedRefresh();
    if (!token) throw new ApiError(401, "AUTH_REQUIRED", "Войдите в аккаунт.");
    const response = await fetch("/api/v1/auth/refresh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: token }),
    });
    if (!response.ok) {
      if (
        currentRevision === revision &&
        (response.status === 401 || response.status === 403)
      )
        session.clear();
      throw await responseError(response);
    }
    const tokens: Tokens = await response.json();
    if (currentRevision === revision) session.accept(tokens);
  })().finally(() => {
    refreshing = null;
  });
  return refreshing;
}
interface Options {
  method?: string;
  body?: unknown;
  signal?: AbortSignal;
  idempotencyKey?: string;
  anonymous?: boolean;
}
export async function api<T>(
  path: string,
  options: Options = {},
  retried = false,
): Promise<T> {
  const actor = options.anonymous ? undefined : account?.user_id;
  const headers: Record<string, string> = { Accept: "application/json" };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (access && !options.anonymous) headers.Authorization = `Bearer ${access}`;
  if (options.idempotencyKey)
    headers["Idempotency-Key"] = options.idempotencyKey;
  const response = await fetch(`/api/v1${path}`, {
    method: options.method ?? "GET",
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    signal: options.signal,
  });
  if (actor && actor !== account?.user_id)
    throw new ApiError(
      409,
      "SESSION_CHANGED",
      "Аккаунт изменился во время запроса.",
    );
  if (
    response.status === 401 &&
    !options.anonymous &&
    !retried &&
    session.hasRefresh()
  ) {
    await refresh();
    if (actor && actor !== account?.user_id)
      throw new ApiError(
        409,
        "SESSION_CHANGED",
        "Аккаунт изменился во время запроса.",
      );
    return api<T>(path, options, true);
  }
  if (!response.ok) {
    if (response.status === 401 && !options.anonymous) session.clear();
    throw await responseError(response);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}
export function command<T>(
  path: string,
  body?: unknown,
  method = "POST",
  idempotencyKey: string = crypto.randomUUID(),
) {
  return api<T>(path, { method, body, idempotencyKey });
}
export function errorMessage(error: unknown) {
  if (error instanceof ApiError)
    return error.status === 409
      ? `${error.message} Обновите страницу перед повторным изменением.`
      : error.message;
  return (error instanceof Error || error instanceof DOMException) &&
    error.name === "AbortError"
    ? "Действие отменено."
    : "Нет связи с сервером. Проверьте подключение и повторите.";
}
export function safeURL(value: string): string | undefined {
  try {
    const url = new URL(value, window.location.origin);
    return ["http:", "https:"].includes(url.protocol) ? url.href : undefined;
  } catch {
    return undefined;
  }
}
export function date(value: string | null | undefined) {
  return value
    ? new Intl.DateTimeFormat("ru", { dateStyle: "medium" }).format(
        new Date(value),
      )
    : "Не указан";
}
