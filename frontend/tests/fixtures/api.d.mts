import type {
  Account,
  Profile,
  Project,
  Paper,
  Application,
  Invitation,
  Notification,
  Dictionary,
} from "../../src/types";
export const ids: Record<string, string>;
export function fixtureAPI(): {
  accounts: Map<string, Account>;
  profiles: Map<string, Profile>;
  projects: Map<string, Project & { files: Record<string, unknown>[] }>;
  papers: Map<string, Paper>;
  applications: Application[];
  invitations: Invitation[];
  notifications: Notification[];
  dictionaries: Record<string, Dictionary[]>;
  favorites: { type: string; target_id: string; user_id: string }[];
  members: { id: string; user_id: string; project_id: string; role: string }[];
  calls: {
    path: string;
    method: string;
    authenticated: boolean;
    idempotency?: string;
  }[];
  handle(
    req: { method: string; headers: Record<string, string> },
    url: URL,
    body: Record<string, unknown>,
  ): Promise<{
    status: number;
    body?: unknown;
    raw?: Uint8Array;
    type?: string;
  }>;
};
