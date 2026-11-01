import { Component, useEffect } from "react";
import type { ReactNode, ErrorInfo } from "react";
import { Link, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { useAuth, RequireAuth } from "./ui/auth";
import { Loading } from "./ui/common";
import { useResource } from "./lib/data";
import { HomePage, CatalogPage, ProfilePage } from "./pages/browse";
import {
  LoginPage,
  RegisterPage,
  RecoveryPage,
  SettingsPage,
  ProfileEditPage,
  DictionaryPage,
} from "./pages/account";
import {
  ProjectPage,
  ProjectFormPage,
  ProjectManagePage,
} from "./pages/projects";
import { PaperPage, PaperFormPage } from "./pages/papers";
import {
  WorkspacePage,
  MyListPage,
  RequestsPage,
  FavoritesPage,
  NotificationsPage,
} from "./pages/workspace";
import { AdminPage } from "./pages/admin";

class Boundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  componentDidCatch(_error: Error, _info: ErrorInfo) {}
  render() {
    return this.state.failed ? (
      <section>
        <h1>Не удалось открыть страницу</h1>
        <p>Попробуйте обновить приложение.</p>
        <button className="button" onClick={() => window.location.reload()}>
          Обновить
        </button>
      </section>
    ) : (
      this.props.children
    );
  }
}
function ScrollAndTitle() {
  const { pathname } = useLocation();
  useEffect(() => {
    window.scrollTo(0, 0);
    document.title = "Stepork Science";
  }, [pathname]);
  return null;
}
function Header() {
  const { account } = useAuth();
  const count = useResource<{ count: number }>(
    "/notifications/unread-count",
    !!account,
  );
  return (
    <>
      <a
        className="skip-link"
        href="#main"
        onClick={(event) => {
          event.preventDefault();
          document.getElementById("main")?.focus();
        }}
      >
        К содержимому
      </a>
      <header className="site-header">
        <div className="header-inner">
          <Link to="/" className="brand">
            Stepork <span>Science</span>
          </Link>
          <nav className="primary-nav" aria-label="Основная навигация">
            <NavLink to="/projects">Проекты</NavLink>
            <NavLink to="/researchers">Исследователи</NavLink>
            <NavLink to="/papers">Работы</NavLink>
            <NavLink to="/workspace">Мой раздел</NavLink>
          </nav>
          <div className="header-actions">
            {account ? (
              <>
                <Link
                  className="icon-button notification-button"
                  to="/notifications"
                  aria-label={`Уведомления${count.data?.count ? `: ${count.data.count} непрочитанных` : ""}`}
                >
                  <svg viewBox="0 0 24 24" aria-hidden="true">
                    <path d="M5 17h14l-2-3V9a5 5 0 0 0-10 0v5zM10 21h4" />
                  </svg>
                  {!!count.data?.count && <i />}
                </Link>
                <Link
                  className="button button-primary create-link"
                  to="/projects/new"
                >
                  Создать проект
                </Link>
                <Link
                  className="avatar"
                  to="/me/profile"
                  aria-label="Мой профиль"
                >
                  Я
                </Link>
              </>
            ) : (
              <Link to="/login" className="button">
                Войти
              </Link>
            )}
          </div>
        </div>
      </header>
    </>
  );
}
const protectedPage = (page: ReactNode) => <RequireAuth>{page}</RequireAuth>;
export function App() {
  const { ready } = useAuth();
  const location = useLocation();
  if (!ready)
    return (
      <main>
        <Loading text="Восстановление сессии…" />
      </main>
    );
  return (
    <>
      <ScrollAndTitle />
      <Header />
      <main id="main" tabIndex={-1}>
        <Boundary key={location.pathname}>
          <Routes>
            <Route path="/" element={<HomePage />} />
            <Route path="/projects" element={<CatalogPage type="projects" />} />
            <Route path="/researchers" element={<CatalogPage type="users" />} />
            <Route path="/papers" element={<CatalogPage type="papers" />} />
            <Route path="/researchers/:id" element={<ProfilePage />} />
            <Route
              path="/projects/new"
              element={protectedPage(<ProjectFormPage />)}
            />
            <Route path="/projects/:id" element={<ProjectPage />} />
            <Route
              path="/projects/:id/edit"
              element={protectedPage(<ProjectFormPage />)}
            />
            <Route
              path="/projects/:id/manage"
              element={protectedPage(<ProjectManagePage />)}
            />
            <Route
              path="/papers/new"
              element={protectedPage(<PaperFormPage />)}
            />
            <Route path="/papers/:id" element={<PaperPage />} />
            <Route
              path="/papers/:id/edit"
              element={protectedPage(<PaperFormPage />)}
            />
            <Route path="/login" element={<LoginPage />} />
            <Route path="/register" element={<RegisterPage />} />
            <Route
              path="/forgot-password"
              element={<RecoveryPage mode="forgot" />}
            />
            <Route
              path="/reset-password"
              element={<RecoveryPage mode="reset" />}
            />
            <Route
              path="/verify-email"
              element={<RecoveryPage mode="verify" />}
            />
            <Route
              path="/me/profile"
              element={protectedPage(<ProfilePage own />)}
            />
            <Route
              path="/me/profile/edit"
              element={protectedPage(<ProfileEditPage />)}
            />
            <Route
              path="/workspace"
              element={protectedPage(<WorkspacePage />)}
            />
            <Route
              path="/me/projects"
              element={protectedPage(<MyListPage type="projects" />)}
            />
            <Route
              path="/me/teams"
              element={protectedPage(<MyListPage type="teams" />)}
            />
            <Route
              path="/me/papers"
              element={protectedPage(<MyListPage type="papers" />)}
            />
            <Route
              path="/me/applications"
              element={protectedPage(<RequestsPage type="applications" />)}
            />
            <Route
              path="/me/invitations"
              element={protectedPage(<RequestsPage type="invitations" />)}
            />
            <Route
              path="/favorites"
              element={protectedPage(<FavoritesPage />)}
            />
            <Route
              path="/notifications"
              element={protectedPage(<NotificationsPage />)}
            />
            <Route path="/settings" element={protectedPage(<SettingsPage />)} />
            <Route
              path="/dictionaries/suggest"
              element={protectedPage(<DictionaryPage />)}
            />
            <Route
              path="/admin"
              element={
                <RequireAuth admin>
                  <AdminPage />
                </RequireAuth>
              }
            />
            <Route
              path="*"
              element={
                <section>
                  <h1>Страница не найдена</h1>
                  <Link className="section-link" to="/">
                    На главную
                  </Link>
                </section>
              }
            />
          </Routes>
        </Boundary>
      </main>
      <footer className="page-footer">
        <span>Stepork Science</span>
        <Link to="/workspace">Личный кабинет</Link>
      </footer>
      <nav className="mobile-nav" aria-label="Мобильная навигация">
        <NavLink to="/" end>
          Главная
        </NavLink>
        <NavLink to="/researchers">Поиск</NavLink>
        <NavLink to="/projects">Проекты</NavLink>
        <NavLink to="/workspace">Мой раздел</NavLink>
        <NavLink to="/me/profile">Профиль</NavLink>
      </nav>
    </>
  );
}
