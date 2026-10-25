# Code Analyzer

A web application that analyses code repositories — per repository and per branch — and keeps every result in a
database for offline viewing, **Chat with Analyst**, and **repository comparison**. It follows the *Hybrid
Deterministic-Semantic Code Intelligence Engine* in `specs/` and uses the same stack, admin model and conventions as
SpecForge (`solution-designer`). The UI implements the `stitch_dual_scientific_color_theme` design: **Dark Telemetry**
and **Light Laboratory** modes.

| | |
|---|---|
| Backend | Python 3.12 · FastAPI · Uvicorn · DuckDB **or** PostgreSQL · PyJWT · bcrypt · cryptography (Fernet) · httpx · LangChain Google GenAI · ldap3 |
| Frontend | React 18 · TypeScript · Vite 6 · Tailwind CSS 3 · React Router 6 · Mermaid 11 · markdown-it (fonts and icons self-hosted, no CDN) |
| LLM | Gemini and Spirare only (Admin › LLM), same adapters as SpecForge |
| Analysis | git (shallow clone per branch) · Python AST · pattern extractors for 20+ languages · Tarjan SCC · PageRank · BM25 + embeddings (RRF) |

## What it does

- **Analyse per repository and branch** — GitHub, GitHub Enterprise, Bitbucket Cloud, Bitbucket Server / Data Center,
  any Git URL, an uploaded `.zip`/`.tar.gz`, or a folder on the server. Pick `master`, `main`, `release/*` or any ref;
  each branch keeps its own run history.
- **Access check + sign-in popup** — before registering or analysing, the app checks whether *you* can read the
  repository (anonymously or with your saved connection). If not, a sign-in popup opens:
  *Continue with GitHub / Bitbucket* (OAuth window, when an OAuth app is configured) or a personal access token / app
  password / HTTP access token, which is verified against the repository before it is stored (encrypted, per user).
  Tokens are passed to `git` per command and never written into a clone.
- **Four-stage engine** (live progress and console stream in the UI):
  1. *Ingestion* — clone/extract, exclusion gates (`node_modules/`, `dist/`, `build/`, `target/`, `.next/`, binaries,
     lockfiles, minified bundles, `.gitignore`), SHA-256 per file and a Merkle root.
  2. *Macro discovery* — manifests (`package.json`, `pyproject.toml`, `pom.xml`, `build.gradle`, `go.mod`, `Cargo.toml`,
     `*.csproj`, `Gemfile`, `composer.json`, …) → exact framework matrix, monorepo containers, architecture style,
     entry points, external systems, Docker/Compose/K8s/Terraform/CI, and README-vs-manifest **drift**.
  3. *Micro parsing & code graph* — symbols, imports, calls, complexity (Python via AST; Java, Kotlin, C#, TS/JS, Go,
     Rust, PHP, Ruby, C/C++, SQL, COBOL, RPG/CL via pattern extractors with a nested-scope guard); `IMPORTS`,
     `EXTENDS`, `IMPLEMENTS`, `INJECTS`, `CALLS`, `INCLUDES` edges; Tarjan SCC cycles; PageRank; canonical layers
     (api, service, data, ui, shared); file roles with evidence; behaviour tags (db-read, db-write, http-call, …).
  4. *Synthesis* — quality audit (CONCERNS.md registry and 0–100 health score), C4 diagrams in Mermaid (Context,
     Container, Component, Dynamic), LLM summaries visited bottom-up over the condensed DAG (cached by content hash),
     and content-addressed chunks + embeddings for chat (unchanged chunks reuse their vectors).
