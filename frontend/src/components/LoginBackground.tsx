import { useEffect, useState } from "react";

interface Props {
  theme: "dark" | "light";
}

const CODE_COLUMNS = [
  {
    file: "src/ingestor/git_clone.py",
    branch: "main@a8f31c2",
    layer: "DATA",
    color: "rgb(var(--layer-data))",
    lines: [
      { ln: 14, tokens: [["k", "async def "], ["f", "ingest_repository"], ["p", "(repo_url, branch):"]] },
      { ln: 15, tokens: [["c", "  # Shallow clone & extract commit tree"]] },
      { ln: 16, tokens: [["p", "  head = "], ["k", "await "], ["f", "resolve_git_ref"], ["p", "(repo_url, branch)"]] },
      { ln: 17, tokens: [["p", "   blobs = "], ["f", "walk_worktree"], ["p", "(head.sha, ignore_rules)"]] },
      { ln: 18, tokens: [["k", "  yield "], ["a", "@TelemetrySpan"], ["p", "("], ["s", '"git.ingest"'], ["p", ")"]] },
      { ln: 19, tokens: [["k", "  return "], ["f", " build_snapshot"], ["p", "(head, blobs, "], ["n", "4096"], ["p", ")"]] },
      { ln: 20, tokens: [["k", "from "], ["s", "analyzer.ast"], ["k", " import "], ["f", "parse_symbols"]] },
      { ln: 21, tokens: [["k", "def "], ["f", "extract_manifests"], ["p", "(tree: GitTree):"]] },
      { ln: 22, tokens: [["p", "  "], ["k", "return "], ["p", "[m "], ["k", "for "], ["p", "m "], ["k", "in "], ["f", "scan_Lockfiles"], ["p", "(tree)]"]] },
    ],
  },
  {
    file: "src/graph/tarjan_scc.ts",
    branch: "feat/pagerank@91d04e",
    layer: "SERVICE",
    color: "rgb(var(--layer-service))",
    lines: [
      { ln: 42, tokens: [["k", "export function "], ["f", "computeDependencyGraph"], ["p", "(ast: ASTNode[]) {"]] },
      { ln: 43, tokens: [["p", "  "], ["k", "const "], ["p", "scc = "], ["f", "tarjanStronglyConnected"], ["p", "(ast.edges);"]] },
      { ln: 44, tokens: [["p", "  "], ["k", "const "], ["p", "ranks = "], ["f", "pageRank"], ["p", "(ast.nodes, "], ["n", "0.85"], ["p", ", "], ["n", "40"], ["p", ");"]] },
      { ln: 45, tokens: [["c", "  // Detect circular dependency clusters"]] },
      { ln: 46, tokens: [["p", "  "], ["k", "for "], ["p", "("], ["k", "const "], ["p", "cycle "], ["k", "of "], ["p", "scc.cycles) {"]] },
      { ln: 47, tokens: [["p", "    "], ["f", "emitTopologyFinding"], ["p", "("], ["s", '"scc.cycle"'], ["p", ", cycle);"]] },
      { ln: 48, tokens: [["p", "  }"]] },
      { ln: 49, tokens: [["p", "  "], ["k", "return "], ["p", "{ scc, ranks, modularity: "], ["n", "0.782"], ["p", " };"]] },
      { ln: 50, tokens: [["p", "}"]] },
    ],
  },
  {
    file: "src/synthesis/c4_model.py",
    branch: "main@a8f31c2",
    layer: "API",
    color: "rgb(var(--layer-api))",
    lines: [
      { ln: 88, tokens: [["a", "@synthesize_architecture"], ["p", "(level="], ["s", '"C4_CONTAINER"'], ["p", ")"]] },
      { ln: 89, tokens: [["k", "def "], ["f", "build_c4_topology"], ["p", "(graph: CodeGraph):"]] },
      { ln: 90, tokens: [["p", "  layers = "], ["f", "classify_file_roles"], ["p", "(graph.symbols)"]] },
      { ln: 91, tokens: [["p", "  edges = "], ["f", "aggregate_cross_layer_calls"], ["p", "(layers)"]] },
      { ln: 92, tokens: [["c", "  # Emit deterministic + semantic summary"]] },
      { ln: 93, tokens: [["p", "  diagram = "], ["f", "render_c4_boundaries"], ["p", "(layers, edges)"]] },
      { ln: 94, tokens: [["k", "  return "], ["f", "SynthesisReport"], ["p", "(diagram, drift="], ["n", "0.02"], ["p", ")"]] },
    ],
  },
  {
    file: "src/ui/TopologyCanvas.tsx",
    branch: "main@a8f31c2",
    layer: "UI",
    color: "rgb(var(--layer-ui))",
    lines: [
      { ln: 12, tokens: [["k", "import "], ["p", "{ "], ["f", "computeDependencyGraph"], ["p", " } "], ["k", "from "], ["s", '"../graph/tarjan_scc"'], ["p", ";"]] },
      { ln: 13, tokens: [["k", "import "], ["p", "{ "], ["f", "build_c4_topology"], ["p", " } "], ["k", "from "], ["s", '"../synthesis/c4_model"'], ["p", ";"]] },
      { ln: 14, tokens: [["k", "export const "], ["f", "InspectorOverlay"], ["p", " = ({ repo }) => {"]] },
      { ln: 15, tokens: [["p", "  "], ["k", "const "], ["p", "metrics = "], ["f", "usePageRankHotspots"], ["p", "(repo.id);"]] },
      { ln: 16, tokens: [["p", "  "], ["k", "return "], ["p", "<"], ["f", "DependencyGraph "], ["a", "scc"], ["p", "={metrics.scc} />;"]] },
      { ln: 17, tokens: [["p", "};"]] },
    ],
  },
];

