/* eslint-disable @typescript-eslint/no-explicit-any */
export type Details = Record<string, any>;

export type CatalogKind = { kind: string; label: string; count: number };
export type CatalogSummary = {
  built: boolean;
  layers: Record<string, { label: string; total: number; kinds: CatalogKind[] }>;
  stats: { items?: number; links?: number; seconds?: number; errors?: string[]; llm_explained?: number };
  databases: { name: string; item_id: string }[];
  links: number;
};

export type CatalogRow = {
  item_id: string;
  item_key: string;
  layer: string;
  kind: string;
  name: string;
  path: string;
  line_start: number;
  line_end: number;
  framework: string;
  signature: string;
  summary: string;
  summary_source: string;
  badge: Details;
};

export type LinkRef = { item_id: string; item_key: string; layer: string; kind: string; name: string; path: string; line_start: number; relation: string; detail: string };

export type CatalogItem = CatalogRow & { language: string; details: Details; links_out: LinkRef[]; links_in: LinkRef[] };

export type Trace = { nodes: { key: string; item_id: string; name: string; kind: string; layer: string }[]; edges: { source: string; target: string; kind: string; detail: string }[]; flowchart: string; sequence: string };

export type Crud = { actors: { key: string; kind: string; name: string }[]; rows: { table: string; item_id: string; cells: Record<string, string> }[] };

export const KIND_ICON: Record<string, string> = {
  endpoint: "api", service: "settings_suggest", job: "schedule", listener: "sensors", database: "database", table: "table", view: "table_view",
  repository: "inventory", query: "manage_search", procedure: "function", trigger: "bolt", screen: "web", component: "widgets", view_model: "dynamic_feed",
  api_client: "cloud_sync", dto: "data_object", enum: "list", exception: "error", config: "tune", config_class: "settings", security: "shield",
  integration: "conversion_path", constants: "pin", utility: "build", bean_config: "deployed_code", manifest: "android", topic: "forum", mapper: "swap_horiz",
  program: "memory", transaction: "confirmation_number", file: "description", copybook: "library_books",
};

export const METHOD_CLASS: Record<string, string> = {
  GET: "text-ok border-ok/40 bg-ok/10", POST: "text-primary border-primary/40 bg-primary/10", PUT: "text-warning border-warning/40 bg-warning/10",
  PATCH: "text-warning border-warning/40 bg-warning/10", DELETE: "text-critical border-critical/40 bg-critical/10", ANY: "text-muted border-line-strong bg-elevated",
};
