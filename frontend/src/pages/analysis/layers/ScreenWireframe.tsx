import { useEffect, useState } from "react";
import { api, errorText } from "../../../api";
import { Spinner } from "../../../components/ui";

interface ScreenWireframeProps {
  analysisId: string;
  itemId: string;
  itemName: string;
  itemPath: string;
  d: any;
  onLine?: (line: number) => void;
}

export function ScreenWireframe({ analysisId, itemId, itemName, itemPath, d, onLine }: ScreenWireframeProps) {
  // Mode: "deterministic" (Option A - default) vs "llm" (Option B - AI layout)
  const [wireframeMode, setWireframeMode] = useState<"deterministic" | "llm">("deterministic");

  // Platform detection: native mobile (iOS / Android) vs web
  const isIos = d.platform === "iOS" || itemPath.endsWith(".swift");
  const isAndroid = d.platform === "Android" || itemPath.endsWith(".kt") || (itemPath.endsWith(".java") && itemPath.includes("android"));
  const isNativeMobile = isIos || isAndroid || d.platform === "Mobile";
  const isWebPlatform = !isNativeMobile && (d.platform === "Web" || (!d.platform && (itemPath.endsWith(".tsx") || itemPath.endsWith(".jsx") || itemPath.endsWith(".html"))));
  const platformName = isIos ? "iOS" : isAndroid ? "Android" : d.platform || (isWebPlatform ? "Web" : "Mobile");

  // Device view: native mobile stays "mobile"; web defaults to "web" with responsive toggle
  const [deviceType, setDeviceType] = useState<"mobile" | "web">(isNativeMobile ? "mobile" : "web");

  // LLM state
  const [llmData, setLlmData] = useState<{ html: string; cached: boolean } | null>(
    d.wireframe_llm ? { html: d.wireframe_llm, cached: true } : null
  );
  const [loadingLlm, setLoadingLlm] = useState(false);
  const [llmError, setLlmError] = useState("");

  const fetchLlmWireframe = async (regenerate = false) => {
    setLoadingLlm(true);
    setLlmError("");
    try {
      const res = await api<{ mode: string; html: string; cached: boolean }>(
        `/api/analyses/${analysisId}/catalog/items/${itemId}/wireframe?mode=llm${regenerate ? "&regenerate=true" : ""}`
      );
      setLlmData({ html: res.html, cached: res.cached });
    } catch (err) {
      setLlmError(errorText(err));
    } finally {
      setLoadingLlm(false);
    }
  };

  useEffect(() => {
    if (wireframeMode === "llm" && !llmData && !loadingLlm && !llmError) {
      void fetchLlmWireframe(false);
    }
  }, [wireframeMode, itemId]);

  const fields: any[] = d.fields ?? [];
  const actions: any[] = d.actions ?? [];
  const title = d.title || itemName.replace(/(Activity|Fragment|View|Screen|Page)$/i, "") || "Screen";
  const primaryRoute = d.routes?.[0] || `/${itemName.toLowerCase().replace(/(activity|fragment|view|screen|page)$/i, "")}`;

  return (
    <div className="space-y-4">
      {/* Top Toolbar: Mode Switcher & Device Toggle */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line pb-3">
        <div className="flex flex-wrap items-center gap-2">
          {/* Mode segmented control */}
          <div className="flex rounded-lg border border-line bg-surface p-0.5">
            <button
              type="button"
              className={`flex items-center gap-1.5 rounded-md px-3 py-1 text-body-sm font-medium transition-colors ${
                wireframeMode === "deterministic" ? "bg-primary text-on-primary shadow-sm" : "text-muted hover:text-ink"
              }`}
              onClick={() => setWireframeMode("deterministic")}
              title="Instant deterministic wireframe from code analysis (zero latency, offline)"
            >
              <span className="material-symbols-outlined icon-sm">bolt</span>
              Instant Mockup
            </button>
            <button
              type="button"
              className={`flex items-center gap-1.5 rounded-md px-3 py-1 text-body-sm font-medium transition-colors ${
                wireframeMode === "llm" ? "bg-primary text-on-primary shadow-sm" : "text-muted hover:text-ink"
              }`}
              onClick={() => setWireframeMode("llm")}
              title="High-fidelity AI generated layout using LLM"
            >
              <span className="material-symbols-outlined icon-sm">auto_awesome</span>
              AI Layout
            </button>
          </div>

          {/* Device chassis: For native mobile (Android/iOS), show native platform badge without browser option.
              For web apps, provide Desktop Browser vs Mobile Responsive preview toggle. */}
          {isNativeMobile ? (
            <div className="flex items-center gap-1.5 rounded-lg border border-line bg-surface px-3 py-1 text-micro font-medium text-slate-300">
              <span className={`material-symbols-outlined icon-sm ${isIos ? "text-sky-400" : "text-emerald-400"}`}>
                {isIos ? "phone_iphone" : "smartphone"}
              </span>
              <span>{isIos ? "iOS (iPhone)" : isAndroid ? "Android Device" : "Mobile Device"}</span>
            </div>
          ) : (
            <div className="flex rounded-lg border border-line bg-surface p-0.5">
              <button
                type="button"
                className={`flex items-center gap-1 rounded-md px-2.5 py-1 text-micro transition-colors ${
                  deviceType === "web" ? "bg-elevated text-ink font-semibold" : "text-muted hover:text-ink"
                }`}
                onClick={() => setDeviceType("web")}
              >
                <span className="material-symbols-outlined icon-sm">laptop_mac</span>
                Desktop Browser
              </button>
              <button
                type="button"
                className={`flex items-center gap-1 rounded-md px-2.5 py-1 text-micro transition-colors ${
                  deviceType === "mobile" ? "bg-elevated text-ink font-semibold" : "text-muted hover:text-ink"
                }`}
                onClick={() => setDeviceType("mobile")}
              >
                <span className="material-symbols-outlined icon-sm">smartphone</span>
                Mobile View
              </button>
            </div>
          )}
        </div>

        {/* Right side controls */}
        <div className="flex items-center gap-2">
          {wireframeMode === "llm" && (
            <>
              {llmData?.cached && <span className="chip border-line-strong text-faint">Cached</span>}
              <button
                type="button"
                className="btn h-7 text-micro"
                onClick={() => void fetchLlmWireframe(true)}
                disabled={loadingLlm}
              >
                <span className={`material-symbols-outlined icon-sm ${loadingLlm ? "animate-spin" : ""}`}>refresh</span>
                Regenerate AI
              </button>
            </>
          )}
          {wireframeMode === "deterministic" && (
            <span className="flex items-center gap-1 text-micro text-faint">
              <span className="material-symbols-outlined icon-sm">touch_app</span>
              Click fields to jump to source code
            </span>
          )}
        </div>
      </div>

      {/* Wireframe Canvas Area */}
      <div className="flex justify-center p-2 sm:p-6 bg-canvas/30 rounded-xl border border-line/60">
        {wireframeMode === "llm" ? (
          /* Option B: LLM Wireframe View */
          loadingLlm ? (
            <div className="flex flex-col items-center justify-center p-12 text-center space-y-3 min-h-[400px]">
              <Spinner />
              <p className="text-body-md font-medium text-ink">Synthesizing visual wireframe layout with LLM...</p>
              <p className="text-micro text-muted max-w-md">
                Analyzing AST widgets, layout tags, and screen code to generate a full visual wireframe.
              </p>
            </div>
          ) : llmError ? (
            <div className="panel p-6 max-w-lg text-center space-y-3 border-critical/30 bg-critical/5">
              <div className="flex items-center justify-center text-critical">
                <span className="material-symbols-outlined text-4xl">error_outline</span>
              </div>
              <h3 className="font-semibold text-body-md text-ink">AI Wireframe Generation Unavailable</h3>
              <p className="text-body-sm text-muted">{llmError}</p>
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => setWireframeMode("deterministic")}
              >
                <span className="material-symbols-outlined icon-sm">bolt</span>
                Switch to Instant Mockup
              </button>
            </div>
          ) : llmData?.html ? (
            <div className={deviceType === "mobile" ? "w-[360px] sm:w-[380px] h-[680px] rounded-[44px] border-[10px] border-slate-800 bg-slate-950 shadow-2xl overflow-hidden flex flex-col" : "w-full max-w-4xl h-[640px] rounded-xl border border-line shadow-2xl overflow-hidden flex flex-col bg-surface"}>
              <iframe
                title="AI Wireframe"
                srcDoc={llmData.html}
                sandbox="allow-same-origin"
                className="w-full h-full border-0 bg-white"
              />
            </div>
          ) : null
        ) : (
          /* Option A: Deterministic Device Wireframe */
          deviceType === "mobile" ? (
            <MobilePhoneFrame
              platform={platformName}
              title={title}
              fields={fields}
              actions={actions}
              routes={d.routes}
              navigation={d.navigation}
              lists={d.lists}
              onLine={onLine}
            />
          ) : (
            <WebBrowserFrame
              title={title}
              route={primaryRoute}
              fields={fields}
              actions={actions}
              navigation={d.navigation}
              onLine={onLine}
            />
          )
        )}
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------------------------------------------------
// Deterministic Mobile Phone Frame (iOS / Android)
// --------------------------------------------------------------------------------------------------------------------

function MobilePhoneFrame({
  platform,
  title,
  fields,
  actions,
  routes,
  navigation,
  lists,
  onLine,
}: {
  platform: string;
  title: string;
  fields: any[];
  actions: any[];
  routes?: string[];
  navigation?: any[];
  lists?: any[];
  onLine?: (line: number) => void;
}) {
  const isIos = platform === "iOS";

  return (
    <div className="w-[340px] sm:w-[380px] h-[680px] rounded-[48px] border-[10px] border-slate-800 bg-slate-950 text-slate-100 shadow-2xl flex flex-col relative overflow-hidden select-none">
      {/* Top Sensors: Dynamic Island (iOS) vs Punch-hole (Android) */}
      <div className="relative pt-2 pb-1 px-6 flex items-center justify-between text-[11px] font-medium text-slate-400 bg-slate-950 z-20">
        <span>{isIos ? "9:41" : "10:00"}</span>
        {isIos ? (
          <div className="w-24 h-5 bg-black rounded-full flex items-center justify-center">
            <span className="w-2.5 h-2.5 rounded-full bg-slate-900 border border-slate-800" />
          </div>
        ) : (
          <div className="w-3.5 h-3.5 rounded-full bg-black border border-slate-900" />
        )}
        <div className="flex items-center gap-1.5">
          <span className="material-symbols-outlined text-[13px]">signal_cellular_alt</span>
          <span className="material-symbols-outlined text-[13px]">wifi</span>
          <span className="material-symbols-outlined text-[14px]">battery_full</span>
        </div>
      </div>

      {/* App Bar / Navigation Header */}
      <div className="px-4 py-2.5 border-b border-slate-800/80 bg-slate-900/90 backdrop-blur flex items-center justify-between z-10">
        <div className="flex items-center gap-2">
          <button type="button" className="text-slate-400 hover:text-white transition-colors">
            <span className="material-symbols-outlined icon-sm">{isIos ? "arrow_back_ios" : "arrow_back"}</span>
          </button>
          <span className="font-semibold text-body-sm text-slate-100 truncate max-w-[200px]" title={title}>
            {title}
          </span>
        </div>
        <div className="flex items-center gap-1.5 text-slate-400">
          <span className="material-symbols-outlined icon-sm">search</span>
          <span className="material-symbols-outlined icon-sm">{isIos ? "ios_share" : "more_vert"}</span>
        </div>
      </div>

      {/* Screen Body Content (Scrollable) */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4 bg-slate-900/50">
        {/* Route / Screen Badge */}
        {routes?.[0] && (
          <div className="flex items-center gap-1.5 text-[11px] font-mono text-slate-400 bg-slate-800/60 px-2.5 py-1 rounded-md border border-slate-700/50">
            <span className="material-symbols-outlined text-[14px] text-primary">link</span>
            <span className="truncate">{routes[0]}</span>
          </div>
        )}

        {/* Fields List */}
        {fields.length > 0 ? (
          <div className="space-y-3">
            {fields.map((f, i) => (
              <FieldWireframeWidget key={i} field={f} onLine={onLine} />
            ))}
          </div>
        ) : (
          /* Empty or Component fallback */
          <div className="rounded-xl border border-dashed border-slate-700 p-6 text-center space-y-2 text-slate-400 my-4">
            <span className="material-symbols-outlined text-3xl opacity-60">web_asset</span>
            <p className="text-body-sm font-medium text-slate-300">{title}</p>
            <p className="text-[11px] text-slate-500">Screen container with dynamic views</p>
          </div>
        )}

        {/* Mock list items if RecyclerView / ListView is detected */}
        {(lists?.length || (!fields.length && !actions.length)) && (
          <div className="space-y-2 pt-2">
            {[1, 2, 3].map((n) => (
              <div key={n} className="flex items-center gap-3 p-3 rounded-xl bg-slate-800/40 border border-slate-800">
                <div className="w-9 h-9 rounded-lg bg-slate-700/60 flex items-center justify-center text-slate-400">
                  <span className="material-symbols-outlined text-lg">image</span>
                </div>
                <div className="flex-1 space-y-1">
                  <div className="h-3 w-28 bg-slate-700/80 rounded" />
                  <div className="h-2 w-16 bg-slate-800 rounded" />
                </div>
                <span className="material-symbols-outlined text-slate-500 text-sm">chevron_right</span>
              </div>
            ))}
          </div>
        )}

        {/* Action Buttons */}
        {actions.length > 0 && (
          <div className="pt-2 space-y-2">
            {actions.map((a, i) => {
              const isFab = (a.widget || "").toLowerCase().includes("floating") || (a.widget || "").toLowerCase().includes("fab");
              if (isFab) return null; // rendered separately
              const isPrimary = i === 0 || (a.label || "").toLowerCase().includes("submit") || (a.label || "").toLowerCase().includes("save") || (a.label || "").toLowerCase().includes("order");
              return (
                <button
                  key={i}
                  type="button"
                  className={`w-full py-2.5 px-4 rounded-xl font-medium text-body-sm flex items-center justify-center gap-2 transition-all ${
                    isPrimary
                      ? "bg-primary text-on-primary shadow-md hover:brightness-110 active:scale-[0.99]"
                      : "bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700"
                  }`}
                  title={a.handler ? `Handler: ${a.handler}` : undefined}
                >
                  <span>{a.label || "Action"}</span>
                  {a.enabled && <span className="text-[10px] opacity-70">({a.enabled})</span>}
                </button>
              );
            })}
          </div>
        )}
      </div>

      {/* Floating Action Button (FAB) if present */}
      {actions.some((a) => (a.widget || "").toLowerCase().includes("floating") || (a.widget || "").toLowerCase().includes("fab")) && (
        <div className="absolute bottom-16 right-4 z-20">
          <button
            type="button"
            className="w-12 h-12 rounded-full bg-primary text-on-primary shadow-xl flex items-center justify-center hover:scale-105 active:scale-95 transition-transform"
            title="Floating Action Button"
          >
            <span className="material-symbols-outlined text-2xl">add</span>
          </button>
        </div>
      )}

      {/* Bottom Navigation Bar */}
      {navigation && navigation.length > 0 && (
        <div className="px-3 py-2 border-t border-slate-800 bg-slate-950/95 flex items-center justify-around text-slate-400">
          <div className="flex flex-col items-center gap-0.5 text-primary">
            <span className="material-symbols-outlined text-lg">home</span>
            <span className="text-[10px]">Home</span>
          </div>
          {navigation.slice(0, 3).map((navItem, idx) => (
            <div key={idx} className="flex flex-col items-center gap-0.5 hover:text-slate-200 cursor-pointer">
              <span className="material-symbols-outlined text-lg">
                {idx === 0 ? "explore" : idx === 1 ? "receipt_long" : "account_circle"}
              </span>
              <span className="text-[10px] truncate max-w-[60px]">{navItem.target || `Nav ${idx + 1}`}</span>
            </div>
          ))}
        </div>
      )}

      {/* Bottom Gesture Bar / Home Indicator */}
      <div className="py-2 bg-slate-950 flex justify-center">
        <div className="w-32 h-1 bg-slate-700 rounded-full" />
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------------------------------------------------
// Deterministic Web Browser Frame
// --------------------------------------------------------------------------------------------------------------------

function WebBrowserFrame({
  title,
  route,
  fields,
  actions,
  navigation,
  onLine,
}: {
  title: string;
  route: string;
  fields: any[];
  actions: any[];
  navigation?: any[];
  onLine?: (line: number) => void;
}) {
  return (
    <div className="w-full max-w-4xl min-h-[580px] rounded-2xl border border-line bg-surface shadow-2xl flex flex-col overflow-hidden select-none">
      {/* Browser Window Chrome */}
      <div className="px-4 py-2.5 bg-inset border-b border-line flex items-center gap-3">
        {/* Window controls */}
        <div className="flex items-center gap-1.5">
          <span className="w-3 h-3 rounded-full bg-red-500/80 inline-block" />
          <span className="w-3 h-3 rounded-full bg-yellow-500/80 inline-block" />
          <span className="w-3 h-3 rounded-full bg-green-500/80 inline-block" />
        </div>

        {/* Back / Forward / Reload */}
        <div className="flex items-center gap-1 text-muted">
          <span className="material-symbols-outlined icon-sm opacity-60">arrow_back</span>
          <span className="material-symbols-outlined icon-sm opacity-40">arrow_forward</span>
          <span className="material-symbols-outlined icon-sm opacity-60">refresh</span>
        </div>

        {/* URL Address Bar */}
        <div className="flex-1 max-w-lg mx-auto flex items-center gap-2 px-3 py-1 rounded-md bg-surface border border-line text-micro font-mono text-muted">
          <span className="material-symbols-outlined text-[14px] text-ok">lock</span>
          <span className="truncate text-ink font-sans">https://app.internal</span>
          <span className="text-primary truncate">{route}</span>
        </div>

        <div className="w-12" />
      </div>

      {/* Web Canvas Viewport */}
      <div className="flex-1 p-6 md:p-8 bg-canvas/40 overflow-y-auto">
        <div className="max-w-2xl mx-auto space-y-6">
          {/* Header Card */}
          <div className="border-b border-line pb-4 flex items-center justify-between">
            <div>
              <div className="flex items-center gap-2">
                <span className="chip border-line-strong text-primary text-micro">Web Screen</span>
                <span className="font-mono text-micro text-faint">{route}</span>
              </div>
              <h1 className="mt-1 font-semibold text-head-md text-ink">{title}</h1>
            </div>
            {navigation && navigation.length > 0 && (
              <div className="flex items-center gap-2">
                {navigation.slice(0, 2).map((nav, i) => (
                  <span key={i} className="chip border-line text-muted text-micro">
                    {nav.target}
                  </span>
                ))}
              </div>
            )}
          </div>

          {/* Form Fields Card */}
          {fields.length > 0 ? (
            <div className="panel p-6 space-y-4 shadow-sm">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                {fields.map((f, i) => (
                  <div key={i} className={f.type === "textarea" ? "sm:col-span-2" : ""}>
                    <FieldWireframeWidget field={f} onLine={onLine} />
                  </div>
                ))}
              </div>

              {/* Action Buttons */}
              {actions.length > 0 && (
                <div className="pt-4 border-t border-line flex flex-wrap items-center justify-end gap-3">
                  {actions.map((a, i) => {
                    const isPrimary = i === 0 || (a.label || "").toLowerCase().includes("submit") || (a.label || "").toLowerCase().includes("save");
                    return (
                      <button
                        key={i}
                        type="button"
                        className={`btn ${isPrimary ? "btn-primary" : ""}`}
                        title={a.handler ? `Handler: ${a.handler}` : undefined}
                      >
                        {a.label || "Button"}
                      </button>
                    );
                  })}
                </div>
              )}
            </div>
          ) : (
            <div className="panel p-12 text-center space-y-3">
              <span className="material-symbols-outlined text-4xl text-muted">web</span>
              <h3 className="font-medium text-body-md text-ink">{title}</h3>
              <p className="text-body-sm text-muted">Web component container with nested routes or widgets.</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------------------------------------------------------
// Field Widget Renderer (Deterministic Form Control)
// --------------------------------------------------------------------------------------------------------------------

function FieldWireframeWidget({ field, onLine }: { field: any; onLine?: (line: number) => void }) {
  const type = (field.type || "text").toLowerCase();
  const label = field.label || field.name || "Field";
  const required = field.required;
  const validations = [...(field.validations || []), ...(field.validation ? [field.validation] : [])];

  const handleClick = () => {
    if (field.line && onLine) {
      onLine(field.line);
    }
  };

  return (
    <div
      className={`group space-y-1.5 p-2 rounded-lg border border-transparent hover:border-primary/40 hover:bg-elevated/40 transition-colors cursor-pointer`}
      onClick={handleClick}
      title={field.line ? `Defined at line ${field.line} (click to view)` : undefined}
    >
      {/* Label Row */}
      <div className="flex items-center justify-between text-micro">
        <label className="font-medium text-slate-300 flex items-center gap-1 group-hover:text-primary transition-colors">
          <span>{label}</span>
          {required && <span className="text-critical font-bold">*</span>}
        </label>
        <span className="font-mono text-[10px] text-slate-500 opacity-60 group-hover:opacity-100">
          {field.widget || type}
          {field.line ? ` :L${field.line}` : ""}
        </span>
      </div>

      {/* Control Visual based on type */}
      {type === "password" ? (
        <div className="h-9 px-3 rounded-lg border border-slate-700 bg-slate-800/80 flex items-center justify-between text-slate-400">
          <span className="tracking-widest font-mono text-xs">••••••••</span>
          <span className="material-symbols-outlined text-[16px] opacity-70">visibility</span>
        </div>
      ) : type === "switch" || type === "toggle" ? (
        <div className="flex items-center justify-between py-1">
          <span className="text-[12px] text-slate-400">Enable</span>
          <div className="w-10 h-5 rounded-full bg-primary/60 p-0.5 flex items-center justify-end">
            <div className="w-4 h-4 rounded-full bg-white shadow-sm" />
          </div>
        </div>
      ) : type === "checkbox" ? (
        <div className="flex items-center gap-2 py-1">
          <div className="w-4 h-4 rounded border border-primary bg-primary/20 flex items-center justify-center text-primary">
            <span className="material-symbols-outlined text-[14px]">check</span>
          </div>
          <span className="text-[12px] text-slate-300">{label}</span>
        </div>
      ) : type === "select" || type === "dropdown" ? (
        <div className="h-9 px-3 rounded-lg border border-slate-700 bg-slate-800/80 flex items-center justify-between text-slate-300 text-body-sm">
          <span>Select {label}...</span>
          <span className="material-symbols-outlined text-[18px] text-slate-400">expand_more</span>
        </div>
      ) : type === "date" || type === "time" ? (
        <div className="h-9 px-3 rounded-lg border border-slate-700 bg-slate-800/80 flex items-center justify-between text-slate-400 text-body-sm">
          <span>YYYY-MM-DD</span>
          <span className="material-symbols-outlined text-[16px]">calendar_today</span>
        </div>
      ) : type === "slider" ? (
        <div className="py-2 space-y-1">
          <div className="h-2 rounded-full bg-slate-700 relative">
            <div className="h-full w-2/3 bg-primary rounded-full" />
            <div className="w-4 h-4 rounded-full bg-white absolute top-1/2 left-2/3 -translate-x-1/2 -translate-y-1/2 shadow-md" />
          </div>
        </div>
      ) : type === "textarea" ? (
        <div className="h-18 px-3 py-2 rounded-lg border border-slate-700 bg-slate-800/80 text-slate-500 text-body-sm">
          <span>Enter {label}...</span>
        </div>
      ) : (
        /* Default Text / Number / Email input */
        <div className="h-9 px-3 rounded-lg border border-slate-700 bg-slate-800/80 flex items-center text-slate-400 text-body-sm">
          <span className="truncate">{field.placeholder || `Enter ${label}...`}</span>
        </div>
      )}

      {/* Validation hint if present */}
      {validations.length > 0 && (
        <div className="flex items-center gap-1 text-[10px] text-warning">
          <span className="material-symbols-outlined text-[12px]">info</span>
          <span>{validations.join(" · ")}</span>
        </div>
      )}
    </div>
  );
}
