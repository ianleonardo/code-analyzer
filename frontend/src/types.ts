import type { Permissions } from "./permissions";

export type User = {
  user_id: string;
  username: string;
  login_id: string;
  role_id: string;
  role_name: string;
  auth_provider?: "local" | "ldap";
  ldap_domain?: string;
  permissions?: Permissions;
  created_at?: string;
  last_login_at?: string | null;
};

export type RoleRecord = {
  role_id: string;
  name: string;
  description: string;
  repositories: "none" | "read" | "write";
  insights: "none" | "read" | "write";
  settings: "none" | "read" | "write";
  is_system: boolean;
  locked: boolean;
  user_count: number;
};

export type AnalysisStatus = "queued" | "running" | "completed" | "failed" | "cancelled";

export type AnalysisBrief = {
  analysis_id: string;
  repo_id: string;
  repo_name?: string;
  source_type?: string;
  branch: string;
  commit_sha: string;
  commit_message?: string;
  status: AnalysisStatus;
  stage: string;
  progress: number;
  message: string;
  health_score: number | null;
  file_count: number;
  loc_total: number;
  architecture_style: string;
  llm_used?: boolean;
  created_at: string;
  started_at?: string | null;
  finished_at: string | null;
  started_by_name?: string;
  run_count?: number;
};

export type Repository = {
  repo_id: string;
  name: string;
  source_type: string;
  provider_label: string;
  url: string;
  web_url: string;
  host: string;
  owner: string;
  slug: string;
  default_branch: string;
  description: string;
  is_private: boolean;
  created_at: string;
  updated_at: string;
  latest_analysis: AnalysisBrief | null;
  analysis_count: number;
  branch_runs?: AnalysisBrief[];
  analyses?: AnalysisBrief[];
  started_analysis?: AnalysisBrief;
};

export type Framework = { name: string; category: string; evidence: string; version?: string; containers?: string[] };
export type Container = { container_id: string; name: string; path: string; ecosystem: string; kind: string; manifest: string; frameworks: Framework[]; file_count: number; loc: number; summary: string };

export type Stats = {
  health_score: number;
  penalties: Record<string, number>;
  avg_complexity: number;
  max_complexity: number;
  duplication_pct: number;
  cycles: number;
  cyclic_files: number;
  layer_violations: number;
  god_modules: number;
  secrets: number;
  test_files: number;
  test_ratio: number;
  unused_files: number;
  todo_markers: number;
  concerns_by_severity: Record<string, number>;
  languages: Record<string, number>;
  layers: Record<string, number>;
  roles: Record<string, number>;
  skipped: Record<string, number>;
  files_total: number;
  files_text: number;
  files_source: number;
  files_parsed: number;
  symbols: number;
  edges: number;
  edge_kinds: Record<string, number>;
  imports_resolved: number;
  imports_unresolved: number;
  chunks: number;
  embedded_chunks: number;
  llm_provider: string;
  llm_file_summaries: number;
  duration_seconds: number;
  top_pagerank: { path: string; score: number }[];
  sccs: string[][];
};

export type Macro = {
  architecture_style: string;
  style_evidence: string[];
  monorepo: boolean;
  frameworks: Framework[];
  entry_points: string[];
  external_systems: { name: string; type: string; evidence: string }[];
  infrastructure: Record<string, unknown>[];
  ci: string[];
  docs: string[];
  readme_excerpt: string;
  drift: { claim: string; detail: string }[];
  business_purpose: string;
  key_capabilities: string[];
  architecture_notes: string[];
  risks: string[];
  external_packages: string[];
};

export type C4 = { context: string; container: string; components: Record<string, string>; dynamic: string; layers: string; containers: { name: string; path: string }[] };

export type Analysis = AnalysisBrief & {
  error: string;
  merkle_root: string;
  commit_author: string;
  commit_date: string;
  summary: string;
  stats: Stats;
  macro: Macro;
  c4: C4;
  repo: { repo_id: string; name: string; source_type: string; url: string; host: string; owner: string; slug: string; default_branch: string; description: string };
  containers: Container[];
};

export type FileRow = {
  file_id: string;
  path: string;
  directory: string;
  language: string;
  container: string;
  layer: Layer;
  role: string;
  role_confidence: number;
  size_bytes: number;
  loc: number;
  code_lines: number;
  comment_lines: number;
  blank_lines: number;
  complexity: number;
  max_function_complexity: number;
  symbol_count: number;
  fan_in: number;
  fan_out: number;
  pagerank: number;
  scc_id: number | null;
  is_entry: boolean;
  is_test: boolean;
  is_generated: boolean;
  is_source: boolean;
  summary: string;
  summary_source: string;
  concepts: string[];
  evidence: string[];
};

