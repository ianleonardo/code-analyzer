"""Java / Kotlin extractors: Spring MVC + REST endpoints, services, schedulers, listeners, Spring Security, JPA /
Hibernate entities, Spring Data / DAO / MyBatis / Room queries, Feign and Retrofit clients, DTOs, enums, exceptions,
constants, configuration-properties classes and @Bean wiring.
"""

from __future__ import annotations

import re

from app.services.analyzer.catalog import sql as sqlmod
from app.services.analyzer.catalog.model import Catalog, Context, Item, TypeRef, join_paths, snippet
from app.services.analyzer.catalog.structure import (
    Annotation,
    Member,
    Param,
    TypeDecl,
    simple_type,
    split_top,
    string_values,
    unwrap_type,
)

JVM = ("Java", "Kotlin")
HTTP_STATUS = {
    "CONTINUE": 100, "OK": 200, "CREATED": 201, "ACCEPTED": 202, "NO_CONTENT": 204, "PARTIAL_CONTENT": 206, "MOVED_PERMANENTLY": 301,
    "FOUND": 302, "SEE_OTHER": 303, "NOT_MODIFIED": 304, "TEMPORARY_REDIRECT": 307, "BAD_REQUEST": 400, "UNAUTHORIZED": 401,
    "PAYMENT_REQUIRED": 402, "FORBIDDEN": 403, "NOT_FOUND": 404, "METHOD_NOT_ALLOWED": 405, "NOT_ACCEPTABLE": 406, "CONFLICT": 409,
    "GONE": 410, "PRECONDITION_FAILED": 412, "PAYLOAD_TOO_LARGE": 413, "UNSUPPORTED_MEDIA_TYPE": 415, "UNPROCESSABLE_ENTITY": 422,
    "LOCKED": 423, "TOO_MANY_REQUESTS": 429, "INTERNAL_SERVER_ERROR": 500, "NOT_IMPLEMENTED": 501, "BAD_GATEWAY": 502,
    "SERVICE_UNAVAILABLE": 503, "GATEWAY_TIMEOUT": 504,
}
RESPONSE_HELPERS = {"ok": 200, "created": 201, "accepted": 202, "noContent": 204, "badRequest": 400, "notFound": 404, "unprocessableEntity": 422,
                    "internalServerError": 500}
MAPPING_FOR = {"GET": "GetMapping", "POST": "PostMapping", "PUT": "PutMapping", "DELETE": "DeleteMapping", "PATCH": "PatchMapping"}
MAPPINGS = {
    "GetMapping": "GET", "PostMapping": "POST", "PutMapping": "PUT", "DeleteMapping": "DELETE", "PatchMapping": "PATCH", "RequestMapping": "",
    "GetExchange": "GET", "PostExchange": "POST", "PutExchange": "PUT", "DeleteExchange": "DELETE", "PatchExchange": "PATCH", "HttpExchange": "",
    "Get": "GET", "Post": "POST", "Put": "PUT", "Delete": "DELETE", "Patch": "PATCH",
}
JAXRS_VERBS = {"GET": "GET", "POST": "POST", "PUT": "PUT", "DELETE": "DELETE", "PATCH": "PATCH", "HEAD": "HEAD", "OPTIONS": "OPTIONS"}
WRAPPERS = {"ResponseEntity", "Mono", "Flux", "Optional", "CompletableFuture", "Future", "Callable", "DeferredResult", "HttpEntity", "Response",
            "Call", "Single", "Observable", "Flow", "LiveData", "StateFlow", "Deferred", "Result", "EntityModel", "CollectionModel", "Uni", "Multi"}
COLLECTIONS = {"List", "Set", "Collection", "Iterable", "Page", "Slice", "Stream", "ArrayList", "MutableList", "Array", "Sequence", "PagedModel", "Map", "HashMap", "Flux"}
FRAMEWORK_PARAMS = {"Model", "ModelMap", "BindingResult", "Errors", "HttpServletRequest", "HttpServletResponse", "HttpSession", "Principal",
                    "Authentication", "Locale", "RedirectAttributes", "SessionStatus", "WebRequest", "ServerWebExchange", "UriComponentsBuilder",
                    "ModelAndView", "Pageable", "Sort", "TimeZone", "ZoneId", "InputStream", "OutputStream", "Reader", "Writer", "Jwt", "OAuth2User",
                    "UserDetails", "Continuation"}
VALIDATIONS = {"NotNull", "NotBlank", "NotEmpty", "Size", "Min", "Max", "DecimalMin", "DecimalMax", "Positive", "PositiveOrZero", "Negative",
               "NegativeOrZero", "Email", "Pattern", "Past", "PastOrPresent", "Future", "FutureOrPresent", "Digits", "Valid", "Validated", "Length",
               "Range", "URL", "AssertTrue", "AssertFalse", "Null", "CreditCardNumber", "UUID", "Unique"}
SERVICE_MARKERS = {"Service", "Component", "Transactional", "Named", "Singleton", "ApplicationScoped", "RequestScoped", "Stateless", "Stateful"}
REPOSITORY_BASES = {"JpaRepository", "CrudRepository", "PagingAndSortingRepository", "Repository", "ListCrudRepository", "ListPagingAndSortingRepository",
                    "JpaSpecificationExecutor", "QuerydslPredicateExecutor", "ReactiveCrudRepository", "R2dbcRepository", "MongoRepository",
                    "CoroutineCrudRepository", "RevisionRepository", "ReactiveSortingRepository", "ElasticsearchRepository", "JpaRepositoryImplementation"}
INHERITED = {
    "CrudRepository": ["save", "saveAll", "findById", "existsById", "findAll", "findAllById", "count", "deleteById", "delete", "deleteAllById", "deleteAll"],
    "JpaRepository": ["save", "saveAll", "saveAndFlush", "findById", "existsById", "findAll", "findAllById", "count", "deleteById", "delete",
                      "deleteAllInBatch", "getReferenceById", "flush"],
    "PagingAndSortingRepository": ["findAll(Pageable)", "findAll(Sort)"],
    "JpaSpecificationExecutor": ["findOne(Specification)", "findAll(Specification)", "count(Specification)"],
}


# --- small helpers --------------------------------------------------------------------------------------------------


def describe_validation(ann: Annotation) -> str:
    n = ann.name
    if n == "NotNull":
        return "required (not null)"
    if n == "NotBlank":
        return "required, not blank"
    if n == "NotEmpty":
        return "required, not empty"
    if n in ("Size", "Length"):
        lo, hi = ann.raw("min"), ann.raw("max")
        if lo and hi:
            return f"length {lo}–{hi}"
        return f"length ≥ {lo}" if lo else f"length ≤ {hi}" if hi else "size constraint"
    if n in ("Min", "DecimalMin"):
        return f"≥ {ann.string('value', '') or ann.raw('value', '')}"
    if n in ("Max", "DecimalMax"):
        return f"≤ {ann.string('value', '') or ann.raw('value', '')}"
    if n == "Range":
        return f"between {ann.raw('min') or '0'} and {ann.raw('max') or '∞'}"
    if n == "Positive":
        return "> 0"
    if n == "PositiveOrZero":
        return "≥ 0"
    if n == "Negative":
        return "< 0"
    if n == "NegativeOrZero":
        return "≤ 0"
    if n == "Email":
        return "valid e-mail address"
    if n == "Pattern":
        return f"matches /{ann.string('regexp', '')}/"
    if n == "Digits":
        return f"up to {ann.raw('integer')} integer and {ann.raw('fraction')} fraction digits"
    if n in ("Past", "PastOrPresent", "Future", "FutureOrPresent"):
        return re.sub(r"(?<!^)([A-Z])", r" \1", n).lower() + " date"
    if n in ("Valid", "Validated"):
        return "validated (@Valid)"
    if n == "URL":
        return "valid URL"
    if n in ("AssertTrue", "AssertFalse"):
        return "must be " + ("true" if n == "AssertTrue" else "false")
    return f"@{n}"


def validations_of(annotations: list[Annotation]) -> list[str]:
    out = []
    for ann in annotations:
        if ann.name in VALIDATIONS:
            text = describe_validation(ann)
            message = ann.string("message")
            out.append(f"{text} — \"{message}\"" if message and not message.startswith("{") else text)
    return out


def type_label(type_text: str) -> str:
    return re.sub(r"\s+", " ", (type_text or "").replace("? extends ", "").strip())


def _line_in(body: str, index: int, base_line: int) -> int:
    return base_line + body.count("\n", 0, index)


def field_types(ref: TypeRef, ctx: Context) -> dict[str, str]:
    """Variable name -> simple type for fields, constructor / primary-constructor params and delegated view models."""
    out: dict[str, str] = {}
    for param in ref.decl.params:
        out[param.name] = simple_type(param.type)
    for member in ref.decl.members:
        if member.kind == "field":
            t = member.type
            if not t and member.initializer:
                vm = re.match(r"(?:viewModels|activityViewModels|hiltViewModel|viewModel|inject|lazy|koinViewModel|navGraphViewModels)\s*<\s*([\w.]+)\s*>", member.initializer)
                cm = re.match(r"([A-Z]\w*)\s*\(", member.initializer)
                t = vm.group(1) if vm else cm.group(1) if cm else ""
            if t:
                out[member.name] = simple_type(t)
        elif member.kind == "constructor":
            for param in member.params:
                out.setdefault(param.name, simple_type(param.type))
    for base, _args in ref.decl.bases:
        base_ref = ctx.type(base, ref.file)
        if base_ref and base_ref.decl is not ref.decl and base_ref.decl.kind in ("class", "object"):
            for name, t in field_types(base_ref, ctx).items() if base_ref.decl.name != ref.decl.name else []:
                out.setdefault(name, t)
    return out


FRAMEWORK_TYPES = {"String", "Integer", "Long", "List", "Map", "Set", "Optional", "Model", "BindingResult", "Logger", "MutableStateFlow", "StateFlow", "MutableLiveData",
                   "LiveData", "SavedStateHandle", "CoroutineScope", "Job", "Flow", "MutableSharedFlow", "SharedFlow", "Channel", "SnackbarHostState", "NavController",
                   "NavHostController", "Context", "Intent", "Bundle", "Modifier", "StringBuilder", "MutableList", "MutableMap", "ArrayList", "HashMap", "Gson", "ObjectMapper",
                   "RedirectAttributes", "ModelMap", "HttpServletRequest", "HttpServletResponse", "Errors", "Object", "Any", "Unit", "Boolean", "Int", "Double", "Date", "LocalDate"}
_CALL = re.compile(r"(?<![\w.])(?:this\.)?([a-z_]\w*)\s*(?:\?|!!)?\s*(?:\.\s*([A-Za-z_]\w*)\s*(?:<[^<>()]*>)?\s*[({]|::\s*([A-Za-z_]\w*))")
_STATIC_CALL = re.compile(r"(?<![\w.])([A-Z]\w+)\s*\.\s*([a-z]\w*)\s*\(")
_LOCAL_DECL = re.compile(r"(?<![\w.])(?:final\s+)?([A-Z][\w]*)(?:<[^<>;=]*>)?\s+([a-z_]\w*)\s*=")
_KT_LOCAL = re.compile(r"\b(?:val|var)\s+([a-z_]\w*)\s*:\s*([A-Z]\w*)")


def calls_in(body: str, var_types: dict[str, str], base_line: int, known_types: set[str] | None = None) -> list[dict]:
    local = dict(var_types)
    for m in _LOCAL_DECL.finditer(body):
        local.setdefault(m.group(2), m.group(1))
    for m in _KT_LOCAL.finditer(body):
        local.setdefault(m.group(1), m.group(2))
    out, seen = [], set()
    for m in _CALL.finditer(body):
        var, method = m.group(1), m.group(2) or m.group(3)
        target = local.get(var)
        if not target or target in FRAMEWORK_TYPES:
            continue
        key = (target, method)
        if key in seen:
            continue
        seen.add(key)
        out.append({"type": target, "method": method, "via": var, "line": _line_in(body, m.start(), base_line)})
    if known_types:
        for m in _STATIC_CALL.finditer(body):
            if m.group(1) in known_types and (m.group(1), m.group(2)) not in seen:
                seen.add((m.group(1), m.group(2)))
                out.append({"type": m.group(1), "method": m.group(2), "via": "static", "line": _line_in(body, m.start(), base_line)})
    return out[:60]


def _first_string(text: str) -> str:
    values = string_values(text or "")
    return values[0] if values else ""