- **Layer Catalogue** — a drill-down per canonical layer, linked end to end (screen → API → service → repository → table):
  - *API*: every endpoint with method, path (incl. context path), handler, path/query/header/body parameters, request and
    response structure expanded to fields with their validation (`@NotBlank`, `@Size`, `@Pattern`, OpenAPI constraints,
    zod / class-validator), status codes, exceptions mapped to HTTP status (`@ResponseStatus`, `@ControllerAdvice`),
    security (`@PreAuthorize`, `@Secured`, Spring Security request rules), guard / validation rules with line numbers,
    and the services it calls. Contract-first controllers are matched to the OpenAPI spec by `operationId`.
  - *Service*: services and use cases with public methods, signatures, `@Transactional`, caching, injected dependencies,
    calls, guard rules (`if … throw`, `Assert`, `require`, `orElseThrow`), events / Kafka / RabbitMQ / JMS messages;
    scheduled jobs (`@Scheduled` cron in plain English, Quartz, Spring Batch, WorkManager) and listeners.
  - *Data*: database engines (drivers, JDBC URLs per profile, dialects, compose images, Room, Core Data, SwiftData),
    tables merged from DDL (Oracle, MariaDB/MySQL, PostgreSQL, H2), JPA / Hibernate entities and `hbm.xml`, Liquibase,
    Room, Prisma, TypeORM — columns, types, keys, relations, indexes, comments, seed rows; repositories and every query
    (Spring Data derived queries rendered as SQL, `@Query` JPQL / native, MyBatis XML, JdbcTemplate / EntityManager SQL),
    views, PL/SQL procedures and packages, triggers, sequences; a method-precise **CRUD matrix** and an **ERD**.
  - *UI*: screens — React (React Router, TanStack Router, Next.js), Thymeleaf / JSP views, Android Activities, Fragments
    and Jetpack Compose (layout XML resolved through `strings.xml`, navigation graphs, NavHost), iOS SwiftUI / UIKit /
    storyboards — with fields, labels, client-side and inherited server-side validation, actions, navigation, view models
    and the backend calls they make (fetch, axios, generated SDKs, Retrofit, URLSession, Alamofire, Moya), matched to
    the backend endpoints.
  - *Shared*: DTOs, enums, exceptions, configuration keys per profile (Spring, `.env`, Gradle BuildConfig, Info.plist,
    xcconfig; secrets masked; where each key is read), Spring Security configuration, `@ConfigurationProperties`,
    `@Bean` wiring, Feign integrations, constants, utilities, MapStruct mappers, Android manifests, topics.
  - Trace any item upstream and downstream as a flowchart or sequence diagram; export everything to Markdown or Excel.
    With an LLM configured, the most important items also get a purpose and business rules citing line numbers (cached
    by content hash). Older analyses can be catalogued offline from their stored files (no clone needed).
- **Workbench** — Overview, C4 Architecture, Layer Catalogue, Directory & Layer Topology, Dual-Pane Graph & Code
  inspector (force graph, code with symbol outline, 1-hop neighbourhood, blast radius), Quality & Smells, Dependencies,
  Chat.
- **Chat with Analyst** — conversations grounded in a stored analysis (or a comparison): repository facts, file
  profiles, graph neighbourhood and retrieved code (BM25 + vectors fused with RRF). Answers cite files; sources open the
  inspector. Works without the clone; without an LLM it lists the most relevant code.
- **Compare** — two repositories or two branches/commits of one: metrics deltas, stack and dependency diff (incl.
  version changes), layers/roles/languages, files added/removed/modified by hash, public symbols, new/resolved concerns,
  **API & data contract changes** from the layer catalogue (endpoints, request / response fields, parameters, columns,
  screens, jobs, configuration) with potentially breaking changes flagged, optional LLM narrative, and chat about the
  comparison.
- **Pull-request impact** (rail › Impact, or a repository › PR impact) — pick an open pull request (GitHub, GitHub
  Enterprise, Bitbucket Cloud incl. forks, Bitbucket Server) or any two refs (also local git folders). The head is diffed
  against its **merge-base** (shallow fetch, deepened until one exists); both commits go through the normal engine
  (an analysis of the same commit is reused). The report shows changed symbols (added / modified / removed, signature
  and behaviour-tag changes such as a new `db-write`), **symbol-level propagation** (a dependant counts only when its
  code uses an affected symbol), affected **endpoints, jobs, listeners, screens** grouped by **feature**, configuration
  changes **by key and environment** with the code that reads each key and risk rules (timeouts ×N, loosened TLS/CORS,
  flipped flags, changed endpoints, committed secrets, removed-but-read keys), Kubernetes / Dockerfile / Terraform / CI
  rules, affected tests and test gaps, graph delta (new layer violations and cycles), dependency bumps, new concerns,
  suggested reviewers (CODEOWNERS + history) and an **explainable risk score** (every factor, weight and
  contribution). With an LLM configured it reads the before / after logic of the riskiest changed functions (claims
  cross-checked against the code, cached by content hash) and writes the narrative, description-vs-diff drift and a
  review checklist; the risk label stays deterministic. The PR head is never built or run.
- **Feature catalogue** (repository › Features) — built automatically after each analysis of the default branch from
  the entry points (LLM, or by route / folder); users confirm and edit features with route / path / item / name patterns
  (confirmed features are never replaced); PR impact groups entry points by them.
