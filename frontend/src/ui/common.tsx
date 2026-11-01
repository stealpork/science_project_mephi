import { useEffect, useId, useRef, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type {
  UseInfiniteQueryResult,
  InfiniteData,
} from "@tanstack/react-query";
import { command, errorMessage, ApiError } from "../lib/api";
import { useDictionary } from "../lib/data";
import { uploadFile } from "../lib/upload";
import { useAuth } from "./auth";
import type { FavoriteType, Page, UploadCreated } from "../types";

export const labels: Record<string, string> = {
  draft: "Черновик",
  recruiting: "Набор команды",
  active: "В работе",
  completed: "Завершён",
  archived: "В архиве",
  published: "Опубликована",
  public_open: "Открытый набор",
  public_invite: "Публичный, по приглашениям",
  private_invite: "Закрытый, по приглашениям",
  pending: "На рассмотрении",
  accepted: "Принято",
  rejected: "Отклонено",
  declined: "Отклонено",
  withdrawn: "Отозвано",
  revoked: "Отозвано",
  expired: "Истекло",
  owner: "Руководитель",
  leader: "Руководитель",
  researcher: "Исследователь",
  analyst: "Аналитик",
  developer: "Разработчик",
  designer: "Дизайнер",
  author: "Автор",
  editor: "Редактор",
  visible: "Виден",
  hidden: "Скрыт",
  blocked: "Заблокирован",
  open: "Открыта",
  in_review: "В работе",
  resolved: "Решена",
  dismissed: "Отклонена",
};
export const label = (value: string) => labels[value] ?? value;
export const formText = (form: FormData, name: string) =>
  String(form.get(name) ?? "");
export function Heading({
  title,
  children,
}: {
  title: string;
  children?: ReactNode;
}) {
  return (
    <header className="page-header">
      <h1>{title}</h1>
      {children}
    </header>
  );
}
export function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: ReactNode;
  hint?: string;
}) {
  const id = useId();
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint && <small id={id}>{hint}</small>}
    </label>
  );
}
export function ErrorNotice({
  error,
  retry,
}: {
  error: unknown;
  retry?: () => void;
}) {
  if (!error) return null;
  return (
    <div className="notice error" role="alert">
      <p>{errorMessage(error)}</p>
      {error instanceof ApiError && error.fields.length > 0 && (
        <ul>
          {error.fields.map((field, index) => (
            <li key={index}>
              {(
                {
                  email: "Email",
                  password: "Пароль",
                  first_name: "Имя",
                  last_name: "Фамилия",
                  title: "Название",
                  year: "Год",
                  course: "Курс",
                } as Record<string, string>
              )[field.path] ?? field.path}
              : {field.message}
            </li>
          ))}
        </ul>
      )}
      {error instanceof ApiError && error.requestId && (
        <small>Код обращения: {error.requestId}</small>
      )}
      {retry && (
        <button className="text-button" onClick={retry}>
          Повторить
        </button>
      )}
    </div>
  );
}
export function Loading({ text = "Загрузка…" }: { text?: string }) {
  return (
    <p role="status" className="loading">
      {text}
    </p>
  );
}
export function Empty({ text = "Пока ничего нет." }: { text?: string }) {
  return <p className="empty-state">{text}</p>;
}
export function ListView<T>({
  result,
  children,
  empty,
}: {
  result: UseInfiniteQueryResult<InfiniteData<Page<T>, unknown>, Error>;
  children: (item: T, index: number) => ReactNode;
  empty?: string;
}) {
  if (result.isPending) return <Loading />;
  const items = result.data?.pages.flatMap((page) => page.items) ?? [];
  return (
    <>
      <ErrorNotice
        error={result.error}
        retry={() => {
          void result.refetch();
        }}
      />
      {!items.length && !result.error && <Empty text={empty} />}
      {items.map(children)}
      {result.hasNextPage && (
        <button
          className="button load-more"
          disabled={result.isFetchingNextPage}
          onClick={() => {
            void result.fetchNextPage();
          }}
        >
          {result.isFetchingNextPage ? "Загрузка…" : "Показать ещё"}
        </button>
      )}
    </>
  );
}
export function DictSelect({
  kind,
  value,
  onChange,
  multiple = false,
  parent,
  required = false,
}: {
  kind: string;
  value: string | string[];
  onChange: (value: string[]) => void;
  multiple?: boolean;
  parent?: string | null;
  required?: boolean;
}) {
  const dictionary = useDictionary(kind, parent);
  return (
    <>
      <select
        value={value}
        multiple={multiple}
        required={required}
        disabled={dictionary.isPending || !!dictionary.error}
        size={multiple ? 4 : undefined}
        onChange={(event) =>
          onChange(
            Array.from(
              event.target.selectedOptions,
              (option) => option.value,
            ).filter(Boolean),
          )
        }
      >
        {!multiple && (
          <option value="">
            {dictionary.isPending ? "Загрузка…" : "Не выбрано"}
          </option>
        )}
        {dictionary.data?.map((entry) => (
          <option key={entry.id} value={entry.id}>
            {entry.name}
          </option>
        ))}
      </select>
      {dictionary.error && (
        <span className="inline-error">
          Справочник недоступен.{" "}
          <button
            type="button"
            className="text-button"
            onClick={(event) => {
              event.preventDefault();
              void dictionary.refetch();
            }}
          >
            Повторить
          </button>
        </span>
      )}
    </>
  );
}
export function DictName({ kind, id }: { kind: string; id: string | null }) {
  const dict = useDictionary(kind);
  if (!id) return null;
  return (
    <>
      {dict.data?.find((entry) => entry.id === id)?.name ??
        (dict.isPending ? "…" : "Не указано")}
    </>
  );
}
export function DictTags({ ids, kind }: { ids: string[]; kind: string }) {
  const dict = useDictionary(kind);
  return (
    <div className="tags">
      {ids.map((id) => (
        <span className="tag" key={id}>
          {dict.data?.find((entry) => entry.id === id)?.name ?? "…"}
        </span>
      ))}
    </div>
  );
}
export function useCommand<T = unknown>(path: string, method = "POST") {
  const client = useQueryClient();
  const attempt = useRef({ signature: "", key: crypto.randomUUID() });
  return useMutation({
    mutationFn: (body: unknown = undefined) => {
      const signature = `${path}:${method}:${JSON.stringify(body)}`;
      if (signature !== attempt.current.signature)
        attempt.current = { signature, key: crypto.randomUUID() };
      return command<T>(path, body, method, attempt.current.key);
    },
    onSuccess: () => {
      attempt.current.signature = "";
      void client.invalidateQueries();
    },
  });
}
export function CommandButton({
  path,
  body,
  method,
  children,
  confirm,
  onDone,
  className = "button",
}: {
  path: string;
  body?: unknown;
  method?: string;
  children: ReactNode;
  confirm?: string;
  onDone?: () => void;
  className?: string;
}) {
  const mutation = useCommand(path, method);
  return (
    <span className="command">
      <button
        type="button"
        className={className}
        disabled={mutation.isPending}
        onClick={() => {
          if (!confirm || window.confirm(confirm))
            mutation.mutate(body, { onSuccess: onDone });
        }}
      >
        {mutation.isPending ? "Выполняется…" : children}
      </button>
      <ErrorNotice error={mutation.error} />
    </span>
  );
}
export function FavoriteButton({
  type,
  id,
  saved,
}: {
  type: FavoriteType;
  id: string;
  saved: boolean | null;
}) {
  const { account } = useAuth();
  const navigate = useNavigate();
  const mutation = useCommand(
    `/favorites/${type}/${id}`,
    saved ? "DELETE" : "PUT",
  );
  return (
    <span className="favorite-control">
      <button
        className={`icon-button ${saved ? "saved" : ""}`}
        disabled={mutation.isPending || (saved === null && !!account)}
        aria-label={saved ? "Удалить из избранного" : "Сохранить в избранное"}
        aria-pressed={!!saved}
        onClick={() => {
          if (!account)
            navigate(
              `/login?next=${encodeURIComponent(window.location.pathname)}`,
            );
          else mutation.mutate(undefined);
        }}
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d="M6 4h12v17l-6-4-6 4z" />
        </svg>
      </button>
      <ErrorNotice error={mutation.error} />
    </span>
  );
}
export function Modal({
  title,
  children,
  close,
}: {
  title: string;
  children: ReactNode;
  close: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const id = useId();
  useEffect(() => {
    const dialog = ref.current!;
    dialog.showModal();
    return () => dialog.close();
  }, []);
  return (
    <dialog
      ref={ref}
      aria-labelledby={id}
      onCancel={(event) => {
        event.preventDefault();
        close();
      }}
    >
      <div className="modal-top">
        <button
          type="button"
          className="icon-button"
          aria-label="Закрыть"
          onClick={close}
        >
          ×
        </button>
      </div>
      <h2 id={id}>{title}</h2>
      {children}
    </dialog>
  );
}
export function RoleSelect({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <select value={value} onChange={(event) => onChange(event.target.value)}>
      {[
        "researcher",
        "analyst",
        "developer",
        "designer",
        "author",
        "editor",
      ].map((role) => (
        <option key={role} value={role}>
          {label(role)}
        </option>
      ))}
    </select>
  );
}
export function ReportButton({ type, id }: { type: FavoriteType; id: string }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("other");
  const [text, setText] = useState("");
  const mutation = useCommand("/reports");
  const { account } = useAuth();
  return (
    account && (
      <>
        <button
          type="button"
          className="text-button secondary"
          onClick={() => setOpen(true)}
        >
          Пожаловаться
        </button>
        {open && (
          <Modal title="Жалоба" close={() => setOpen(false)}>
            <form
              onSubmit={(event) => {
                event.preventDefault();
                mutation.mutate(
                  {
                    target_type: type,
                    target_id: id,
                    reason_code: reason,
                    description: text,
                  },
                  { onSuccess: () => setOpen(false) },
                );
              }}
            >
              <Field label="Причина">
                <select
                  value={reason}
                  onChange={(event) => setReason(event.target.value)}
                >
                  <option value="other">Другое</option>
                  <option value="spam">Спам</option>
                  <option value="abuse">Оскорбления</option>
                  <option value="plagiarism">Плагиат</option>
                </select>
              </Field>
              <Field label="Описание">
                <textarea
                  required
                  minLength={10}
                  maxLength={5000}
                  value={text}
                  onChange={(event) => setText(event.target.value)}
                />
              </Field>
              <ErrorNotice error={mutation.error} />
              <button className="button" disabled={mutation.isPending}>
                Отправить жалобу
              </button>
            </form>
          </Modal>
        )}
      </>
    )
  );
}
export function FileInput({
  purpose,
  resourceId,
  replacesFileId,
  onUploaded,
}: {
  purpose: "paper_pdf" | "project_pdf" | "avatar";
  resourceId: string;
  replacesFileId?: string | null;
  onUploaded: (upload: UploadCreated) => Promise<void>;
}) {
  const controller = useRef<AbortController | null>(null);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState("");
  const [failure, setFailure] = useState("");
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => () => controller.current?.abort(), []);
  async function handle(file: File | undefined) {
    if (!file || busy) return;
    setBusy(true);
    setFailure("");
    controller.current = new AbortController();
    try {
      const result = await uploadFile(
        file,
        purpose,
        resourceId,
        controller.current.signal,
        setProgress,
        replacesFileId,
      );
      await onUploaded(result);
    } catch (error) {
      setProgress("");
      setFailure(
        error instanceof ApiError
          ? errorMessage(error)
          : error instanceof Error
            ? error.message
            : "Не удалось загрузить файл.",
      );
    } finally {
      setBusy(false);
      if (input.current) input.current.value = "";
    }
  }
  return (
    <div className="file-upload">
      <Field
        label={purpose === "avatar" ? "Фотография" : "PDF-файл"}
        hint={
          purpose === "avatar"
            ? "JPEG, PNG или WebP, до 5 МБ."
            : "До 25 МБ. Файл появится после проверки безопасности."
        }
      >
        <input
          ref={input}
          type="file"
          accept={
            purpose === "avatar"
              ? "image/jpeg,image/png,image/webp"
              : "application/pdf"
          }
          disabled={busy}
          onChange={(event) => {
            void handle(event.target.files?.[0]);
          }}
        />
      </Field>
      {progress && <p role="status">{progress}</p>}
      {failure && (
        <p role="alert" className="notice error">
          {failure}
        </p>
      )}
      {busy && (
        <button
          type="button"
          className="text-button"
          onClick={() => controller.current?.abort()}
        >
          Отменить загрузку
        </button>
      )}
    </div>
  );
}
export function FormActions({
  busy,
  error,
  text = "Сохранить",
  back,
}: {
  busy: boolean;
  error: unknown;
  text?: string;
  back?: string;
}) {
  return (
    <>
      <ErrorNotice error={error} />
      <div className="form-footer">
        {back && (
          <Link className="button" to={back}>
            Отмена
          </Link>
        )}
        <button className="button button-primary" disabled={busy}>
          {busy ? "Сохранение…" : text}
        </button>
      </div>
    </>
  );
}
export const prevent = (fn: () => void) => (event: FormEvent) => {
  event.preventDefault();
  fn();
};