def throws_in(body: str, base_line: int) -> list[dict]:
    out, seen = [], set()
    patterns = [
        r"throw\s+new\s+([A-Z]\w*)\s*\(", r"throw\s+([A-Z]\w*)\s*\(", r"orElseThrow\s*\(\s*(?:\(\s*\)\s*->\s*new\s+|[A-Z]\w*::new|\{\s*)?([A-Z]\w*)?",
        r"ResponseStatusException\s*\(",
    ]
    for pattern in patterns:
        for m in re.finditer(pattern, body):
            name = m.group(1) if m.groups() and m.group(1) else ("ResponseStatusException" if "ResponseStatus" in pattern else "NoSuchElementException")
            tail = body[m.end() - 1 : m.end() + 240] if body[m.end() - 1 : m.end()] == "(" else body[m.end() : m.end() + 240]
            message = _first_string(tail.split(";")[0].split("\n")[0])
            status = ""
            sm = re.search(r"HttpStatus\.(\w+)", tail.split(";")[0])
            if sm:
                status = str(HTTP_STATUS.get(sm.group(1), sm.group(1)))
            key = (name, message)
            if key in seen:
                continue
            seen.add(key)
            out.append({"exception": name, "message": message[:200], "status": status, "line": _line_in(body, m.start(), base_line)})
    return out[:20]


def _condition_at(body: str, open_index: int) -> tuple[str, int]:
    from app.services.analyzer.catalog.structure import match_close

    close = match_close(body, open_index)
    return re.sub(r"\s+", " ", body[open_index + 1 : close]).strip(), close


def _outcome(statement: str) -> str:
    s = statement.strip()
    m = re.search(r"throw\s+(?:new\s+)?([A-Z]\w*)\s*\(([^;]*)", s)
    if m:
        msg = _first_string(m.group(2))
        return f"throws {m.group(1)}" + (f": \"{msg[:120]}\"" if msg else "")
    m = re.search(r"rejectValue\s*\(([^;]*)\)", s)
    if m:
        args = string_values(m.group(1))
        return f"rejects field '{args[0]}'" + (f" ({args[-1]})" if len(args) > 1 else "") if args else "rejects the input"
    m = re.search(r"ResponseEntity\s*\.\s*(\w+)\s*\(", s)
    if m and m.group(1) in RESPONSE_HELPERS:
        return f"responds {RESPONSE_HELPERS[m.group(1)]}"
    m = re.search(r"HttpStatus\.(\w+)", s)
    if m:
        return f"responds {HTTP_STATUS.get(m.group(1), m.group(1))}"
    m = re.search(r"return\s+\"(redirect:[^\"]*|[\w/.-]+)\"", s)
    if m:
        return ("redirects to " if m.group(1).startswith("redirect:") else "renders view ") + m.group(1).replace("redirect:", "")
    if re.search(r"\breturn\s+(null|false|Optional\.empty|emptyList|listOf\(\))\b", s):
        return "returns empty / false"
    if re.search(r"\breturn\b", s):
        return "returns early"
    if re.search(r"\b(continue|break)\b", s):
        return "skips the item"
    m = re.search(r"(?:log|logger|LOG)\.(warn|error)\s*\(([^;]*)", s)
    if m:
        return f"logs {m.group(1)}: \"{_first_string(m.group(2))[:100]}\""
    m = re.search(r"\.(set\w+)\s*\(([^;]{0,80})\)", s)
    if m:
        return f"{m.group(1)}({m.group(2).strip()})"
    return ""


def rules_in(body: str, base_line: int, language: str = "Java") -> list[dict]:
    """Guards and validation rules: if (cond) { throw / reject / return error }, Assert / require / Preconditions, ?: throw."""
    out: list[dict] = []
    for m in re.finditer(r"\bif\s*\(", body):
        condition, close = _condition_at(body, m.end() - 1)
        rest = body[close + 1 : close + 600].lstrip()
        if rest.startswith("{"):
            from app.services.analyzer.catalog.structure import match_close

            end = match_close(rest, 0)
            block = rest[1:end]
        else:
            block = rest.split(";")[0] if language == "Java" else rest.split("\n")[0]
        outcome = _outcome(block)
        if outcome and len(condition) < 300:
            out.append({"kind": "guard", "condition": condition, "outcome": outcome, "line": _line_in(body, m.start(), base_line)})
    for m in re.finditer(r"\b(Assert\.(?:notNull|isTrue|hasText|hasLength|notEmpty|state|isNull)|Objects\.requireNonNull|Preconditions\.check(?:Argument|State|NotNull)|Validate\.\w+|require|check|requireNotNull|checkNotNull)\s*\(", body):
        cond, close = _condition_at(body, m.end() - 1)
        parts = split_top(cond)
        message = _first_string(cond) or _first_string(body[close + 1 : close + 120].split("\n")[0])
        out.append({"kind": "assert", "condition": parts[0] if parts else cond, "outcome": f"fails with \"{message[:120]}\"" if message else f"fails ({m.group(1)})",
                    "line": _line_in(body, m.start(), base_line)})
    for m in re.finditer(r"([\w.()\[\]?!]+)\s*\?:\s*throw\s+([A-Z]\w*)\s*\(([^)\n]*)", body):
        out.append({"kind": "guard", "condition": f"{m.group(1)} is null", "outcome": f"throws {m.group(2)}" + (f": \"{_first_string(m.group(3))}\"" if _first_string(m.group(3)) else ""),
                    "line": _line_in(body, m.start(), base_line)})
    for m in re.finditer(r"\.orElseThrow\s*\(", body):
        before = body[max(0, m.start() - 160) : m.start()]
        call = re.findall(r"(\w+\.\w+\([^;{}]*\))\s*$", before)
        tail = body[m.end() : m.end() + 200]
        exc = re.search(r"([A-Z]\w*(?:Exception|Error))", tail)
        out.append({"kind": "guard", "condition": f"{call[-1] if call else 'the lookup'} finds nothing", "outcome": f"throws {exc.group(1) if exc else 'NoSuchElementException'}" +
                    (f": \"{_first_string(tail.split(')')[0])}\"" if _first_string(tail.split(')')[0]) else ""), "line": _line_in(body, m.start(), base_line)})
    if re.search(r"\.hasErrors\s*\(\s*\)", body):
        pass  # covered by the if-rule above
    seen, unique = set(), []
    for rule in out:
        key = (rule["condition"], rule["outcome"])
        if key not in seen:
            seen.add(key)
            unique.append(rule)
    return unique[:25]


def status_codes(body: str) -> list[int]:
    codes: set[int] = set()
    for m in re.finditer(r"ResponseEntity\s*\.\s*(\w+)\s*\(", body):
        if m.group(1) in RESPONSE_HELPERS:
            codes.add(RESPONSE_HELPERS[m.group(1)])
    for m in re.finditer(r"HttpStatus(?:Code)?\.(\w+)|HttpStatus\.valueOf\((\d{3})\)|status\((\d{3})\)", body):
        if m.group(1) and m.group(1) in HTTP_STATUS:
            codes.add(HTTP_STATUS[m.group(1)])
        elif m.group(2) or m.group(3):
            codes.add(int(m.group(2) or m.group(3)))
    return sorted(codes)


def events_in(body: str, ctx: Context, base_line: int) -> list[dict]:
    out = []
    for m in re.finditer(r"publish(?:Event)?\s*\(\s*(?:new\s+)?([A-Z]\w*)", body):
        out.append({"kind": "event", "name": m.group(1), "line": _line_in(body, m.start(), base_line)})
    for m in re.finditer(r"(\w*[kK]afka\w*|\w*[rR]abbit\w*|\w*[jJ]ms\w*|\w*[aA]mqp\w*|streamBridge|\w*[tT]emplate)\s*\.\s*(send|convertAndSend|sendDefault|publish)\s*\(\s*([^,)]+)", body):
        destination = ctx.placeholder(ctx.resolve(m.group(3)))
        kind = "kafka" if "afka" in m.group(1) else "rabbitmq" if re.search("abbit|mqp", m.group(1)) else "jms" if "ms" in m.group(1).lower() else "stream" if "stream" in m.group(1).lower() else "message"
        if destination and not destination.startswith("{"):
            out.append({"kind": kind, "name": destination, "line": _line_in(body, m.start(), base_line)})
    return out[:20]


def http_calls(body: str, ctx: Context, base_line: int) -> list[dict]:
    out = []
    for m in re.finditer(r"\b\w*(?:restTemplate|RestTemplate|template)\s*\.\s*(getForObject|getForEntity|postForObject|postForEntity|postForLocation|put|delete|patchForObject|exchange)\s*\(\s*([^,)]+)(?:,\s*HttpMethod\.(\w+))?", body):
        verb = {"getForObject": "GET", "getForEntity": "GET", "postForObject": "POST", "postForEntity": "POST", "postForLocation": "POST", "put": "PUT",
                "delete": "DELETE", "patchForObject": "PATCH"}.get(m.group(1), m.group(3) or "")
        if m.group(1) == "exchange":
            hm = re.search(r"HttpMethod\.(\w+)", body[m.start() : m.start() + 300])
            verb = hm.group(1) if hm else ""
        out.append({"client": "RestTemplate", "method": verb, "url": ctx.placeholder(ctx.resolve(m.group(2))), "line": _line_in(body, m.start(), base_line)})
    for m in re.finditer(r"\.\s*(get|post|put|delete|patch|method)\s*\(\s*(?:HttpMethod\.(\w+))?\s*\)\s*\.\s*uri\s*\(\s*([^,)]+)", body):
        out.append({"client": "WebClient", "method": (m.group(2) or m.group(1)).upper(), "url": ctx.placeholder(ctx.resolve(m.group(3))), "line": _line_in(body, m.start(), base_line)})
    return out[:20]


SQL_START = re.compile(r"^\s*(SELECT|INSERT|UPDATE|DELETE|MERGE|WITH|CALL|\{\s*call|EXEC|BEGIN)\b", re.I)


def sql_in(body: str, base_line: int) -> list[dict]:
    """SQL found in string literals (text blocks and concatenations): jdbcTemplate, EntityManager, Hibernate Session, ..."""
    out = []
    statements = re.split(r";\s*\n|;(?=\s*[\w}])", body)
    offset = 0
    for statement in statements:
        values = string_values(statement)
        index = body.find(statement, offset)
        offset = max(offset, index if index >= 0 else offset)
        if not values:
            continue
        joined = " ".join(v.replace("\\n", " ").replace("\\t", " ") for v in values)
        sm = SQL_START.search(joined) or re.search(r"\b(SELECT\s[\s\S]+?\sFROM\s|INSERT\s+INTO\s|UPDATE\s+\w+\s+SET\s|DELETE\s+FROM\s)", joined, re.I)
        if not sm:
            continue
        sql_text = joined[sm.start() :].strip()
        native = bool(re.search(r"createNativeQuery|jdbc|Jdbc|createSQLQuery|prepareStatement|executeQuery|executeUpdate|nativeQuery", statement))
        jpql = bool(re.search(r"createQuery|@Query", statement)) and not native
        procedure = re.search(r"\{\s*call\s+([\w.]+)|\bCALL\s+([\w.]+)|\bEXEC(?:UTE)?\s+([\w.]+)", sql_text, re.I)
        out.append({"sql": re.sub(r"\s+", " ", sql_text)[:3000], "dialect": "JPQL" if jpql else "SQL", "line": base_line + body.count("\n", 0, max(0, index)) if index >= 0 else base_line,
                    "procedure": next((g for g in procedure.groups() if g), "") if procedure else ""})
    for m in re.finditer(r"(?:withProcedureName|withFunctionName|createStoredProcedureQuery|procedureName\s*=|@Procedure\s*\(\s*(?:name\s*=|procedureName\s*=|value\s*=)?)\s*\(?\s*\"([\w.$#]+)\"", body):
        out.append({"sql": f"CALL {m.group(1)}", "dialect": "SQL", "line": _line_in(body, m.start(), base_line), "procedure": m.group(1)})
    return out[:30]


# --- DTO expansion --------------------------------------------------------------------------------------------------


def dto_fields(ctx: Context, type_text: str, near, depth: int = 0, seen: set[str] | None = None) -> dict:
    """{'type': 'Owner', 'collection': bool, 'fields': [...], 'path': ..., 'line': ...} for a request / response type."""
    seen = set(seen or ())
    inner, peeled = unwrap_type(type_text, WRAPPERS | COLLECTIONS)
    name = simple_type(inner)
    out: dict = {"type": type_label(type_text), "name": name, "collection": any(p in COLLECTIONS for p in peeled), "fields": []}
    if name in seen or depth > 2:
        return out
    ref = ctx.type(name, near)
    if ref is None:
        return out
    seen.add(name)
    out["path"], out["line"] = ref.file.path, ref.decl.line
    out["fields"] = fields_of(ctx, ref, depth, seen)
    if ref.decl.kind == "enum":
        out["enum"] = ref.decl.enum_constants
    return out


