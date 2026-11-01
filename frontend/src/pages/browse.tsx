import { useState } from "react";
import {
  Link,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import { useAuth } from "../ui/auth";
import {
  DictName,
  DictSelect,
  DictTags,
  Empty,
  ErrorNotice,
  FavoriteButton,
  Field,
  Heading,
  label,
  ListView,
  Loading,
  Modal,
  ReportButton,
  FormActions,
  RoleSelect,
  useCommand,
  formText,
} from "../ui/common";
import { useList, useResource } from "../lib/data";
import { query, safeURL } from "../lib/api";
import type { Paper, Profile, Project, RecommendationPage } from "../types";

export function ProjectRow({
  project,
  match,
}: {
  project: Project;
  match?: { score: number | null; reasons: string[] };
}) {
  return (
    <article className="project-card">
      <div className="project-card-top">
        <span className="tag-label">
          <DictName kind="research_areas" id={project.area_id} />
        </span>
        <span className="status-badge">{label(project.status)}</span>
      </div>
      <h3>
        <Link to={`/projects/${project.id}`}>{project.title}</Link>
      </h3>
      <div className="row-save">
        <FavoriteButton
          type="project"
          id={project.id}
          saved={project.is_favorited}
        />
      </div>
      <p className="project-description">
        {project.summary || project.description.slice(0, 220)}
      </p>
      <DictTags kind="skills" ids={project.required_skill_ids} />
      <div className="project-card-footer">
        <span>
          {project.members_count} из {project.team_size} участников
        </span>
        <span>{label(project.access_type)}</span>
        {match && match.score !== null && (
          <span>{match.score}% совпадения</span>
        )}
        <Link to={`/projects/${project.id}`}>Подробнее</Link>
      </div>
      {match && match.reasons.length > 0 && (
        <p className="match-reason">{match.reasons.join(" · ")}</p>
      )}
    </article>
  );
}
export function PersonRow({ person }: { person: Profile }) {
  return (
    <article className="person-card">
      <div className="avatar">
        {person.first_name[0]}
        {person.last_name[0]}
      </div>
      <div className="person-info">
        <h3>
          <Link to={`/researchers/${person.user_id}`}>
            {person.first_name} {person.last_name}
          </Link>
        </h3>
        <p>
          <DictName kind="universities" id={person.university_id} />
          {person.course ? ` · ${person.course} курс` : ""}
        </p>
        <p>{person.bio}</p>
        <div className="tags">
          {person.skills.map((skill) => (
            <span className="tag" key={skill.id}>
              {skill.name}
            </span>
          ))}
        </div>
      </div>
      <div className="person-save">
        <FavoriteButton
          type="user"
          id={person.user_id}
          saved={person.is_favorited}
        />
      </div>
    </article>
  );
}
export function PaperRow({ paper }: { paper: Paper }) {
  return (
    <article className="paper-row">
      <span className="pdf-icon" aria-hidden="true">
        PDF
      </span>
      <div className="paper-info">
        <h3>
          <Link to={`/papers/${paper.id}`}>{paper.title}</Link>
        </h3>
        <p>
          {paper.authors.map((author) => author.display_name).join(", ")}
          {paper.year ? ` · ${paper.year}` : ""}
        </p>
        <p>
          <DictName kind="research_areas" id={paper.area_id} /> ·{" "}
          {label(paper.status)}
        </p>
        <p className="abstract">{paper.abstract.slice(0, 280)}</p>
      </div>
      <FavoriteButton type="paper" id={paper.id} saved={paper.is_favorited} />
    </article>
  );
}
export function HomePage() {
  const { account } = useAuth();
  const [category, setCategory] = useState("projects");
  const navigate = useNavigate();
  const projects = useResource<RecommendationPage>(
    "/recommendations/projects?limit=3",
    !!account,
  );
  const publicProjects = useList<Project>(
    "/projects",
    { status: "recruiting" },
    !account,
  );
  const papers = useList<Paper>("/papers");
  const people = useList<Profile>("/users");
  return (
    <>
      <Heading title="Обзор" />
      <section className="search-panel" aria-label="Поиск">
        <div className="search-tabs" role="tablist" aria-label="Где искать">
          {[
            ["projects", "Проекты"],
            ["researchers", "Исследователи"],
            ["papers", "Научные работы"],
          ].map(([id, text], index, all) => (
            <button
              key={id}
              id={`search-${id}`}
              role="tab"
              aria-selected={category === id}
              aria-controls="search-panel"
              tabIndex={category === id ? 0 : -1}
              className={`search-tab ${category === id ? "active" : ""}`}
              onClick={() => setCategory(id)}
              onKeyDown={(event) => {
                const next =
                  event.key === "Home"
                    ? 0
                    : event.key === "End"
                      ? all.length - 1
                      : event.key === "ArrowRight"
                        ? (index + 1) % all.length
                        : event.key === "ArrowLeft"
                          ? (index + all.length - 1) % all.length
                          : -1;
                if (next >= 0) {
                  event.preventDefault();
                  setCategory(all[next][0]);
                  document.getElementById(`search-${all[next][0]}`)?.focus();
                }
              }}
            >
              {text}
            </button>
          ))}
        </div>
        <form
          id="search-panel"
          role="tabpanel"
          aria-labelledby={`search-${category}`}
          className="search-box"
          onSubmit={(event) => {
            event.preventDefault();
            const data = new FormData(event.currentTarget);
            navigate(`/${category}${query({ q: formText(data, "q") })}`);
          }}
        >
          <input
            name="q"
            aria-label="Поисковый запрос"
            placeholder="Тема, название или навык"
            maxLength={200}
          />
          <button className="button button-primary">Найти</button>
        </form>
      </section>
      <section>
        <div className="section-heading">
          <h2>{account ? "Подходящие проекты" : "Открытые проекты"}</h2>
          <Link className="section-link" to="/projects">
            Все проекты
          </Link>
        </div>
        <div className="project-grid">
          {account ? (
            <>
              {projects.isPending && <Loading />}
              <ErrorNotice
                error={projects.error}
                retry={() => {
                  void projects.refetch();
                }}
              />
              {projects.data?.items.map((item) => (
                <ProjectRow
                  key={item.project.id}
                  project={item.project}
                  match={{
                    score: item.score,
                    reasons: item.reasons.map((reason) =>
                      typeof reason === "string"
                        ? reason
                        : (reason.text ?? reason.label ?? ""),
                    ),
                  }}
                />
              ))}
              {projects.data && !projects.data.items.length && (
                <Empty text="Подходящих проектов пока нет. Заполните интересы в профиле или откройте каталог." />
              )}
            </>
          ) : (
            <>
              {publicProjects.isPending && <Loading />}
              <ErrorNotice error={publicProjects.error} />
              {publicProjects.data?.pages[0]?.items
                .slice(0, 3)
                .map((project) => (
                  <ProjectRow key={project.id} project={project} />
                ))}
              {publicProjects.data &&
                !publicProjects.data.pages[0]?.items.length && (
                  <Empty text="Пока нет проектов с открытым набором." />
                )}
            </>
          )}
        </div>
      </section>
      <div className="home-secondary">
        <section>
          <div className="section-heading">
            <h2>Новые работы</h2>
            <Link className="section-link" to="/papers">
              Все работы
            </Link>
          </div>
          {papers.isPending && <Loading />}
          <ErrorNotice error={papers.error} />
          {papers.data?.pages[0]?.items.slice(0, 2).map((paper) => (
            <PaperRow key={paper.id} paper={paper} />
          ))}
          {papers.data && !papers.data.pages[0]?.items.length && (
            <Empty text="Работы ещё не опубликованы." />
          )}
        </section>
        <section>
          <div className="section-heading">
            <h2>Исследователи</h2>
            <Link className="section-link" to="/researchers">
              Все
            </Link>
          </div>
          {people.isPending && <Loading />}
          <ErrorNotice error={people.error} />
          {people.data?.pages[0]?.items.slice(0, 2).map((person) => (
            <PersonRow key={person.user_id} person={person} />
          ))}
          {people.data && !people.data.pages[0]?.items.length && <Empty />}
        </section>
      </div>
    </>
  );
}
export function CatalogPage({
  type,
}: {
  type: "users" | "projects" | "papers";
}) {
  const [params, setParams] = useSearchParams();
  const filters = Object.fromEntries(params.entries());
  const projects = useList<Project>("/projects", filters, type === "projects");
  const people = useList<Profile>("/users", filters, type === "users");
  const papers = useList<Paper>("/papers", filters, type === "papers");
  const set = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    next.delete("cursor");
    setParams(next);
  };
  return (
    <>
      <Heading
        title={
          type === "users"
            ? "Исследователи"
            : type === "projects"
              ? "Проекты"
              : "Научные работы"
        }
      >
        {type !== "users" && (
          <Link
            className="button"
            to={type === "projects" ? "/projects/new" : "/papers/new"}
          >
            {type === "projects" ? "Создать проект" : "Добавить работу"}
          </Link>
        )}
      </Heading>
      <form
        className="catalog-search"
        onSubmit={(event) => {
          event.preventDefault();
          set("q", formText(new FormData(event.currentTarget), "q"));
        }}
      >
        <input
          name="q"
          key={params.get("q")}
          defaultValue={params.get("q") ?? ""}
          aria-label="Поиск в каталоге"
          placeholder={
            type === "users" ? "Имя, интересы или опыт" : "Название или тема"
          }
          maxLength={200}
        />
        <button className="button">Найти</button>
      </form>
      <details className="filters">
        <summary>Фильтры</summary>
        <div className="filter-fields">
          <Field label="Направление">
            <DictSelect
              kind="research_areas"
              value={filters.area_id ?? ""}
              onChange={(values) => set("area_id", values[0] ?? "")}
            />
          </Field>
          <Field label="Университет">
            <DictSelect
              kind="universities"
              value={filters.university_id ?? ""}
              onChange={(values) => set("university_id", values[0] ?? "")}
            />
          </Field>
          {type !== "papers" && (
            <Field label="Навык">
              <DictSelect
                kind="skills"
                value={filters.skill_id ?? ""}
                onChange={(values) => set("skill_id", values[0] ?? "")}
              />
            </Field>
          )}
          {type === "projects" && (
            <>
              <Field label="Статус">
                <select
                  value={filters.status ?? ""}
                  onChange={(event) => set("status", event.target.value)}
                >
                  <option value="">Все</option>
                  {["recruiting", "active", "completed"].map((status) => (
                    <option key={status} value={status}>
                      {label(status)}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Команда от">
                <input
                  type="number"
                  min="1"
                  max="100"
                  value={filters.team_size_min ?? ""}
                  onChange={(event) => set("team_size_min", event.target.value)}
                />
              </Field>
              <Field label="Команда до">
                <input
                  type="number"
                  min="1"
                  max="100"
                  value={filters.team_size_max ?? ""}
                  onChange={(event) => set("team_size_max", event.target.value)}
                />
              </Field>
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  checked={filters.has_free_slots === "true"}
                  onChange={(event) =>
                    set("has_free_slots", event.target.checked ? "true" : "")
                  }
                />
                Есть свободные места
              </label>
            </>
          )}
          {type === "users" && (
            <>
              <Field label="Специальность">
                <DictSelect
                  kind="specialties"
                  value={filters.specialty_id ?? ""}
                  onChange={(values) => set("specialty_id", values[0] ?? "")}
                />
              </Field>
              <Field label="Город">
                <DictSelect
                  kind="cities"
                  value={filters.city_id ?? ""}
                  onChange={(values) => set("city_id", values[0] ?? "")}
                />
              </Field>
              <Field label="Курс">
                <input
                  type="number"
                  min="1"
                  max="8"
                  value={filters.course ?? ""}
                  onChange={(event) => set("course", event.target.value)}
                />
              </Field>
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  checked={filters.has_project_experience === "true"}
                  onChange={(event) =>
                    set(
                      "has_project_experience",
                      event.target.checked ? "true" : "",
                    )
                  }
                />
                С опытом проектов
              </label>
            </>
          )}
          {type === "papers" && (
            <>
              <Field label="Год от">
                <input
                  type="number"
                  min="1800"
                  max="2100"
                  value={filters.year_from ?? ""}
                  onChange={(event) => set("year_from", event.target.value)}
                />
              </Field>
              <Field label="Год до">
                <input
                  type="number"
                  min="1800"
                  max="2100"
                  value={filters.year_to ?? ""}
                  onChange={(event) => set("year_to", event.target.value)}
                />
              </Field>
              <Field label="Автор">
                <input
                  value={filters.author_name ?? ""}
                  onChange={(event) => set("author_name", event.target.value)}
                />
              </Field>
              <Field label="Ключевое слово">
                <input
                  value={filters.keyword ?? ""}
                  onChange={(event) => set("keyword", event.target.value)}
                />
              </Field>
            </>
          )}
          <button
            type="button"
            className="text-button"
            onClick={() => setParams({})}
          >
            Сбросить фильтры
          </button>
        </div>
      </details>
      {type !== "users" && (
        <div className="results-toolbar">
          <span>Результаты поиска</span>
          <label className="sort">
            <span className="sr-only">Сортировка</span>
            <select
              value={filters.sort ?? "newest"}
              onChange={(event) => set("sort", event.target.value)}
            >
              <option value="newest">Сначала новые</option>
              {type === "projects" ? (
                <option value="deadline">По дедлайну</option>
              ) : (
                <>
                  <option value="year_desc">По году</option>
                  <option value="most_saved">По сохранениям</option>
                </>
              )}
            </select>
          </label>
        </div>
      )}
      {type === "projects" ? (
        <ListView result={projects} empty="Проекты не найдены.">
          {(project) => <ProjectRow key={project.id} project={project} />}
        </ListView>
      ) : type === "users" ? (
        <ListView result={people} empty="Исследователи не найдены.">
          {(person) => <PersonRow key={person.user_id} person={person} />}
        </ListView>
      ) : (
        <ListView result={papers} empty="Работы не найдены.">
          {(paper) => <PaperRow key={paper.id} paper={paper} />}
        </ListView>
      )}
    </>
  );
}
export function ProfilePage({ own = false }: { own?: boolean }) {
  const { id } = useParams();
  const { account } = useAuth();
  const path = own ? "/me/profile" : `/users/${id}`;
  const result = useResource<Profile | { status: "initializing" }>(path);
  const profile =
    result.data && "user_id" in result.data ? result.data : undefined;
  const projects = useList<Project>(
    "/projects",
    { member_id: profile?.user_id },
    !!profile,
  );
  const papers = useList<Paper>(
    "/papers",
    { author_user_id: profile?.user_id },
    !!profile,
  );
  const avatar = useResource<{ url: string }>(
    `/users/${profile?.user_id}/avatar-url`,
    !!profile?.avatar_file_id,
  );
  const [invite, setInvite] = useState(false);
  if (result.isPending) return <Loading />;
  if (result.error)
    return (
      <ErrorNotice
        error={result.error}
        retry={() => {
          void result.refetch();
        }}
      />
    );
  if (!profile)
    return (
      <>
        <Heading title="Профиль создаётся" />
        <p>
          Регистрация принята. Подготовка профиля обычно занимает несколько
          секунд.
        </p>
        <button
          className="button"
          onClick={() => {
            void result.refetch();
          }}
        >
          Проверить готовность
        </button>
      </>
    );
  return (
    <>
      <div className="profile-hero">
        {avatar.data && safeURL(avatar.data.url) ? (
          <img className="avatar avatar-large" src={avatar.data.url} alt="" />
        ) : (
          <span className="avatar avatar-large">
            {profile.first_name[0]}
            {profile.last_name[0]}
          </span>
        )}
        <div>
          <h1>
            {profile.first_name} {profile.last_name}
          </h1>
          <p>
            <DictName kind="universities" id={profile.university_id} />
            {profile.course ? ` · ${profile.course} курс` : ""}
          </p>
          <p>
            <DictName kind="specialties" id={profile.specialty_id} />
          </p>
        </div>
        <div className="profile-actions">
          {profile.viewer.is_self || account?.user_id === profile.user_id ? (
            <Link className="button" to="/me/profile/edit">
              Редактировать
            </Link>
          ) : (
            <>
              <FavoriteButton
                type="user"
                id={profile.user_id}
                saved={profile.is_favorited}
              />
              {profile.viewer.can_invite && (
                <button className="button" onClick={() => setInvite(true)}>
                  Пригласить в проект
                </button>
              )}
            </>
          )}
        </div>
      </div>
      <div className="profile-body">
        <div className="detail-copy">
          <section>
            <h2>О себе</h2>
            <p className="preserve">
              {profile.bio || "Описание пока не добавлено."}
            </p>
          </section>
          <section>
            <h2>Проекты</h2>
            <ListView result={projects}>
              {(project) => <ProjectRow key={project.id} project={project} />}
            </ListView>
          </section>
          <section>
            <h2>Научные работы</h2>
            <ListView result={papers}>
              {(paper) => <PaperRow key={paper.id} paper={paper} />}
            </ListView>
          </section>
          <section>
            <h2>Опыт</h2>
            {profile.experience.length ? (
              profile.experience.map((entry, index) => (
                <article className="plain-row" key={index}>
                  <h3>{entry.title}</h3>
                  <p>{entry.description}</p>
                  <small>
                    {entry.source === "platform"
                      ? "Участие на платформе"
                      : "Указано пользователем"}
                    {entry.completed ? " · завершено" : ""}
                  </small>
                </article>
              ))
            ) : (
              <Empty />
            )}
          </section>
        </div>
        <aside className="profile-side">
          <h2>Интересы</h2>
          <p>
            {profile.interests.map((interest) => interest.name).join(", ") ||
              "Не указаны"}
          </p>
          <h2>Навыки</h2>
          <p>
            {profile.skills.map((skill) => skill.name).join(", ") ||
              "Не указаны"}
          </p>
          <h2>Контакты</h2>
          {profile.contacts.length ? (
            profile.contacts.map((contact, index) => (
              <p key={index}>
                {contact.type}: {contact.value}
              </p>
            ))
          ) : (
            <p>Нет доступных контактов.</p>
          )}
          <ReportButton type="user" id={profile.user_id} />
        </aside>
      </div>
      {invite && (
        <InviteToProject
          userId={profile.user_id}
          close={() => setInvite(false)}
        />
      )}
    </>
  );
}
function InviteToProject({
  userId,
  close,
}: {
  userId: string;
  close: () => void;
}) {
  const projects = useList<Project>("/me/projects", { ownership: "owner" });
  const [id, setId] = useState("");
  const [role, setRole] = useState("researcher");
  const [message, setMessage] = useState("");
  const mutation = useCommand(`/projects/${id}/invitations`);
  return (
    <Modal title="Пригласить в проект" close={close}>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          mutation.mutate(
            { user_id: userId, role, message },
            { onSuccess: close },
          );
        }}
      >
        <Field label="Проект">
          <select
            required
            value={id}
            onChange={(event) => setId(event.target.value)}
          >
            <option value="">Выберите свой проект</option>
            {projects.data?.pages
              .flatMap((page) => page.items)
              .map((project) => (
                <option key={project.id} value={project.id}>
                  {project.title}
                </option>
              ))}
          </select>
        </Field>
        <ErrorNotice error={projects.error} />
        {projects.hasNextPage && (
          <button
            type="button"
            className="text-button"
            onClick={() => {
              void projects.fetchNextPage();
            }}
          >
            Ещё проекты
          </button>
        )}
        <Field label="Роль">
          <RoleSelect value={role} onChange={setRole} />
        </Field>
        <Field label="Сообщение">
          <textarea
            maxLength={2000}
            value={message}
            onChange={(event) => setMessage(event.target.value)}
          />
        </Field>
        <FormActions
          busy={mutation.isPending}
          error={mutation.error}
          text="Пригласить"
        />
      </form>
    </Modal>
  );
}