export type SymbolRow = { name: string; qualified_name: string; kind: string; parent: string; line_start: number; line_end: number; complexity: number; signature: string; path?: string };
export type EdgeRef = { path: string; kind: string; weight: number; detail: string; layer: Layer; role: string };
export type Blast = { path: string; impacted_count: number; test_count: number; direct: number; risk: number; risk_label: string; layers: Record<string, number>; impacted: { path: string; depth: number; layer: Layer; role: string; is_test: boolean }[] };
export type Concern = { concern_id?: string; severity: "critical" | "warning" | "info"; category: string; title: string; path: string; layer?: Layer; detail: string; evidence: string };

export type FileDetail = FileRow & {
  content: string | null;
  symbols: SymbolRow[];
  outgoing: EdgeRef[];
  incoming: EdgeRef[];
  concerns: Concern[];
  blast_radius: Blast;
};

export type Layer = "api" | "service" | "data" | "ui" | "shared";
export const LAYERS: Layer[] = ["api", "service", "data", "ui", "shared"];

export type GraphNode = { path: string; layer: Layer; role: string; language: string; code_lines: number; complexity: number; fan_in: number; fan_out: number; pagerank: number; scc_id: number | null; is_entry: boolean };
export type GraphEdge = { source_path: string; target_path: string; kind: string; weight: number };

export type TreeNode = { name: string; path: string; type: "dir" | "file"; files?: number; loc: number; layers?: Record<string, number>; layer?: Layer; role?: string; language?: string; complexity?: number; is_test?: boolean; fan_in?: number; scc_id?: number | null; children?: TreeNode[] };

export type ChatSource = { analysis_id: string; path: string; scope: string; line_start: number; line_end: number; score: number; side?: "left" | "right" };
export type ChatMessage = { message_id: string; role: "user" | "assistant"; content: string; sources: ChatSource[]; created_at: string };
export type ChatSession = {
  session_id: string;
  user_id?: string;
  owner_name?: string;
  is_public?: boolean;
  is_owner?: boolean;
  scope_type: "analysis" | "comparison";
  scope_id: string;
  title: string;
  scope_label?: string;
  created_at: string;
  updated_at: string;
  message_count?: number;
  messages?: ChatMessage[];
  llm_configured?: boolean;
};

export type LlmProvider = {
  provider: "gemini" | "spirare";
  label: string;
  chat_model: string;
  chat_base_url: string;
  embedding_model: string;
  embedding_base_url: string;
  api_key_configured: boolean;
  embedding_api_key_configured: boolean;
  verify_ssl: boolean;
  max_model_length: number;
  updated_at: string;
};
export type Settings = { active_provider: "gemini" | "spirare"; global_system_prompt: string; llm_file_limit: number; embedding_chunk_limit: number; llm_catalog_limit?: number; llm_pr_function_limit?: number; updated_at: string; providers: LlmProvider[] };

export type GitCheck = {
  repo: { provider: string; provider_label: string; host: string; owner: string; slug: string; name: string; clone_url: string; web_url: string };
  status: "ok" | "auth_required" | "no_access" | "not_found" | "error";
  message: string;
  has_credential: boolean;
  used_credential: boolean;
  default_branch?: string;
  is_private?: boolean;
  description?: string;
  oauth_available: boolean;
  token_help: { url: string; hint: string; username_required: boolean; username_label?: string };
};
export type Branch = { name: string; sha: string };
export type GitCredential = { credential_id: string; provider: string; provider_label: string; host: string; username: string; account_name: string; auth_method: string; created_at: string; last_verified_at: string | null };