def fields_of(ctx: Context, ref: TypeRef, depth: int = 0, seen: set[str] | None = None) -> list[dict]:
    seen = set(seen or ())
    decl = ref.decl
    out: list[dict] = []
    sources: list[tuple[str, str, list[Annotation], bool, str]] = []
    for param in decl.params:
        nullable = param.type.strip().endswith("?")
        sources.append((param.name, param.type, param.annotations, nullable, param.default))
    for member in decl.members:
        if member.kind != "field" or "static" in member.modifiers or "const" in member.modifiers or member.has("Transient", "JsonIgnore"):
            continue
        if decl.kind == "enum":
            continue
        nullable = member.type.strip().endswith("?")
        sources.append((member.name, member.type, member.annotations, nullable, member.initializer))
    for name, type_text, field_annotations, nullable, default in sources:
        validations = validations_of(field_annotations)
        json_name = next((a.string("value", "") for a in field_annotations if a.name in ("JsonProperty", "SerializedName", "Json", "SerialName") and a.string("value", "")), "")
        primitive = type_text.strip() in ("int", "long", "boolean", "double", "float", "short", "byte", "char")
        required = any(v.startswith("required") for v in validations) or primitive or (ref.file.language == "Kotlin" and not nullable and not default)
        entry = {"name": json_name or name, "type": type_label(type_text), "required": required, "validations": validations}
        column = next((a for a in field_annotations if a.name == "Column"), None)
        if column is not None:
            if column.raw("nullable") == "false":
                entry["validations"] = entry["validations"] + ["column NOT NULL"]
            if column.raw("length"):
                entry["validations"] = entry["validations"] + [f"column length {column.raw('length')}"]
        if default and not re.match(r"^(null|None)$", default):
            entry["default"] = default[:80]
        inner, _ = unwrap_type(type_text, WRAPPERS | COLLECTIONS)
        child = ctx.type(simple_type(inner), ref.file)
        if child and depth < 2 and simple_type(inner) not in seen and child.decl.kind in ("class", "record", "interface", "struct", "object"):
            if not child.decl.has("Entity") or depth == 0:
                nested = fields_of(ctx, child, depth + 1, seen | {decl.name})
                if nested:
                    entry["fields"] = nested[:40]
        elif child and child.decl.kind == "enum":
            entry["enum"] = child.decl.enum_constants[:30]
        out.append(entry)
    for base, _args in decl.bases:
        base_ref = ctx.type(base, ref.file)
        if base_ref and base_ref.decl.kind in ("class", "record") and base not in seen and base_ref.decl is not decl:
            out += [f for f in fields_of(ctx, base_ref, depth, seen | {decl.name}) if f["name"] not in {o["name"] for o in out}]
    return out[:80]


# --- extraction -----------------------------------------------------------------------------------------------------


def _iter_types(ctx: Context):
    for file in ctx.code_files(*JVM):
        fs = ctx.structure(file)
        if fs is None:
            continue
        for decl in fs.all_types():
            yield TypeRef(decl, fs, file)


def _is_controller(decl: TypeDecl, ctx: Context | None = None, file=None) -> bool:
    if decl.has("RestController", "Controller", "RepositoryRestController", "BasePathAwareController"):
        return True
    if decl.kind == "class" and decl.has("Path") and any(m.has(*JAXRS_VERBS, "Path") for m in decl.methods()):
        return True
    if decl.kind == "class" and decl.name.endswith(("Controller", "Resource", "Endpoint")) and any(m.has(*MAPPINGS, *JAXRS_VERBS) for m in decl.methods()):
        return True
    if ctx is not None and decl.kind == "class" and decl.bases:
        for base_name in decl.base_names():
            base_ref = ctx.type(base_name, file)
            if base_ref and base_ref.decl.kind == "interface":
                if base_ref.decl.has("RequestMapping", "HttpExchange", "Path") or any(m.has(*MAPPINGS, *JAXRS_VERBS) for m in base_ref.decl.methods()):
                    return True
    return False


def _framework(file) -> str:
    return "Spring" if file.language in JVM else file.language


def _exception_statuses(ctx: Context) -> dict[str, dict]:
    """Exception -> {status, handler, message} from @ResponseStatus classes and @ControllerAdvice @ExceptionHandler methods."""
    out: dict[str, dict] = {}
    for ref in _iter_types(ctx):
        decl = ref.decl
        rs = decl.ann("ResponseStatus")
        if rs is not None and any(b.endswith(("Exception", "Error", "Throwable")) for b in decl.base_names()):
            code = rs.idents("value", "code", "")
            out[decl.name] = {"status": HTTP_STATUS.get(code[-1], code[-1]) if code else "", "source": f"@ResponseStatus on {decl.name}", "reason": rs.string("reason")}
        if decl.has("ControllerAdvice", "RestControllerAdvice") or (ref.file.language in JVM and any(m.has("ExceptionHandler") for m in decl.methods())):
            for method in decl.methods():
                handler = method.ann("ExceptionHandler")
                if handler is None:
                    continue
                names = handler.idents("value", "") or [simple_type(p.type) for p in method.params if simple_type(p.type).endswith(("Exception", "Error", "Throwable"))]
                status_ann = method.ann("ResponseStatus")
                codes = status_codes(method.body)
                status = HTTP_STATUS.get(status_ann.idents("value", "code", "")[-1], "") if status_ann and status_ann.idents("value", "code", "") else (codes[0] if codes else "")
                for name in names:
                    out.setdefault(name, {})
                    out[name].update({"status": status or out[name].get("status", ""), "handler": f"{decl.name}.{method.name}", "path": ref.file.path, "line": method.line})
    return out


def extract(ctx: Context, cat: Catalog) -> None:
    exceptions = _exception_statuses(ctx)
    cat.extra["exception_statuses"] = exceptions
    entity_refs: list[TypeRef] = []
    implemented_interfaces: set[str] = set()
    for ref in _iter_types(ctx):
        decl = ref.decl
        if decl.has("Entity", "Table") and decl.kind in ("class", "record") or (decl.has("Entity") and ref.file.language == "Kotlin"):
            entity_refs.append(ref)
        if decl.kind == "class":
            implemented_interfaces.update(decl.base_names())
    for ref in entity_refs:
        _entity(ctx, cat, ref)
    for ref in _iter_types(ctx):
        decl, file = ref.decl, ref.file
        try:
            if _is_controller(decl, ctx, file):
                _controller(ctx, cat, ref, exceptions)
            elif decl.has("FeignClient"):
                _client(ctx, cat, ref, "feign")
            elif decl.kind == "interface" and decl.name not in implemented_interfaces and (decl.has("HttpExchange") or any(m.has("GetExchange", "PostExchange", "PutExchange", "DeleteExchange", "PatchExchange", "HttpExchange") for m in decl.methods())):
                _client(ctx, cat, ref, "feign")
            elif decl.kind == "interface" and decl.name not in implemented_interfaces and any(m.has("GET", "POST", "PUT", "DELETE", "PATCH", "HTTP", "HEAD") for m in decl.methods()) and not decl.has("Path"):
                _client(ctx, cat, ref, "retrofit")
            elif decl.kind == "interface" and (set(decl.base_names()) & REPOSITORY_BASES or decl.has("RepositoryDefinition")):
                _spring_data_repository(ctx, cat, ref)
            elif decl.has("Dao") and decl.kind in ("interface", "class"):
                _annotated_dao(ctx, cat, ref)
            elif "org.mapstruct" in file.text[:3000] and decl.has("Mapper"):
                _mapper(cat, ref)
            elif decl.kind == "interface" and re.search(r"(Repository|Dao|DAO|DataSource)$", decl.name):
                _interface_repository(ctx, cat, ref)
            elif decl.has("Dao") or (decl.has("Mapper") and decl.kind == "interface"):
                _annotated_dao(ctx, cat, ref)
            elif decl.has("Repository") or (decl.name.endswith(("Dao", "DAO", "RepositoryImpl", "DaoImpl", "Repository", "DataSource")) and decl.kind == "class"):
                _class_repository(ctx, cat, ref)
            if decl.has("Configuration", "EnableWebSecurity", "EnableWebFluxSecurity") or "WebSecurityConfigurerAdapter" in decl.base_names():
                _security(ctx, cat, ref)
                _beans(ctx, cat, ref)
                _functional_routes(ctx, cat, ref)
            if decl.has("ConfigurationProperties"):
                _config_properties(ctx, cat, ref)
            if decl.kind == "enum":
                _enum(cat, ref)
            elif any(b.endswith(("Exception", "Error", "Throwable")) for b in decl.base_names()) and decl.kind in ("class", "object"):
                _exception(cat, ref, exceptions)
            if _is_constants(decl):
                _constants(ctx, cat, ref)
        except (IndexError, ValueError, KeyError, RecursionError):
            continue
    # services, jobs, listeners and utilities after repositories so links resolve
    for ref in _iter_types(ctx):
        try:
            _service_like(ctx, cat, ref)
        except (IndexError, ValueError, KeyError, RecursionError):
            continue


def _functional_routes(ctx: Context, cat: Catalog, ref: TypeRef) -> None:
    """Extracts Spring WebFlux RouterFunction / Kotlin router DSL endpoints."""
    decl, file = ref.decl, ref.file
    if not re.search(r"RouterFunction|RouterFunctions|coRouter|router\s*\{", decl.body):
        return
    for method in decl.methods():
        if not re.search(r"RouterFunction|coRouter|router\s*\{", (method.type or "") + " " + method.body):
            continue
        base_line = method.body_line or method.line
        for m in re.finditer(r"\b(GET|POST|PUT|DELETE|PATCH)\s*\(\s*\"([^\"]+)\"", method.body):
            verb, route_path = m.group(1), m.group(2)
            line = _line_in(method.body, m.start(), base_line)
            name = f"{verb} {route_path}"
            cat.add(Item(
                "api", "endpoint", f"api:{name}", name, file.path, line, line, file.language, "Spring WebFlux",
                f"{decl.name}.{method.name} -> {verb} {route_path}",
                {"method": verb, "path": route_path, "style": "REST (WebFlux RouterFunction)",
                 "handler": {"class": decl.name, "method": method.name, "path": file.path, "line": line},
                 "params": [{"in": "path", "name": p, "type": "String", "required": True} for p in re.findall(r"\{(\w+)\}", route_path)],
                 "status_codes": [200]},
                code=snippet(file.text, max(1, line - 2), line + 10, 15),
            ))


# --- API ------------------------------------------------------------------------------------------------------------


