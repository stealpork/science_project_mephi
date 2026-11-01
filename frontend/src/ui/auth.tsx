import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  useSyncExternalStore,
} from "react";
import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { session, errorMessage } from "../lib/api";

const AuthContext = createContext({
  account: session.snapshot(),
  ready: false,
});
export function AuthProvider({ children }: { children: ReactNode }) {
  const client = useQueryClient();
  const subscribe = useCallback(
    (notify: () => void) => {
      let previous = session.snapshot()?.user_id;
      return session.subscribe(() => {
        const next = session.snapshot()?.user_id;
        if (previous !== next) {
          client.clear();
          previous = next;
        }
        notify();
      });
    },
    [client],
  );
  const account = useSyncExternalStore(subscribe, session.snapshot);
  const [ready, setReady] = useState(!session.hasRefresh());
  const [failure, setFailure] = useState("");
  useEffect(() => {
    let active = true;
    session
      .restore()
      .catch((error) => {
        if (active && session.hasRefresh()) setFailure(errorMessage(error));
      })
      .finally(() => {
        if (active) setReady(true);
      });
    return () => {
      active = false;
    };
  }, []);
  if (failure)
    return (
      <main>
        <h1>Не удалось восстановить сессию</h1>
        <p role="alert">{failure}</p>
        <button className="button" onClick={() => window.location.reload()}>
          Повторить
        </button>{" "}
        <button
          className="button"
          onClick={() => {
            session.clear();
            setFailure("");
          }}
        >
          Продолжить без входа
        </button>
      </main>
    );
  return <AuthContext value={{ account, ready }}>{children}</AuthContext>;
}
export const useAuth = () => useContext(AuthContext);
export function RequireAuth({
  children,
  admin = false,
}: {
  children: ReactNode;
  admin?: boolean;
}) {
  const auth = useAuth();
  const location = useLocation();
  if (!auth.ready) return <p role="status">Восстановление сессии…</p>;
  if (!auth.account)
    return (
      <Navigate
        to={`/login?next=${encodeURIComponent(location.pathname + location.search)}`}
        replace
      />
    );
  if (
    admin &&
    !auth.account.global_roles.some((role) =>
      ["admin", "moderator"].includes(role),
    )
  )
    return (
      <section>
        <h1>Нет доступа</h1>
        <p>Раздел доступен модераторам и администраторам.</p>
      </section>
    );
  return children;
}