export type Comparison = {
  comparison_id: string;
  title: string;
  left_analysis_id: string;
  right_analysis_id: string;
  narrative: string;
  created_at: string;
  created_by_name?: string;
  result: ComparisonResult;
};
export type Side = { analysis_id: string; repo_id: string; repo_name: string; branch: string; commit_sha: string; finished_at: string; health_score: number; summary: string };
export type CatalogDiffPart = { added: string[]; removed: string[]; changed: { name: string; changes: { text: string; breaking: boolean }[] }[] };
export type CatalogDiff = { available: boolean; breaking: string[]; [kind: string]: CatalogDiffPart | string[] | boolean | undefined };
export type ComparisonResult = {
  catalog?: CatalogDiff;
  left: Side;
  right: Side;
  same_repository: boolean;
  metrics: { metric: string; left: number; right: number; delta: number | null }[];
  stack: { only_left: string[]; only_right: string[]; common: string[]; categories: Record<string, string> };
  dependencies: { only_left: string[]; only_right: string[]; version_changes: { name: string; ecosystem: string; left: string; right: string }[]; common: number };
  dependency_similarity_pct: number;
  structure: {
    architecture_style: { left: string; right: string };
    languages: { left: Record<string, number>; right: Record<string, number> };
    layers: { left: Record<string, number>; right: Record<string, number> };
    roles: { left: Record<string, number>; right: Record<string, number> };
    containers: { left: string[]; right: string[] };
  };
  files: {
    added: { path: string; loc: number; layer: string; role: string }[];
    removed: { path: string; loc: number; layer: string; role: string }[];
    modified: { path: string; loc_left: number; loc_right: number; delta: number; complexity_delta: number; layer: string; role: string }[];
    counts: { added: number; removed: number; modified: number; unchanged: number };
    content_similarity_pct: number;
  };
  symbols: { added?: string[]; removed?: string[]; added_count?: number; removed_count?: number };
  concerns: { new: Concern[]; resolved: Concern[]; persisting: number };
};

export type Dashboard = {
  llm_configured: boolean;
  llm_provider: string;
  embedding_configured: boolean;
  database: { engine: string; location: string; schema_version: number };
  repositories?: number;
  analyses?: Record<string, number>;
  running?: { analysis_id: string; repo_name: string; branch: string; status: string; stage: string; progress: number; message: string }[];
  recent?: (AnalysisBrief & { languages: string[]; concerns: Record<string, number> })[];
  total_loc?: number;
  avg_health?: number | null;
  comparisons?: number;
  chats?: number;
};

export type AnalysisEvent = { event_id: string; ts: string; stage: string; level: string; message: string };

// --- pull-request impact analysis -----------------------------------------------------------------------------

export type ImpactStatus = AnalysisStatus;
export type RiskLabel = "LOW" | "MODERATE" | "HIGH" | "CRITICAL" | "";
export type ImpactSeverity = "critical" | "high" | "medium" | "low" | "info";

export type PullRequest = {
  number: number; title: string; author: string; description?: string; source_branch: string; target_branch: string; head_sha: string; base_sha: string;
  url: string; state: string; updated_at: string; source_clone_url?: string; stale?: boolean;
  latest_run?: { run_id: string; status: ImpactStatus; risk: number | null; risk_label: RiskLabel; head_sha: string; created_at: string } | null;
};
export type PullRequestList = { supported: boolean; provider: string; pull_requests: PullRequest[]; message?: string };
export type GitRef = { name: string; sha: string; type: "branch" | "tag" };

