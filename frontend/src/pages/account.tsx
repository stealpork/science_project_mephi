import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, command, session } from "../lib/api";
import { useList, useResource } from "../lib/data";
import { useAuth } from "../ui/auth";
import {
  CommandButton,
  DictSelect,
  ErrorNotice,
  Field,
  FileInput,
  FormActions,
  Heading,
  ListView,
  Loading,
  useCommand,
} from "../ui/common";
import type {
  Contact,
  Experience,
  MFAChallenge,
  Profile,
  Session,
  Tokens,
} from "../types";

export function nextPath(value: string | null, fallback = "/workspace") {
  return value?.startsWith("/") &&
    !value.startsWith("//") &&
    !value.includes("\\")
    ? value
    : fallback;
}
export function LoginPage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const { account } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [challenge, setChallenge] = useState("");
  const [code, setCode] = useState("");
  const [recovery, setRecovery] = useState(false);
  const mutation = useMutation({
    mutationFn: () =>
      api<Tokens | MFAChallenge>(
        challenge ? "/auth/2fa/challenge" : "/auth/login",
        {
          method: "POST",
          anonymous: true,
          body: challenge
            ? {
                challenge_token: challenge,
                [recovery ? "recovery_code" : "code"]: code,
              }
            : { email, password },
        },
      ),
    onSuccess: (result) => {
      if ("requires_2fa" in result) {
        setChallenge(result.challenge_token);
        setPassword("");
      } else {
        session.accept(result);
        navigate(nextPath(params.get("next")), { replace: true });
      }
    },
  });
  if (account)
    return (
      <>
        <Heading title="Вы уже вошли" />
        <Link className="button" to={nextPath(params.get("next"))}>
          Продолжить
        </Link>
      </>
    );
  return (
    <section className="narrow">
      <Heading title={challenge ? "Подтверждение входа" : "Вход"} />
      <form
        onSubmit={(event) => {
          event.preventDefault();
          mutation.mutate();
        }}
      >
        {challenge ? (
          <>
            <Field
              label={
                recovery
                  ? "Код восстановления"
                  : "Код из приложения-аутентификатора"
              }
            >
              <input
                autoComplete="one-time-code"
                required
                value={code}
                onChange={(event) => setCode(event.target.value)}
                minLength={6}
                maxLength={100}
              />
            </Field>
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={recovery}
                onChange={(event) => {
                  setRecovery(event.target.checked);
                  setCode("");
                }}
              />
              Использовать код восстановления
            </label>
          </>
        ) : (
          <>
            <Field label="Email">
              <input
                type="email"
                required
                autoComplete="username"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
              />
            </Field>
            <Field label="Пароль">
              <input
                type="password"
                required
                maxLength={128}
                autoComplete="current-password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
              />
            </Field>
          </>
        )}
        <FormActions
          busy={mutation.isPending}
          error={mutation.error}
          text="Войти"
        />
      </form>
      <div className="auth-links">
        <Link to="/register">Зарегистрироваться</Link>
        <Link to="/forgot-password">Забыли пароль?</Link>
      </div>
    </section>
  );
}
export function RegisterPage() {
  const navigate = useNavigate();
  const [university, setUniversity] = useState("");
  const [faculty, setFaculty] = useState("");
  const [specialty, setSpecialty] = useState("");
  const mutation = useMutation({
    mutationFn: async (form: HTMLFormElement) => {
      const data = new FormData(form);
      const email = String(data.get("email"));
      const password = String(data.get("password"));
      await command("/auth/register", {
        first_name: data.get("first_name"),
        last_name: data.get("last_name"),
        email,
        password,
        university_id: university || null,
        faculty_id: faculty || null,
        specialty_id: specialty || null,
        course: data.get("course") ? Number(data.get("course")) : null,
      });
      const tokens = await api<Tokens | MFAChallenge>("/auth/login", {
        method: "POST",
        body: { email, password },
        anonymous: true,
      });
      if ("access_token" in tokens) session.accept(tokens);
    },
    onSuccess: () => navigate("/me/profile/edit"),
  });
  return (
    <section className="form-page">
      <Heading title="Регистрация" />
      <form
        onSubmit={(event) => {
          event.preventDefault();
          mutation.mutate(event.currentTarget);
        }}
      >
        <div className="form-grid">
          <Field label="Имя">
            <input
              required
              name="first_name"
              autoComplete="given-name"
              maxLength={80}
            />
          </Field>
          <Field label="Фамилия">
            <input
              required
              name="last_name"
              autoComplete="family-name"
              maxLength={80}
            />
          </Field>
        </div>
        <Field label="Email">
          <input required type="email" name="email" autoComplete="email" />
        </Field>
        <Field label="Пароль" hint="От 10 до 128 символов.">
          <input
            required
            type="password"
            name="password"
            autoComplete="new-password"
            minLength={10}
            maxLength={128}
          />
        </Field>
        <Field label="Университет">
          <DictSelect
            kind="universities"
            value={university}
            onChange={(values) => {
              setUniversity(values[0] ?? "");
              setFaculty("");
            }}
          />
        </Field>
        <Field label="Факультет / институт">
          <DictSelect
            kind="faculties"
            parent={university}
            value={faculty}
            onChange={(values) => setFaculty(values[0] ?? "")}
          />
        </Field>
        <Field label="Специальность">
          <DictSelect
            kind="specialties"
            value={specialty}
            onChange={(values) => setSpecialty(values[0] ?? "")}
          />
        </Field>
        <Field label="Курс">
          <input type="number" name="course" min="1" max="8" />
        </Field>
        <FormActions
          busy={mutation.isPending}
          error={mutation.error}
          text="Зарегистрироваться"
        />
        <p className="muted">
          Уже есть аккаунт?{" "}
          <Link className="section-link" to="/login">
            Войти
          </Link>
        </p>
      </form>
    </section>
  );
}
export function RecoveryPage({
  mode,
}: {
  mode: "forgot" | "reset" | "verify";
}) {
  const [params] = useSearchParams();
  const token = params.get("token");
  const [done, setDone] = useState(false);
  const mutation = useCommand(
    mode === "forgot"
      ? "/auth/password/forgot"
      : mode === "reset"
        ? "/auth/password/reset"
        : "/auth/verify-email",
  );
  return (
    <section className="narrow">
      <Heading
        title={
          mode === "verify" ? "Подтверждение email" : "Восстановление пароля"
        }
      />
      {done ? (
        <p role="status">
          {mode === "forgot"
            ? "Если аккаунт существует, письмо со ссылкой отправлено."
            : mode === "reset"
              ? "Пароль изменён. Можно войти."
              : "Email подтверждён."}{" "}
          <Link className="section-link" to="/login">
            Войти
          </Link>
        </p>
      ) : mode !== "forgot" && !token ? (
        <p role="alert">
          В ссылке нет токена. Откройте полную ссылку из письма.
        </p>
      ) : (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            const form = new FormData(event.currentTarget);
            mutation.mutate(
              mode === "forgot"
                ? { email: form.get("email") }
                : mode === "reset"
                  ? { token, new_password: form.get("password") }
                  : { token },
              {
                onSuccess: () => {
                  setDone(true);
                  if (mode === "reset") session.clear();
                },
              },
            );
          }}
        >
          {mode === "forgot" && (
            <Field label="Email">
              <input required name="email" type="email" autoComplete="email" />
            </Field>
          )}
          {mode === "reset" && (
            <Field label="Новый пароль">
              <input
                required
                name="password"
                type="password"
                minLength={10}
                maxLength={128}
                autoComplete="new-password"
              />
            </Field>
          )}
          {mode === "verify" && <p>Подтвердите адрес электронной почты.</p>}
          <FormActions
            busy={mutation.isPending}
            error={mutation.error}
            text={mode === "forgot" ? "Отправить письмо" : "Подтвердить"}
          />
        </form>
      )}
    </section>
  );
}
export function ProfileEditPage() {
  const profile = useResource<Profile | { status: "initializing" }>(
    "/me/profile",
  );
  if (profile.isPending) return <Loading />;
  if (profile.error)
    return (
      <ErrorNotice
        error={profile.error}
        retry={() => {
          void profile.refetch();
        }}
      />
    );
  if (!profile.data || !("user_id" in profile.data))
    return (
      <>
        <Heading title="Профиль создаётся" />
        <p>Подождите несколько секунд после регистрации.</p>
        <button
          className="button"
          onClick={() => {
            void profile.refetch();
          }}
        >
          Проверить готовность
        </button>
      </>
    );
  return <ProfileEditor key={profile.data.version} profile={profile.data} />;
}
function ProfileEditor({ profile }: { profile: Profile }) {
  const navigate = useNavigate();
  const client = useQueryClient();
  const [data, setData] = useState({
    first_name: profile.first_name,
    last_name: profile.last_name,
    bio: profile.bio,
    university_id: profile.university_id ?? "",
    faculty_id: profile.faculty_id ?? "",
    department_id: profile.department_id ?? "",
    specialty_id: profile.specialty_id ?? "",
    city_id: profile.city_id ?? "",
    course: profile.course?.toString() ?? "",
  });
  const update = (key: keyof typeof data, value: string) =>
    setData((previous) => ({ ...previous, [key]: value }));
  const [skills, setSkills] = useState(profile.skills.map((skill) => skill.id));
  const [interests, setInterests] = useState(
    profile.interests.map((interest) => interest.id),
  );
  const [contacts, setContacts] = useState<Contact[]>(profile.contacts);
  const [experience, setExperience] = useState<Experience[]>(
    profile.experience.filter((entry) => entry.source !== "platform"),
  );
  const mutation = useCommand("/me/profile", "PATCH");
  return (
    <section className="form-page">
      <Heading title="Редактировать профиль" />
      <form
        onSubmit={(event) => {
          event.preventDefault();
          mutation.mutate(
            {
              ...data,
              version: profile.version,
              university_id: data.university_id || null,
              faculty_id: data.faculty_id || null,
              department_id: data.department_id || null,
              specialty_id: data.specialty_id || null,
              city_id: data.city_id || null,
              course: data.course ? Number(data.course) : null,
              skill_ids: skills,
              interest_ids: interests,
              contacts,
              experience,
            },
            { onSuccess: () => navigate("/me/profile") },
          );
        }}
      >
        <div className="form-grid">
          <Field label="Имя">
            <input
              required
              maxLength={80}
              value={data.first_name}
              onChange={(event) => update("first_name", event.target.value)}
            />
          </Field>
          <Field label="Фамилия">
            <input
              required
              maxLength={80}
              value={data.last_name}
              onChange={(event) => update("last_name", event.target.value)}
            />
          </Field>
        </div>
        <Field label="О себе">
          <textarea
            value={data.bio}
            maxLength={4000}
            onChange={(event) => update("bio", event.target.value)}
          />
        </Field>
        <Field label="Университет">
          <DictSelect
            kind="universities"
            value={data.university_id}
            onChange={(values) => {
              setData((previous) => ({
                ...previous,
                university_id: values[0] ?? "",
                faculty_id: "",
                department_id: "",
              }));
            }}
          />
        </Field>
        <div className="form-grid">
          <Field label="Факультет / институт">
            <DictSelect
              kind="faculties"
              parent={data.university_id}
              value={data.faculty_id}
              onChange={(values) =>
                setData((previous) => ({
                  ...previous,
                  faculty_id: values[0] ?? "",
                  department_id: "",
                }))
              }
            />
          </Field>
          <Field label="Кафедра">
            <DictSelect
              kind="departments"
              parent={data.faculty_id}
              value={data.department_id}
              onChange={(values) => update("department_id", values[0] ?? "")}
            />
          </Field>
        </div>
        <div className="form-grid">
          <Field label="Специальность">
            <DictSelect
              kind="specialties"
              value={data.specialty_id}
              onChange={(values) => update("specialty_id", values[0] ?? "")}
            />
          </Field>
          <Field label="Курс">
            <input
              type="number"
              min="1"
              max="8"
              value={data.course}
              onChange={(event) => update("course", event.target.value)}
            />
          </Field>
        </div>
        <Field label="Город">
          <DictSelect
            kind="cities"
            value={data.city_id}
            onChange={(values) => update("city_id", values[0] ?? "")}
          />
        </Field>
        <Field
          label="Научные интересы"
          hint="Можно выбрать несколько: Ctrl / Cmd + щелчок."
        >
          <DictSelect
            kind="research_interests"
            value={interests}
            onChange={setInterests}
            multiple
          />
        </Field>
        <Field
          label="Навыки и технологии"
          hint="Можно выбрать несколько: Ctrl / Cmd + щелчок."
        >
          <DictSelect
            kind="skills"
            value={skills}
            onChange={setSkills}
            multiple
          />
        </Field>
        <p className="muted">
          <Link className="section-link" to="/dictionaries/suggest">
            Предложить отсутствующее значение
          </Link>
        </p>
        <section className="form-section">
          <h2>Контакты</h2>
          {contacts.map((contact, index) => (
            <fieldset key={index}>
              <legend>Контакт {index + 1}</legend>
              <Field label="Тип">
                <select
                  value={contact.type}
                  onChange={(event) =>
                    setContacts((rows) =>
                      rows.map((row, i) =>
                        i === index
                          ? { ...row, type: event.target.value }
                          : row,
                      ),
                    )
                  }
                >
                  {["email", "telegram", "website", "phone", "other"].map(
                    (type) => (
                      <option key={type}>{type}</option>
                    ),
                  )}
                </select>
              </Field>
              <Field label="Значение">
                <input
                  required
                  maxLength={500}
                  value={contact.value}
                  onChange={(event) =>
                    setContacts((rows) =>
                      rows.map((row, i) =>
                        i === index
                          ? { ...row, value: event.target.value }
                          : row,
                      ),
                    )
                  }
                />
              </Field>
              <Field label="Кто видит">
                <select
                  value={contact.visibility}
                  onChange={(event) =>
                    setContacts((rows) =>
                      rows.map((row, i) =>
                        i === index
                          ? {
                              ...row,
                              visibility: event.target
                                .value as Contact["visibility"],
                            }
                          : row,
                      ),
                    )
                  }
                >
                  <option value="private">Только я</option>
                  <option value="authenticated">Зарегистрированные</option>
                  <option value="public">Все</option>
                </select>
              </Field>
              <button
                type="button"
                className="text-button"
                onClick={() =>
                  setContacts((rows) => rows.filter((_, i) => i !== index))
                }
              >
                Удалить контакт
              </button>
            </fieldset>
          ))}
          <button
            type="button"
            className="button"
            disabled={contacts.length >= 10}
            onClick={() =>
              setContacts((rows) => [
                ...rows,
                { type: "email", value: "", visibility: "private" },
              ])
            }
          >
            Добавить контакт
          </button>
        </section>
        <section className="form-section">
          <h2>Исследовательский опыт</h2>
          {experience.map((entry, index) => (
            <fieldset key={index}>
              <legend>Опыт {index + 1}</legend>
              <Field label="Название">
                <input
                  required
                  minLength={2}
                  maxLength={200}
                  value={entry.title}
                  onChange={(event) =>
                    setExperience((rows) =>
                      rows.map((row, i) =>
                        i === index
                          ? { ...row, title: event.target.value }
                          : row,
                      ),
                    )
                  }
                />
              </Field>
              <Field label="Описание">
                <textarea
                  maxLength={2000}
                  value={entry.description ?? ""}
                  onChange={(event) =>
                    setExperience((rows) =>
                      rows.map((row, i) =>
                        i === index
                          ? { ...row, description: event.target.value }
                          : row,
                      ),
                    )
                  }
                />
              </Field>
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  checked={!!entry.completed}
                  onChange={(event) =>
                    setExperience((rows) =>
                      rows.map((row, i) =>
                        i === index
                          ? { ...row, completed: event.target.checked }
                          : row,
                      ),
                    )
                  }
                />
                Завершён
              </label>
              <button
                type="button"
                className="text-button"
                onClick={() =>
                  setExperience((rows) => rows.filter((_, i) => i !== index))
                }
              >
                Удалить опыт
              </button>
            </fieldset>
          ))}
          <button
            type="button"
            className="button"
            disabled={experience.length >= 50}
            onClick={() =>
              setExperience((rows) => [
                ...rows,
                {
                  title: "",
                  description: "",
                  completed: false,
                  source: "self_reported",
                },
              ])
            }
          >
            Добавить опыт
          </button>
        </section>
        <FormActions
          busy={mutation.isPending}
          error={mutation.error}
          back="/me/profile"
        />
      </form>
      <section className="form-section">
        <h2>Фотография</h2>
        <p className="muted">Перед загрузкой сохраните изменения выше.</p>
        <FileInput
          purpose="avatar"
          resourceId={profile.user_id}
          onUploaded={async (upload) => {
            await command(
              "/me/profile",
              { version: profile.version, avatar_file_id: upload.file_id },
              "PATCH",
            );
            await client.invalidateQueries();
          }}
        />
        {profile.avatar_file_id && (
          <CommandButton
            path="/me/profile"
            method="PATCH"
            body={{ version: profile.version, avatar_file_id: null }}
          >
            Удалить фотографию
          </CommandButton>
        )}
      </section>
    </section>
  );
}
export function SettingsPage() {
  const { account } = useAuth();
  const [done, setDone] = useState("");
  const [mfa, setMfa] = useState<{ provisioning_uri: string } | null>(null);
  const [codes, setCodes] = useState<string[]>([]);
  const password = useCommand("/account/password/change");
  const setup = useCommand<typeof mfa>("/account/2fa/setup");
  const enable = useCommand<{ recovery_codes: string[] }>(
    "/account/2fa/enable",
  );
  const disable = useCommand("/account/2fa/disable");
  const logout = useCommand("/auth/logout");
  const deletion = useCommand<{ deletion_id: string }>("/account/me", "DELETE");
  const sessions = useList<Session>("/account/sessions");
  return (
    <section className="form-page">
      <Heading title="Настройки" />
      <p>{account?.email}</p>
      {done && (
        <p role="status" className="notice">
          {done}
        </p>
      )}
      {!account?.email_verified && (
        <section className="form-section">
          <h2>Email не подтверждён</h2>
          <CommandButton
            path="/auth/resend-verification"
            body={{ email: account?.email }}
            onDone={() => setDone("Письмо для подтверждения отправлено.")}
          >
            Отправить письмо
          </CommandButton>
        </section>
      )}
      <section className="form-section">
        <h2>Пароль</h2>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            const form = new FormData(event.currentTarget);
            password.mutate(
              {
                current_password: form.get("current"),
                new_password: form.get("new"),
              },
              { onSuccess: () => setDone("Пароль изменён.") },
            );
          }}
        >
          <Field label="Текущий пароль">
            <input
              required
              name="current"
              type="password"
              autoComplete="current-password"
              maxLength={128}
            />
          </Field>
          <Field label="Новый пароль">
            <input
              required
              name="new"
              type="password"
              autoComplete="new-password"
              minLength={10}
              maxLength={128}
            />
          </Field>
          <FormActions
            busy={password.isPending}
            error={password.error}
            text="Изменить пароль"
          />
        </form>
      </section>
      <section className="form-section">
        <h2>Двухфакторная авторизация</h2>
        <p className="muted">TOTP из приложения-аутентификатора.</p>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            setup.mutate(
              {
                current_password: new FormData(event.currentTarget).get(
                  "password",
                ),
              },
              { onSuccess: setMfa },
            );
          }}
        >
          <Field label="Пароль для настройки 2FA">
            <input
              name="password"
              type="password"
              required
              autoComplete="current-password"
              maxLength={128}
            />
          </Field>
          <FormActions
            busy={setup.isPending}
            error={setup.error}
            text="Настроить 2FA"
          />
        </form>
        {mfa && (
          <div className="notice">
            <p>Добавьте секрет вручную в аутентификатор:</p>
            <code className="secret">
              {new URL(mfa.provisioning_uri).searchParams.get("secret")}
            </code>
            <form
              onSubmit={(event) => {
                event.preventDefault();
                enable.mutate(
                  { code: new FormData(event.currentTarget).get("code") },
                  {
                    onSuccess: (result) => {
                      setCodes(result.recovery_codes);
                      setMfa(null);
                      setDone("2FA включена. Сохраните резервные коды.");
                    },
                  },
                );
              }}
            >
              <Field label="Код подтверждения 2FA">
                <input
                  name="code"
                  required
                  minLength={6}
                  maxLength={30}
                  autoComplete="one-time-code"
                />
              </Field>
              <FormActions
                busy={enable.isPending}
                error={enable.error}
                text="Включить 2FA"
              />
            </form>
          </div>
        )}
        {codes.length > 0 && (
          <div className="notice">
            <h3>Резервные коды — сохраните сейчас</h3>
            <ul>
              {codes.map((code) => (
                <li key={code}>
                  <code>{code}</code>
                </li>
              ))}
            </ul>
            <button className="button" onClick={() => setCodes([])}>
              Я сохранил коды
            </button>
          </div>
        )}
        <details>
          <summary>Отключить 2FA</summary>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              const form = new FormData(event.currentTarget);
              disable.mutate(
                {
                  current_password: form.get("password"),
                  code: form.get("code"),
                },
                { onSuccess: () => setDone("2FA отключена.") },
              );
            }}
          >
            <Field label="Пароль для отключения 2FA">
              <input
                name="password"
                required
                type="password"
                autoComplete="current-password"
              />
            </Field>
            <Field label="Код 2FA">
              <input
                name="code"
                required
                minLength={6}
                maxLength={30}
                autoComplete="one-time-code"
              />
            </Field>
            <FormActions
              busy={disable.isPending}
              error={disable.error}
              text="Отключить 2FA"
            />
          </form>
        </details>
      </section>
      <section className="form-section">
        <h2>Активные сессии</h2>
        <ListView result={sessions}>
          {(entry) => (
            <article className="plain-row" key={entry.id}>
              <p>
                {entry.user_agent || "Браузер"} · {entry.ip_address}
              </p>
              <CommandButton
                path={`/account/sessions/${entry.id}`}
                method="DELETE"
                confirm="Завершить эту сессию? Возможно, потребуется повторный вход."
              >
                Завершить сессию
              </CommandButton>
            </article>
          )}
        </ListView>
      </section>
      <section className="form-section">
        <h2>Выход</h2>
        <button
          className="button"
          disabled={logout.isPending}
          onClick={() =>
            logout.mutate(undefined, { onSuccess: () => session.clear() })
          }
        >
          Выйти из аккаунта
        </button>
        <ErrorNotice error={logout.error} />
        {logout.error && (
          <button className="text-button" onClick={() => session.clear()}>
            Забыть сессию на этом устройстве
          </button>
        )}
      </section>
      <details className="form-section">
        <summary>Удаление аккаунта</summary>
        <p className="muted">
          Совместные проекты будут переданы раннему участнику. Опубликованные
          материалы сохранят авторство, если не удалены отдельно.
        </p>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            if (
              !window.confirm(
                "Отправить запрос на удаление аккаунта? Это необратимое действие.",
              )
            )
              return;
            deletion.mutate(
              {
                current_password: new FormData(event.currentTarget).get(
                  "password",
                ),
                confirm: true,
              },
              {
                onSuccess: () => {
                  session.clear();
                  window.location.assign("/login");
                },
              },
            );
          }}
        >
          <Field label="Пароль для удаления аккаунта">
            <input
              required
              name="password"
              type="password"
              autoComplete="current-password"
            />
          </Field>
          <FormActions
            busy={deletion.isPending}
            error={deletion.error}
            text="Удалить аккаунт"
          />
        </form>
      </details>
    </section>
  );
}
export function DictionaryPage() {
  const [kind, setKind] = useState("skills");
  const mutation = useCommand("/dictionaries/suggestions");
  const suggestions = useList<{ id: string; name: string; status: string }>(
    "/me/dictionary-suggestions",
  );
  const [done, setDone] = useState(false);
  return (
    <section className="form-page">
      <Heading title="Предложить значение" />
      <form
        onSubmit={(event) => {
          event.preventDefault();
          const form = new FormData(event.currentTarget);
          mutation.mutate(
            { kind, name: form.get("name"), comment: form.get("comment") },
            { onSuccess: () => setDone(true) },
          );
        }}
      >
        <Field label="Справочник">
          <select
            value={kind}
            onChange={(event) => setKind(event.target.value)}
          >
            {[
              ["skills", "Навыки"],
              ["research_interests", "Научные интересы"],
              ["research_areas", "Направления"],
              ["universities", "Университеты"],
              ["specialties", "Специальности"],
              ["cities", "Города"],
            ].map(([id, name]) => (
              <option key={id} value={id}>
                {name}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Название">
          <input required minLength={2} maxLength={200} name="name" />
        </Field>
        <Field label="Комментарий">
          <textarea maxLength={1000} name="comment" />
        </Field>
        <FormActions
          busy={mutation.isPending}
          error={mutation.error}
          text="Предложить"
        />
        {done && <p role="status">Предложение отправлено на рассмотрение.</p>}
      </form>
      <section className="form-section">
        <h2>Мои предложения</h2>
        <ListView result={suggestions}>
          {(entry) => (
            <p className="plain-row" key={entry.id}>
              {entry.name} · {entry.status}
            </p>
          )}
        </ListView>
      </section>
    </section>
  );
}
