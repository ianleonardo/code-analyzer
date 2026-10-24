import { FormEvent, useEffect, useState } from "react";
import { api, errorText, post, put } from "../../api";
import { useAuth } from "../../auth";
import { ErrorNote, Field, PageHeader } from "../../components/ui";
import { can } from "../../permissions";
import type { LlmProvider, Settings } from "../../types";

type Form = { chat_model: string; chat_base_url: string; embedding_model: string; embedding_base_url: string; api_key: string; embedding_api_key: string; verify_ssl: boolean; max_model_length: string };

function formFor(p: LlmProvider): Form {
  return { chat_model: p.chat_model, chat_base_url: p.chat_base_url, embedding_model: p.embedding_model, embedding_base_url: p.embedding_base_url, api_key: "", embedding_api_key: "", verify_ssl: p.verify_ssl !== false, max_model_length: String(p.max_model_length ?? 128000) };
}

function ProviderCard({ provider, active, onActivate, onSaved }: { provider: LlmProvider; active: boolean; onActivate: () => void; onSaved: (p: LlmProvider) => void }) {
  const [form, setForm] = useState<Form>(formFor(provider));
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [testing, setTesting] = useState(false);
  const set = (field: keyof Form, value: string | boolean) => setForm((f) => ({ ...f, [field]: value }));
  const spirare = provider.provider === "spirare";

  async function save(event: FormEvent) {
    event.preventDefault();
    setError("");
    setMessage("");
    try {
      const payload = spirare ? form : { ...form, max_model_length: undefined, verify_ssl: undefined };
      const saved = await put<LlmProvider>(`/api/settings/providers/${provider.provider}`, payload);
      setForm(formFor(saved));
      setMessage("Saved");
      onSaved(saved);
    } catch (err) {
      setError(errorText(err));
    }
  }
  async function clearKey(field: "api_key" | "embedding_api_key") {
    const saved = await put<LlmProvider>(`/api/settings/providers/${provider.provider}`, { [`clear_${field}`]: true });
    onSaved(saved);
    setMessage("Key removed");
  }
  async function test() {
    setTesting(true);
    setMessage("");
    setError("");
    try {
      const result = await post<{ chat: string; embedding: string }>(`/api/settings/providers/${provider.provider}/test`);
      setMessage(`Chat: ${result.chat} · Embeddings: ${result.embedding}`);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setTesting(false);
    }
  }

  return (
    <form className={`panel space-y-3 p-3 ${active ? "border-primary" : ""}`} onSubmit={(e) => void save(e)} aria-label={`${provider.label} settings`}>
      <div className="flex items-center justify-between">
        <h2 className="flex items-center gap-2 text-head-md">
          <span className="material-symbols-outlined text-primary">{spirare ? "hub" : "auto_awesome"}</span>
          {provider.label}
        </h2>
        <label className="flex items-center gap-2 font-mono text-micro">
          <input type="radio" name="active-provider" checked={active} onChange={onActivate} aria-label={`Use ${provider.label} as active provider`} />
          {active ? <span className="text-ok">ACTIVE PROVIDER</span> : "USE AS ACTIVE"}
        </label>
      </div>
      <fieldset className="space-y-2">
        <legend className="label-micro">Inference / chat</legend>
        <Field label="Chat model"><input className="field" value={form.chat_model} onChange={(e) => set("chat_model", e.target.value)} /></Field>
        {spirare && <Field label="Max model length (tokens)"><input className="field" inputMode="numeric" value={form.max_model_length} onChange={(e) => set("max_model_length", e.target.value)} /></Field>}
        <Field label="Chat URL"><input className="field" value={form.chat_base_url} placeholder="Provider default" onChange={(e) => set("chat_base_url", e.target.value)} /></Field>
        <Field label={`API key ${provider.api_key_configured ? "(configured)" : ""}`}>
          <div className="flex gap-2">
            <input className="field" type="password" autoComplete="new-password" value={form.api_key} placeholder={provider.api_key_configured ? "Leave blank to keep the saved key" : "Enter API key"} onChange={(e) => set("api_key", e.target.value)} />
            {provider.api_key_configured && <button type="button" className="btn h-8" onClick={() => void clearKey("api_key")}>Remove</button>}
          </div>
        </Field>
      </fieldset>
      <fieldset className="space-y-2">
        <legend className="label-micro">Embeddings / retrieval</legend>
        <Field label="Embedding model"><input className="field" value={form.embedding_model} onChange={(e) => set("embedding_model", e.target.value)} /></Field>
        <Field label="Embedding URL"><input className="field" value={form.embedding_base_url} placeholder="Provider default" onChange={(e) => set("embedding_base_url", e.target.value)} /></Field>
        <Field label={`Embedding API key ${provider.embedding_api_key_configured ? "(configured)" : ""}`}>
          <div className="flex gap-2">
            <input className="field" type="password" autoComplete="new-password" value={form.embedding_api_key} placeholder="Optional. Blank uses the chat key" onChange={(e) => set("embedding_api_key", e.target.value)} />
            {provider.embedding_api_key_configured && <button type="button" className="btn h-8" onClick={() => void clearKey("embedding_api_key")}>Remove</button>}
          </div>
        </Field>
      </fieldset>
      {spirare && (
        <label className="flex items-center gap-2 text-body-md">
          <input type="checkbox" checked={!form.verify_ssl} onChange={(e) => set("verify_ssl", !e.target.checked)} aria-label="Bypass SSL certificate check" />
          Bypass SSL certificate check
        </label>
      )}
      <ErrorNote message={error} />
      {message && <p className="font-mono text-micro text-ok" role="status">{message}</p>}
      <div className="flex gap-2">
        <button className="btn btn-primary" type="submit">Save {provider.label}</button>
        <button className="btn" type="button" disabled={testing} onClick={() => void test()}>
          <span className="material-symbols-outlined icon-sm">network_check</span>{testing ? "Testing…" : "Test connection"}
        </button>
      </div>
    </form>
  );
}

