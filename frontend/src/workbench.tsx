import { createContext, useContext, useMemo, useState, type ReactNode } from "react";

/** What the open analysis publishes to the header (repository, branch, SHA, Merkle root, health) and search. */
export type WorkbenchInfo = {
  analysisId: string;
  repoName: string;
  branch: string;
  sha: string;
  merkle: string;
  health: number | null;
  source: string;
  status: string;
} | null;

type Value = { info: WorkbenchInfo; setInfo: (info: WorkbenchInfo) => void };
const WorkbenchContext = createContext<Value>({ info: null, setInfo: () => undefined });

export function WorkbenchProvider({ children }: { children: ReactNode }) {
  const [info, setInfo] = useState<WorkbenchInfo>(null);
  const value = useMemo(() => ({ info, setInfo }), [info]);
  return <WorkbenchContext.Provider value={value}>{children}</WorkbenchContext.Provider>;
}

export function useWorkbench() {
  return useContext(WorkbenchContext);
}
