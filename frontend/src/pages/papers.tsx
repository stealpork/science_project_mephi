import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { command, safeURL } from "../lib/api";
import { useResource } from "../lib/data";
import {
  CommandButton,
  DictName,
  DictSelect,
  ErrorNotice,
  FavoriteButton,
  Field,
  FileInput,
  FormActions,
  Heading,
  label,
  Loading,
  ReportButton,
  useCommand,
  formText,
} from "../ui/common";
import { FileLink } from "./projects";
import type { Author, Paper } from "../types";

export function PaperPage() {
  const { id } = useParams();
  const result = useResource<Paper>(`/papers/${id}`);
  if (result.isPending) return <Loading />;
  if (result.error || !result.data)
    return (
      <ErrorNotice
        error={result.error}
        retry={() => {
          void result.refetch();
        }}
      />
    );
  const paper = result.data;
  return (
    <>
      <Link className="detail-back" to="/papers">
        Все научные работы
      </Link>
      <header className="detail-heading">
        <div className="detail-meta">
          <DictName kind="research_areas" id={paper.area_id} />
          <span>{paper.year}</span>
          <span>{label(paper.status)}</span>
        </div>
        <h1>{paper.title}</h1>
        <div className="detail-meta">
          {paper.authors.map((author) => (
            <span key={author.author_id}>
              {author.user_id ? (
                <Link to={`/researchers/${author.user_id}`}>
                  {author.display_name}
                </Link>
              ) : (
                author.display_name
              )}
            </span>
          ))}
          <FavoriteButton
            type="paper"
            id={paper.id}
            saved={paper.is_favorited}
          />
        </div>
      </header>
      <div className="detail-layout">
        <div className="detail-copy">
          <section>
            <h2>Аннотация</h2>
            <p className="preserve">
              {paper.abstract || "Аннотация пока не добавлена."}
            </p>
          </section>
          <section>
            <h2>Ключевые слова</h2>
            <p>{paper.keywords.join(", ") || "Не указаны."}</p>
          </section>
          {paper.project_id && (
            <p>
              <Link
                className="section-link"
                to={`/projects/${paper.project_id}`}
              >
                Связанный проект
              </Link>
            </p>
          )}
          {paper.external_links.length > 0 && (
            <section>
              <h2>Ссылки</h2>
              {paper.external_links
                .filter((url) => safeURL(url))
                .map((url) => (
                  <p key={url}>
                    <a
                      className="section-link break-word"
                      href={safeURL(url)}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      {url}
                    </a>
                  </p>
                ))}
            </section>
          )}
          <ReportButton type="paper" id={paper.id} />
        </div>
        <aside className="join-card">
          <h2>Материал</h2>
          {paper.file_id ? (
            <div className="stack">
              <FileLink path={`/papers/${paper.id}/file-url`}>
                Открыть PDF
              </FileLink>
              <FileLink path={`/papers/${paper.id}/file-url`} mode="attachment">
                Скачать PDF
              </FileLink>
            </div>
          ) : (
            <p>PDF ещё не загружен.</p>
          )}
          <p>
            {paper.views_count} просмотров · {paper.saves_count} сохранений
          </p>
          {paper.viewer.can_edit && (
            <Link
              className="button button-full"
              to={`/papers/${paper.id}/edit`}
            >
              Редактировать работу
            </Link>
          )}
        </aside>
      </div>
    </>
  );
}
export function PaperFormPage() {
  const { id } = useParams();
  const result = useResource<Paper>(`/papers/${id}`, !!id);
  if (id && result.isPending) return <Loading />;
  if (id && result.error) return <ErrorNotice error={result.error} />;
  if (id && !result.data?.viewer.can_edit)
    return (
      <p role="alert">
        Редактирование доступно только явно добавленным соавторам.
      </p>
    );
  return <PaperEditor key={result.data?.id ?? "new"} paper={result.data} />;
}
function PaperEditor({ paper }: { paper?: Paper }) {
  const navigate = useNavigate();
  const client = useQueryClient();
  const [area, setArea] = useState(paper?.area_id ?? "");
  const [university, setUniversity] = useState(paper?.university_id ?? "");
  const [projectId, setProjectId] = useState(paper?.project_id ?? "");
  const mutation = useCommand<Paper>(
    paper ? `/papers/${paper.id}` : "/papers",
    paper ? "PATCH" : "POST",
  );
  return (
    <section className="form-page">
      <Heading
        title={paper ? "Редактировать работу" : "Добавить научную работу"}
      />
      {!paper && (
        <p className="muted">
          Сохраните описание, затем добавьте PDF и опубликуйте работу.
        </p>
      )}
      <form
        onSubmit={(event) => {
          event.preventDefault();
          const form = new FormData(event.currentTarget);
          const expanded =
            !!paper?.project_id && projectId !== paper.project_id;
          if (
            expanded &&
            !window.confirm(
              "Изменение связи с проектом может расширить доступ к работе. Продолжить?",
            )
          )
            return;
          mutation.mutate(
            {
              title: form.get("title"),
              abstract: form.get("abstract"),
              year: Number(form.get("year")) || null,
              area_id: area || null,
              university_id: university || null,
              keywords: formText(form, "keywords")
                .split(",")
                .map((word) => word.trim())
                .filter(Boolean),
              external_links: formText(form, "links")
                .split("\n")
                .map((url) => url.trim())
                .filter(Boolean),
              project_id: projectId || null,
              ...(paper
                ? {
                    version: paper.version,
                    confirm_visibility_expansion: expanded,
                  }
                : { authors: [] }),
            },
            {
              onSuccess: (result) =>
                navigate(`/papers/${result.id}/edit`, { replace: true }),
            },
          );
        }}
      >
        <Field label="Название">
          <input
            name="title"
            required
            minLength={3}
            maxLength={300}
            defaultValue={paper?.title}
          />
        </Field>
        <Field label="Аннотация">
          <textarea
            name="abstract"
            required
            maxLength={20000}
            defaultValue={paper?.abstract}
          />
        </Field>
        <Field label="Направление">
          <DictSelect
            kind="research_areas"
            value={area}
            onChange={(values) => setArea(values[0] ?? "")}
            required
          />
        </Field>
        <Field label="Университет">
          <DictSelect
            kind="universities"
            value={university}
            onChange={(values) => setUniversity(values[0] ?? "")}
          />
        </Field>
        <Field label="Год">
          <input
            name="year"
            type="number"
            min="1800"
            max="2100"
            required
            defaultValue={paper?.year ?? new Date().getFullYear()}
          />
        </Field>
        <Field label="Ключевые слова через запятую">
          <input
            name="keywords"
            maxLength={3000}
            defaultValue={paper?.keywords.join(", ")}
          />
        </Field>
        <details className="form-section">
          <summary>Связь с проектом и внешние ссылки</summary>
          <Field
            label="ID проекта"
            hint="Оставьте пустым для отдельной работы. Доступ проверяется сервером."
          >
            <input
              value={projectId}
              onChange={(event) => setProjectId(event.target.value)}
              pattern="[0-9a-fA-F-]{36}"
            />
          </Field>
          <Field label="Ссылки, каждая с новой строки">
            <textarea
              name="links"
              defaultValue={paper?.external_links.join("\n")}
              placeholder="https://doi.org/…"
            />
          </Field>
        </details>
        <FormActions
          busy={mutation.isPending}
          error={mutation.error}
          text={paper ? "Сохранить описание" : "Сохранить и добавить PDF"}
          back={paper ? `/papers/${paper.id}` : "/papers"}
        />
      </form>
      {paper && (
        <>
          <section className="form-section">
            <h2>PDF</h2>
            <p className="muted">
              Перед заменой файла сохраните изменения описания.
            </p>
            {paper.file_id && (
              <FileLink path={`/papers/${paper.id}/file-url`}>
                Текущий PDF
              </FileLink>
            )}
            <FileInput
              purpose="paper_pdf"
              resourceId={paper.id}
              replacesFileId={paper.file_id}
              onUploaded={async (upload) => {
                await command(
                  `/papers/${paper.id}`,
                  { version: paper.version, file_id: upload.file_id },
                  "PATCH",
                );
                await client.invalidateQueries();
              }}
            />
          </section>
          {paper.viewer.can_manage_authors && <AuthorsManager paper={paper} />}
          <section className="form-section">
            <h2>Публикация</h2>
            <p className="muted">
              {paper.status === "draft"
                ? "Для публикации нужны аннотация, направление, год и готовый PDF."
                : `Статус: ${label(paper.status)}`}
            </p>
            <div className="actions">
              {paper.status === "draft" && (
                <CommandButton path={`/papers/${paper.id}/publish`}>
                  Опубликовать работу
                </CommandButton>
              )}
              <Link className="button" to={`/papers/${paper.id}`}>
                Открыть страницу работы
              </Link>
              {paper.viewer.is_owner && paper.status !== "archived" && (
                <CommandButton
                  path={`/papers/${paper.id}`}
                  method="DELETE"
                  confirm="Архивировать научную работу?"
                >
                  В архив
                </CommandButton>
              )}
            </div>
          </section>
        </>
      )}
    </section>
  );
}
function AuthorsManager({ paper }: { paper: Paper }) {
  const mutation = useCommand(`/papers/${paper.id}/authors`);
  const [done, setDone] = useState(false);
  return (
    <section className="form-section">
      <h2>Соавторы и доступ</h2>
      <p className="muted">
        Редактировать могут только соавторы, которым явно назначен аккаунт.
        Совпадение ФИО не даёт доступ.
      </p>
      {paper.authors.map((author) => (
        <AuthorControls key={author.author_id} author={author} paper={paper} />
      ))}
      <details>
        <summary>Добавить соавтора</summary>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            const form = new FormData(event.currentTarget);
            mutation.mutate(
              {
                display_name: form.get("name"),
                affiliation: form.get("affiliation"),
                user_id: form.get("user_id") || null,
                position: paper.authors.length + 1,
              },
              { onSuccess: () => setDone(true) },
            );
          }}
        >
          <Field label="ФИО соавтора">
            <input name="name" required minLength={2} maxLength={200} />
          </Field>
          <Field label="Организация">
            <input name="affiliation" maxLength={300} />
          </Field>
          <Field
            label="ID аккаунта соавтора"
            hint="Необязательно. Указание ID даёт этому аккаунту право редактирования."
          >
            <input name="user_id" pattern="[0-9a-fA-F-]{36}" />
          </Field>
          <FormActions
            busy={mutation.isPending}
            error={mutation.error}
            text="Добавить соавтора"
          />
          {done && <p role="status">Соавтор добавлен.</p>}
        </form>
      </details>
    </section>
  );
}
function AuthorControls({ author, paper }: { author: Author; paper: Paper }) {
  const [editing, setEditing] = useState(false);
  const [userId, setUserId] = useState("");
  const [position, setPosition] = useState(author.position);
  const path = `/papers/${paper.id}/authors/${author.author_id}`;
  const mutation = useCommand(path, "PATCH");
  return (
    <article className="plain-row">
      <p>
        {author.display_name} {author.affiliation && `· ${author.affiliation}`}
      </p>
      <p className="muted">
        {author.user_id
          ? `Аккаунт: ${author.user_id}${author.user_id === paper.owner_id ? " · Владелец" : " · Есть доступ к редактированию"}`
          : "Внешний соавтор · Нет доступа к редактированию"}
      </p>
      <button
        type="button"
        className="text-button"
        onClick={() => setEditing((previous) => !previous)}
      >
        {editing ? "Свернуть" : "Изменить соавтора"}
      </button>
      {editing && (
        <>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              const form = new FormData(event.currentTarget);
              mutation.mutate(
                {
                  display_name: form.get("name"),
                  affiliation: form.get("affiliation"),
                  position,
                },
                { onSuccess: () => setEditing(false) },
              );
            }}
          >
            <Field label="ФИО">
              <input
                name="name"
                minLength={2}
                maxLength={200}
                required
                defaultValue={author.display_name}
              />
            </Field>
            <Field label="Организация соавтора">
              <input
                name="affiliation"
                maxLength={300}
                defaultValue={author.affiliation}
              />
            </Field>
            <Field label="Порядок в списке">
              <input
                type="number"
                min="1"
                max="100"
                required
                value={position}
                onChange={(event) => setPosition(Number(event.target.value))}
              />
            </Field>
            <FormActions
              busy={mutation.isPending}
              error={mutation.error}
              text="Сохранить соавтора"
            />
          </form>
          {author.user_id !== paper.owner_id && (
            <div className="stack">
              {author.user_id ? (
                <>
                  <CommandButton
                    path={`${path}/unlink`}
                    confirm="Отозвать доступ к редактированию, сохранив авторство?"
                  >
                    Отозвать доступ
                  </CommandButton>
                  <CommandButton
                    path={`/papers/${paper.id}/transfer-ownership`}
                    body={{ user_id: author.user_id }}
                    confirm="Передать владение работой соавтору?"
                  >
                    Передать владение работой
                  </CommandButton>
                </>
              ) : (
                <form onSubmit={(event) => event.preventDefault()}>
                  <Field label="ID аккаунта для выдачи доступа">
                    <input
                      required
                      pattern="[0-9a-fA-F-]{36}"
                      value={userId}
                      onChange={(event) => setUserId(event.target.value)}
                    />
                  </Field>
                  {/^[0-9a-fA-F-]{36}$/.test(userId) && (
                    <CommandButton
                      path={`${path}/link`}
                      body={{ user_id: userId }}
                      confirm="Дать этому аккаунту право редактирования работы?"
                    >
                      Выдать доступ соавтору
                    </CommandButton>
                  )}
                </form>
              )}
              <CommandButton
                path={path}
                method="DELETE"
                confirm="Удалить соавтора из работы?"
              >
                Удалить соавтора
              </CommandButton>
            </div>
          )}
        </>
      )}
    </article>
  );
}