def _mapping(ann: Annotation, ctx: Context) -> tuple[list[str], list[str]]:
    raw = ann.raw("value", "path", "uri", "")
    paths: list[str] = []
    if raw:
        inner = raw.strip()
        if inner.startswith(("{", "[")):
            inner = inner[1:-1]
        paths = [ctx.placeholder(ctx.resolve(part)) for part in split_top(inner)] or [""]
    else:
        paths = [""]
    methods = [MAPPINGS[ann.name]] if MAPPINGS.get(ann.name) else ([JAXRS_VERBS[ann.name]] if JAXRS_VERBS.get(ann.name) else [])
    if not methods:
        methods = [m for m in ann.idents("method") if m in ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS")] or ["ANY"]
    return paths, methods


def _security_of(*annotation_lists: list[Annotation]) -> dict:
    out: dict = {}
    for group in annotation_lists:
        for ann in group:
            if ann.name in ("PreAuthorize", "PostAuthorize"):
                expr = ann.string("value", "")
                out.setdefault("expressions", []).append(expr)
                out.setdefault("roles", []).extend(re.findall(r"has(?:Any)?(?:Role|Authority)\(([^)]*)\)", expr))
            elif ann.name in ("Secured", "RolesAllowed"):
                out.setdefault("roles", []).extend(ann.strings("value", ""))
            elif ann.name in ("PermitAll", "AnonymousAllowed"):
                out["public"] = True
            elif ann.name == "DenyAll":
                out["deny"] = True
    if "roles" in out:
        out["roles"] = sorted({r.strip().strip("'\"") for chunk in out["roles"] for r in chunk.split(",") if r.strip().strip("'\"")})
    return out


def _param_entry(ctx: Context, param: Param, file, rest_style: bool) -> dict | None:
    t = simple_type(param.type)
    kind = {"PathVariable": "path", "PathParam": "path", "RequestParam": "query", "QueryParam": "query", "QueryValue": "query",
            "RequestHeader": "header", "HeaderParam": "header", "Header": "header", "CookieValue": "cookie", "CookieParam": "cookie",
            "RequestBody": "body", "Body": "body", "ModelAttribute": "form", "FormParam": "form", "BeanParam": "form",
            "RequestPart": "part", "MatrixVariable": "matrix", "SessionAttribute": "session", "RequestAttribute": "attribute"}
    found = next((a for a in param.annotations if a.name in kind), None)
    if param.ann("AuthenticationPrincipal", "CurrentUser") or (found is None and t in FRAMEWORK_PARAMS):
        if t in ("Pageable",):
            return {"in": "query", "name": "page, size, sort", "type": "Pageable", "required": False, "validations": [], "note": "Spring Data paging"}
        return {"in": "context", "name": param.name, "type": type_label(param.type), "required": False, "validations": []}
    if found is None:
        primitive = t in ("String", "int", "Integer", "long", "Long", "boolean", "Boolean", "double", "Double", "LocalDate", "UUID", "BigDecimal")
        where = "query" if primitive else ("form" if not rest_style else "body" if param.ann("Valid", "Validated") else "form")
        found_name = param.name
        required = False
    else:
        where = kind[found.name]
        found_name = found.string("value", "name", "") or param.name
        required = found.raw("required") != "false" and not found.raw("defaultValue") and not param.type.strip().endswith("?") and simple_type(param.type) != "Optional"
    entry = {"in": where, "name": found_name, "type": type_label(param.type), "required": required, "validations": validations_of(param.annotations)}
    if found is not None and found.raw("defaultValue"):
        entry["default"] = found.string("defaultValue")
    if param.default:
        entry["default"] = param.default
    return entry


def _view_returns(body: str) -> tuple[list[str], list[str]]:
    views, redirects = [], []
    for m in re.finditer(r"return\s+(?:new\s+ModelAndView\s*\(\s*)?\"([^\"]+)\"|setViewName\s*\(\s*\"([^\"]+)\"|(?:VIEW|view)\w*\s*=\s*\"([^\"]+)\"", body):
        value = m.group(1) or m.group(2) or m.group(3)
        if value.startswith("redirect:"):
            redirects.append(value[len("redirect:") :])
        elif value.startswith("forward:"):
            redirects.append(value[len("forward:") :])
        elif re.match(r"^[\w/.-]+$", value):
            views.append(value)
    for m in re.finditer(r"return\s+((?:[A-Z]\w*\.)?[A-Z_][A-Z0-9_]+)\s*;", body):
        views.append("{" + m.group(1) + "}")
    return sorted(set(views)), sorted(set(redirects))


def _controller(ctx: Context, cat: Catalog, ref: TypeRef, exceptions: dict) -> None:
    decl, file = ref.decl, ref.file
    class_mapping = decl.ann("RequestMapping", "HttpExchange", "Path") or (decl.ann("Controller") if decl.ann("Controller") and decl.ann("Controller").raw("value", "path", "uri", "") else None)
    base_interfaces = [ctx.type(b, file) for b in decl.base_names()] if decl.bases else []
    base_interfaces = [b for b in base_interfaces if b and b.decl.kind == "interface"]
    if class_mapping is None:
        for bref in base_interfaces:
            class_mapping = bref.decl.ann("RequestMapping", "HttpExchange", "Path")
            if class_mapping is not None:
                break
    base_paths = _mapping(class_mapping, ctx)[0] if class_mapping else [""]
    is_jaxrs = decl.has("Path") or any(m.has(*JAXRS_VERBS, "Path") for m in decl.methods())
    rest = decl.has("RestController") or decl.has("ResponseBody") or is_jaxrs or any(b.decl.has("RestController", "HttpExchange", "Path") for b in base_interfaces)
    ops = cat.extra.get("openapi_ops", {})
    context_path = (ctx.config_values.get("server.servlet.context-path") or ctx.config_values.get("server.context-path") or "").rstrip("/")
    var_types = field_types(ref, ctx)
    known = set(ctx.types)
    constants_view = {k: v for k, v in ctx.constants.items()}
    for method in decl.methods():
        mapping = next((a for a in method.annotations if a.name in MAPPINGS or a.name in JAXRS_VERBS), None)
        path_ann = method.ann("Path")
        iface_method = None
        if mapping is None and base_interfaces:
            for bref in base_interfaces:
                iface_method = next((im for im in bref.decl.methods() if im.name == method.name and len(im.params) == len(method.params)), None)
                if iface_method:
                    mapping = next((a for a in iface_method.annotations if a.name in MAPPINGS or a.name in JAXRS_VERBS), None)
                    path_ann = path_ann or iface_method.ann("Path")
                    if mapping is not None or path_ann is not None:
                        break
        spec = None
        if mapping is None and path_ann is not None:
            paths = _mapping(path_ann, ctx)[0]
            verbs = ["GET"]
            mapping = path_ann
        elif mapping is None:
            spec = ops.get(method.name) if decl.bases and method.public else None
            if spec is None:
                continue
            mapping = Annotation(MAPPING_FOR.get(spec["method"], "RequestMapping"), "")
            paths, verbs = [spec["path"]], [spec["method"]]
        else:
            paths, verbs = _mapping(mapping, ctx)
            if path_ann is not None and paths == [""]:
                paths = _mapping(path_ann, ctx)[0]
        effective_params = method.params
        if iface_method is not None:
            merged_params = []
            for p, ip in zip(method.params, iface_method.params):
                merged_params.append(Param(p.name, p.type, p.annotations or ip.annotations, p.default))
            effective_params = merged_params
        method_rest = rest or method.has("ResponseBody")
        params = [p for p in (_param_entry(ctx, param, file, method_rest) for param in effective_params) if p]
        body_param = next((param for param in effective_params if param.ann("RequestBody", "Body")), None)
        form_param = next((param for param in effective_params if (param.ann("ModelAttribute", "BeanParam") or (all(a.name in VALIDATIONS for a in param.annotations) and simple_type(param.type) not in FRAMEWORK_PARAMS and ctx.type(simple_type(param.type), file))) and not param.ann("RequestBody", "Body")), None)
        request = None
        if body_param is not None:
            request = dto_fields(ctx, body_param.type, file)
            request["validated"] = bool(body_param.ann("Valid", "Validated"))
            request["content_type"] = ", ".join(mapping.strings("consumes")) or "application/json"
        elif form_param is not None and not method_rest:
            request = dto_fields(ctx, form_param.type, file)
            request["validated"] = bool(form_param.ann("Valid", "Validated"))
            request["content_type"] = "application/x-www-form-urlencoded (form binding)"
        views, redirects = _view_returns(method.body)
        views = [constants_view.get(v.strip("{}"), constants_view.get(v.strip("{}").split(".")[-1], v)) for v in views]
        redirects += [v[len("redirect:"):] for v in views if v.startswith("redirect:")]
        views = [v for v in views if not v.startswith("redirect:")]
        response: dict = {}
        return_type = method.type or ("Unit" if file.language == "Kotlin" else "void")
        if not method_rest and simple_type(return_type) in ("String", "ModelAndView", "View"):
            response = {"kind": "view", "type": type_label(return_type), "views": views, "redirects": redirects}
        else:
            response = dto_fields(ctx, return_type, file)
            response["kind"] = "body" if simple_type(return_type) not in ("void", "Unit") else "empty"
            response["content_type"] = ", ".join(mapping.strings("produces")) or ("application/json" if response["kind"] == "body" else "")
        codes = status_codes(method.body)
        rs = method.ann("ResponseStatus")
        if rs is not None and rs.idents("value", "code", ""):
            codes = sorted(set(codes) | {HTTP_STATUS.get(rs.idents("value", "code", "")[-1], 200)})
        throws = throws_in(method.body, method.body_line or method.line)
        for item in throws:
            mapped = exceptions.get(item["exception"])
            if mapped and not item["status"]:
                item["status"] = str(mapped.get("status", ""))
                item["handled_by"] = mapped.get("handler", mapped.get("source", ""))
        if not codes:
            codes = [201 if verbs == ["POST"] and method_rest else 200]
        if redirects:
            codes = sorted(set(codes) | {302})
        codes = sorted(set(codes) | {int(t["status"]) for t in throws if str(t["status"]).isdigit()})
        if request and request.get("validated"):
            codes = sorted(set(codes) | ({400} if method_rest else set()))
        model_attrs = sorted(set(re.findall(r"(?:model|mav|modelAndView|map)\s*\.\s*(?:addAttribute|addObject|put)\s*\(\s*\"(\w+)\"", method.body)))
        security = _security_of(decl.annotations, method.annotations)
        if security.get("roles"):
            security["roles"] = sorted({ctx.constants.get(r.lstrip("@").split(".")[-1], r) for r in security["roles"]})
        if spec is not None:
            openapi = spec["details"]
            params = [dict(p, validations=p.get("validations", [])) for p in openapi.get("params", [])] + [p for p in params if p["in"] == "context"]
            if openapi.get("request") and (request is None or not request.get("fields")):
                request = dict(openapi["request"], validated=True, source="OpenAPI")
            spec_response = openapi.get("responses", {})
            ok = spec_response.get("200") or spec_response.get("201") or {}
            if ok and not response.get("fields"):
                response = dict(response, **{k: v for k, v in ok.items() if k != "description"}, source="OpenAPI")
            codes = sorted(set(codes) | {int(c) for c in spec_response if str(c).isdigit()})
        calls = calls_in(method.body, var_types, method.body_line or method.line, known)
        rules = rules_in(method.body, method.body_line or method.line, file.language)
        for rule in rules:
            ident = re.match(r"^returns early$", rule["outcome"])
            rm = re.search(r"return\s+([A-Z_][A-Z0-9_]+)\s*;", method.body[max(0, method.body.find(rule["condition"])) :][:400]) if ident else None
            if rm and rm.group(1) in ctx.constants:
                value = ctx.constants[rm.group(1)]
                rule["outcome"] = ("redirects to " + value[9:]) if value.startswith("redirect:") else f"renders view {value}"
        events = events_in(method.body, ctx, method.body_line or method.line)
        outbound = http_calls(method.body, ctx, method.body_line or method.line)
        for base in base_paths:
            for path in paths:
                full = join_paths(base, path)
                for verb in verbs:
                    name = f"{verb} {full}"
                    item = cat.add(Item(
                        "api", "endpoint", f"api:{name}", name, file.path, method.line, method.end_line, file.language, "Spring MVC" if not method_rest else "Spring Web (REST)",
                        f"{decl.name}.{method.name}({', '.join(type_label(p.type) + ' ' + p.name for p in method.params)})" + (f": {type_label(return_type)}" if return_type else ""),
                        {
                            "method": verb, "path": full, "style": "REST" if method_rest else "MVC (server-rendered)",
                            "handler": {"class": decl.name, "method": method.name, "path": file.path, "line": method.line},
                            "params": params, "request": request, "response": response, "status_codes": codes,
                            "consumes": mapping.strings("consumes"), "produces": mapping.strings("produces"),
                            "security": security, "errors": throws, "rules": rules, "calls": calls, "events": events, "http_calls": outbound,
                            "model_attributes": model_attrs, "annotations": [a.text()[:200] for a in method.annotations],
                            "context_path": context_path, "url": f"{context_path}{full}" if context_path else full,
                            **({"openapi": spec["details"], "contract_first": True} if spec else {}),
                        },
                        summary=(spec["details"].get("summary", "") if spec else ""), summary_source=("openapi" if spec and spec["details"].get("summary") else ""),
                        code=snippet(file.text, method.line, method.end_line, 80),
                    ))
                    cat.pending_calls += [(item.key, c["type"], c["method"], c["line"]) for c in calls]
                    for view in views:
                        cat.pending_views.append((item.key, view))
                    for redirect in redirects:
                        cat.pending_redirects.append((item.key, redirect))
                    if request and request.get("name"):
                        cat.pending_types.append((item.key, request["name"], "request"))
                    if response.get("name"):
                        cat.pending_types.append((item.key, response["name"], "response"))
                    for t in throws:
                        cat.pending_types.append((item.key, t["exception"], "throws"))
                    for event in events:
                        cat.pending_topics.append((item.key, event, "publishes"))


def _retrofit_bases(ctx: Context) -> dict[str, str]:
    """Retrofit.Builder().baseUrl(X) ... .create(Api::class.java): interface name -> resolved base URL ("*" when every builder agrees)."""
    cached = getattr(ctx, "_retrofit_bases", None)
    if cached is not None:
        return cached
    from app.services.analyzer.catalog.structure import match_close, split_top
    from app.services.analyzer.catalog.uris import resolver_for

    resolver = resolver_for(ctx)
    out: dict[str, str] = {}
    seen: set[str] = set()
    for file in ctx.code_files("Kotlin", "Java"):
        if "baseUrl" not in file.text or "Retrofit" not in file.text:
            continue
        for m in re.finditer(r"\.\s*baseUrl\s*\(", file.text):
            args = split_top(file.text[m.end() : match_close(file.text, m.end() - 1)])
            if not args:
                continue
            base = resolver.resolve(args[0], file).text.strip()
            if not base or base.startswith("{"):
                continue
            seen.add(base)
            tail = file.text[m.end() : m.end() + 1500]
            for c in re.finditer(r"\.\s*create\s*\(\s*(\w+)(?:::class\.java|\.class)\s*\)", tail):
                out.setdefault(c.group(1), base)
    if len(seen) == 1:
        out["*"] = next(iter(seen))
    ctx._retrofit_bases = out  # type: ignore[attr-defined]
    return out


def _client(ctx: Context, cat: Catalog, ref: TypeRef, style: str) -> None:
    """Feign (server-side outbound integration) or Retrofit (mobile / client API) interface."""
    decl, file = ref.decl, ref.file
    feign = decl.ann("FeignClient")
    base = ""
    target_name = ""
    if feign is not None:
        base = ctx.placeholder(ctx.resolve(feign.raw("url") or "")) if feign.raw("url") else ""
        base = join_paths(base, ctx.placeholder(feign.string("path"))) if feign.string("path") else base
        target_name = ctx.placeholder(feign.string("name", "value", ""))
    class_mapping = decl.ann("RequestMapping")
    if class_mapping is not None:
        base = join_paths(base, _mapping(class_mapping, ctx)[0][0])
    methods = []
    retrofit = style == "retrofit"
    android = ctx.android and retrofit
    for method in decl.methods():
        verb, path = "", ""
        if retrofit:
            ann = method.ann("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "HTTP")
            if ann is None:
                continue
            verb = ann.name if ann.name != "HTTP" else (ann.string("method") or "ANY")
            raw_path = ann.raw("value", "path", "") or '""'
            path = ctx.resolve(raw_path)
            if "{" in path or "$" in raw_path or "(" in raw_path or "." in raw_path:
                from app.services.analyzer.catalog.uris import resolver_for

                better = resolver_for(ctx).resolve(raw_path, file, decl.name).text
                if better and better.count("{") <= path.count("{"):
                    path = better
        else:
            ann = next((a for a in method.annotations if a.name in MAPPINGS), None)
            if ann is None:
                continue
            paths, verbs = _mapping(ann, ctx)
            verb, path = verbs[0], paths[0]
        params = []
        for param in method.params:
            pa = next((a for a in param.annotations if a.name in ("Path", "Query", "QueryMap", "Body", "Header", "HeaderMap", "Field", "FieldMap", "Part", "Url",
                                                                   "PathVariable", "RequestParam", "RequestBody", "RequestHeader")), None)
            where = {"Path": "path", "PathVariable": "path", "Query": "query", "QueryMap": "query", "RequestParam": "query", "Body": "body", "RequestBody": "body",
                     "Header": "header", "HeaderMap": "header", "RequestHeader": "header", "Field": "form", "FieldMap": "form", "Part": "part", "Url": "url"}.get(pa.name if pa else "", "context")
            if where == "context" and simple_type(param.type) == "Continuation":
                continue
            params.append({"in": where, "name": (pa.string("value", "") if pa else "") or param.name, "type": type_label(param.type), "required": not param.type.strip().endswith("?") and where != "context"})
        full = path if re.match(r"^https?://", path) else join_paths(base, path) if (base or path) else "/"
        body_param = next((p for p in method.params if p.ann("Body", "RequestBody")), None)
        entry = {"name": method.name, "method": verb, "path": full, "params": params, "returns": type_label(method.type), "line": method.line,
                 "request": dto_fields(ctx, body_param.type, file) if body_param else None, "response": dto_fields(ctx, method.type, file) if method.type else None}
        methods.append(entry)
        key = f"client:{verb} {full}" if retrofit else f"out:{target_name or decl.name}:{verb} {full}"
        layer = "ui" if android else "shared"
        item = cat.add(Item(layer, "api_client" if retrofit else "integration", key, f"{verb} {full}", file.path, method.line, method.end_line, file.language,
                            "Retrofit" if retrofit else "OpenFeign", f"{decl.name}.{method.name}(...): {type_label(method.type)}",
                            {"client": decl.name, "method": verb, "path": full, "operation": method.name, "params": params, "request": entry["request"],
                             "response": entry["response"], "target": target_name,
                             "base_url": base or (_retrofit_bases(ctx).get(decl.name) or _retrofit_bases(ctx).get("*", "") if retrofit else "")},
                            code=snippet(file.text, method.line - 1, method.end_line, 20)))
        cat.type_members.setdefault(decl.name, {})[method.name] = item.key


def _service_type_kind(ref: TypeRef, ctx: Context) -> str | None:
    decl = ref.decl
    names = set(decl.base_names())
    if decl.kind not in ("class", "object", "interface"):
        return None
    if decl.name.endswith(("Repository", "RepositoryImpl", "Dao", "DAO", "DaoImpl", "DataSource")) and decl.kind in ("class", "interface"):
        return None
    if _is_controller(decl) or decl.has("Entity", "Repository", "Configuration", "ControllerAdvice", "RestControllerAdvice", "Mapper", "Dao", "FeignClient"):
        return None
    if names & {"ViewModel", "AndroidViewModel"}:
        return "view_model"
    if names & {"Worker", "CoroutineWorker", "ListenableWorker", "QuartzJobBean", "Job", "Tasklet", "ItemProcessor", "ItemReader", "ItemWriter", "RxWorker"}:
        return "job"
    if decl.has(*SERVICE_MARKERS) or decl.name.endswith(("Service", "ServiceImpl", "UseCase", "Interactor", "Facade", "Manager", "Orchestrator", "Handler", "Processor", "Engine")):
        if decl.kind == "interface" and not decl.name.endswith(("Service", "UseCase")):
            return None
        if ref.file.layer in ("ui",) and not decl.has(*SERVICE_MARKERS):
            return None
        return "service"
    if any(m.has("Scheduled", "KafkaListener", "RabbitListener", "JmsListener", "EventListener", "TransactionalEventListener", "SqsListener", "StreamListener") for m in decl.methods()):
        return "service"
    return None


def _service_like(ctx: Context, cat: Catalog, ref: TypeRef) -> None:
    kind = _service_type_kind(ref, ctx)
    decl, file = ref.decl, ref.file
    if kind is None:
        if _is_utility(decl):
            _utility(ctx, cat, ref)
        return
    var_types = field_types(ref, ctx)
    known = set(ctx.types)
    deps = sorted({t for name, t in var_types.items() if t in ctx.types and t != decl.name})
    methods = []
    jobs_found = []
    listeners = []
    all_calls: list[dict] = []
    class_tx = decl.ann("Transactional")
    own_methods = {m.name for m in decl.methods() if m.kind != "constructor"}
    method_calls: dict[str, list[str]] = {}
    for method in decl.methods():
        if method.kind == "constructor":
            continue
        found = calls_in(method.body, var_types, method.body_line or method.line, known)
        own = [f"{decl.name}.{name}" for name in dict.fromkeys(re.findall(r"(?<![\w.])(?:this\.)?([a-z]\w*)\s*\(", method.body)) if name in own_methods and name != method.name]
        method_calls[method.name] = sorted(set(method_calls.get(method.name, []) + [f"{c['type']}.{c['method']}" for c in found] + own))
        if not method.public:
            all_calls += found
        if not method.public and not method.has("Scheduled", "KafkaListener", "RabbitListener", "JmsListener", "EventListener", "TransactionalEventListener", "Transactional"):
            if kind != "job" or method.name not in ("doWork", "execute", "executeInternal", "process", "read", "write", "createWork"):
                continue
        body_line = method.body_line or method.line
        calls = calls_in(method.body, var_types, body_line, known)
        all_calls += calls
        tx = method.ann("Transactional") or class_tx
        entry = {
            "name": method.name, "signature": f"{method.name}({', '.join(type_label(p.type) + ' ' + p.name for p in method.params)})" + (f": {type_label(method.type)}" if method.type else ""),
            "returns": type_label(method.type), "params": [{"name": p.name, "type": type_label(p.type), "validations": validations_of(p.annotations)} for p in method.params],
            "line": method.line, "end_line": method.end_line,
            "transactional": ({"read_only": tx.raw("readOnly") == "true", "propagation": (tx.idents("propagation") or [""])[-1], "rollback_for": tx.idents("rollbackFor")} if tx else None),
            "annotations": [a.text()[:160] for a in method.annotations if a.name not in ("Override",)],
            "calls": [f"{c['type']}.{c['method']}" for c in calls], "rules": rules_in(method.body, body_line, file.language),
            "throws": throws_in(method.body, body_line), "events": events_in(method.body, ctx, body_line), "http_calls": http_calls(method.body, ctx, body_line),
            "sql": sql_in(method.body, body_line),
        }
        cache = method.ann("Cacheable", "CacheEvict", "CachePut")
        if cache is not None:
            entry["cache"] = {"kind": cache.name, "names": cache.strings("value", "cacheNames", "")}
        if method.has("Async"):
            entry["async"] = True
        security = _security_of(method.annotations)
        if security:
            entry["security"] = security
        methods.append(entry)
        scheduled = method.ann("Scheduled", "Schedules")
        if scheduled is not None:
            jobs_found.append((method, scheduled, calls))
        listener = method.ann("KafkaListener", "RabbitListener", "JmsListener", "EventListener", "TransactionalEventListener", "SqsListener", "StreamListener", "KafkaHandler")
        if listener is not None:
            listeners.append((method, listener, calls))
    layer = "ui" if kind == "view_model" else "service"
    key = f"vm:{decl.name}" if kind == "view_model" else f"svc:{decl.name}"
    item_kind = "view_model" if kind == "view_model" else "job" if kind == "job" else "service"
    if kind == "job":
        key = f"job:{decl.name}"
    implements = [b for b in decl.base_names() if b in ctx.types and b not in ("Object",)]
    details = {
        "class": decl.name, "type": decl.kind, "stereotype": next((a.name for a in decl.annotations if a.name in SERVICE_MARKERS | {"HiltViewModel"}), ""),
        "dependencies": deps, "implements": implements, "methods": methods, "transactional": bool(class_tx),
        "annotations": [a.text()[:160] for a in decl.annotations], "security": _security_of(decl.annotations),
        "events": [e for m in methods for e in m["events"]], "http_calls": [h for m in methods for h in m["http_calls"]],
        "state": [{"name": m.name, "type": type_label(m.type) or m.initializer[:60]} for m in decl.fields() if re.search(r"(StateFlow|LiveData|State<|Flow<|mutableStateOf|MutableStateFlow|MutableLiveData)", (m.type or "") + m.initializer)][:20],
        "method_calls": method_calls,
    }
    if kind == "job":
        details["schedule"] = _worker_schedule(ctx, decl.name)
        details["job_type"] = next((b for b in decl.base_names() if b in ("Worker", "CoroutineWorker", "QuartzJobBean", "Job", "Tasklet", "ItemProcessor", "ItemReader", "ItemWriter", "RxWorker")), "")
    item = cat.add(Item(layer, item_kind, key, decl.name, file.path, decl.line, decl.end_line, file.language,
                        "Android" if kind == "view_model" or ctx.android and file.language == "Kotlin" else "Spring" if ctx.spring else file.language,
                        decl.header.split("{")[0].strip()[:300], details, code=_class_excerpt(file.text, decl)))
    cat.type_keys.setdefault(decl.name, item.key)
    for method in methods:
        cat.type_members.setdefault(decl.name, {})[method["name"]] = item.key
    cat.pending_calls += [(item.key, c["type"], c["method"], c["line"]) for c in all_calls]
    for event in details["events"]:
        cat.pending_topics.append((item.key, event, "publishes"))
    for base in implements:
        cat.pending_implements.append((item.key, base))
    for method, scheduled, calls in jobs_found:
        schedule = {k: ctx.placeholder(ctx.resolve(scheduled.raw(k) or "")) for k in ("cron", "fixedRate", "fixedDelay", "initialDelay", "zone", "fixedRateString", "fixedDelayString") if scheduled.raw(k)}
        job = cat.add(Item("service", "job", f"job:{decl.name}.{method.name}", f"{decl.name}.{method.name}", file.path, method.line, method.end_line, file.language, "Spring Scheduler",
                           f"@Scheduled {' '.join(f'{k}={v}' for k, v in schedule.items())}",
                           {"class": decl.name, "method": method.name, "trigger": "scheduled", "schedule": schedule, "human": describe_schedule(schedule),
                            "calls": [f"{c['type']}.{c['method']}" for c in calls], "rules": rules_in(method.body, method.body_line or method.line, file.language),
                            "transactional": bool(method.ann("Transactional") or class_tx), "lock": (method.ann("SchedulerLock").string("name") if method.ann("SchedulerLock") else "")},
                           code=snippet(file.text, method.line - 2, method.end_line, 60)))
        cat.link(job.key, item.key, "member of")
        cat.pending_calls += [(job.key, c["type"], c["method"], c["line"]) for c in calls]
    for method, listener, calls in listeners:
        destination = [ctx.placeholder(ctx.resolve(v)) for v in split_top((listener.raw("topics", "queues", "destination", "value", "") or "").strip("{}[]"))]
        event_type = next((simple_type(p.type) for p in method.params), "")
        kind_name = {"KafkaListener": "kafka", "RabbitListener": "rabbitmq", "JmsListener": "jms", "SqsListener": "sqs", "StreamListener": "stream"}.get(listener.name, "event")
        name = f"{decl.name}.{method.name}"
        job = cat.add(Item("service", "listener", f"listener:{name}", name, file.path, method.line, method.end_line, file.language, listener.name,
                           f"@{listener.name} {', '.join(destination) or event_type}",
                           {"class": decl.name, "method": method.name, "transport": kind_name, "destinations": destination, "group": listener.string("groupId", "group", "id"),
                            "event_type": event_type, "calls": [f"{c['type']}.{c['method']}" for c in calls], "rules": rules_in(method.body, method.body_line or method.line, file.language)},
                           code=snippet(file.text, method.line - 2, method.end_line, 60)))
        cat.link(job.key, item.key, "member of")
        cat.pending_calls += [(job.key, c["type"], c["method"], c["line"]) for c in calls]
        for destination_name in destination or [event_type]:
            if destination_name:
                cat.pending_topics.append((job.key, {"kind": kind_name, "name": destination_name}, "consumes"))
    for method_entry in methods:
        for statement in method_entry["sql"]:
            _query_from_sql(ctx, cat, item.key, decl.name, method_entry["name"], statement, file)


def _worker_schedule(ctx: Context, name: str) -> dict:
    for file in ctx.code_files(*JVM):
        if name not in file.text:
            continue
        m = re.search(rf"PeriodicWorkRequest(?:Builder)?\s*<\s*{name}\s*>\s*\(\s*([^,]+),\s*(?:TimeUnit|java\.util\.concurrent\.TimeUnit)\.(\w+)", file.text)
        if m:
            return {"every": f"{m.group(1).strip()} {m.group(2).lower()}", "where": file.path}
        if re.search(rf"OneTimeWorkRequest(?:Builder)?\s*<\s*{name}\s*>", file.text):
            return {"once": True, "where": file.path}
        m = re.search(rf"newJob\s*\(\s*{name}\.class\)[\s\S]{{0,400}}?cronSchedule\s*\(\s*\"([^\"]+)\"", file.text)
        if m:
            return {"cron": m.group(1), "where": file.path}
    return {}


def describe_schedule(schedule: dict) -> str:
    cron = schedule.get("cron", "")
    if cron:
        return describe_cron(cron)
    for key in ("fixedRate", "fixedRateString", "fixedDelay", "fixedDelayString"):
        value = schedule.get(key)
        if value:
            label = "every" if "Rate" in key else "with a delay of"
            return f"{label} {_duration(value)}" + (" after the previous run finishes" if "Delay" in key else "")
    return ""


def _duration(value: str) -> str:
    value = value.strip()
    if value.isdigit():
        ms = int(value)
        for unit, size in (("hour", 3_600_000), ("minute", 60_000), ("second", 1000)):
            if ms % size == 0 and ms >= size:
                n = ms // size
                return f"{n} {unit}{'s' if n != 1 else ''}"
        return f"{ms} ms"
    m = re.match(r"PT(\d+)([HMS])", value, re.I)
    if m:
        return f"{m.group(1)} {dict(H='hours', M='minutes', S='seconds')[m.group(2).upper()]}"
    return value


def describe_cron(cron: str) -> str:
    parts = cron.split()
    if len(parts) == 6:
        sec, minute, hour, dom, month, dow = parts
    elif len(parts) == 5:
        sec, (minute, hour, dom, month, dow) = "0", parts
    else:
        return f"cron {cron}"
    names = {"MON": "Monday", "TUE": "Tuesday", "WED": "Wednesday", "THU": "Thursday", "FRI": "Friday", "SAT": "Saturday", "SUN": "Sunday",
             "1": "Monday", "2": "Tuesday", "3": "Wednesday", "4": "Thursday", "5": "Friday", "6": "Saturday", "0": "Sunday", "7": "Sunday"}
    when = ""
    if minute.startswith("*/") or minute.startswith("0/"):
        when = f"every {minute.split('/')[1]} minutes"
    elif hour.startswith("*/") or hour.startswith("0/"):
        when = f"every {hour.split('/')[1]} hours at minute {minute}"
    elif hour == "*" and minute.isdigit():
        when = f"every hour at minute {minute}"
    elif hour.replace(",", "").isdigit() and minute.isdigit():
        when = "at " + ", ".join(f"{int(h):02d}:{int(minute):02d}" for h in hour.split(","))
    elif sec.startswith("*/") or sec.startswith("0/"):
        when = f"every {sec.split('/')[1]} seconds"
    else:
        when = f"cron {cron}"
    if dow not in ("*", "?"):
        days = "-".join(names.get(d.upper(), d) for d in dow.split("-")) if "-" in dow else ", ".join(names.get(d.upper(), d) for d in dow.split(","))
        when += f" on {days}"
    elif dom not in ("*", "?"):
        when += f" on day {dom} of the month"
    else:
        when += " every day" if when.startswith("at ") else ""
    if month not in ("*", "?"):
        when += f" in month {month}"
    return when


def _class_excerpt(text: str, decl: TypeDecl, limit: int = 140) -> str:
    return snippet(text, decl.line, decl.end_line, limit)


def _is_utility(decl: TypeDecl) -> bool:
    return decl.kind in ("class", "object") and bool(re.search(r"(Util|Utils|Helper|Helpers|Utility|Utilities|Extensions?|Formatter|Converter)$", decl.name))


def _utility(ctx: Context, cat: Catalog, ref: TypeRef) -> None:
    decl, file = ref.decl, ref.file
    methods = [{"name": m.name, "signature": f"{m.name}({', '.join(type_label(p.type) + ' ' + p.name for p in m.params)})" + (f": {type_label(m.type)}" if m.type else ""), "line": m.line}
               for m in decl.methods() if m.public and m.kind == "method"]
    if not methods:
        return
    item = cat.add(Item("shared", "utility", f"util:{decl.name}", decl.name, file.path, decl.line, decl.end_line, file.language, "", decl.header.split("{")[0][:200],
                        {"class": decl.name, "methods": methods[:60]}, code=_class_excerpt(file.text, decl, 80)))
    cat.type_keys.setdefault(decl.name, item.key)


def _is_constants(decl: TypeDecl) -> bool:
    if decl.kind not in ("class", "interface", "object"):
        return False
    if not re.search(r"(Constants?|Consts|Keys|Codes|Messages|Defaults|Limits|Paths|Urls|Endpoints|Routes)$", decl.name):
        return False
    return sum(1 for m in decl.fields() if m.initializer and ({"static", "final"} <= set(m.modifiers) or "const" in m.modifiers or decl.kind in ("interface", "object"))) >= 2


def _constants(ctx: Context, cat: Catalog, ref: TypeRef) -> None:
    decl, file = ref.decl, ref.file
    values = [{"name": m.name, "type": type_label(m.type), "value": m.initializer[:200], "line": m.line} for m in decl.fields() if m.initializer]
    cat.add(Item("shared", "constants", f"const:{decl.name}", decl.name, file.path, decl.line, decl.end_line, file.language, "", decl.header.split("{")[0][:200],
                 {"class": decl.name, "constants": values[:200]}, code=_class_excerpt(file.text, decl, 60)))


def _enum(cat: Catalog, ref: TypeRef) -> None:
    decl, file = ref.decl, ref.file
    if not decl.enum_constants:
        return
    item = cat.add(Item("shared", "enum", f"enum:{decl.name}", decl.name, file.path, decl.line, decl.end_line, file.language, "", decl.header.split("{")[0][:200],
                        {"values": decl.enum_constants[:200], "fields": [{"name": m.name, "type": type_label(m.type)} for m in decl.fields()][:20]},
                        code=_class_excerpt(file.text, decl, 60)))
    cat.type_keys.setdefault(decl.name, item.key)


def _exception(cat: Catalog, ref: TypeRef, statuses: dict) -> None:
    decl, file = ref.decl, ref.file
    mapped = statuses.get(decl.name, {})
    item = cat.add(Item("shared", "exception", f"exc:{decl.name}", decl.name, file.path, decl.line, decl.end_line, file.language, "",
                        decl.header.split("{")[0][:200],
                        {"extends": decl.base_names(), "status": mapped.get("status", ""), "handled_by": mapped.get("handler", mapped.get("source", "")),
                         "reason": mapped.get("reason", "")}, code=_class_excerpt(file.text, decl, 40)))
    cat.type_keys.setdefault(decl.name, item.key)


def _config_properties(ctx: Context, cat: Catalog, ref: TypeRef) -> None:
    decl, file = ref.decl, ref.file
    prefix = decl.ann("ConfigurationProperties").string("value", "prefix", "")
    fields = fields_of(ctx, ref)
    item = cat.add(Item("shared", "config_class", f"cfgcls:{decl.name}", decl.name, file.path, decl.line, decl.end_line, file.language, "Spring Boot",
                        f"@ConfigurationProperties(\"{prefix}\")", {"prefix": prefix, "fields": fields}, code=_class_excerpt(file.text, decl, 60)))
    for f in fields:
        cat.pending_config_usage.append((f"{prefix}.{f['name']}" if prefix else f["name"], file.path, decl.line, item.key))
        cat.pending_config_usage.append((f"{prefix}.{sqlmod.snake(f['name']).replace('_', '-')}" if prefix else f["name"], file.path, decl.line, item.key))


def _beans(ctx: Context, cat: Catalog, ref: TypeRef) -> None:
    decl, file = ref.decl, ref.file
    beans = [{"name": (m.ann("Bean").string("value", "name", "") or m.name), "type": type_label(m.type), "line": m.line,
              "profile": (m.ann("Profile").string("value", "") if m.ann("Profile") else ""), "conditional": [a.text()[:120] for a in m.annotations if a.name.startswith("Conditional")]}
             for m in decl.methods() if m.has("Bean")]
    enables = [a.name for a in decl.annotations if a.name.startswith("Enable")]
    if not beans and not enables:
        return
    cat.add(Item("shared", "bean_config", f"beans:{decl.name}", decl.name, file.path, decl.line, decl.end_line, file.language, "Spring", decl.header.split("{")[0][:200],
                 {"beans": beans, "enables": enables, "profile": decl.ann("Profile").string("value", "") if decl.ann("Profile") else "",
                  "imports": decl.ann("Import").idents("value", "") if decl.ann("Import") else []}, code=_class_excerpt(file.text, decl, 80)))


_MATCHER = re.compile(
    r"(requestMatchers|antMatchers|mvcMatchers|regexMatchers|pathMatchers|securityMatcher|requestMatcher)\s*\(([^()]*(?:\([^()]*\)[^()]*)*)\)\s*\.\s*(permitAll|authenticated|denyAll|anonymous|hasRole|hasAnyRole|hasAuthority|hasAnyAuthority|access|fullyAuthenticated|rememberMe|hasIpAddress)\s*\(([^()]*(?:\([^()]*\)[^()]*)*)\)"
)
_KT_AUTHORIZE = re.compile(r"authorize\s*\(\s*(?:HttpMethod\.(\w+)\s*,\s*)?(\"[^\"]*\"|anyRequest)\s*,\s*(permitAll|authenticated|denyAll|anonymous|hasRole\(\"[^\"]*\"\)|hasAnyRole\([^)]*\)|hasAuthority\(\"[^\"]*\"\)|hasAnyAuthority\([^)]*\)|fullyAuthenticated)")


def _security(ctx: Context, cat: Catalog, ref: TypeRef) -> None:
    decl, file = ref.decl, ref.file
    text = decl.body
    if not re.search(r"SecurityFilterChain|HttpSecurity|ServerHttpSecurity|WebSecurityConfigurerAdapter|authorizeHttpRequests|authorizeRequests|authorizeExchange", text):
        return
    rules = []
    base_line = decl.body_line
    for m in _MATCHER.finditer(text):
        args = m.group(2)
        verb = (re.search(r"HttpMethod\.(\w+)", args) or [None, ""])[1]
        patterns = [ctx.placeholder(ctx.resolve(p)) for p in split_top(re.sub(r"HttpMethod\.\w+\s*,?", "", args))]
        patterns = [p for p in patterns if p and not p.startswith("{")] or ["(matcher)"]
        access = m.group(3)
        roles = string_values(m.group(4))
        rules.append({"patterns": patterns, "method": verb, "access": access, "roles": roles, "line": _line_in(text, m.start(), base_line)})
    for m in re.finditer(r"anyRequest\s*\(\s*\)\s*\.\s*(\w+)\s*\(([^)]*)\)", text):
        rules.append({"patterns": ["/**"], "method": "", "access": m.group(1), "roles": string_values(m.group(2)), "line": _line_in(text, m.start(), base_line), "any_request": True})
    for m in re.finditer(r"anyExchange\s*\(\s*\)\s*\.\s*(\w+)\s*\(([^)]*)\)", text):
        rules.append({"patterns": ["/**"], "method": "", "access": m.group(1), "roles": string_values(m.group(2)), "line": _line_in(text, m.start(), base_line), "any_request": True})
    for m in _KT_AUTHORIZE.finditer(text):
        pattern = "/**" if m.group(2) == "anyRequest" else m.group(2).strip('"')
        access = m.group(3).split("(")[0]
        rules.append({"patterns": [pattern], "method": m.group(1) or "", "access": access, "roles": string_values(m.group(3)), "line": _line_in(text, m.start(), base_line),
                      "any_request": m.group(2) == "anyRequest"})
    mechanisms = []
    for name, pattern in (("Form login", r"formLogin"), ("HTTP Basic", r"httpBasic"), ("OAuth2 login", r"oauth2Login"), ("OAuth2 resource server (JWT)", r"oauth2ResourceServer[\s\S]{0,80}jwt"),
                          ("OAuth2 resource server", r"oauth2ResourceServer"), ("SAML2", r"saml2Login"), ("Remember-me", r"rememberMe"), ("Logout", r"\.logout\b|logout\s*\{"),
                          ("X.509", r"x509\("), ("LDAP", r"ldapAuthentication|LdapAuthenticationProvider"), ("Session: stateless", r"SessionCreationPolicy\.STATELESS"),
                          ("CSRF disabled", r"csrf\s*\(\s*(?:\w+\s*->\s*\w+\.disable|AbstractHttpConfigurer::disable)|csrf\(\)\s*\.disable|csrf\s*\{\s*disable"), ("CORS", r"\.cors\b|cors\s*\{")):
        if re.search(pattern, text):
            if name == "OAuth2 resource server" and "OAuth2 resource server (JWT)" in mechanisms:
                continue
            mechanisms.append(name)
    filters = sorted(set(re.findall(r"addFilter(?:Before|After|At)?\s*\(\s*(?:new\s+)?(\w+)", text)))
    encoder = sorted(set(re.findall(r"new\s+(\w*PasswordEncoder)\s*\(", ctx.by_path[file.path].text or "")))
    method_security = [a.text() for a in decl.annotations if a.name in ("EnableMethodSecurity", "EnableGlobalMethodSecurity", "EnableReactiveMethodSecurity")]
    item = cat.add(Item("shared", "security", f"sec:{decl.name}", decl.name, file.path, decl.line, decl.end_line, file.language, "Spring Security",
                        "HTTP security configuration",
                        {"rules": rules, "mechanisms": mechanisms, "filters": filters, "password_encoder": encoder, "method_security": method_security,
                         "user_details": sorted(set(re.findall(r"(\w+UserDetailsService\w*|InMemoryUserDetailsManager|JdbcUserDetailsManager)", ctx.by_path[file.path].text or "")))},
                        code=_class_excerpt(file.text, decl, 160)))
    cat.security_items.append(item.key)


# --- data -----------------------------------------------------------------------------------------------------------


def _entity(ctx: Context, cat: Catalog, ref: TypeRef) -> None:
    decl, file = ref.decl, ref.file
    room = decl.ann("Entity") is not None and (decl.ann("Entity").raw("tableName") is not None or "androidx.room" in (file.text or "")[:3000])
    table_ann = decl.ann("Table")
    if room:
        table_name = decl.ann("Entity").string("tableName") or decl.name
    else:
        table_name = (table_ann.string("name", "value", "") if table_ann else "") or (decl.ann("Entity").string("name", "") if decl.ann("Entity") else "") or (sqlmod.snake(decl.name) if ctx.spring else decl.name)
    schema = table_ann.string("schema") if table_ann else ""
    columns: list[dict] = []
    relations: list[dict] = []
    chain = [ref]
    seen = {decl.name}
    current = ref
    for _ in range(4):
        parent = None
        for base, _args in current.decl.bases:
            candidate = ctx.type(base, current.file)
            if candidate and candidate.decl.name not in seen and candidate.decl.kind in ("class",) and (candidate.decl.has("MappedSuperclass", "Entity") or ctx.spring):
                parent = candidate
                break
        if parent is None:
            break
        seen.add(parent.decl.name)
        chain.append(parent)
        current = parent
    for part in reversed(chain):
        for name, type_text, member_annotations, member_line in _persistent_members(part):
            if any(a.name in ("Transient", "Ignore") for a in member_annotations):
                continue
            ann = {a.name: a for a in member_annotations}
            relation = next((n for n in ("ManyToOne", "OneToOne", "OneToMany", "ManyToMany", "ElementCollection", "Embedded", "EmbeddedId") if n in ann), "")
            column_ann = ann.get("Column") or ann.get("ColumnInfo") or ann.get("JoinColumn")
            column_name = ""
            if column_ann is not None:
                column_name = column_ann.string("name", "")
            target = simple_type(unwrap_type(type_text, COLLECTIONS | WRAPPERS)[0])
            if relation in ("OneToMany", "ManyToMany", "ElementCollection") or (relation == "OneToOne" and ann["OneToOne"].string("mappedBy")):
                join_table = ann.get("JoinTable") or ann.get("CollectionTable")
                relations.append({"field": name, "kind": relation, "target": target, "mapped_by": ann[relation].string("mappedBy") if relation in ann else "",
                                  "join_table": join_table.string("name") if join_table else "", "cascade": ann[relation].idents("cascade") if relation in ann else [],
                                  "fetch": (ann[relation].idents("fetch") or [""])[-1] if relation in ann else "", "line": member_line})
                if join_table is not None and join_table.string("name"):
                    jt = join_table.string("name")
                    join_cols = [a for a in string_values(join_table.raw("joinColumns") or "")] or [f"{sqlmod.snake(decl.name)}_id"]
                    inverse = [a for a in string_values(join_table.raw("inverseJoinColumns") or "")] or [f"{sqlmod.snake(target)}_id"]
                    cat.tables_registry.setdefault(sqlmod.table_key(jt), {"name": jt, "columns": [], "sources": [], "foreign_keys": []})
                    entry = cat.tables_registry[sqlmod.table_key(jt)]
                    entry["sources"].append(f"@JoinTable in {decl.name}")
                    for col, ref_entity in ((join_cols[0], decl.name), (inverse[0], target)):
                        if col.lower() not in {c["name"].lower() for c in entry["columns"]}:
                            entry["columns"].append({"name": col, "type": "", "nullable": False, "pk": True, "source": "jpa"})
                        entry["foreign_keys"].append({"columns": [col], "ref_entity": ref_entity})
                    entry.setdefault("path", file.path)
                    entry.setdefault("line", member_line)
                continue
            if relation in ("ManyToOne", "OneToOne"):
                column_name = column_name or (f"{sqlmod.snake(name)}_id" if ctx.spring else f"{name}_id")
                relations.append({"field": name, "kind": relation, "target": target, "join_column": column_name, "line": member_line,
                                  "fetch": (ann[relation].idents("fetch") or [""])[-1], "optional": ann[relation].raw("optional") != "false"})
            elif relation in ("Embedded", "EmbeddedId"):
                embedded = ctx.type(target, file)
                if embedded:
                    for e_name, e_type, e_anns, _ in _persistent_members(embedded):
                        e_col = next((a.string("name", "") for a in e_anns if a.name == "Column"), "") or sqlmod.snake(e_name)
                        columns.append({"name": e_col, "field": f"{name}.{e_name}", "java_type": type_label(e_type), "pk": relation == "EmbeddedId", "nullable": True, "source": "jpa"})
                continue
            column_name = column_name or (sqlmod.snake(name) if ctx.spring and not room else name)
            col = {"name": column_name, "field": name, "java_type": type_label(type_text), "pk": "Id" in ann or "PrimaryKey" in ann or "EmbeddedId" in ann,
                   "nullable": not ("Id" in ann or (column_ann is not None and column_ann.raw("nullable") == "false") or "NotNull" in ann or "NonNull" in ann or "PrimaryKey" in ann),
                   "unique": column_ann is not None and column_ann.raw("unique") == "true", "source": "jpa" if not room else "room", "line": member_line}
            if column_ann is not None:
                for k in ("length", "precision", "scale", "columnDefinition"):
                    if column_ann.raw(k):
                        col[k] = column_ann.string(k) if k == "columnDefinition" else column_ann.raw(k)
            gen = ann.get("GeneratedValue")
            if gen is not None or ("PrimaryKey" in ann and ann["PrimaryKey"].raw("autoGenerate") == "true"):
                col["generated"] = (gen.idents("strategy") or ["AUTO"])[-1] if gen is not None else "AUTO"
                seq = ann.get("SequenceGenerator")
                if seq is not None:
                    col["sequence"] = seq.string("sequenceName", "name")
            if "Enumerated" in ann:
                col["enumerated"] = (ann["Enumerated"].idents("value", "") or ["ORDINAL"])[-1]
            if "Version" in ann:
                col["version"] = True
            if "Lob" in ann:
                col["lob"] = True
            if relation:
                col["relation"] = relation
                col["references_entity"] = target
            validations = validations_of(member_annotations)
            if validations:
                col["validations"] = validations
            columns.append(col)
    key = sqlmod.table_key(table_name)
    entry = cat.tables_registry.setdefault(key, {"name": table_name, "columns": [], "sources": [], "foreign_keys": []})
    entry.update({"entity": decl.name, "entity_path": file.path, "entity_line": decl.line, "schema": schema or entry.get("schema", ""),
                  "orm": "Room" if room else ("Hibernate (JPA)" if "hibernate" in ctx.dep_names or ctx.spring else "JPA")})
    entry.setdefault("path", file.path)
    entry.setdefault("line", decl.line)
    entry["end_line"] = decl.end_line
    entry["sources"].append("Room @Entity" if room else "JPA @Entity")
    entry["entity_columns"] = columns
    entry["relations"] = relations
    entry["entity_code"] = _class_excerpt(file.text, decl, 120)
    if table_ann is not None:
        entry["indexes"] = entry.get("indexes", []) + [{"name": a, "columns": []} for a in string_values(table_ann.raw("indexes") or "")[:0]]
        uniques = re.findall(r"columnNames\s*=\s*\{?([^})]*)", table_ann.raw("uniqueConstraints") or "")
        if uniques:
            entry["unique"] = [{"columns": string_values(u)} for u in uniques]
    cat.entity_tables[decl.name] = key


def _persistent_members(ref: TypeRef) -> list[tuple[str, str, list[Annotation], int]]:
    decl = ref.decl
    out = []
    for param in decl.params:
        out.append((param.name, param.type, param.annotations, decl.line))
    for member in decl.members:
        if member.kind == "field" and "static" not in member.modifiers and "const" not in member.modifiers and "transient" not in member.modifiers:
            out.append((member.name, member.type, member.annotations, member.line))
    if not any(a for _, _, anns, _ in out for a in anns if a.name in ("Id", "EmbeddedId", "PrimaryKey", "Column")):
        # property-access mapping: annotations on getters
        for member in decl.methods():
            if re.match(r"^(get|is)[A-Z]", member.name) and member.has("Id", "Column", "ManyToOne", "OneToMany", "ManyToMany", "OneToOne", "JoinColumn"):
                name = member.name[3:] if member.name.startswith("get") else member.name[2:]
                out.append((name[0].lower() + name[1:], member.type, member.annotations, member.line))
    return out


def _repository_item(ctx: Context, cat: Catalog, ref: TypeRef, entity: str, id_type: str, framework: str, base: str) -> Item:
    decl, file = ref.decl, ref.file
    table_key = cat.entity_tables.get(entity, sqlmod.table_key(sqlmod.snake(entity)) if entity else "")
    item = cat.add(Item("data", "repository", f"repo:{decl.name}", decl.name, file.path, decl.line, decl.end_line, file.language, framework,
                        decl.header.split("{")[0].strip()[:300],
                        {"entity": entity, "id_type": id_type, "base": base, "table": table_key, "inherited_methods": list(dict.fromkeys(INHERITED.get(base, []) + (INHERITED["CrudRepository"] if base in ("JpaRepository", "PagingAndSortingRepository") else []))) if base else [],
                         "methods": []}, code=_class_excerpt(file.text, decl, 100)))
    cat.type_keys.setdefault(decl.name, item.key)
    if table_key:
        cat.pending_tables.append((item.key, table_key, "manages", ""))
    return item


def _spring_data_repository(ctx: Context, cat: Catalog, ref: TypeRef) -> None:
    decl, file = ref.decl, ref.file
    base, entity, id_type = "", "", ""
    for name, args in decl.bases:
        if name in REPOSITORY_BASES:
            base = name
            if args:
                entity = args[0]
                id_type = args[1] if len(args) > 1 else ""
            break
    if not entity and decl.ann("RepositoryDefinition"):
        idents = decl.ann("RepositoryDefinition").idents("domainClass")
        entity = idents[0] if idents else ""
    for name, _args in decl.bases:
        if name not in REPOSITORY_BASES and name in ctx.types:
            cat.pending_implements.append((f"repo:{decl.name}", name))
    item = _repository_item(ctx, cat, ref, entity, id_type, "Spring Data JPA" if "Jpa" in base or base in ("CrudRepository", "Repository", "ListCrudRepository", "PagingAndSortingRepository") else f"Spring Data ({base})", base)
    table = _table_for_entity(ctx, cat, entity)
    columns = _column_map(cat, entity)
    for method in decl.methods():
        query_ann = method.ann("Query", "NativeQuery")
        modifying = method.has("Modifying")
        if query_ann is not None:
            raw_sql = query_ann.string("value", "")
            native = query_ann.raw("nativeQuery") == "true" or query_ann.name == "NativeQuery"
            statement = {"sql": re.sub(r"\s+", " ", ctx.resolve(query_ann.raw("value", "") or "") or raw_sql).strip(), "dialect": "SQL" if native else "JPQL", "line": method.line}
            _add_query(ctx, cat, item, decl.name, method, statement, file, modifying)
        elif method.has("Procedure"):
            proc = method.ann("Procedure")
            name = proc.string("value", "procedureName", "name", "") or method.name
            _add_query(ctx, cat, item, decl.name, method, {"sql": f"CALL {name}", "dialect": "SQL", "procedure": name, "line": method.line}, file, modifying)
        else:
            derived = sqlmod.derived_query(method.name, table, columns)
            if derived:
                _add_query(ctx, cat, item, decl.name, method, {"sql": derived["sql"], "dialect": "derived", "line": method.line, "fields": derived["fields"]}, file, modifying)
            else:
                _add_query(ctx, cat, item, decl.name, method, {"sql": "", "dialect": "custom", "line": method.line}, file, modifying)


def _mapper(cat: Catalog, ref: TypeRef) -> None:
    decl, file = ref.decl, ref.file
    methods = [{"name": m.name, "signature": f"{m.name}({', '.join(type_label(p.type) + ' ' + p.name for p in m.params)})" + (f": {type_label(m.type)}" if m.type else ""),
                "line": m.line, "mappings": [a.text()[:160] for a in m.annotations if a.name in ("Mapping", "Mappings")]} for m in decl.methods()]
    item = cat.add(Item("shared", "mapper", f"mapper:{decl.name}", decl.name, file.path, decl.line, decl.end_line, file.language, "MapStruct", decl.header.split("{")[0][:200],
                        {"class": decl.name, "methods": methods, "uses": decl.ann("Mapper").idents("uses")}, code=_class_excerpt(file.text, decl, 80)))
    cat.type_keys.setdefault(decl.name, item.key)


def _interface_repository(ctx: Context, cat: Catalog, ref: TypeRef) -> None:
    """A plain repository / DAO interface with several implementations (JDBC, JPA, ...)."""
    decl = ref.decl
    m = re.match(r"(\w+?)(?:Repository|Dao|DAO|Mapper|DataSource)$", decl.name)
    entity = m.group(1) if m and m.group(1) in cat.entity_tables else ""
    item = _repository_item(ctx, cat, ref, entity, "", "Repository interface", "")
    item.details["interface"] = True
    for method in decl.methods():
        signature = f"{method.name}({', '.join(type_label(p.type) + ' ' + p.name for p in method.params)})" + (f": {type_label(method.type)}" if method.type else "")
        item.details["methods"].append({"name": method.name, "signature": signature, "operation": "/".join(sqlmod.crud_of_method(method.name)), "line": method.line})
        cat.type_members.setdefault(decl.name, {})[method.name] = item.key
    for base in decl.base_names():
        if base not in REPOSITORY_BASES and base in ctx.types:
            cat.pending_implements.append((item.key, base))


def _annotated_dao(ctx: Context, cat: Catalog, ref: TypeRef) -> None:
    """Room @Dao and MyBatis @Mapper interfaces."""
    decl, file = ref.decl, ref.file
    room = decl.has("Dao")
    entities = set()
    item = _repository_item(ctx, cat, ref, "", "", "Room" if room else "MyBatis", "")
    for method in decl.methods():
        ann = method.ann("Query", "Select", "Insert", "Update", "Delete", "Upsert", "RawQuery", "SelectProvider", "InsertProvider")
        if ann is None:
            continue
        sql_text = " ".join(ann.strings("value", "")) if ann.name in ("Query", "Select") or ann.strings("value", "") else ""
        if not sql_text:
            entity = simple_type(unwrap_type(method.params[0].type, COLLECTIONS)[0]) if method.params else ""
            entities.add(entity)
            table = _table_for_entity(ctx, cat, entity)
            sql_text = {"Insert": f"INSERT INTO {table}", "Upsert": f"INSERT OR REPLACE INTO {table}", "Update": f"UPDATE {table} SET ...", "Delete": f"DELETE FROM {table} WHERE <primary key>"}.get(ann.name, "")
        _add_query(ctx, cat, item, decl.name, method, {"sql": re.sub(r"\s+", " ", sql_text).strip(), "dialect": "SQL", "line": method.line}, file, False)
    if entities:
        item.details["entity"] = ", ".join(sorted(e for e in entities if e))


def _class_repository(ctx: Context, cat: Catalog, ref: TypeRef) -> None:
    decl, file = ref.decl, ref.file
    if decl.kind != "class":
        return
    entity = ""
    m = re.match(r"(\w+?)(?:Repository|Dao|DAO)(?:Impl)?$", decl.name)
    if m and m.group(1) in cat.entity_tables:
        entity = m.group(1)
    item = _repository_item(ctx, cat, ref, entity, "", "JDBC / EntityManager" if re.search(r"jdbc|Jdbc|JdbcTemplate", file.text) else "JPA EntityManager" if "EntityManager" in file.text else "DAO", "")
    for base in decl.base_names():
        if re.search(r"(Repository|Dao|DAO|DataSource)$", base) and base in ctx.types:
            cat.pending_implements.append((item.key, base))
    var_types = field_types(ref, ctx)
    for method in decl.methods():
        if method.kind == "constructor":
            continue
        statements = sql_in(method.body, method.body_line or method.line)
        for statement in statements:
            _add_query(ctx, cat, item, decl.name, method, statement, file, False)
        if not statements:
            calls = calls_in(method.body, var_types, method.body_line or method.line)
            if calls:
                item.details.setdefault("method_calls", {})[method.name] = [f"{c['type']}.{c['method']}" for c in calls]
            if calls and method.public:
                item.details["methods"].append({"name": method.name, "signature": f"{method.name}(...)", "line": method.line, "calls": [f"{c['type']}.{c['method']}" for c in calls]})
                cat.pending_calls += [(item.key, c["type"], c["method"], c["line"]) for c in calls]
        if not statements and re.search(r"\b(?:entityManager|em|session)\s*\.\s*(persist|merge|remove|find|getReference|save|update|delete|saveOrUpdate|get|load)\s*\(", method.body):
            ops = sorted({op for op_name in re.findall(r"\b(?:entityManager|em|session)\s*\.\s*(\w+)\s*\(", method.body) for op in sqlmod.crud_of_method(op_name.replace("persist", "save"))})
            if entity:
                cat.pending_tables.append((item.key, cat.entity_tables[entity], "/".join(ops) or "R", method.name))
    cat.type_members.setdefault(decl.name, {})


def _table_for_entity(ctx: Context, cat: Catalog, entity: str) -> str:
    key = cat.entity_tables.get(entity)
    if key:
        return cat.tables_registry.get(key, {}).get("name", key)
    return sqlmod.snake(entity) if entity else ""


def _column_map(cat: Catalog, entity: str) -> dict[str, str]:
    key = cat.entity_tables.get(entity)
    entry = cat.tables_registry.get(key or "", {})
    out = {c["field"]: c["name"] for c in entry.get("entity_columns", []) if c.get("field")}
    for rel in entry.get("relations", []):
        if rel.get("join_column"):
            out[rel["field"]] = rel["join_column"]
    return out


def _add_query(ctx: Context, cat: Catalog, repo: Item, owner: str, method: Member, statement: dict, file, modifying: bool) -> None:
    sql_text = statement.get("sql", "")
    dialect = statement.get("dialect", "SQL")
    if dialect == "JPQL":
        entities = sqlmod.jpql_entities(sql_text)
        tables = {}
        for entity, ops in entities.items():
            key = cat.entity_tables.get(entity) or sqlmod.table_key(sqlmod.snake(entity))
            tables[key] = ops
    elif dialect == "custom":
        tables = {}
    else:
        tables = {sqlmod.table_key(t): ops for t, ops in sqlmod.tables_in(sql_text).items()}
        if not tables and repo.details.get("table"):
            tables = {repo.details["table"]: ["R"]}
    operation = sqlmod.operation(sql_text) if sql_text else ""
    if dialect == "derived":
        operation = "DELETE" if re.match(r"^(delete|remove)", method.name) else "SELECT"
        if repo.details.get("table"):
            tables = {repo.details["table"]: ["D" if operation == "DELETE" else "R"]}
    if dialect == "custom":
        ops = sqlmod.crud_of_method(method.name)
        if repo.details.get("table") and ops:
            tables = {repo.details["table"]: ops}
        operation = {"C": "INSERT", "R": "SELECT", "U": "UPDATE", "D": "DELETE"}.get(ops[0], "") if ops else ""
    params = [{"name": (p.ann("Param", "Bind").string("value", "") if p.ann("Param", "Bind") else "") or p.name, "type": type_label(p.type)} for p in method.params]
    name = f"{owner}.{method.name}"
    signature = f"{method.name}({', '.join(type_label(p.type) + ' ' + p.name for p in method.params)})" + (f": {type_label(method.type)}" if method.type else "")
    query = cat.add(Item("data", "query", f"query:{name}", name, file.path, statement.get("line", method.line), method.end_line, file.language, repo.framework, signature,
                         {"repository": owner, "method": method.name, "sql": sql_text, "dialect": dialect, "operation": operation, "tables": tables, "params": params,
                          "returns": type_label(method.type), "modifying": modifying, "procedure": statement.get("procedure", ""),
                          "native": dialect == "SQL", "fields": statement.get("fields", [])},
                         code=snippet(file.text, method.line - 3, method.end_line, 30)))
    repo.details.setdefault("methods", []).append({"name": method.name, "signature": signature, "operation": operation, "dialect": dialect, "line": method.line,
                                                   "tables": tables, "query": query.key})
    cat.link(repo.key, query.key, "declares")
    cat.type_members.setdefault(owner, {})[method.name] = query.key
    for table, ops in tables.items():
        cat.pending_tables.append((query.key, table, "/".join(ops), ""))
    if statement.get("procedure"):
        cat.pending_routines.append((query.key, statement["procedure"]))


def _query_from_sql(ctx: Context, cat: Catalog, owner_key: str, owner: str, method_name: str, statement: dict, file) -> None:
    tables = {sqlmod.table_key(t): ops for t, ops in sqlmod.tables_in(statement["sql"]).items()} if statement["dialect"] == "SQL" else {
        cat.entity_tables.get(e) or sqlmod.table_key(sqlmod.snake(e)): ops for e, ops in sqlmod.jpql_entities(statement["sql"]).items()}
    name = f"{owner}.{method_name}"
    if f"query:{name}" in cat.items and cat.items[f"query:{name}"].details.get("sql") == statement["sql"]:
        return
    query = cat.add(Item("data", "query", f"query:{name}", name, file.path, statement["line"], statement["line"], file.language, "JDBC" if statement["dialect"] == "SQL" else "JPA",
                         statement["sql"][:200], {"repository": owner, "method": method_name, "sql": statement["sql"], "dialect": statement["dialect"],
                                                  "operation": sqlmod.operation(statement["sql"]), "tables": tables, "params": [], "procedure": statement.get("procedure", ""), "inline": True},
                         code=snippet(file.text, statement["line"] - 2, statement["line"] + 12, 16)))
    cat.link(owner_key, query.key, "runs")
    for table, ops in tables.items():
        cat.pending_tables.append((query.key, table, "/".join(ops), ""))
    if statement.get("procedure"):
        cat.pending_routines.append((query.key, statement["procedure"]))
