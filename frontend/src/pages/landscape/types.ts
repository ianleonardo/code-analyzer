export type LinkKind = "http" | "messaging" | "database" | "external";
export type LinkStatus = "auto" | "confirmed" | "review" | "rejected" | "candidate" | "external" | "unresolved";

export type LandscapeRepo = { repo_id: string; name: string; source_type: string; default_branch: string; pinned_analysis_id: string };
export type LandscapeStats = {
  links?: number; external?: number; review?: number; unresolved?: number; by_kind?: Record<string, number>;
  members?: { repo_id: string; name: string; analysis_id: string; branch: string }[]; missing?: string[];
};
export type Landscape = {
  landscape_id: string; name: string; description: string; auto_accept: "high" | "high_medium"; status: string; message: string;
  created_by: string; created_by_name: string; created_at: string; updated_at: string; computed_at: string | null; stats: LandscapeStats; repos: LandscapeRepo[];
};

export type LandscapeLink = {
  link_id: string; landscape_id: string; group_key: string; kind: LinkKind; direction: "out" | "in";
  source_repo_id: string; source_repo_name: string; source_analysis_id: string; source_key: string; source_name: string; source_kind: string; source_path: string; source_line: number;
  target_repo_id: string; target_repo_name: string; target_analysis_id: string; target_key: string; target_name: string; target_kind: string; target_path: string; target_line: number;
  external_name: string; external_type: string; label: string; score: number; confidence: "" | "high" | "medium" | "low"; ambiguous: boolean; status: LinkStatus;
  features: string[]; evidence: string[]; explanation: string;
};

export type ReviewGroup = {
  group_key: string; kind: LinkKind; source_repo_id: string; source_repo_name: string; source_name: string; source_kind: string; source_path: string; source_line: number;
  label: string; candidates: LandscapeLink[]; features: string[]; ambiguous: boolean; top_score?: number;
  decision?: { targets?: string[]; external?: boolean; external_name?: string; reject?: boolean; note: string; decided_at: string; decided_by: string };
};
export type ReviewQueue = { pending: ReviewGroup[]; decided: ReviewGroup[]; unresolved: ReviewGroup[] };

export type MapNode = { id: string; label: string; type: "repository" | "external"; source_type?: string; external_type?: string };
export type MapEdge = { source: string; target: string; kind: LinkKind; count: number; review: number; labels: string[]; features: string[]; links?: LandscapeLink[] };
export type SystemMap = { nodes: MapNode[]; edges: MapEdge[]; features: string[]; mermaid: string };

export type ProvidedInterface = {
  repo_id: string; repo_name: string; key: string; name: string; kind: LinkKind; item_kind: string; path: string; line: number; features: string[]; analysis_id?: string;
  consumers: { repo_id: string; repo_name: string; name: string; path: string; line: number; status: string; confidence: string }[];
};
export type ExternalSystem = { name: string; type: string; users: { repo_id: string; repo_name: string; name: string; kind: string; direction: string }[] };

export type EntryPoint = { repo_id: string; repo_name: string; item_key: string; kind: string; name: string; path: string };
export type JourneyStep = { from: string; to: string; kind: string; label: string; via: string; status: string; target: string; group_key: string };
export type Journey = { title: string; start: { repo_id: string; key: string; name: string; kind: string }; steps: JourneyStep[]; participants: string[]; mermaid: string };

export const KIND_META: Record<LinkKind, { label: string; icon: string; color: string }> = {
  http: { label: "HTTP / REST", icon: "http", color: "text-layer-api border-layer-api/50" },
  messaging: { label: "Messaging", icon: "conversion_path", color: "text-layer-service border-layer-service/50" },
  database: { label: "Shared database", icon: "database", color: "text-layer-data border-layer-data/50" },
  external: { label: "External system", icon: "public", color: "text-muted border-line" },
};

export const STATUS_META: Record<string, { label: string; color: string }> = {
  auto: { label: "auto", color: "text-ok border-ok/50" },
  confirmed: { label: "confirmed", color: "text-ok border-ok/50 bg-ok/10" },
  review: { label: "to review", color: "text-warning border-warning/50" },
  rejected: { label: "rejected", color: "text-faint border-line" },
  candidate: { label: "alternative", color: "text-faint border-line" },
  external: { label: "external", color: "text-muted border-line" },
  unresolved: { label: "unresolved", color: "text-critical border-critical/50" },
};
