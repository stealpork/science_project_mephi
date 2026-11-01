import { useSearchParams, Link } from "react-router-dom";
import { useAuth } from "../ui/auth";
import { useList } from "../lib/data";
import { date } from "../lib/api";
import { CommandButton, Heading, label, ListView } from "../ui/common";
import { PaperRow, PersonRow, ProjectRow } from "./browse";
import type {
  Application,
  Favorite,
  FavoriteType,
  Invitation,
  Notification,
  Paper,
  Profile,
  Project,
} from "../types";

export function WorkspacePage() {
  const { account } = useAuth();
  return (
    <>
      <Heading title="Мой раздел" />
      <div className="workspace-links">
        {[
          ["/me/projects", "Мои проекты"],
          ["/me/teams", "Мои команды"],
          ["/me/papers", "Мои работы"],
          ["/me/applications", "Мои заявки"],
          ["/me/invitations", "Приглашения"],
          ["/favorites", "Избранное"],
          ["/me/profile", "Профиль"],
          ["/notifications", "Уведомления"],
          ["/settings", "Настройки"],
          ...(account?.global_roles.some((role) =>
            ["admin", "moderator"].includes(role),
          )
            ? [["/admin", "Модерация"]]
            : []),
        ].map(([path, name]) => (
          <Link className="workspace-link" key={path} to={path}>
            <strong>{name}</strong>
          </Link>
        ))}
      </div>
      <p className="muted form-section">
        Мессенджер и комментарии запланированы на следующий этап.
      </p>
    </>
  );
}
export function MyListPage({
  type,
}: {
  type: "projects" | "teams" | "papers";
}) {
  const projects = useList<Project>(
    `/me/${type === "teams" ? "teams" : "projects"}`,
    {},
    type !== "papers",
  );
  const papers = useList<Paper>("/me/papers", {}, type === "papers");
  return (
    <>
      <Heading
        title={
          type === "teams"
            ? "Мои команды"
            : type === "projects"
              ? "Мои проекты"
              : "Мои работы"
        }
      >
        <Link
          className="button"
          to={type === "papers" ? "/papers/new" : "/projects/new"}
        >
          {type === "papers" ? "Добавить работу" : "Создать проект"}
        </Link>
      </Heading>
      {type === "papers" ? (
        <ListView result={papers}>
          {(paper) => <PaperRow paper={paper} key={paper.id} />}
        </ListView>
      ) : (
        <ListView result={projects}>
          {(project) => <ProjectRow project={project} key={project.id} />}
        </ListView>
      )}
    </>
  );
}
export function RequestsPage({
  type,
}: {
  type: "applications" | "invitations";
}) {
  const applications = useList<Application>(
    "/me/applications",
    {},
    type === "applications",
  );
  const invitations = useList<Invitation>(
    "/me/invitations",
    {},
    type === "invitations",
  );
  return (
    <>
      <Heading title={type === "applications" ? "Мои заявки" : "Приглашения"} />
      <nav className="tabs-row" aria-label="Мои обращения">
        <Link
          to="/me/applications"
          className={`tab-pill ${type === "applications" ? "active" : ""}`}
        >
          Заявки
        </Link>
        <Link
          to="/me/invitations"
          className={`tab-pill ${type === "invitations" ? "active" : ""}`}
        >
          Приглашения
        </Link>
      </nav>
      {type === "applications" ? (
        <ListView result={applications}>
          {(entry) => (
            <article className="plain-row" key={entry.id}>
              <Link
                className="section-link"
                to={`/projects/${entry.project_id}`}
              >
                Открыть проект
              </Link>
              <p>
                {label(entry.status)} · {label(entry.requested_role)}
              </p>
              <p className="preserve">{entry.message}</p>
              {entry.decision_reason && <p>Ответ: {entry.decision_reason}</p>}
              {entry.status === "pending" && (
                <CommandButton
                  path={`/projects/${entry.project_id}/applications/${entry.id}/withdraw`}
                  body={{ reason: "", role: entry.requested_role }}
                >
                  Отозвать заявку
                </CommandButton>
              )}
            </article>
          )}
        </ListView>
      ) : (
        <ListView result={invitations}>
          {(entry) => (
            <article className="plain-row" key={entry.id}>
              <h2>{entry.project?.title ?? "Проект недоступен"}</h2>
              <p>{entry.project?.summary}</p>
              <p>
                {label(entry.status)} · {label(entry.role)} · До{" "}
                {date(entry.expires_at)}
              </p>
              <p className="preserve">{entry.message}</p>
              <div className="actions">
                {entry.status === "pending" &&
                  new Date(entry.expires_at) > new Date() && (
                    <>
                      <CommandButton
                        path={`/projects/${entry.project_id}/invitations/${entry.id}/accept`}
                      >
                        Принять приглашение
                      </CommandButton>
                      <CommandButton
                        path={`/projects/${entry.project_id}/invitations/${entry.id}/decline`}
                      >
                        Отклонить приглашение
                      </CommandButton>
                    </>
                  )}
                {entry.status === "accepted" && (
                  <Link
                    className="section-link"
                    to={`/projects/${entry.project_id}`}
                  >
                    Открыть проект
                  </Link>
                )}
              </div>
            </article>
          )}
        </ListView>
      )}
    </>
  );
}
export function FavoritesPage() {
  const [params, setParams] = useSearchParams();
  const type: FavoriteType =
    params.get("type") === "user"
      ? "user"
      : params.get("type") === "paper"
        ? "paper"
        : "project";
  const favorites = useList<Favorite>("/favorites", { type });
  return (
    <>
      <Heading title="Избранное" />
      <nav className="tabs-row" aria-label="Тип избранного">
        {[
          ["project", "Проекты"],
          ["user", "Люди"],
          ["paper", "Работы"],
        ].map(([id, name]) => (
          <button
            className={`tab-pill ${type === id ? "active" : ""}`}
            key={id}
            aria-pressed={type === id}
            onClick={() => setParams({ type: id })}
          >
            {name}
          </button>
        ))}
      </nav>
      <ListView result={favorites}>
        {(entry) => (
          <div key={entry.target_id}>
            {entry.availability === "available" && entry.card ? (
              entry.type === "project" ? (
                <ProjectRow
                  project={{ ...(entry.card as Project), is_favorited: true }}
                />
              ) : entry.type === "paper" ? (
                <PaperRow
                  paper={{ ...(entry.card as Paper), is_favorited: true }}
                />
              ) : (
                <PersonRow
                  person={{ ...(entry.card as Profile), is_favorited: true }}
                />
              )
            ) : (
              <article className="plain-row">
                <p>
                  {entry.availability === "temporarily_unavailable"
                    ? "Материал временно недоступен."
                    : "Материал скрыт или больше недоступен."}
                </p>
                <CommandButton
                  path={`/favorites/${entry.type}/${entry.target_id}`}
                  method="DELETE"
                >
                  Убрать из избранного
                </CommandButton>
              </article>
            )}
          </div>
        )}
      </ListView>
    </>
  );
}
export function NotificationsPage() {
  const [params, setParams] = useSearchParams();
  const unread = params.get("unread") === "true";
  const notifications = useList<Notification>("/notifications", {
    unread_only: unread,
  });
  return (
    <>
      <Heading title="Уведомления">
        <CommandButton
          path="/notifications/read-all"
          body={{ before: new Date().toISOString() }}
        >
          Прочитать все
        </CommandButton>
      </Heading>
      <label className="checkbox-label">
        <input
          type="checkbox"
          checked={unread}
          onChange={(event) =>
            setParams(event.target.checked ? { unread: "true" } : {})
          }
        />
        Только непрочитанные
      </label>
      <ListView result={notifications}>
        {(entry) => {
          const path =
            entry.action === "unavailable" ||
            entry.action === "temporarily_unavailable"
              ? undefined
              : entry.target?.type === "invitation"
                ? "/me/invitations"
                : entry.target?.type === "project"
                  ? `/projects/${entry.target.id}`
                  : entry.target?.type === "paper"
                    ? `/papers/${entry.target.id}`
                    : undefined;
          return (
            <article
              className={`notification-row ${entry.read_at ? "" : "unread"}`}
              key={entry.id}
            >
              <div>
                <h2>{entry.title || "Обновление"}</h2>
                <p>{entry.body}</p>
                <small>
                  {date(entry.created_at)}
                  {entry.read_at ? " · Прочитано" : " · Не прочитано"}
                </small>
                <div className="actions">
                  {path && (
                    <Link className="section-link" to={path}>
                      Открыть
                    </Link>
                  )}
                  {!entry.read_at && (
                    <CommandButton
                      className="text-button"
                      path={`/notifications/${entry.id}/read`}
                    >
                      Прочитать
                    </CommandButton>
                  )}
                </div>
              </div>
            </article>
          );
        }}
      </ListView>
    </>
  );
}