export type ImpactRunBrief = {
  run_id: string; repo_id: string; pr_id: string; mode: "pr" | "refs"; title: string; base_ref: string; head_ref: string; base_sha: string; head_sha: string;
  merge_base_sha: string; base_analysis_id: string; head_analysis_id: string; status: ImpactStatus; stage: string; progress: number; message: string; error: string;
  risk: number | null; risk_label: RiskLabel; llm_used: boolean; ruleset_version: string; started_by: string; created_at: string; started_at: string | null; finished_at: string | null;
  repo_name?: string; pr_number?: number | null; pr_url?: string | null; stats?: Partial<ImpactStats>;
};
export type ImpactStats = {
  files: number; additions: number; deletions: number; symbols: number; impacted: number; impacted_strong: number; entry_points: number; ahead: number; behind: number;
  history_shallow: boolean; llm_functions: number; llm_functions_cached: number; duration_seconds: number;
};
export type ImpactFile = {
  path: string; old_path: string; status: "A" | "M" | "D" | "R"; additions: number; deletions: number; binary: boolean; layer: string; role: string; language: string;
  container: string; change_kinds: string[]; tags_added: string[]; tags_removed: string[]; sensitive: boolean;
};
export type LogicExplanation = {
  summary: string; rules_changed: { before: string; after: string; line: number }[]; inputs_outputs: string;
  side_effects: { kind: string; detail: string; line: number; verified: "yes" | "unverified" | "n/a" }[]; error_handling: string; risk_notes: string[]; test_focus: string[]; unverified: number;
};
export type ChangedSymbol = {
  path: string; qualified_name: string; name: string; kind: string; change_type: "added" | "modified" | "removed"; signature_before: string; signature_after: string;
  exported: boolean; line_start: number; line_end: number; flags: string[]; logic: Partial<LogicExplanation>; logic_source: "" | "llm" | "cache";
};
export type ImpactedFile = { path: string; depth: number; via_path: string; via_kind: string; symbols: string[]; confidence: "high" | "medium" | "low"; reason: string; layer: string; role: string; is_test: boolean; container?: string };
export type EntryPoint = {
  item_key: string; kind: string; name: string; path: string; line: number; layer: string; reach: "removed" | "added" | "direct" | "same file" | "transitive" | "linked";
  depth: number; via: string; symbols: string[]; framework: string; summary: string; feature_ids?: string[]; contract_changes?: { text: string; breaking: boolean }[]; breaking?: boolean;
};
export type ConfigFinding = { severity: ImpactSeverity; rule: string; message: string; key?: string; environment?: string; path?: string; line?: number };
export type ConfigKeyChange = {
  key: string; environment: string; change: "added" | "removed" | "changed"; before: string | null; after: string | null; path: string; line: number; file_kind: string;
  secret: boolean; readers: { path: string; line: number; via: string }[]; category: string; consumer: string; findings: ConfigFinding[];
};
export type ImpactResult = {
  pr: { number: number; title: string; author: string; description: string; url: string; source_branch: string; target_branch: string; state: string } | null;
  files: ImpactFile[]; entry_points: EntryPoint[]; impacted: ImpactedFile[];
  config: { keys: ConfigKeyChange[]; findings: ConfigFinding[]; infra: ConfigFinding[]; ci: ConfigFinding[]; environments: Record<string, { added: number; removed: number; changed: number; findings: number }>; files: { path: string; status: string; kind: string; environment: string }[]; external_config: string[] };
  tests: { tests: { path: string; reason: string; depth: number }[]; covered: { path: string; symbol: string; change_type: string; tests: string[] }[]; indirect: { path: string; symbol: string; change_type: string; tests: string[] }[]; gaps: { path: string; symbol: string; change_type: string }[]; functions: number; gap_ratio: number };
  graph: {
    added_edges: { source: string; target: string; kind: string }[]; removed_edges: { source: string; target: string; kind: string }[]; added_count: number; removed_count: number;
    layer_violations: { source: string; target: string; kind: string; from_layer: string; to_layer: string; severity: string }[]; new_cycles: string[][]; resolved_cycles: string[][];
    behaviour: { path: string; tag: string; change: string }[]; containers: { container: string; changed: number; impacted: number }[];
  };
  dependencies: { added: { name: string; ecosystem: string; version: string; container: string }[]; removed: { name: string; ecosystem: string; version: string; container: string }[]; changed: { name: string; ecosystem: string; before: string; after: string; major: boolean; downgrade: boolean; container: string }[] };
  concerns: { new: Concern[]; resolved: Concern[]; new_in_scope: Concern[] };
  reviewers: { name: string; source: string; files: string[]; score: number }[];
  risk: { score: number; label: RiskLabel; factors: { factor: string; value: number; weight: number; contribution: number; detail: string }[]; overrides: string[]; hotspots: { path: string; commits: number }[] };
  features: { feature_id: string; name: string; confirmed: boolean }[];
  stats: ImpactStats; limits: string[];
};
export type ImpactNarrative = { summary?: string; risk_explanation?: string; features?: { feature: string; summary: string }[]; description_drift?: string[]; review_checklist?: string[]; source?: "llm" | "rules" };
export type ImpactRun = ImpactRunBrief & {
  repo: { repo_id: string; name: string; source_type: string; url: string; default_branch: string };
  result: ImpactResult; narrative: ImpactNarrative; symbols: ChangedSymbol[]; started_by_name: string;
  pull_request: { number: number; title: string; author: string; description: string; url: string; source_branch: string; target_branch: string; state: string; head_sha: string } | null;
};
export type ImpactEvent = { event_id: string; run_id: string; ts: string; level: string; message: string };
export type DiffHunk = { old_start: number; old_count: number; new_start: number; new_count: number; header: string; added: [number, string][]; removed: [number, string][] };
export type FileDiffPayload = { path: string; old_path: string; status: string; additions: number; deletions: number; change_kinds: string[]; hunks: DiffHunk[] };
export type FeaturePattern = { type: "route" | "path" | "item" | "name"; value: string };
export type Feature = {
  feature_id: string; repo_id: string; name: string; description: string; patterns: FeaturePattern[]; source: string; confirmed: boolean; created_at: string; updated_at: string;
  entry_count?: number; entries?: { item_key: string; kind: string; name: string; path: string }[];
};
export type FeatureCatalogue = { analysis_id: string; features: Feature[]; unassigned: { item_key: string; kind: string; name: string; path: string }[]; entry_total: number; created?: number };
