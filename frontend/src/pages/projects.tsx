import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { api, command, date, safeURL } from "../lib/api";
import { useList, useResource } from "../lib/data";
import { useAuth } from "../ui/auth";
import {
  CommandButton,
  DictName,
  DictSelect,
  DictTags,
  Empty,
  ErrorNotice,
  FavoriteButton,
  Field,
  FileInput,
  FormActions,
  Heading,
  label,
  ListView,
  Loading,
  Modal,
  ReportButton,
  RoleSelect,
  useCommand,
  formText,
} from "../ui/common";
import type {
  Access,
  Application,
  Invitation,
  Member,
  Profile,
  Project,
  ProjectFiles,
} from "../types";

export function FileLink({
  path,
  mode = "inline",
  children,
}: {
  path: string;
  mode?: "inline" | "attachment";
  children: string;
}) {
  const [url, setURL] = useState<string>();
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  return (
    <span className="file-link">
      {url ? (
        <a
          href={url}
          target="_blank"
          rel="noopener noreferrer"
          className="section-link"
          onClick={() => {
            setTimeout(() => setURL(undefined), 0);
          }}
        >
          {children}
        </a>
      ) : (
        <button
          type="button"
          className="text-button"
          disabled={busy}
          onClick={() => {
            setBusy(true);
            setError(null);
            api<{ url: string }>(`${path}?mode=${mode}`)
              .then((result) => {
                const checked = safeURL(result.url);
                if (!checked) throw new Error("Недопустимая ссылка на файл.");
                setURL(checked);
              })
              .catch(setError)
              .finally(() => setBusy(false));
          }}
        >
          {busy
            ? "Подготовка ссылки…"
            : `Подготовить: ${children.toLowerCase()}`}
        </button>
      )}
      <ErrorNotice error={error} />
    </span>
  );
}
export function UserName({ id }: { id: string }) {
  const profile = useResource<Profile>(`/users/${id}`);
  return profile.data ? (
    <Link className="section-link" to={`/researchers/${id}`}>
      {profile.data.first_name} {profile.data.last_name}
    </Link>
  ) : (
    <span>
      {profile.isPending ? "Загрузка участника…" : "Аккаунт недоступен"}
    </span>
  );
}
export function ProjectPage() {
  const { id } = useParams();
  const { account } = useAuth();
  const result = useResource<Project>(`/projects/${id}`);
  const [applying, setApplying] = useState(false);
  const members = useList<Member>(`/projects/${id}/members`, {}, !!result.data);
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
  const project = result.data;
  return (
    <>
      <Link className="detail-back" to="/projects">
        Все проекты
      </Link>
      <header className="detail-heading">
        <div className="detail-meta">
          <DictName kind="research_areas" id={project.area_id} />
          <span>{label(project.status)}</span>
        </div>
        <h1>{project.title}</h1>
        <div className="detail-meta">
          <span>Автор: {project.creator_name}</span>
          <span>{label(project.access_type)}</span>
          <span>Дедлайн: {date(project.deadline)}</span>
          <FavoriteButton
            type="project"
            id={project.id}
            saved={project.is_favorited}
          />
        </div>
      </header>
      <div className="detail-layout">
        <div className="detail-copy">
          <section>
            <h2>Описание</h2>
            <p className="preserve">
              {project.description || "Описание пока не добавлено."}
            </p>
          </section>
          <section>
            <h2>Цель исследования</h2>
            <p className="preserve">{project.research_goal || "Не указана."}</p>
          </section>
          <section>
            <h2>Кого ищем</h2>
            <DictTags kind="skills" ids={project.required_skill_ids} />
            <p>
              {project.required_roles.map(label).join(", ") ||
                "Роли не ограничены"}
            </p>
          </section>
          <section>
            <h2>Команда</h2>
            <ListView result={members}>
              {(member) => (
                <div className="team-person" key={member.id}>
                  <div>
                    <UserName id={member.user_id} />
                    <p>{label(member.role)}</p>
                  </div>
                </div>
              )}
            </ListView>
          </section>
          <ProjectMaterials project={project} />
          {project.results.length > 0 && (
            <section>
              <h2>Результаты</h2>
              {project.results.map((item, index) => (
                <p key={index}>
                  {item.url && safeURL(item.url) ? (
                    <a
                      className="section-link"
                      href={safeURL(item.url)}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      {item.title || item.url}
                    </a>
                  ) : (
                    item.title
                  )}
                </p>
              ))}
            </section>
          )}
          <ReportButton type="project" id={project.id} />
        </div>
        <aside className="join-card">
          <h2>Участие</h2>
          <div className="seats">
            <span>В команде</span>
            <strong>
              {project.members_count} из {project.team_size}
            </strong>
          </div>
          <p>Свободных мест: {project.free_slots}</p>
          {project.viewer.can_manage ? (
            <>
              <Link
                className="button button-full"
                to={`/projects/${id}/manage`}
              >
                Управление проектом
              </Link>
              <Link className="button button-full" to={`/projects/${id}/edit`}>
                Редактировать
              </Link>
            </>
          ) : project.viewer.is_member ? (
            <>
              <p>Вы участник команды.</p>
              <CommandButton
                path={`/projects/${id}/leave`}
                confirm="Выйти из команды?"
              >
                Выйти из проекта
              </CommandButton>
            </>
          ) : !account ? (
            <Link
              className="button button-primary button-full"
              to={`/login?next=${encodeURIComponent(`/projects/${id}`)}`}
            >
              Войти для участия
            </Link>
          ) : project.viewer.can_apply ? (
            <button
              className="button button-primary button-full"
              onClick={() => setApplying(true)}
            >
              Подать заявку
            </button>
          ) : (
            <p>
              {project.viewer.application_status === "pending"
                ? "Ваша заявка на рассмотрении."
                : project.access_type !== "public_open"
                  ? "Участие только по приглашению."
                  : "Сейчас нельзя подать заявку."}
            </p>
          )}
          {project.viewer.application_status === "pending" && (
            <Link className="section-link" to="/me/applications">
              Мои заявки
            </Link>
          )}
          {project.viewer.invitation_status === "pending" && (
            <Link className="section-link" to="/me/invitations">
              Открыть приглашение
            </Link>
          )}
        </aside>
      </div>
      {applying && (
        <ApplicationForm id={project.id} close={() => setApplying(false)} />
      )}
    </>
  );
}
function ApplicationForm({ id, close }: { id: string; close: () => void }) {
  const [role, setRole] = useState("researcher");
  const mutation = useCommand(`/projects/${id}/applications`);
  return (
    <Modal title="Подать заявку" close={close}>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          mutation.mutate(
            {
              requested_role: role,
              message: new FormData(event.currentTarget).get("message"),
            },
            { onSuccess: close },
          );
        }}
      >
        <Field label="Роль">
          <RoleSelect value={role} onChange={setRole} />
        </Field>
        <Field label="Опыт и польза для проекта">
          <textarea name="message" required minLength={10} maxLength={4000} />
        </Field>
        <FormActions
          busy={mutation.isPending}
          error={mutation.error}
          text="Отправить заявку"
        />
      </form>
    </Modal>
  );
}
function ProjectMaterials({ project }: { project: Project }) {
  const files = useResource<ProjectFiles>(`/projects/${project.id}/files`);
  const client = useQueryClient();
  return (
    <section>
      <h2>Материалы</h2>
      {files.isPending && <Loading />}
      <ErrorNotice
        error={files.error}
        retry={() => {
          void files.refetch();
        }}
      />
      {files.data && (
        <>
          <p className="muted">
            Файлов: {files.data.used_slots} из {files.data.limit}
            {files.data.reserved_slots
              ? ` · Загружается: ${files.data.reserved_slots}`
              : ""}
          </p>
          {!files.data.items.length && (
            <Empty text="Материалы пока не добавлены." />
          )}
          {files.data.items.map((file) => (
            <article className="plain-row" key={file.id}>
              <p>{file.title || file.filename || "Материал проекта"}</p>
              <div className="actions">
                <FileLink
                  path={`/projects/${project.id}/files/${file.file_id}/file-url`}
                >
                  Открыть PDF
                </FileLink>
                {project.viewer.can_manage && file.origin !== "paper" && (
                  <CommandButton
                    path={`/projects/${project.id}/files/${file.file_id}`}
                    method="DELETE"
                    confirm="Удалить материал из проекта?"
                  >
                    Удалить файл
                  </CommandButton>
                )}
                {file.paper_id && (
                  <Link
                    className="section-link"
                    to={`/papers/${file.paper_id}`}
                  >
                    К научной работе
                  </Link>
                )}
              </div>
            </article>
          ))}
          {files.data.can_upload && (
            <FileInput
              purpose="project_pdf"
              resourceId={project.id}
              onUploaded={async (upload) => {
                const reservation =
                  upload.reservation_id ??
                  (
                    await command<{ id: string }>(
                      `/projects/${project.id}/file-reservations`,
                      { file_id: upload.file_id },
                    )
                  ).id;
                await command(`/projects/${project.id}/files`, {
                  file_id: upload.file_id,
                  reservation_id: reservation,
                  title: "Материал проекта",
                });
                await client.invalidateQueries();
              }}
            />
          )}
        </>
      )}
    </section>
  );
}
export function ProjectFormPage() {
  const { id } = useParams();
  const result = useResource<Project>(`/projects/${id}`, !!id);
  if (id && result.isPending) return <Loading />;
  if (id && result.error) return <ErrorNotice error={result.error} />;
  if (id && !result.data?.viewer.can_manage)
    return <p role="alert">Редактирование доступно руководителю проекта.</p>;
  return <ProjectEditor key={result.data?.id ?? "new"} project={result.data} />;
}
function ProjectEditor({ project }: { project?: Project }) {
  const navigate = useNavigate();
  const mutation = useCommand<Project>(
    project ? `/projects/${project.id}` : "/projects",
    project ? "PATCH" : "POST",
  );
  const [area, setArea] = useState(project?.area_id ?? "");
  const [university, setUniversity] = useState(project?.university_id ?? "");
  const [skills, setSkills] = useState(project?.required_skill_ids ?? []);
  const [interests, setInterests] = useState(project?.interest_ids ?? []);
  const [access, setAccess] = useState<Access>(
    project?.access_type ?? "public_open",
  );
  const [roles, setRoles] = useState(project?.required_roles ?? []);
  const [specialties, setSpecialties] = useState(
    project?.preferred_specialty_ids ?? [],
  );
  const initialDeadline = project?.deadline
    ? new Date(
        new Date(project.deadline).getTime() -
          new Date(project.deadline).getTimezoneOffset() * 60_000,
      )
        .toISOString()
        .slice(0, 16)
    : "";
  return (
    <section className="form-page">
      <Heading title={project ? "Редактировать проект" : "Создать проект"} />
      <p className="muted">
        Сначала сохраним черновик. Публикация — отдельным действием после
        проверки.
      </p>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          const form = new FormData(event.currentTarget);
          const expanded =
            project?.access_type === "private_invite" &&
            access !== "private_invite";
          if (
            expanded &&
            !window.confirm(
              "Проект и связанные материалы станут публичными. Подтверждаете?",
            )
          )
            return;
          mutation.mutate(
            {
              title: form.get("title"),
              summary: form.get("summary"),
              description: form.get("description"),
              research_goal: form.get("goal"),
              area_id: area || null,
              university_id: university || null,
              required_skill_ids: skills,
              interest_ids: interests,
              required_roles: roles,
              preferred_specialty_ids: specialties,
              access_type: access,
              team_size: Number(form.get("size")),
              min_completed_projects: form.get("experience")
                ? Number(form.get("experience"))
                : null,
              eligible_courses: formText(form, "courses")
                .split(",")
                .map((value) => Number(value.trim()))
                .filter((value) => value >= 1 && value <= 8),
              deadline: form.get("deadline")
                ? new Date(String(form.get("deadline"))).toISOString()
                : null,
              ...(project
                ? {
                    version: project.version,
                    confirm_visibility_expansion: expanded,
                  }
                : {}),
            },
            { onSuccess: (result) => navigate(`/projects/${result.id}`) },
          );
        }}
      >
        <Field label="Название">
          <input
            name="title"
            required
            minLength={3}
            maxLength={200}
            defaultValue={project?.title}
          />
        </Field>
        <Field label="Короткое описание">
          <textarea
            name="summary"
            maxLength={700}
            defaultValue={project?.summary}
          />
        </Field>
        <Field label="Описание">
          <textarea
            name="description"
            required
            maxLength={20000}
            defaultValue={project?.description}
          />
        </Field>
        <Field label="Цель исследования">
          <textarea
            name="goal"
            required
            maxLength={10000}
            defaultValue={project?.research_goal}
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
        <Field
          label="Требуемые навыки"
          hint="Для нескольких значений: Ctrl / Cmd + щелчок."
        >
          <DictSelect
            kind="skills"
            value={skills}
            onChange={setSkills}
            multiple
          />
        </Field>
        <Field label="Научные интересы">
          <DictSelect
            kind="research_interests"
            value={interests}
            onChange={setInterests}
            multiple
          />
        </Field>
        <fieldset>
          <legend>Требуемые специалисты</legend>
          <div className="checkbox-grid">
            {[
              "researcher",
              "analyst",
              "developer",
              "designer",
              "author",
              "editor",
            ].map((role) => (
              <label className="checkbox-label" key={role}>
                <input
                  type="checkbox"
                  checked={roles.includes(role)}
                  onChange={(event) =>
                    setRoles((previous) =>
                      event.target.checked
                        ? [...previous, role]
                        : previous.filter((value) => value !== role),
                    )
                  }
                />
                {label(role)}
              </label>
            ))}
          </div>
        </fieldset>
        <div className="form-grid">
          <Field label="Размер команды, включая вас">
            <input
              name="size"
              type="number"
              min="1"
              max="100"
              required
              defaultValue={project?.team_size ?? 5}
            />
          </Field>
          <Field label="Дедлайн">
            <input
              name="deadline"
              type="datetime-local"
              defaultValue={initialDeadline}
            />
          </Field>
        </div>
        <Field label="Доступ">
          <select
            value={access}
            onChange={(event) => setAccess(event.target.value as Access)}
          >
            {["public_open", "public_invite", "private_invite"].map((value) => (
              <option key={value} value={value}>
                {label(value)}
              </option>
            ))}
          </select>
        </Field>
        <details className="form-section">
          <summary>Дополнительные критерии рекомендаций</summary>
          <Field label="Предпочтительные специальности">
            <DictSelect
              kind="specialties"
              value={specialties}
              onChange={setSpecialties}
              multiple
            />
          </Field>
          <Field
            label="Курсы через запятую"
            hint="Например: 2, 3, 4. Пустое поле — без ограничения."
          >
            <input
              name="courses"
              defaultValue={project?.eligible_courses.join(", ")}
              pattern="\s*[1-8](\s*,\s*[1-8])*\s*"
            />
          </Field>
          <Field label="Минимум завершённых проектов">
            <input
              name="experience"
              type="number"
              min="0"
              max="100"
              defaultValue={project?.min_completed_projects ?? ""}
            />
          </Field>
        </details>
        <FormActions
          busy={mutation.isPending}
          error={mutation.error}
          text={project ? "Сохранить изменения" : "Сохранить черновик"}
          back={project ? `/projects/${project.id}` : "/projects"}
        />
      </form>
    </section>
  );
}
export function ProjectManagePage() {
  const { id } = useParams();
  const project = useResource<Project>(`/projects/${id}`);
  if (project.isPending) return <Loading />;
  if (project.error) return <ErrorNotice error={project.error} />;
  if (!project.data?.viewer.can_manage)
    return <p role="alert">Управление доступно руководителю.</p>;
  return <ProjectManager project={project.data} />;
}
function ProjectManager({ project }: { project: Project }) {
  const base = `/projects/${project.id}`;
  const [tab, setTab] = useState("applications");
  const applications = useList<Application>(
    `${base}/applications`,
    {},
    tab === "applications",
  );
  const invitations = useList<Invitation>(
    `${base}/invitations`,
    {},
    tab === "invitations",
  );
  const members = useList<Member>(`${base}/members`, {}, tab === "members");
  const [role, setRole] = useState("researcher");
  const invite = useCommand(`${base}/invitations`);
  const [invited, setInvited] = useState(false);
  return (
    <>
      <Heading title="Управление проектом">
        <Link className="section-link" to={base}>
          К проекту
        </Link>
      </Heading>
      <h2>{project.title}</h2>
      <div className="actions form-section">
        <Link className="button" to={`${base}/edit`}>
          Редактировать
        </Link>
        {project.status === "draft" && (
          <CommandButton path={`${base}/publish`}>
            Опубликовать проект
          </CommandButton>
        )}
        {project.status === "recruiting" && (
          <CommandButton path={`${base}/start`}>
            Начать исследование
          </CommandButton>
        )}
        {project.status === "active" && (
          <CommandButton
            path={`${base}/complete`}
            confirm="Завершить исследование?"
          >
            Завершить проект
          </CommandButton>
        )}
        {["active", "completed"].includes(project.status) && (
          <CommandButton path={`${base}/reopen`}>Открыть набор</CommandButton>
        )}
        {project.status !== "archived" && (
          <CommandButton
            path={base}
            method="DELETE"
            confirm="Архивировать проект?"
          >
            В архив
          </CommandButton>
        )}
      </div>
      <nav className="tabs-row" aria-label="Управление проектом">
        {[
          ["applications", "Заявки"],
          ["invitations", "Приглашения"],
          ["members", "Команда"],
        ].map(([key, text]) => (
          <button
            className={`tab-pill ${tab === key ? "active" : ""}`}
            key={key}
            onClick={() => setTab(key)}
            aria-pressed={tab === key}
          >
            {text}
          </button>
        ))}
      </nav>
      {tab === "applications" && (
        <ListView result={applications}>
          {(application) => (
            <ApplicationDecision
              key={application.id}
              application={application}
            />
          )}
        </ListView>
      )}
      {tab === "invitations" && (
        <>
          <form
            className="form-page"
            onSubmit={(event) => {
              event.preventDefault();
              const form = new FormData(event.currentTarget);
              invite.mutate(
                {
                  user_id: form.get("user_id"),
                  role,
                  message: form.get("message"),
                },
                { onSuccess: () => setInvited(true) },
              );
            }}
          >
            <h2>Пригласить участника</h2>
            <Field
              label="ID пользователя"
              hint="ID указан в адресе профиля. Также можно пригласить человека со страницы его профиля."
            >
              <input name="user_id" required pattern="[0-9a-fA-F-]{36}" />
            </Field>
            <Field label="Роль">
              <RoleSelect value={role} onChange={setRole} />
            </Field>
            <Field label="Сообщение">
              <textarea name="message" maxLength={2000} />
            </Field>
            <FormActions
              busy={invite.isPending}
              error={invite.error}
              text="Отправить приглашение"
            />
            {invited && <p role="status">Приглашение отправлено.</p>}
          </form>
          <ListView result={invitations}>
            {(invitation) => (
              <article className="plain-row" key={invitation.id}>
                <UserName id={invitation.user_id} />
                <p>
                  {label(invitation.role)} · {label(invitation.status)}
                </p>
                {invitation.status === "pending" && (
                  <CommandButton
                    path={`${base}/invitations/${invitation.id}/revoke`}
                  >
                    Отозвать приглашение
                  </CommandButton>
                )}
              </article>
            )}
          </ListView>
        </>
      )}
      {tab === "members" && (
        <ListView result={members}>
          {(member) => (
            <MemberControls key={member.id} project={project} member={member} />
          )}
        </ListView>
      )}
    </>
  );
}
function ApplicationDecision({ application }: { application: Application }) {
  const [role, setRole] = useState(application.requested_role);
  const [reason, setReason] = useState("");
  const path = `/projects/${application.project_id}/applications/${application.id}`;
  return (
    <article className="plain-row">
      <UserName id={application.user_id} />
      <p>
        {label(application.status)} · {label(application.requested_role)}
      </p>
      <p className="preserve">{application.message}</p>
      {application.status === "pending" && (
        <div className="decision-form">
          <Field label="Роль в команде">
            <RoleSelect value={role} onChange={setRole} />
          </Field>
          <Field label="Комментарий к решению">
            <textarea
              value={reason}
              maxLength={2000}
              onChange={(event) => setReason(event.target.value)}
            />
          </Field>
          <div className="actions">
            <CommandButton path={`${path}/accept`} body={{ role, reason }}>
              Принять заявку
            </CommandButton>
            <CommandButton path={`${path}/reject`} body={{ role, reason }}>
              Отклонить заявку
            </CommandButton>
          </div>
        </div>
      )}
    </article>
  );
}
function MemberControls({
  project,
  member,
}: {
  project: Project;
  member: Member;
}) {
  const [role, setRole] = useState(member.role);
  const mutation = useCommand(
    `/projects/${project.id}/members/${member.user_id}`,
    "PATCH",
  );
  return (
    <article className="plain-row">
      <UserName id={member.user_id} />
      <p>{label(member.role)}</p>
      {member.user_id !== project.owner_id && (
        <>
          <form
            className="inline-form"
            onSubmit={(event) => {
              event.preventDefault();
              mutation.mutate({ role });
            }}
          >
            <Field label="Новая роль">
              <RoleSelect value={role} onChange={setRole} />
            </Field>
            <button className="button" disabled={mutation.isPending}>
              Изменить роль
            </button>
            <ErrorNotice error={mutation.error} />
          </form>
          <div className="actions">
            <CommandButton
              path={`/projects/${project.id}/transfer-ownership`}
              body={{ user_id: member.user_id }}
              confirm="Передать руководство этому участнику? Вы потеряете права руководителя."
            >
              Передать владение
            </CommandButton>
            <CommandButton
              path={`/projects/${project.id}/members/${member.user_id}`}
              method="DELETE"
              confirm="Исключить участника?"
            >
              Исключить
            </CommandButton>
          </div>
        </>
      )}
    </article>
  );
}