export function LlmPage() {
  const { user } = useAuth();
  const canWrite = can(user, "settings", "write");
  const [settings, setSettings] = useState<Settings | null>(null);
  const [prompt, setPrompt] = useState("");
  const [fileLimit, setFileLimit] = useState("40");
  const [chunkLimit, setChunkLimit] = useState("3000");
  const [catalogLimit, setCatalogLimit] = useState("60");
  const [prLimit, setPrLimit] = useState("25");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  useEffect(() => {
    api<Settings>("/api/settings").then((s) => { setSettings(s); setPrompt(s.global_system_prompt); setFileLimit(String(s.llm_file_limit)); setChunkLimit(String(s.embedding_chunk_limit)); setCatalogLimit(String(s.llm_catalog_limit ?? 60)); setPrLimit(String(s.llm_pr_function_limit ?? 25)); }).catch((err) => setError(errorText(err)));
  }, []);

  async function saveGeneral(body: Partial<Settings> & Record<string, unknown>) {
    setError("");
    setMessage("");
    try {
      const saved = await put<Settings>("/api/settings", body);
      setSettings(saved);
      setMessage("Saved");
    } catch (err) {
      setError(errorText(err));
    }
  }

  return (
    <div>
      <PageHeader icon="smart_toy" title="LLM" subtitle="Two providers, as in SpecForge: Gemini and Spirare. API keys are encrypted and write-only." />
      <div className="max-w-6xl space-y-4 p-4">
        <ErrorNote message={error} />
        {!canWrite && <p className="text-muted">View only. Your role can see these settings but not change them.</p>}
        {settings && (
          <fieldset disabled={!canWrite} className="min-w-0 space-y-4">
            <div className="grid gap-4 lg:grid-cols-2">
              {settings.providers.map((p) => (
                <ProviderCard
                  key={p.provider}
                  provider={p}
                  active={settings.active_provider === p.provider}
                  onActivate={() => void saveGeneral({ active_provider: p.provider })}
                  onSaved={(saved) => setSettings((s) => (s ? { ...s, providers: s.providers.map((x) => (x.provider === saved.provider ? saved : x)) } : s))}
                />
              ))}
            </div>
            <form className="panel space-y-3 p-3" onSubmit={(e) => { e.preventDefault(); void saveGeneral({ global_system_prompt: prompt, llm_file_limit: Number(fileLimit), embedding_chunk_limit: Number(chunkLimit), llm_catalog_limit: Number(catalogLimit), llm_pr_function_limit: Number(prLimit) }); }}>
              <div className="label-micro">Analysis & chat</div>
              <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
                <Field label="Files summarised by the LLM per analysis" hint="The most central files (PageRank), visited bottom-up. Cached by content hash across runs. 0 disables.">
                  <input className="field" inputMode="numeric" value={fileLimit} onChange={(e) => setFileLimit(e.target.value)} />
                </Field>
                <Field label="Code chunks embedded per analysis" hint="Dense vectors for chat retrieval; unchanged chunks reuse earlier vectors. 0 = BM25 only.">
                  <input className="field" inputMode="numeric" value={chunkLimit} onChange={(e) => setChunkLimit(e.target.value)} />
                </Field>
                <Field label="Layer catalogue items explained by the LLM" hint="Purpose and business rules for endpoints, screens, services, jobs and tables (most important first). Cached by content hash. 0 = deterministic only.">
                  <input className="field" inputMode="numeric" value={catalogLimit} onChange={(e) => setCatalogLimit(e.target.value)} />
                </Field>
                <Field label="Changed functions explained per PR impact run" hint="The LLM reads the before / after code of the riskiest changed functions; claims are cross-checked against the code. Cached by content hash. 0 = deterministic only.">
                  <input className="field" inputMode="numeric" value={prLimit} onChange={(e) => setPrLimit(e.target.value)} />
                </Field>
              </div>
              <Field label="Global system prompt (Chat with Analyst)">
                <textarea className="field min-h-32 font-sans" value={prompt} onChange={(e) => setPrompt(e.target.value)} />
              </Field>
              {message && <p className="font-mono text-micro text-ok">{message}</p>}
              {canWrite && <button className="btn btn-primary" type="submit">Save</button>}
            </form>
          </fieldset>
        )}
      </div>
    </div>
  );
}
