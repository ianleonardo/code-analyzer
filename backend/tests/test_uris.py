"""The URI resolver: constants, templates, enums, helper functions, builders and build configuration."""

import textwrap

from app.services.analyzer.catalog import SourceFile
from app.services.analyzer.catalog.model import Context
from app.services.analyzer.catalog.uris import resolver_for, split_url


def _file(path: str, language: str, text: str) -> SourceFile:
    return SourceFile(path=path, language=language, category="code", layer="shared", role="", is_test=False, container="", text=textwrap.dedent(text).strip())


def _resolver(*files: SourceFile):
    ctx = Context(list(files), [], [], [])
    ctx.build_index()
    return ctx, resolver_for(ctx)


KOTLIN = _file("core/net/Api.kt", "Kotlin", '''
    package com.acme.core.net
    object Api {
        const val BASE = "https://gw.acme.com"
        const val V2 = "/v2"
        const val USERS = "$V2/users"
        val ACCOUNTS: String = BASE + V2 + "/accounts"
        fun user(id: Long) = "$USERS/$id"
        fun txn(id: Long, acct: String): String {
            val root = "$ACCOUNTS/$acct"
            return root + "/txn/" + id
        }
        fun report(kind: String) = String.format("%s/reports/%s", BASE, kind)
        val mobile: String get() = BuildConfig.API_URL + "/mobile"
    }
    enum class Ep(val path: String, val method: String = "GET") {
        LOGIN("/auth/login", "POST"), PROFILE("/me");
    }
''')
GRADLE = _file("app/build.gradle.kts", "Kotlin", '''
    android { defaultConfig { buildConfigField("String", "API_URL", "\\"https://api.acme.com/spl\\"") } }
''')


def test_constants_templates_and_helper_functions():
    ctx, r = _resolver(KOTLIN, GRADLE)
    file = KOTLIN
    assert r.resolve("Api.USERS", file).text == "/v2/users"
    assert r.resolve("Api.ACCOUNTS", file).text == "https://gw.acme.com/v2/accounts"
    got = r.resolve("Api.user(id)", file, scope={"id": "{id}"})
    assert got.text == "/v2/users/{id}" and got.complete
    assert r.resolve("Api.txn(7, \"A1\")", file).text == "https://gw.acme.com/v2/accounts/A1/txn/7"
    assert r.resolve("Api.report(\"daily\")", file).text == "https://gw.acme.com/reports/daily"
    assert r.resolve("Api.mobile", file).text == "https://api.acme.com/spl/mobile"


def test_enum_arguments_and_unresolved_parts():
    ctx, r = _resolver(KOTLIN, GRADLE)
    assert r.resolve("Ep.LOGIN.path", KOTLIN).text == "/auth/login"
    assert r.resolve("Ep.LOGIN.method", KOTLIN).text == "POST"
    assert r.resolve("Ep.PROFILE.method", KOTLIN).text == "GET" or r.resolve("Ep.PROFILE.method", KOTLIN).text == "{method}"
    unknown = r.resolve("serverUrl + \"/health\"", KOTLIN)
    assert unknown.text == "{serverUrl}/health" and unknown.unresolved == ["serverUrl"] and unknown.fraction == 0.0
    assert split_url(unknown.text) == ("", "/health")
    assert split_url("https://h.com:8080/a/b") == ("https://h.com:8080", "/a/b")


def test_swift_interpolation_enums_and_builders():
    swift = _file("Core/Net.swift", "Swift", '''
        enum Endpoint: String {
            case users = "/v1/users"
            case login = "/auth/login"
        }
        struct Cfg {
            static let base = "https://gw.acme.com"
            static let prefix = base + "/mobile"
        }
        final class Repo {
            func user(id: Int) -> String { "\\(Cfg.prefix)/users/\\(id)" }
            func login() -> String { return Cfg.prefix + Endpoint.login.rawValue }
            func url(_ id: Int) -> URL { URL(string: user(id: id))! }
        }
    ''')
    ctx, r = _resolver(swift)
    assert r.resolve("Cfg.prefix", swift).text == "https://gw.acme.com/mobile"
    assert r.resolve("user(id: 5)", swift, owner="Repo").text == "https://gw.acme.com/mobile/users/5"
    assert r.resolve("login()", swift, owner="Repo").text == "https://gw.acme.com/mobile/auth/login"
    assert r.resolve("Endpoint.users.rawValue", swift).text == "/v1/users"
    assert r.resolve("URL(string: Cfg.prefix + \"/ping\")!", swift).text == "https://gw.acme.com/mobile/ping"
    builder = r.resolve("Uri.parse(Cfg.base).buildUpon().appendPath(\"v3\").appendPath(\"items\").build()", swift)
    assert builder.text == "https://gw.acme.com/v3/items"


def test_java_concatenation_and_xcconfig():
    java = _file("src/Paths.java", "Java", '''
        public class Paths {
            public static final String ROOT = "/api";
            public static final String ORDERS = ROOT + "/orders";
            static String one(String id) { return ORDERS + "/" + id; }
        }
    ''')
    xc = _file("Config/Prod.xcconfig", "Other", "API_BASE = https:/$()/api.acme.com/spl\n")
    plist = _file("App/Info.plist", "Other", "<dict><key>API_BASE</key><string>$(API_BASE)</string></dict>")
    ctx, r = _resolver(java, xc, plist)
    assert r.resolve("Paths.ORDERS", java).text == "/api/orders"
    assert r.resolve("Paths.one(\"9\")", java).text == "/api/orders/9"
    assert r.build_fields["API_BASE"][0] == "https://api.acme.com/spl"
    assert r.resolve('Bundle.main.object(forInfoDictionaryKey: "API_BASE") as? String', _file("a.swift", "Swift", "")).text == "https://api.acme.com/spl"
