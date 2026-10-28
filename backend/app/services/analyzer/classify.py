"""Multi-signal file classification: canonical layer (api, service, data, ui, shared), fine-grained role, and
behavioural concept tags (db-read, db-write, http-call, env-access, validation, tx-scope, messaging, ...).

Signals are path segments, file-name suffixes, framework annotations/decorators, routes and content patterns. Every
decision keeps its evidence so the UI can show why a file got its role.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from app.services.analyzer.parsers import ParseResult, strip_comments_and_strings

LAYERS = ("api", "service", "data", "ui", "shared")

_SEGMENTS: dict[str, set[str]] = {
    "api": {"api", "apis", "controller", "controllers", "route", "routes", "router", "routers", "handler", "handlers",
            "endpoint", "endpoints", "rest", "graphql", "resolvers", "resolver", "grpc", "web", "http", "resources",
            "middleware", "middlewares", "gateway", "views_api", "webapi"},
    "service": {"service", "services", "usecase", "usecases", "use_cases", "domain", "application", "business", "core",
                "logic", "manager", "managers", "worker", "workers", "jobs", "job", "tasks", "interactors", "workflows",
                "processors", "engine", "flows", "commands", "handlers_domain"},
    "data": {"model", "models", "entity", "entities", "repository", "repositories", "repo", "repos", "dao", "daos", "db",
             "database", "persistence", "migrations", "migration", "schema", "schemas", "prisma", "orm", "mapper",
             "mappers", "dal", "storage", "store_db", "sql", "seeds", "seeders", "dto_db", "infrastructure"},
    "ui": {"components", "component", "pages", "page", "screens", "screen", "layouts", "layout", "templates", "template",
           "ui", "frontend", "client", "public", "styles", "style", "css", "assets", "hooks", "widgets", "views",
           "containers", "app_ui", "static", "www", "webapp", "stories"},
    "shared": {"util", "utils", "helper", "helpers", "common", "shared", "lib", "libs", "config", "configs",
               "constants", "types", "typings", "interfaces", "dto", "dtos", "exceptions", "errors", "support", "tools",
               "internal", "pkg"},
}
_SUFFIXES: dict[str, tuple[str, ...]] = {
    "api": ("controller", "controllers", "router", "routes", "route", "handler", "endpoint", "resource", "api", "resolver", "middleware", "servlet", "action", "views"),
    "service": ("service", "services", "usecase", "manager", "worker", "job", "task", "processor", "interactor", "orchestrator", "engine", "facade", "workflow"),
    "data": ("repository", "repo", "dao", "entity", "model", "models", "mapper", "schema", "migration", "table", "record", "store", "dbcontext", "query", "queries"),
    "ui": ("component", "page", "screen", "view", "layout", "widget", "hook", "styles", "style", "template", "dialog", "modal", "form", "panel", "button"),
    "shared": ("util", "utils", "helper", "helpers", "constants", "types", "config", "settings", "exception", "error", "errors", "dto", "common", "enum", "enums"),
}
_CONTENT: list[tuple[str, re.Pattern, int, str]] = [
    ("api", re.compile(r"@(RestController|Controller|RequestMapping|GetMapping|PostMapping|Path|Get|Post|Put|Delete|Patch|Resolver|WebServlet)\b|\[ApiController\]|\[Http(Get|Post|Put|Delete)|ControllerBase\b|APIRouter\(|@(app|router|bp|blueprint|api)\.(get|post|put|delete|patch|route)\b|\b(app|router)\.(get|post|put|delete|patch|use)\s*\(|express\.Router\(|http\.HandleFunc|gin\.Context|echo\.Context|fiber\.Ctx|@api_view|APIView|ViewSet\b|HttpRequest\b|NextApiRequest|NextResponse|Request\s*,\s*Response"), 5, "HTTP routing / controller annotations"),
    ("service", re.compile(r"@(Service|Injectable|Component|Transactional|Scheduled|KafkaListener|RabbitListener|JmsListener)\b|\bclass\s+\w+(Service|UseCase|Manager|Handler|Processor)\b|@celery\.task|@shared_task|@app\.task"), 4, "service / business component markers"),
    ("data", re.compile(r"@(Entity|Table|Repository|Document|Column|Id|MappedSuperclass|Mapper|Query)\b|\bextends\s+(JpaRepository|CrudRepository|Repository)\b|models\.Model\b|declarative_base\(|DeclarativeBase\b|\bBase\s*=|mongoose\.(Schema|model)\(|@prisma/client|PrismaClient|DbContext\b|DbSet<|gorm\.Model|sqlalchemy|\bCREATE\s+TABLE\b|EXEC\s+SQL|\bTypeOrmModule|@Entity\(|Sequelize\b|knex\(|ActiveRecord::|ApplicationRecord", re.I), 4, "ORM / persistence markers"),
    ("ui", re.compile(r"\bReact\b|import\s+React\b|useState\(|useEffect\(|<template>|@Component\(\{|defineComponent\(|className=|\bJSX\b|render\(\s*\)|\bst\.(write|title|sidebar|button|markdown|columns|dataframe)\(|Streamlit|<div[\s>]|<View[\s>]|SwiftUI|@Composable|Widget\s+build\("), 4, "UI framework usage"),
]

ROLE_RULES: list[tuple[str, str, list[tuple[str, re.Pattern, int]]]] = [
    # role, layer hint, [(signal type, pattern, weight)]
    ("Controller / Route Handler", "api", [
        ("name", re.compile(r"(controller|handler|endpoint|resource|resolver|views?|routes?|api)\.\w+$", re.I), 4),
        ("content", re.compile(r"@(RestController|Controller|GetMapping|PostMapping|RequestMapping|Path|Get|Post)\b|\[ApiController\]|APIRouter|@(app|router|bp)\.(get|post|put|delete|route)|\b(app|router)\.(get|post|put|delete)\s*\(|http\.HandleFunc|func\s*\(\w+\s+\*?\w+\)\s*\w+\(\s*w\s+http\.ResponseWriter|ctx\s+\*gin\.Context|@api_view|APIView"), 6),
    ]),
    ("API Router / Gateway", "api", [
        ("name", re.compile(r"(^|/)(router|routes|urls|routing|app-routing\.module|gateway)\.\w+$", re.I), 5),
        ("content", re.compile(r"include_router\(|urlpatterns\s*=|createBrowserRouter|<Routes>|RouterModule\.for|app\.use\(\s*['\"]/|express\.Router\(\)"), 4),
    ]),
    ("Middleware / Interceptor", "api", [
        ("name", re.compile(r"(middleware|interceptor|filter|guard)s?\.\w+$", re.I), 5),
        ("content", re.compile(r"implements\s+(Filter|HandlerInterceptor|NestMiddleware|CanActivate)|@Middleware|BaseHTTPMiddleware|def\s+dispatch\(|next\(\)\s*;?\s*$|OncePerRequestFilter", re.M), 4),
    ]),
    ("Service (Business Logic)", "service", [
        ("name", re.compile(r"(service|services|manager|facade|engine|logic|orchestrator)\.\w+$", re.I), 5),
        ("content", re.compile(r"@Service\b|@Injectable\(\)|class\s+\w+Service\b|interface\s+I?\w+Service\b"), 4),
    ]),
    ("Use Case / Interactor", "service", [
        ("name", re.compile(r"(usecase|use_case|interactor|command|query)handler?\.\w+$", re.I), 5),
        ("path", re.compile(r"(^|/)(usecases?|use_cases|interactors|application/(commands|queries))/", re.I), 4),
    ]),
    ("Repository / DAO", "data", [
        ("name", re.compile(r"(repository|repo|dao|store|gateway_db|crud)\.\w+$", re.I), 6),
        ("content", re.compile(r"@Repository\b|extends\s+(JpaRepository|CrudRepository|PagingAndSortingRepository|MongoRepository)|Repository<|session\.query\(|\.objects\.(filter|get|all)\(|getRepository\(|prisma\.\w+\.(find|create|update|delete)|DbSet<|db\.(Query|Exec)\("), 5),
    ]),
    ("Entity / Domain Model", "data", [
        ("name", re.compile(r"(entity|entities|model|models|domain)\.\w+$|/(models?|entities|domain)/[^/]+\.\w+$", re.I), 4),
        ("content", re.compile(r"@Entity\b|@Table\b|@Document\b|models\.Model\b|mongoose\.Schema|Column\(|mapped_column\(|@Column\b|gorm\.Model|class\s+\w+\(Base\)|class\s+\w+\(SQLModel|ApplicationRecord"), 5),
    ]),
    ("Schema / DTO", "shared", [
        ("name", re.compile(r"(dto|dtos|schema|schemas|request|response|payload|contract|types?)\.\w+$", re.I), 5),
        ("content", re.compile(r"class\s+\w+(Dto|DTO|Request|Response|Payload|Schema)\b|BaseModel\)|z\.object\(|@dataclass|record\s+\w+\(|interface\s+\w+(Request|Response|Props|Dto)\b"), 3),
    ]),
    ("Validator", "shared", [
        ("name", re.compile(r"(validator|validators|validation|rules?)\.\w+$", re.I), 6),
        ("content", re.compile(r"implements\s+(Validator|ConstraintValidator)|AbstractValidator<|@validator|field_validator|Joi\.object|yup\.object|validateSync|ValidationPipe"), 4),
    ]),
    ("Mapper / Serializer", "data", [
        ("name", re.compile(r"(mapper|mappers|serializer|serializers|converter|transformer|adapter|assembler)\.\w+$", re.I), 6),
        ("content", re.compile(r"@Mapper\b|serializers\.(Model)?Serializer|ModelSerializer|IMapper|AutoMapper|toDto\(|fromEntity\("), 3),
    ]),
    ("Migration / Schema Script", "data", [
        ("path", re.compile(r"(^|/)(migrations?|db/migrate|alembic/versions|flyway|liquibase|changelog)/", re.I), 7),
        ("content", re.compile(r"op\.(create_table|add_column)|class\s+\w+\(migrations\.Migration\)|Schema::create", re.I), 3),
    ]),
    ("Stored Procedure / SQL", "data", [
        ("ext", re.compile(r"\.(sql|pls|pks|pkb)$", re.I), 6),
    ]),
    ("Configuration", "shared", [
        ("name", re.compile(r"(config|configuration|settings|conf|env|properties|constants)\.\w+$|^\.?env|application[-.]?\w*\.(ya?ml|properties)$", re.I), 5),
        ("content", re.compile(r"@Configuration\b|@ConfigurationProperties|BaseSettings\)|registerAs\(|module\.exports\s*=\s*\{|defineConfig\("), 4),
    ]),
    ("Dependency Wiring / Module", "shared", [
        ("name", re.compile(r"(module|container|di|injector|providers|bootstrap|wiring|app\.module)\.\w+$", re.I), 4),
        ("content", re.compile(r"@Module\(|@NgModule\(|services\.Add(Scoped|Singleton|Transient)|@Bean\b|providers:\s*\["), 4),
    ]),
    ("Entry Point", "shared", [
        ("name", re.compile(r"(^|/)(main|index|app|server|program|manage|wsgi|asgi|__main__|bootstrap)\.\w+$", re.I), 3),
        ("content", re.compile(r"@SpringBootApplication|if\s+__name__\s*==\s*['\"]__main__['\"]|func\s+main\(\)|static\s+void\s+main\(|fn\s+main\(\)|app\.listen\(|uvicorn\.run\(|createRoot\(|ReactDOM\.render|WebApplication\.CreateBuilder|platformBrowserDynamic"), 6),
    ]),
    ("CLI Command", "shared", [
        ("path", re.compile(r"(^|/)(cli|cmd|commands|bin|scripts|management/commands)/", re.I), 4),
        ("content", re.compile(r"@click\.(command|group)|typer\.Typer|argparse\.ArgumentParser|cobra\.Command|clap::Parser|BaseCommand\)|yargs|commander"), 5),
    ]),
    ("Background Job / Scheduler", "service", [
        ("name", re.compile(r"(job|jobs|task|tasks|worker|workers|scheduler|cron|batch)\.\w+$", re.I), 5),
        ("content", re.compile(r"@Scheduled|@celery\.task|@shared_task|@app\.task|cron\.schedule|BackgroundService|IHostedService|@Job\b|Quartz|apscheduler|BullMQ|Worker\("), 5),
    ]),
    ("Event Consumer / Listener", "service", [
        ("name", re.compile(r"(consumer|listener|subscriber|eventhandler|handlers?)\.\w+$", re.I), 4),
        ("content", re.compile(r"@KafkaListener|@RabbitListener|@JmsListener|@EventListener|@SqsListener|@Subscribe|consumer\.(subscribe|run)\(|\.on\(['\"]message|IConsumer<|@MessagePattern|@EventPattern"), 6),
    ]),
    ("Event Publisher / Producer", "service", [
        ("name", re.compile(r"(producer|publisher|emitter|outbox)\.\w+$", re.I), 5),
        ("content", re.compile(r"KafkaTemplate|producer\.send\(|\.publish\(|basic_publish|SendMessage\(|EventEmitter|IPublishEndpoint"), 3),
    ]),
    ("Outbound Client / Integration", "service", [
        ("name", re.compile(r"(client|clients|gateway|integration|connector|proxy|sdk|api_client|http)\.\w+$", re.I), 5),
        ("content", re.compile(r"RestTemplate|WebClient|@FeignClient|HttpClient\b|axios\.(create|get|post)|fetch\(|requests\.(get|post|put|delete)|httpx\.(get|post|Client|AsyncClient)|http\.NewRequest|reqwest::"), 3),
    ]),
    ("Security / Auth", "shared", [
        ("name", re.compile(r"(auth|security|jwt|oauth|login|permission|permissions|rbac|acl|crypto|password|session)s?\.\w+$", re.I), 5),
        ("content", re.compile(r"@PreAuthorize|@Secured|SecurityFilterChain|WebSecurityConfigurerAdapter|jwt\.(encode|decode|sign|verify)|bcrypt|passport\.|OAuth2|authenticate\(", re.I), 3),
    ]),
    ("Exception / Error Type", "shared", [
        ("name", re.compile(r"(exception|exceptions|error|errors)\.\w+$", re.I), 6),
        ("content", re.compile(r"class\s+\w+(Exception|Error)\s*(\(|extends|:)"), 3),
    ]),
    ("UI Page / Screen", "ui", [
        ("path", re.compile(r"(^|/)(pages|screens|views|routes|app)/.+\.(tsx|jsx|vue|svelte|html|cshtml|jsp|razor)$", re.I), 5),
        ("name", re.compile(r"(page|screen|view)\.(tsx|jsx|vue|svelte)$", re.I), 5),
    ]),
    ("UI Component", "ui", [
        ("ext", re.compile(r"\.(tsx|jsx|vue|svelte)$", re.I), 4),
        ("path", re.compile(r"(^|/)components?/", re.I), 4),
        ("content", re.compile(r"export\s+(default\s+)?function\s+[A-Z]\w*\s*\(|const\s+[A-Z]\w*\s*[:=].*=>\s*[(<]|@Component\(\{|defineComponent\("), 3),
    ]),
    ("UI State / Hook", "ui", [
        ("name", re.compile(r"(^|/)(use[A-Z]\w*|store|stores|slice|reducer|context|atoms?)\.\w+$"), 5),
        ("content", re.compile(r"createSlice\(|createContext\(|defineStore\(|create\(\s*\(set|export\s+function\s+use[A-Z]"), 4),
    ]),
    ("Styling", "ui", [("ext", re.compile(r"\.(css|scss|sass|less|styl)$", re.I), 8)]),
    ("Markup / Template", "ui", [("ext", re.compile(r"\.(html|htm|hbs|ejs|jsp|cshtml|razor|xaml)$", re.I), 6)]),
    ("Utility / Helper", "shared", [
        ("name", re.compile(r"(util|utils|helper|helpers|common|tools|misc|format|formatter|parser)s?\.\w+$", re.I), 5),
        ("path", re.compile(r"(^|/)(utils?|helpers?|lib|common|shared)/", re.I), 2),
    ]),
    ("Types / Constants", "shared", [
        ("name", re.compile(r"(^|[/._-])(types?|typings|constants?|enums?|interfaces?|consts)\.(ts|py|go|java|cs|kt)$", re.I), 5),
        ("ext", re.compile(r"\.d\.ts$", re.I), 8),
    ]),
    ("Test", "shared", [
        # directory / snake-case markers are case-insensitive; CamelCase suffixes (OrderServiceTest, OrderIT) are not,
        # so audit.sql, latest.js or contest.py are not mistaken for tests
        ("path", re.compile(r"(?i:(^|/)(tests?|__tests__|spec|specs|test_\w+|integration-tests|e2e|cypress|playwright)/|(_test|_spec|\.test|\.spec)\.\w+$|(^|/)test_[^/]+\.py$|(^|/)conftest\.py$)"
                            r"|(?<=[a-z0-9])(Test|Tests|IT)\.\w+$|(^|/)(Test|Tests)\.\w+$"), 9),
    ]),
    ("Test Fixture / Mock", "shared", [
        ("path", re.compile(r"(^|/)(fixtures?|mocks?|__mocks__|stubs?|fakes?|testdata|factories)/|(mock|fixture|stub|fake|factory)s?\.\w+$", re.I), 8),
    ]),
    ("Build / Tooling Script", "shared", [
        ("name", re.compile(r"(^|/)(makefile|build\.gradle(\.kts)?|pom\.xml|webpack\.config\.\w+|vite\.config\.\w+|rollup\.config\.\w+|babel\.config\.\w+|gulpfile\.\w+|gruntfile\.\w+|setup\.py|noxfile\.py|tasks\.py|jest\.config\.\w+|tsconfig.*\.json|eslint.*|\.prettierrc.*|package\.json|pyproject\.toml|cargo\.toml|go\.mod)$", re.I), 8),
        ("ext", re.compile(r"\.(sh|bash|ps1|bat|cmd)$", re.I), 5),
    ]),
    ("Infrastructure / Deployment", "shared", [
        ("name", re.compile(r"(^|/)(dockerfile[^/]*|docker-compose[^/]*|compose\.ya?ml|chart\.yaml|values\.yaml|jenkinsfile|\.gitlab-ci\.yml|azure-pipelines\.yml|bitbucket-pipelines\.yml|serverless\.ya?ml|template\.ya?ml|procfile|nginx\.conf)$", re.I), 9),
        ("path", re.compile(r"(^|/)(\.github/workflows|deploy|deployment|k8s|kubernetes|helm|terraform|infra|infrastructure|ansible|\.circleci)/", re.I), 6),
        ("ext", re.compile(r"\.(tf|hcl)$", re.I), 8),
    ]),
    ("Documentation", "shared", [("ext", re.compile(r"\.(md|mdx|rst|adoc|txt)$", re.I), 9)]),
    ("COBOL Program", "service", [("ext", re.compile(r"\.(cbl|cob|cobol|cblle|sqlcblle|sqlcbl|pco|ccp)$", re.I), 8)]),
    ("COBOL Copybook", "data", [("ext", re.compile(r"\.(cpy|copy|cpybk|dcl|dclgen)$", re.I), 8)]),
    ("RPG Copy Member", "shared", [("ext", re.compile(r"\.(rpgleinc|rpginc)$", re.I), 9)]),
    ("RPG / CL Program", "service", [("ext", re.compile(r"\.(rpg|rpgle|sqlrpgle|rpglec|sqlrpg|rpg38|clle|clp|cl38)$", re.I), 8)]),
    ("DDS Display / Printer File", "ui", [("ext", re.compile(r"\.(dspf|prtf)$", re.I), 9)]),
    ("DDS File Definition", "data", [("ext", re.compile(r"\.(pf|lf|dds)$", re.I), 8)]),
    ("CICS BMS Map", "ui", [("ext", re.compile(r"\.bms$", re.I), 9)]),
    ("CICS Resource Definitions", "shared", [("ext", re.compile(r"\.csd$", re.I), 9)]),
    ("Batch Job (JCL)", "service", [("ext", re.compile(r"\.(jcl|prc|jclproc)$", re.I), 9)]),
    ("API Contract (Protobuf / GraphQL / OpenAPI)", "api", [
        ("ext", re.compile(r"\.(proto|graphql|gql)$", re.I), 8),
        ("name", re.compile(r"(openapi|swagger)\.(ya?ml|json)$", re.I), 8),
    ]),
]

CONCEPTS: list[tuple[str, re.Pattern]] = [
    ("db-read", re.compile(r"\bSELECT\b[\s\S]{0,200}?\bFROM\b|\.find(One|All|Many|By\w*|_by)?\(|\.objects\.(filter|get|all)\(|session\.(query|get|scalars?|execute)\(|findBy\w+\(|\.(fetch(all|one|many)|query)\(|getRepository\(|\.where\(|FindAsync\(|ToListAsync\(|db\.Query|QueryRow|\.select\(", re.I)),
    ("db-write", re.compile(r"\b(INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|MERGE\s+INTO)\b|\.(save|saveAll|persist|merge|insert|insertOne|insertMany|updateOne|updateMany|deleteOne|deleteMany|bulk_create|add_all|commit|SaveChanges|SaveChangesAsync|upsert|create)\(|session\.add\(|db\.Exec\(", re.I)),
    ("http-call", re.compile(r"\brequests\.(get|post|put|delete|patch)\(|httpx\.|axios[.(]|\bfetch\(|RestTemplate|WebClient|HttpClient|@FeignClient|http\.(Get|Post|NewRequest)|urllib\.request|reqwest::|Net::HTTP|Guzzle", re.I)),
    ("env-access", re.compile(r"os\.environ|os\.getenv|process\.env|System\.getenv|os\.Getenv|@Value\(\s*\"\$\{|Environment\.GetEnvironmentVariable|env::var|ENV\[|import\.meta\.env|config\.get\(")),
    ("validation", re.compile(r"@Valid\b|@NotNull|@NotBlank|@Size\(|field_validator|@validator|BaseModel\)|z\.(object|string)\(|yup\.|Joi\.|class-validator|IsNotEmpty\(|FluentValidation|AbstractValidator|validate\(|ValidationError", re.I)),
    ("tx-scope", re.compile(r"@Transactional|transaction\.atomic|\.begin\(\)|BEGIN\s+TRANSACTION|beginTransaction|\$transaction\(|TransactionScope|session\.begin|with\s+\w+\.transaction\(|db\.transaction\(|COMMIT\b", re.I)),
    ("messaging", re.compile(r"KafkaTemplate|@KafkaListener|kafka|RabbitTemplate|@RabbitListener|amqp|pika\.|sqs|SNS|pubsub|@JmsListener|IBM\s*MQ|MQQueue|producer\.send|consumer\.(poll|subscribe)", re.I)),
    ("file-io", re.compile(r"\bopen\(|fs\.(read|write)|Files\.(read|write|newBuffered)|FileInputStream|FileOutputStream|os\.Open|File\.(Read|Write)|shutil\.|pathlib", re.I)),
    ("auth", re.compile(r"jwt|oauth|bcrypt|password|@PreAuthorize|@Secured|authenticate|authorize|login|ldap", re.I)),
    ("cache", re.compile(r"redis|@Cacheable|@CacheEvict|lru_cache|memcache|IMemoryCache|cache\.(get|set)\(", re.I)),
    ("scheduling", re.compile(r"@Scheduled|cron|celery\.beat|apscheduler|setInterval\(|BackgroundService|Timer\(", re.I)),
    ("logging", re.compile(r"\blog(ger)?\.(info|warn|warning|error|debug)\(|console\.(log|error|warn)\(|logging\.getLogger|slf4j|ILogger<|zap\.|logrus", re.I)),
]

TEST_PATTERN = ROLE_RULES[[rule[0] for rule in ROLE_RULES].index("Test")][2][0][1]


@dataclass
class Classification:
    layer: str
    role: str
    confidence: float
    concepts: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    is_test: bool = False


def _segments(path: str) -> list[str]:
    return [part.lower() for part in PurePosixPath(path).parts[:-1]]


def _stem_tokens(path: str) -> list[str]:
    name = PurePosixPath(path).name
    stem = name.split(".")[0]
    tokens = re.findall(r"[A-Z]?[a-z0-9]+|[A-Z]+(?![a-z])", stem)
    return [token.lower() for token in tokens] + [stem.lower()]


def classify(path: str, language: str, category: str, text: str, parsed: ParseResult | None) -> Classification:
    evidence: list[str] = []
    raw_sample = text[:60000]
    sample = strip_comments_and_strings(raw_sample, language) if category in ("code", "script") else raw_sample
    is_test = bool(TEST_PATTERN.search(path))

    scores = {layer: 0.0 for layer in LAYERS}
    for segment in _segments(path):
        for layer, names in _SEGMENTS.items():
            if segment in names:
                scores[layer] += 3
                evidence.append(f"folder '{segment}/' → {layer}")
    tokens = _stem_tokens(path)
    for layer, suffixes in _SUFFIXES.items():
        if tokens and (tokens[-2] if len(tokens) > 1 else tokens[0]) in suffixes:
            scores[layer] += 3
            evidence.append(f"file name suffix '{tokens[-2] if len(tokens) > 1 else tokens[0]}' → {layer}")
    if category in ("code", "markup", "style", "script"):
        for layer, pattern, weight, label in _CONTENT:
            if pattern.search(sample):
                scores[layer] += weight
                evidence.append(f"{label} → {layer}")
    if category in ("markup", "style") or path.lower().endswith((".tsx", ".jsx", ".vue", ".svelte")):
        scores["ui"] += 3
    if parsed and parsed.routes:
        scores["api"] += 3
        evidence.append(f"{len(parsed.routes)} route declaration(s) → api")

    role, confidence, role_layer, role_evidence = _role(path, sample, category)
    legacy_role = _legacy_role(language, parsed)
    if legacy_role and (confidence < 0.5 or role in ("COBOL Program", "COBOL Copybook")):
        role, role_layer = legacy_role
        confidence = max(confidence, 0.8)
        role_evidence = role_evidence + [f"{language} source → {role}"]
    evidence += role_evidence
    if role_layer and role not in ("Test", "Test Fixture / Mock", "Documentation", "Build / Tooling Script", "Configuration", "Infrastructure / Deployment"):
        scores[role_layer] += 2
    layer = max(scores, key=lambda key: (scores[key], key == "shared"))
    if scores[layer] <= 0:
        layer = role_layer or "shared"
    if legacy_role and not is_test:
        layer = role_layer  # a COBOL program with embedded SQL is business logic, not the data layer
    concepts = [] if category not in ("code", "script") else [name for name, pattern in CONCEPTS if pattern.search(sample)]
    if is_test:
        role = "Test" if role not in ("Test Fixture / Mock",) else role
    return Classification(layer, role, round(confidence, 2), concepts, evidence[:12], is_test)


LEGACY_ROLES = {
    "COBOL": ("COBOL Program", "service"), "COBOL Copybook": ("COBOL Copybook", "data"), "RPG": ("RPG / CL Program", "service"),
    "CL": ("RPG / CL Program", "service"), "JCL": ("Batch Job (JCL)", "service"), "DDS": ("DDS File Definition", "data"),
    "BMS": ("CICS BMS Map", "ui"), "CICS CSD": ("CICS Resource Definitions", "shared"),
}


def _legacy_role(language: str, parsed: ParseResult | None) -> tuple[str, str] | None:
    if language not in LEGACY_ROLES:
        return None
    if language == "COBOL" and parsed is not None and not any(s.kind == "program" for s in parsed.symbols):
        return ("COBOL Copybook", "data")
    if language == "RPG" and parsed is not None and not parsed.has_main and not parsed.symbols:
        return ("RPG Copy Member", "shared")
    return LEGACY_ROLES[language]


def _role(path: str, sample: str, category: str) -> tuple[str, float, str, list[str]]:
    name = PurePosixPath(path).name
    totals: list[tuple[float, str, str, list[str]]] = []
    for role, layer, rules in ROLE_RULES:
        score = 0.0
        hits: list[str] = []
        for kind, pattern, weight in rules:
            if kind == "name" and pattern.search(path if "/" in pattern.pattern else name):
                score += weight
                hits.append(f"name matches {role}")
            elif kind == "path" and pattern.search(path):
                score += weight
                hits.append(f"path matches {role}")
            elif kind == "ext" and pattern.search(name):
                score += weight
                hits.append(f"extension → {role}")
            elif kind == "content" and category in ("code", "markup", "script", "config") and pattern.search(sample):
                score += weight
                hits.append(f"content markers → {role}")
        if score:
            totals.append((score, role, layer, hits))
    if not totals:
        fallback = {"doc": "Documentation", "config": "Configuration", "data": "Data / Fixture", "style": "Styling", "markup": "Markup / Template"}.get(category, "Module")
        return fallback, 0.3, "shared", []
    totals.sort(key=lambda item: -item[0])
    best = totals[0]
    runner = totals[1][0] if len(totals) > 1 else 0.0
    confidence = min(0.99, 0.45 + 0.5 * (best[0] - runner) / max(best[0], 1) + min(best[0], 12) / 40)
    return best[1], confidence, best[2], best[3][:3]