const EXTRACTED_NODES = [
  { id: "ingest_repository", label: "ingest_repository()", kind: "fn", layer: "DATA", x: 17, y: 24, pr: "0.91", color: "var(--layer-data)" },
  { id: "parse_symbols", label: "parse_symbols()", kind: "import", layer: "DATA", x: 24, y: 42, pr: "0.76", color: "var(--primary)" },
  { id: "computeDependencyGraph", label: "computeDependencyGraph()", kind: "fn", layer: "SERVICE", x: 76, y: 22, pr: "0.94", color: "var(--layer-service)" },
  { id: "pageRank", label: "pageRank(nodes, 0.85)", kind: "call", layer: "SERVICE", x: 82, y: 38, pr: "0.88", color: "var(--primary)" },
  { id: "build_c4_topology", label: "build_c4_topology()", kind: "fn", layer: "API", x: 20, y: 74, pr: "0.83", color: "var(--layer-api)" },
  { id: "InspectorOverlay", label: "<DependencyGraph />", kind: "jsx", layer: "UI", x: 78, y: 76, pr: "0.79", color: "var(--layer-ui)" },
];

const EXTRACTED_EDGES = [
  { from: 0, to: 1, label: "AST stream", color: "var(--primary)" },
  { from: 1, to: 2, label: "symbols[]", color: "var(--layer-service)" },
  { from: 2, to: 3, label: "SCC + PageRank", color: "var(--primary)" },
  { from: 1, to: 4, label: "call graph", color: "var(--layer-api)" },
  { from: 2, to: 5, label: "imports", color: "var(--layer-ui)" },
  { from: 4, to: 5, label: "C4 container", color: "var(--ok)" },
];

export function LoginBackground({ theme }: Props) {
  const [activePulse, setActivePulse] = useState(0);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setActivePulse((prev) => (prev + 1) % 6);
    }, 2200);
    return () => window.clearInterval(timer);
  }, []);

  if (theme === "light") {
    return <LightIsometricBlueprint activePulse={activePulse} />;
  }
  return <DarkGhostCodeExtraction activePulse={activePulse} />;
}

/* ============================================================================
 * MODE 4 (Dark Mode): Ghost Code & Syntax Token Extraction
 * ========================================================================== */