- **System landscapes** (rail › Systems) — named groups of repositories linked end to end from their layer catalogues:
  *HTTP* (screens, Retrofit / Feign / RestTemplate / WebClient / fetch / URLSession calls → endpoints in another
  repository, using method + path template, context path, base-URL configuration per profile, host and service names),
  *messaging* (Kafka / RabbitMQ / JMS / SQS publisher → consumers), *shared database* tables (same datasource / engine;
  device-local stores excluded) and *external* systems (unmatched hosts, brokers, databases, partner APIs). Every link
  has a score, a confidence and its evidence. Auto-accept is configurable per landscape (*high* or *high + medium*,
  default); ambiguous or doubtful links wait in a **Review** queue whose popup shows the call site, every candidate with
  evidence and code, and an explanation (rules, or the LLM) — confirm one or several, mark external, or reject; decisions
  are re-applied when the links are recomputed after a new analysis. Views: system map (filter by link type / feature),
  provided interfaces with their consumers, end-to-end **journeys** as sequence diagrams (screen → API → service →
  topic → consumer → table), all links; export to Markdown, PDF and Excel. PR impact lists the **consumers in other
  repositories** of the affected endpoints and topics.
- **Admin** (as in SpecForge) — LLM (Gemini, Spirare; encrypted write-only keys; active provider; test connection),
  Users (local or Active Directory), User Roles (none/read/write on *Repositories*, *Insights*, *Settings*), Database,
  Audit Log.

## Quick start

```bash
cp .env.example .env               # set JWT_SECRET; adjust anything else
cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && cd ..
npm ci
./scripts/start.sh                 # Windows: scripts\start.bat
```

Open http://localhost:7778. Ports are 8001 (API) and 7778 (UI) so Code Analyzer runs next to SpecForge (8000/7777).
First-start users (change the passwords with `SEED_*` in `.env` or under Admin › Users):

| Login ID | Password | Role |
|---|---|---|
| `admin` | `AdminPass123!` | Administrator |
| `analyst` | `AnalystPass123!` | Analyst |
| `viewer` | `ViewerPass123!` | Viewer |

`git` must be on the PATH of the backend. Stop with `./scripts/stop.sh`.

## Database: DuckDB or PostgreSQL

One schema (`backend/app/db.py`) written in portable SQL, so tables and columns are identical on both engines.

```ini
# DuckDB (default): a single file
DB_ENGINE=duckdb
DUCKDB_PATH=./data/codeanalyzer.duckdb

# PostgreSQL
DB_ENGINE=postgres
DATABASE_URL=postgresql://codeanalyzer:secret@dbhost:5432/codeanalyzer
```

Tables are created on start-up and versioned in `schema_migrations`. To move data between engines (app stopped):

```bash
cd backend
.venv/bin/python -m app.tools.migrate_db --from duckdb --to postgres \
  --duckdb-path ../data/codeanalyzer.duckdb --database-url postgresql://user:pass@host:5432/codeanalyzer
```

Tables: `roles`, `users`, `app_settings`, `llm_providers`, `git_credentials`, `repositories`, `analyses`,
`analysis_events`, `analysis_containers`, `analysis_files`, `analysis_file_contents`, `analysis_symbols`,
`analysis_edges`, `analysis_dependencies`, `analysis_concerns`, `analysis_chunks`, `analysis_catalog`,
`analysis_catalog_links`, `comparisons`, `chat_sessions`, `chat_messages`, `audit_log`. Schema version 2 added the layer
catalogue tables; existing databases are upgraded on start-up.

## Git hosts and sign-in

| Host | URL forms | Sign-in |
|---|---|---|
| GitHub / GitHub Enterprise | `https://github.com/org/repo`, `git@github.com:org/repo.git` | OAuth popup or PAT (`repo` scope / read-only Contents) |
| Bitbucket Cloud | `https://bitbucket.org/team/repo` | OAuth popup, Atlassian API token + account email (scope `read:repository:bitbucket`), or a repository/workspace access token |
| Bitbucket Server / Data Center | `https://host[/context]/scm/KEY/repo.git`, `…/projects/KEY/repos/repo/browse` | HTTP access token (username optional) |
| Any Git | `https://host/path/repo.git` | username + token/password |

- Add GitHub Enterprise / Bitbucket Server host names to `GITHUB_ENTERPRISE_HOSTS` / `BITBUCKET_SERVER_HOSTS` when
  their URLs do not follow the usual shapes. `GIT_VERIFY_SSL=false` accepts internal certificates.
- OAuth popup (optional): create a GitHub OAuth app and/or a Bitbucket OAuth consumer with callback
  `{PUBLIC_BASE_URL}/api/git/oauth/github/callback` (or `/bitbucket/callback`) and set the `*_OAUTH_CLIENT_*` values.
- When a host's REST API is blocked by a proxy but `git` over HTTPS works, the access check and branch list fall back
  to `git ls-remote`.
- Each user manages their own connections under **Git connections** (verify / remove).

## LLM (Admin › LLM)

