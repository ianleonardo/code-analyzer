import type { User } from "./types";

export type Area = "repositories" | "insights" | "settings";
export type Level = "none" | "read" | "write";
export type Permissions = Record<Area, Level>;

export const AREAS: { key: Area; label: string; help: string }[] = [
  { key: "repositories", label: "Repositories", help: "Register repositories, run and browse analyses" },
  { key: "insights", label: "Insights", help: "Chat with Analyst and repository comparison" },
  { key: "settings", label: "Settings", help: "LLM, users, roles, database and audit log" },
];

const RANK: Record<Level, number> = { none: 0, read: 1, write: 2 };
const NONE: Permissions = { repositories: "none", insights: "none", settings: "none" };

export function permissionsOf(user: Pick<User, "permissions"> | null | undefined): Permissions {
  return user?.permissions ? { ...NONE, ...user.permissions } : NONE;
}

export function can(user: Pick<User, "permissions"> | null | undefined, area: Area, need: Level = "read"): boolean {
  return RANK[permissionsOf(user)[area]] >= RANK[need];
}