function DarkGhostCodeExtraction({ activePulse }: { activePulse: number }) {
  const cardPositions = [
    "left-[3%] top-[6%] w-[380px]",
    "right-[3%] top-[7%] w-[400px]",
    "left-[4%] bottom-[6%] w-[390px]",
    "right-[4%] bottom-[7%] w-[410px]",
  ];

  return (
    <div
      className="pointer-events-none fixed inset-0 overflow-hidden select-none z-0"
      aria-hidden="true"
      data-testid="login-bg-dark"
    >
      {/* Subtle radial vignette so the center login card stands out */}
      <div
        className="absolute inset-0"
        style={{
          background:
            "radial-gradient(circle at 50% 50%, rgba(10, 13, 18, 0.25) 0%, rgba(10, 13, 18, 0.72) 55%, rgba(10, 13, 18, 0.92) 100%)",
        }}
      />

      {/* Git commit stream rail on far left */}
      <div className="absolute left-5 top-0 bottom-0 hidden xl:flex flex-col justify-between py-12 opacity-40 font-mono text-[10px] text-faint">
        <div className="flex items-center gap-2">
          <span className="h-2 w-2 rounded-full bg-status-git animate-ping" />
          <span>GIT::INGEST // origin/main</span>
        </div>
        <div className="space-y-6 border-l border-line pl-3 ml-1">
          {["a8f31c2 feat(ast): extract symbol call graph", "91d04e7 perf(scc): Tarjan cycle compression", "4c02b19 feat(c4): container boundary inference", "7e19a04 fix(rank): weighted PageRank damping"].map((commit, i) => (
            <div key={commit} className="relative flex items-center gap-2">
              <span
                className=" -left-[17px] absolute h-2 w-2 rounded-full border"
                style={{
                  borderColor: i === activePulse % 4 ? "rgb(var(--primary))" : "rgb(var(--line-strong))",
                  backgroundColor: i === activePulse % 4 ? "rgb(var(--primary))" : "rgb(var(--canvas))",
                }}
              />
              <span className={i === activePulse % 4 ? "text-primary" : "text-faint"}>{commit}</span>
            </div>
          ))}
        </div>
        <div className="text-faint">TELEMETRY::ACTIVE [60fps]</div>
      </div>

      {/* Floating Ghost Code Repository Files */}
      {CODE_COLUMNS.map((col, idx) => {
        const isScanned = activePulse % CODE_COLUMNS.length === idx;
        return (
          <div
            key={col.file}
            className={`hidden lg:block absolute ${cardPositions[idx]} rounded-sm border transition-all duration-700 ${
              isScanned ? "border-primary/40 opacity-55" : "border-line/60 opacity-30"
            } bg-inset/70 backdrop-blur-[1px]`}
            style={{
              animation: `loginFloat ${11 + idx * 2}s ease-in-out infinite alternate`,
            }}
          >
            {/* File header bar */}
            <div className="flex items-center justify-between border-b border-line/60 px-2.5 py-1 font-mono text-[10px]">
              <div className="flex items-center gap-1.5">
                <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: col.color }} />
                <span className="text-muted">{col.file}</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-faint">{col.branch}</span>
                <span
                  className="px-1 rounded-sm text-[9px] border"
                  style={{ borderColor: col.color, color: col.color }}
                >
                  {col.layer}
                </span>
              </div>
            </div>

            {/* Laser Token Scanner Beam */}
            <div className="relative overflow-hidden p-2.5 font-mono text-[11px] leading-[17px]">
              <div
                className="absolute inset-x-0 h-6 pointer-events-none"
                style={{
                  background:
                    "linear-gradient(180deg, transparent 0%, rgba(0, 229, 255, 0.12) 50%, rgba(0, 229, 255, 0.28) 95%, transparent 100%)",
                  borderBottom: "1px solid rgba(0, 229, 255, 0.45)",
                  animation: `loginCodeScan ${5 + idx}s linear infinite`,
                }}
              />

              {col.lines.map((line, lIdx) => {
                const highlightToken = isScanned && (lIdx === 0 || lIdx === 2);
                return (
                  <div
                    key={line.ln}
                    className={`flex items-baseline gap-2.5 px-1 rounded-sm transition-colors duration-500 ${
                      highlightToken ? "bg-primary/10" : ""
                    }`}
                  >
                    <span className="w-5 text-right text-[10px] text-faint/60">{line.ln}</span>
                    <div className="truncate">
                      {line.tokens.map(([type, text], tIdx) => {
                        const cls =
                          type === "k"
                            ? "tok-k"
                            : type === "f"
                            ? "tok-f"
                            : type === "s"
                            ? "tok-s"
                            : type === "c"
                            ? "tok-c"
                            : type === "n"
                            ? "tok-n"
                            : type === "a"
                            ? "tok-a"
                            : "text-muted";
                        const isExtractedSymbol = type === "f" && (lIdx === 0 || lIdx === 2);
                        return (
                          <span
                            key={tIdx}
                            className={`${cls} ${
                              isExtractedSymbol
                                ? "px-1 py-0.5 rounded-sm border border-primary/50 bg-primary/10 shadow-[0_0_8px_rgba(0,229,255,0.25)]"
                                : ""
                            }`}
                          >
                            {text}
                          </span>
                        );
                      })}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}

      {/* SVG AST & Dependency Graph Extraction Overlay */}
      <svg className="absolute inset-0 h-full w-full" viewBox="0 0 100 100" preserveAspectRatio="none">
        <defs>
          <radialGradient id="nodeGlowDark" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="rgb(var(--primary))" stopOpacity="0.45" />
            <stop offset="100%" stopColor="rgb(var(--primary))" stopOpacity="0" />
          </radialGradient>
        </defs>

        {/* Curved Bezier Dependency Edges connecting extracted syntax tokens */}
        {EXTRACTED_EDGES.map((edge, idx) => {
          const from = EXTRACTED_NODES[edge.from];
          const to = EXTRACTED_NODES[edge.to];
          const midX = (from.x + to.x) / 2;
          const midY = (from.y + to.y) / 2 - 6;
          const pathD = `M ${from.x} ${from.y} Q ${midX} ${midY} ${to.x} ${to.y}`;
          const isHot = idx === activePulse;
          return (
            <g key={`${edge.from}-${edge.to}`}>
              <path
                d={pathD}
                fill="none"
                stroke={`rgb(${edge.color})`}
                strokeWidth={isHot ? "0.22" : "0.12"}
                strokeOpacity={isHot ? "0.65" : "0.26"}
                strokeDasharray="0.8 0.6"
                style={{ animation: "loginDashFlow 12s linear infinite" }}
              />
              {/* Animated data pulse traveling along the extracted AST edge */}
              <circle r={isHot ? "0.45" : "0.28"} fill={`rgb(${edge.color})`} opacity={isHot ? 0.95 : 0.55}>
                <animateMotion dur={`${3.5 + idx * 0.6}s`} repeatCount="indefinite" path={pathD} />
              </circle>
            </g>
          );
        })}

        {/* Tarjan SCC Cluster Orbit Ring */}
        <circle
          cx="79"
          cy="30"
          r="9"
          fill="none"
          stroke="rgb(var(--layer-service))"
          strokeWidth="0.12"
          strokeOpacity="0.32"
          strokeDasharray="1.2 0.8"
        />
      </svg>

      {/* Extracted Symbol Floating Telemetry Pills */}
      {EXTRACTED_NODES.map((node, idx) => {
        const isActive = idx === activePulse;
        return (
          <div
            key={node.id}
            className="hidden md:flex absolute -translate-x-1/2 -translate-y-1/2 items-center gap-1.5 rounded-sm border bg-surface/85 px-2 py-0.5 font-mono text-[10px] transition-all duration-500"
            style={{
              left: `${node.x}%`,
              top: `${node.y}%`,
              borderColor: isActive ? `rgb(${node.color})` : "rgb(var(--line))",
              boxShadow: isActive ? `0 0 14px rgb(${node.color} / 0.35)` : "none",
              opacity: isActive ? 0.85 : 0.45,
            }}
          >
            <span className="h-1.5 w-1.5 rounded-full" style={{ backgroundColor: `rgb(${node.color})` }} />
            <span className="text-ink">{node.label}</span>
            <span className="text-faint text-[9px]">PR:{node.pr}</span>
          </div>
        );
      })}
    </div>
  );
}

/* ============================================================================
 * MODE 3 (Light Mode): Isometric C4 Architecture Blueprint & Radar Sweep
 * ========================================================================== */
function LightIsometricBlueprint({ activePulse }: { activePulse: number }) {
  const planes = [
    {
      id: "C4-L1",
      name: "UI / PRESENTATION LAYER",
      meta: "React · Mermaid · CodeView · TopologyCanvas",
      color: "var(--layer-ui)",
      offsetY: -175,
      nodes: [
        { x: -110, y: -25, label: "RepositoryPage" },
        { x: 15, y: -45, label: "DependencyGraph" },
        { x: 95, y: 20, label: "InspectorTab" },
      ],
    },
    {
      id: "C4-L2",
      name: "API / CONTROLLER GATEWAY",
      meta: "REST · Auth · Branch Diff · Chat SSE",
      color: "var(--layer-api)",
      offsetY: -60,
      nodes: [
        { x: -95, y: 10, label: "POST /analyses" },
        { x: 0, y: -15, label: "GET /topology" },
        { x: 105, y: -10, label: "POST /chat" },
      ],
    },
    {
      id: "C4-L3",
      name: "SERVICE / GRAPH INTELLIGENCE",
      meta: "AST Parser · Tarjan SCC · PageRank · C4 Synth",
      color: "var(--layer-service)",
      offsetY: 55,
      nodes: [
        { x: -105, y: -10, label: "SymbolExtractor" },
        { x: -5, y: 20, label: "TarjanSCC [O(V+E)]" },
        { x: 100, y: 5, label: "PageRankScorer" },
      ],
    },
    {
      id: "C4-L4",
      name: "DATA & GIT INGESTION STORE",
      meta: "GitHub · Bitbucket · Worktree · SQLite/Postgres",
      color: "var(--layer-data)",
      offsetY: 170,
      nodes: [
        { x: -90, y: 15, label: "GitWorktreeClone" },
        { x: 10, y: -20, label: "SymbolGraphStore" },
        { x: 95, y: 15, label: "SnapshotArchive" },
      ],
    },
  ];

  return (
    <div
      className="pointer-events-none fixed inset-0 overflow-hidden select-none z-0"
      aria-hidden="true"
      data-testid="login-bg-light"
    >
      {/* Subtle laboratory radial fade so the central login card stays crisp */}
      <div
        className="absolute inset-0"
        style={{
          background:
            "radial-gradient(circle at 50% 50%, rgba(248, 249, 250, 0.15) 0%, rgba(248, 249, 250, 0.55) 60%, rgba(248, 249, 250, 0.85) 100%)",
        }}
      />

      {/* Laboratory CAD Corner Reticles & Metadata */}
      <div className="absolute left-6 top-6 hidden lg:block font-mono text-[10px] text-muted/70 space-y-0.5">
        <div className="flex items-center gap-1.5 text-ink/80 font-semibold">
          <span className="inline-block h-2 w-2 border border-primary bg-primary/20" />
          <span>CAD//C4-TOPOLOGY-PROJECTION [ISO-30°]</span>
        </div>
        <div>SCALE: 1:1000 · TARJAN-SCC OVERLAY · PAGERANK TELEMETRY</div>
      </div>

      <div className="absolute left-6 bottom-6 hidden lg:flex items-center gap-4 font-mono text-[10px] text-muted/75">
        <span>REV: 04.2 // GIT-AST-SYNTHESIS</span>
        <span className="h-3 w-[1px] bg-line" />
        <span>LAYERS: UI → API → SERVICE → DATA</span>
      </div>

      {/* Left & Right Isometric Blueprint Columns so architecture frames the login card */}
      <svg
        className="absolute inset-0 h-full w-full"
        viewBox="-700 -360 1400 720"
        preserveAspectRatio="xMidYMid slice"
      >
        <defs>
          <linearGradient id="radarSweepLight" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stopColor="rgb(var(--primary))" stopOpacity="0" />
            <stop offset="50%" stopColor="rgb(var(--primary))" stopOpacity="0.16" />
            <stop offset="100%" stopColor="rgb(var(--primary))" stopOpacity="0" />
          </linearGradient>
        </defs>

        {/* Render twin isometric C4 stacks flanking left (-430) and right (+430) of the login card */}
        {[-430, 430].map((stackX, stackIdx) => (
          <g key={stackX} transform={`translate(${stackX}, 10)`}>
            {/* Vertical Inter-Layer Call Trace Pillars */}
            {[-95, 5, 100].map((colX, cIdx) => (
              <g key={colX}>
                <line
                  x1={colX}
                  y1={-190}
                  x2={colX}
                  y2={195}
                  stroke="rgb(var(--primary))"
                  strokeWidth="1"
                  strokeOpacity="0.28"
                  strokeDasharray="4 4"
                  style={{ animation: "loginDashFlow 10s linear infinite" }}
                />
                {/* Vertical Call / Dependency Data Packet */}
                <circle r="3.2" fill="rgb(var(--primary))" opacity="0.75">
                  <animate
                    attributeName="cy"
                    values={cIdx % 2 === 0 ? "-180;185;-180" : "185;-180;185"}
                    dur={`${5 + cIdx * 1.4}s`}
                    repeatCount="indefinite"
                  />
                  <animate attributeName="cx" values={`${colX};${colX}`} dur="1s" repeatCount="indefinite" />
                </circle>
              </g>
            ))}

            {/* 4 Stacked Isometric C4 Architectural Planes */}
            {planes.map((plane, pIdx) => {
              const isHighlighted = (activePulse + stackIdx) % planes.length === pIdx;
              return (
                <g key={plane.id} transform={`translate(0, ${plane.offsetY})`}>
                  {/* Isometric Diamond Wireframe Plane: (0,-75) -> (210,0) -> (0,75) -> (-210,0) */}
                  <polygon
                    points="0,-78 220,0 0,78 -220,0"
                    fill={`rgb(${plane.color} / ${isHighlighted ? "0.09" : "0.035"})`}
                    stroke={`rgb(${plane.color})`}
                    strokeWidth={isHighlighted ? "1.5" : "0.9"}
                    strokeOpacity={isHighlighted ? "0.7" : "0.35"}
                  />

                  {/* Internal Isometric Sub-grid lines */}
                  <line x1="-110" y1="-39" x2="110" y2="39" stroke={`rgb(${plane.color})`} strokeOpacity="0.18" strokeWidth="0.7" />
                  <line x1="-110" y1="39" x2="110" y2="-39" stroke={`rgb(${plane.color})`} strokeOpacity="0.18" strokeWidth="0.7" />
                  <line x1="-165" y1="-19.5" x2="55" y2="58.5" stroke={`rgb(${plane.color})`} strokeOpacity="0.12" strokeWidth="0.5" strokeDasharray="3 3" />
                  <line x1="-55" y1="-58.5" x2="165" y2="19.5" stroke={`rgb(${plane.color})`} strokeOpacity="0.12" strokeWidth="0.5" strokeDasharray="3 3" />

                  {/* Animated Radar Sweep Bar across the isometric plane */}
                  <polygon
                    points="-150,-25 -70,-53 150,25 70,53"
                    fill="url(#radarSweepLight)"
                    style={{
                      animation: `loginIsoSweep ${4.5 + pIdx * 0.5}s ease-in-out infinite alternate`,
                    }}
                  />

                  {/* Plane Layer Tag */}
                  <text
                    x={stackIdx === 0 ? "-215" : "125"}
                    y="-48"
                    fill={`rgb(${plane.color})`}
                    fontSize="9"
                    fontFamily="JetBrains Mono, monospace"
                    fontWeight="600"
                    opacity="0.85"
                  >
                    [{plane.id}] {plane.name}
                  </text>

                  {/* Intra-layer dependency edges */}
                  <polyline
                    points={plane.nodes.map((n) => `${n.x},${n.y}`).join(" ")}
                    fill="none"
                    stroke={`rgb(${plane.color})`}
                    strokeWidth="1"
                    strokeOpacity="0.45"
                    strokeDasharray="2 2"
                  />

                  {/* Module Nodes on the Plane */}
                  {plane.nodes.map((node) => (
                    <g key={node.label} transform={`translate(${node.x}, ${node.y})`}>
                      <circle
                        r={isHighlighted ? "5.5" : "3.5"}
                        fill={`rgb(${plane.color} / 0.18)`}
                        stroke={`rgb(${plane.color})`}
                        strokeWidth="1.2"
                      />
                      <circle r="1.8" fill={`rgb(${plane.color})`} />
                      <text
                        x="7"
                        y="3"
                        fill="rgb(var(--ink))"
                        fontSize="8.5"
                        fontFamily="JetBrains Mono, monospace"
                        opacity="0.72"
                      >
                        {node.label}
                      </text>
                    </g>
                  ))}
                </g>
              );
            })}
          </g>
        ))}

        {/* Horizontal Cross-Stack Git -> C4 Synthesis Bridge behind card */}
        <path
          d="M -210 -60 C -90 -120, 90 -120, 210 -60"
          fill="none"
          stroke="rgb(var(--primary))"
          strokeWidth="1"
          strokeOpacity="0.25"
          strokeDasharray="5 5"
        />
        <path
          d="M -210 55 C -90 115, 90 115, 210 55"
          fill="none"
          stroke="rgb(var(--layer-service))"
          strokeWidth="1"
          strokeOpacity="0.25"
          strokeDasharray="5 5"
        />
      </svg>
    </div>
  );
}