Exactly two providers, configured like SpecForge: **Gemini** (`langchain-google-genai`) and **Spirare**
(OpenAI-style endpoints with `max_model_length`, prompt fitting and SSL bypass). Keys are Fernet-encrypted and never
returned. Extra settings: *files summarised by the LLM per analysis* (most central first, default 40), *code chunks
embedded per analysis* (default 3000; 0 = BM25 only) and *layer catalogue items explained by the LLM* (default 60).
Everything deterministic works without an LLM.

## Roles

| Area | read | write |
|---|---|---|
| Repositories | browse repositories, analyses, files, graph, code | register repositories, run / cancel / delete analyses |
| Insights | Chat with Analyst, view comparisons | create / delete comparisons and narratives |
| Settings | view LLM, users, roles, database, audit | change them |

Predefined: Viewer (read, read, none), Analyst (write, write, none), Administrator (all write; locked). Custom roles
can be added. The last user who can manage settings cannot be removed or demoted. Git connections are personal.

## Docker (Linux x86 / Production)

The application can be built and run on Linux x86 (`linux/amd64`) with persistent storage and environment configuration outside of Docker.

### 1. Build
```bash
./build.sh                                     # Builds for linux/amd64 as code-analyzer:latest
```

### 2. Configure & Start
Store data and environment variables on the host filesystem:
- **Environment**: Reads `./.env` (copy from `.env.example`).
- **Data storage**: Automatically mounted to `./data` outside Docker (DuckDB database, workspaces, uploads).

```bash
./start.sh                                     # Starts container on port 8001
./start.sh 8080                                # Starts on custom port 8080
```

Access the application at `http://localhost:8001`.

### 3. Stop
```bash
./stop.sh                                      # Gracefully stops container; data in ./data remains preserved
```

Or using Docker Compose:
```bash
docker compose up --build                      # DuckDB mounted to ./data, http://localhost:8001
DB_ENGINE=postgres docker compose --profile postgres up --build   # with a PostgreSQL container
```

The image (`Dockerfile`) serves the API and the built frontend on one port, like SpecForge.

## Tests

```bash
cd backend && .venv/bin/ruff check app tests && .venv/bin/pytest            # DuckDB
TEST_DATABASE_URL=postgresql://postgres@localhost:5432/ca_test .venv/bin/pytest  # DuckDB and PostgreSQL
npm test && npm run lint && npm run build
```

## Layout

```
backend/app/
  main.py, config.py, db.py (dual engine + schema), seed.py, deps.py, permissions.py, crypto.py, audit_middleware.py
  routers/   auth, admin (settings, users, roles, audit, database), git, repositories, analyses, chat, comparisons, dashboard
  services/  git_hosts, git_credentials, repositories, analyses, retrieval, chat, compare, jobs, users, roles, audit,
             llm, llm_settings, llm_adapters/{gemini, spirare}, ldap_auth
  services/analyzer/  ingest, languages, macro, parsers, graph, classify, quality, c4, synthesis, chunks, pipeline
  services/analyzer/catalog/  structure (Java/Kotlin/Swift/TS declarations), jvm (Spring, JPA, Retrofit, Room),
             android, swift, web (React, Next.js, Express, NestJS, OpenAPI, Thymeleaf/JSP), data (SQL, Hibernate,
             Liquibase, MyBatis, engines), sql, config, linker, enrich (LLM)
  services/catalog.py  persistence, reads, trace, ERD, CRUD, Markdown / Excel export, contract diff
  tools/migrate_db.py
frontend/src/
  components/  Shell (header, nav rail, telemetry dock), DependencyGraph, CodeView, Mermaid, ChatPanel,
               AddRepository, GitSignIn, BranchPicker, SearchPalette (Ctrl+K), ui
  pages/       Dashboard, Repositories, Repository, Analyses, Analysis (+ analysis/* tabs incl. LayersTab), Compare, Chat,
               GitConnections, Account, settings/{Llm, Users, Roles, Database, AuditLog}
```

## Notes on the architecture spec

The specification describes an enterprise deployment (Temporal, Tree-sitter + SCIP indexers, Neo4j, LanceDB). This
implementation keeps the same four stages and outputs in a self-contained form that runs anywhere SpecForge runs:
background worker threads with progress events and restart recovery instead of Temporal; the standard-library Python
AST and declarative pattern extractors instead of Tree-sitter/SCIP; graph edges stored relationally (both engines)
instead of Neo4j; vectors stored with the chunks instead of a vector database. Cross-file resolution is therefore
heuristic for dynamically dispatched calls.

The layer catalogue is extracted statically. It is strongest on annotation-driven frameworks (Spring, JPA, Retrofit,
Room, NestJS) and on literal routes and URLs; routes or URLs assembled at run time, reflection, and calls made through
dependency-injection containers that are only wired at run time are reported as unresolved rather than guessed.
