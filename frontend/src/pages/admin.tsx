import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, date } from "../lib/api";
import { useList, useResource } from "../lib/data";
import { useAuth } from "../ui/auth";
import {
  ErrorNotice,
  Field,
  FormActions,
  Heading,
  label,
  ListView,
  Loading,
  Modal,
  useCommand,
  formText,
} from "../ui/common";
import type { AdminRecord } from "../types";

const text = (value: unknown) =>
  typeof value === "string" || typeof value === "number" ? String(value) : "";
const tabs = [
  ["users", "Пользователи"],
  ["projects", "Проекты"],
  ["papers", "Работы"],
  ["reports", "Жалобы"],
  ["dictionary-suggestions", "Предложения"],
  ["dictionaries", "Справочники"],
  ["settings", "Настройки"],
  ["audit", "Аудит"],
];
export function AdminPage() {
  const [params, setParams] = useSearchParams();
  const rawTab = params.get("tab") ?? "reports";
  const tab = tabs.some(([id]) => id === rawTab) ? rawTab : "reports";
  const { account } = useAuth();
  const admin = !!account?.global_roles.includes("admin");
  const records = useList<AdminRecord>(
    `/admin/${tab}`,
    { q: params.get("q") },
    !["settings", "dictionaries"].includes(tab),
  );
  const [moderation, setModeration] = useState<{
    id: string;
    title: string;
    operation: string;
  } | null>(null);
  const [actionId, setActionId] = useState("");
  return (
    <>
      <Heading title="Модерация" />
      <nav className="tabs-row" aria-label="Разделы модерации">
        {tabs
          .filter(
            ([key]) => admin || !["settings", "dictionaries"].includes(key),
          )
          .map(([key, name]) => (
            <button
              key={key}
              className={`tab-pill ${tab === key ? "active" : ""}`}
              aria-pressed={tab === key}
              onClick={() => setParams({ tab: key })}
            >
              {name}
            </button>
          ))}
      </nav>
      {actionId && <ActionStatus id={actionId} />}
      {tab === "settings" ? (
        admin ? (
          <AdminSettings />
        ) : (
          <p>Требуются права администратора.</p>
        )
      ) : tab === "dictionaries" ? (
        admin ? (
          <DictionaryAdmin />
        ) : (
          <p>Требуются права администратора.</p>
        )
      ) : (
        <>
          {["users", "projects", "papers"].includes(tab) && (
            <form
              className="catalog-search"
              onSubmit={(event) => {
                event.preventDefault();
                setParams({
                  tab,
                  q: formText(new FormData(event.currentTarget), "q"),
                });
              }}
            >
              <input
                aria-label="Поиск в модерации"
                name="q"
                key={params.get("q")}
                defaultValue={params.get("q") ?? ""}
              />
              <button className="button">Найти</button>
            </form>
          )}
          <ListView result={records}>
            {(entry) => (
              <article className="plain-row" key={entry.id}>
                <h2>
                  {text(entry.title) ||
                    text(entry.email) ||
                    text(entry.name) ||
                    text(entry.action) ||
                    "Запись"}
                </h2>
                {["users", "projects", "papers"].includes(tab) && (
                  <>
                    <p>
                      {label(text(entry.moderation_status ?? entry.status))}
                    </p>
                    <div className="actions">
                      <Link
                        className="section-link"
                        to={
                          tab === "users"
                            ? `/researchers/${entry.id}`
                            : `/${tab}/${entry.id}`
                        }
                      >
                        Открыть
                      </Link>
                      <button
                        className="button"
                        onClick={() =>
                          setModeration({
                            id: entry.id,
                            title: text(entry.title ?? entry.email),
                            operation:
                              tab === "users"
                                ? entry.status === "blocked"
                                  ? "unblock"
                                  : "block"
                                : entry.moderation_status === "hidden"
                                  ? "restore"
                                  : "hide",
                          })
                        }
                      >
                        {tab === "users"
                          ? entry.status === "blocked"
                            ? "Разблокировать"
                            : "Заблокировать"
                          : entry.moderation_status === "hidden"
                            ? "Восстановить"
                            : "Скрыть"}
                      </button>
                    </div>
                  </>
                )}
                {tab === "reports" && <ReportDecision entry={entry} />}
                {tab === "dictionary-suggestions" && (
                  <SuggestionDecision entry={entry} />
                )}
                {tab === "audit" && (
                  <p>
                    {date(entry.created_at)} · {text(entry.target_type)} ·{" "}
                    {text(entry.target_id)} · Инициатор: {text(entry.actor_id)}
                  </p>
                )}
              </article>
            )}
          </ListView>
        </>
      )}
      {moderation && (
        <ModerationForm
          key={`${tab}/${moderation.id}`}
          path={`/admin/${tab}/${moderation.id}/${moderation.operation}`}
          title={moderation.title}
          close={() => setModeration(null)}
          onAction={setActionId}
        />
      )}
    </>
  );
}
function ModerationForm({
  path,
  title,
  close,
  onAction,
}: {
  path: string;
  title: string;
  close: () => void;
  onAction: (id: string) => void;
}) {
  const mutation = useCommand<{
    id?: string;
    action_id?: string;
    status: string;
  }>(path);
  return (
    <Modal title="Решение модератора" close={close}>
      <p>{title}</p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          mutation.mutate(
            { reason: new FormData(event.currentTarget).get("reason") },
            {
              onSuccess: (result) => {
                onAction(result.action_id ?? result.id ?? "");
                close();
              },
            },
          );
        }}
      >
        <Field label="Обоснование">
          <textarea required name="reason" minLength={3} maxLength={2000} />
        </Field>
        <FormActions
          busy={mutation.isPending}
          error={mutation.error}
          text="Подтвердить решение"
        />
      </form>
    </Modal>
  );
}
function ActionStatus({ id }: { id: string }) {
  const result = useQuery({
    queryKey: ["admin-action", id],
    queryFn: ({ signal }) =>
      api<{ status: string; result?: unknown }>(`/admin/actions/${id}`, {
        signal,
      }),
    refetchInterval: (query) =>
      query.state.data?.status === "pending" && query.state.dataUpdateCount < 30
        ? 2000
        : false,
  });
  return (
    <div className="notice" role="status">
      {result.isPending
        ? "Решение принято, проверяем исполнение…"
        : result.data?.status === "succeeded"
          ? "Решение выполнено."
          : result.data?.status === "failed"
            ? "Не удалось выполнить решение. Проверьте аудит."
            : "Решение в очереди. Обновите статус позже."}
      <ErrorNotice error={result.error} />
      <button
        className="text-button"
        onClick={() => {
          void result.refetch();
        }}
      >
        Проверить статус
      </button>
    </div>
  );
}
function ReportDecision({ entry }: { entry: AdminRecord }) {
  const mutation = useCommand(`/admin/reports/${entry.id}`, "PATCH");
  return (
    <>
      <p>
        {text(entry.reason_code)} · {label(text(entry.status))}
      </p>
      <p className="preserve">{text(entry.description)}</p>
      <p className="muted">
        Объект: {text(entry.target_type)} · {text(entry.target_id)}
      </p>
      <form
        className="decision-form"
        onSubmit={(event) => {
          event.preventDefault();
          const form = new FormData(event.currentTarget);
          mutation.mutate({
            version: entry.version,
            status: form.get("status"),
            resolution: form.get("resolution"),
          });
        }}
      >
        <Field label="Статус жалобы">
          <select
            name="status"
            defaultValue={
              entry.status === "open" ? "in_review" : text(entry.status)
            }
          >
            {["in_review", "resolved", "dismissed"].map((status) => (
              <option key={status} value={status}>
                {label(status)}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Решение по жалобе">
          <textarea
            name="resolution"
            required
            minLength={3}
            maxLength={5000}
            defaultValue={text(entry.resolution)}
          />
        </Field>
        <FormActions
          busy={mutation.isPending}
          error={mutation.error}
          text="Сохранить решение"
        />
      </form>
    </>
  );
}
function SuggestionDecision({ entry }: { entry: AdminRecord }) {
  const [decision, setDecision] = useState("approve");
  const mutation = useCommand(
    `/admin/dictionary-suggestions/${entry.id}/${decision}`,
  );
  return (
    <>
      <p>
        {text(entry.kind)} · {label(text(entry.status))}
      </p>
      <p>{text(entry.comment)}</p>
      {entry.status === "pending" && (
        <form
          className="decision-form"
          onSubmit={(event) => {
            event.preventDefault();
            const form = new FormData(event.currentTarget);
            mutation.mutate({
              reason: form.get("reason"),
              ...(decision === "merge"
                ? { target_id: form.get("target") }
                : {}),
            });
          }}
        >
          <Field label="Решение по предложению">
            <select
              value={decision}
              onChange={(event) => setDecision(event.target.value)}
            >
              <option value="approve">Одобрить</option>
              <option value="merge">Объединить</option>
              <option value="reject">Отклонить</option>
            </select>
          </Field>
          {decision === "merge" && (
            <Field label="ID существующего значения">
              <input name="target" required pattern="[0-9a-fA-F-]{36}" />
            </Field>
          )}
          <Field label="Комментарий модератора">
            <textarea name="reason" maxLength={2000} />
          </Field>
          <FormActions
            busy={mutation.isPending}
            error={mutation.error}
            text="Применить решение"
          />
        </form>
      )}
    </>
  );
}
function AdminSettings() {
  const result = useResource<{
    version: number;
    max_files_per_project: number;
  }>("/admin/settings/project-files");
  const mutation = useCommand("/admin/settings/project-files", "PATCH");
  if (result.isPending) return <Loading />;
  if (result.error) return <ErrorNotice error={result.error} />;
  return (
    <form
      className="form-page"
      key={result.data?.version}
      onSubmit={(event) => {
        event.preventDefault();
        const form = new FormData(event.currentTarget);
        mutation.mutate({
          version: result.data?.version,
          max_files_per_project: Number(form.get("limit")),
          reason: form.get("reason"),
        });
      }}
    >
      <h2>Файлы проектов</h2>
      <Field label="Максимум файлов на проект">
        <input
          name="limit"
          type="number"
          required
          min="1"
          max="1000"
          defaultValue={result.data?.max_files_per_project}
        />
      </Field>
      <Field label="Причина изменения">
        <textarea name="reason" required minLength={3} maxLength={2000} />
      </Field>
      <FormActions busy={mutation.isPending} error={mutation.error} />
    </form>
  );
}
function DictionaryAdmin() {
  const [kind, setKind] = useState("skills");
  const mutation = useCommand(`/admin/dictionaries/${kind}`);
  const [created, setCreated] = useState(false);
  const entries = useList<AdminRecord>(`/dictionaries/${kind}`);
  return (
    <>
      <form
        className="form-page"
        onSubmit={(event) => {
          event.preventDefault();
          const form = new FormData(event.currentTarget);
          mutation.mutate(
            {
              name: form.get("name"),
              parent_id: form.get("parent") || null,
              skill_kind:
                kind === "skills" ? form.get("skill_kind") || null : null,
            },
            { onSuccess: () => setCreated(true) },
          );
        }}
      >
        <Field label="Справочник">
          <select
            value={kind}
            onChange={(event) => {
              setKind(event.target.value);
              setCreated(false);
            }}
          >
            {[
              "skills",
              "research_areas",
              "research_interests",
              "universities",
              "faculties",
              "departments",
              "specialties",
              "cities",
            ].map((value) => (
              <option key={value}>{value}</option>
            ))}
          </select>
        </Field>
        <Field label="Название нового значения">
          <input name="name" required minLength={2} maxLength={200} />
        </Field>
        <Field label="ID родительского значения">
          <input name="parent" pattern="[0-9a-fA-F-]{36}" />
        </Field>
        {kind === "skills" && (
          <Field label="Тип навыка">
            <select name="skill_kind">
              <option value="skill">Навык</option>
              <option value="technology">Технология</option>
              <option value="">Не указан</option>
            </select>
          </Field>
        )}
        <FormActions
          busy={mutation.isPending}
          error={mutation.error}
          text="Создать значение"
        />
        {created && <p role="status">Значение создано.</p>}
      </form>
      <ListView result={entries}>
        {(entry) => (
          <DictionaryEntry key={entry.id} entry={entry} kind={kind} />
        )}
      </ListView>
    </>
  );
}
function DictionaryEntry({
  entry,
  kind,
}: {
  entry: AdminRecord;
  kind: string;
}) {
  const mutation = useCommand(
    `/admin/dictionaries/${kind}/${entry.id}`,
    "PATCH",
  );
  return (
    <form
      className="plain-row"
      onSubmit={(event) => {
        event.preventDefault();
        const form = new FormData(event.currentTarget);
        mutation.mutate({
          version: entry.version,
          name: form.get("name"),
          active: form.has("active"),
        });
      }}
    >
      <Field label="Название значения">
        <input
          name="name"
          required
          minLength={2}
          maxLength={200}
          defaultValue={text(entry.name)}
        />
      </Field>
      <small className="break-word">ID: {entry.id}</small>
      <label className="checkbox-label">
        <input name="active" type="checkbox" defaultChecked={!!entry.active} />
        Активно
      </label>
      <FormActions busy={mutation.isPending} error={mutation.error} />
    </form>
  );
}
