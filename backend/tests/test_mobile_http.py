"""Mobile HTTP calls found through constants, helpers, wrappers (repository -> use case -> ApiClient) and build configuration."""

import textwrap

from app.services.analyzer.catalog import SourceFile, build


def _file(path: str, lang: str, text: str, category: str = "code") -> SourceFile:
    return SourceFile(path=path, language=lang, category=category, layer="ui", role="", is_test=False, container="", text=textwrap.dedent(text).strip())


ANDROID = [
    _file("app/build.gradle.kts", "Kotlin", 'android { defaultConfig { buildConfigField("String", "API_URL", "\\"https://api.acme.com/spl\\"") } }', "config"),
    _file("app/src/main/AndroidManifest.xml", "XML", '<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.acme.core"><application/></manifest>', "config"),
    _file("core/src/main/java/com/acme/core/net/ApiPaths.kt", "Kotlin", '''
        package com.acme.core.net

        object ApiPaths {
            const val V2 = "/v2"
            const val ACCOUNTS = "$V2/accounts"
            fun account(id: String) = "$ACCOUNTS/$id"
            fun statement(id: String, month: Int) = String.format("%s/%s/statements/%d", ACCOUNTS, id, month)
        }
        enum class Ep(val path: String) { LOGIN("/auth/login"), PROFILE("/v2/me") }
    '''),
    _file("core/src/main/java/com/acme/core/net/ApiClient.kt", "Kotlin", '''
        package com.acme.core.net

        import okhttp3.OkHttpClient
        import okhttp3.Request

        class ApiClient(private val http: OkHttpClient) {
            private val base = BuildConfig.API_URL

            fun get(path: String): String {
                val request = Request.Builder().url(base + path).get().build()
                return http.newCall(request).execute().body!!.string()
            }
            fun send(method: String, path: String, body: String): String {
                val request = Request.Builder().url("$base$path").method(method, null).build()
                return http.newCall(request).execute().body!!.string()
            }
        }
    '''),
    _file("core/src/main/java/com/acme/core/data/AccountRepository.kt", "Kotlin", '''
        package com.acme.core.data

        import com.acme.core.net.ApiClient
        import com.acme.core.net.ApiPaths
        import com.acme.core.net.Ep

        class AccountRepository(private val api: ApiClient) {
            fun load(id: String) = api.get(ApiPaths.account(id))
            fun statement(id: String, month: Int) = fetch(ApiPaths.statement(id, month))
            private fun fetch(path: String) = api.get(path)
            fun login(user: String) = api.send("POST", Ep.LOGIN.path, user)
            fun me() = api.get(Ep.PROFILE.path)
            fun cached(key: String) = cache.get(key)
        }
    '''),
    _file("core/src/main/java/com/acme/core/ui/AccountViewModel.kt", "Kotlin", '''
        package com.acme.core.ui

        import androidx.lifecycle.ViewModel
        import com.acme.core.data.AccountRepository

        class AccountViewModel(private val repo: AccountRepository) : ViewModel() {
            fun open(id: String) = repo.load(id)
        }
    '''),
]

IOS = [
    _file("Core/Config/Prod.xcconfig", "Other", "API_BASE = https:/$()/api.acme.com/spl\n", "config"),
    _file("Core/Net/Endpoints.swift", "Swift", '''
        import Foundation

        enum Endpoint: String {
            case accounts = "/v2/accounts"
            case login = "/auth/login"
        }
        struct Cfg {
            static let base = Bundle.main.object(forInfoDictionaryKey: "API_BASE") as! String
            static func account(_ id: String) -> String { "\\(Endpoint.accounts.rawValue)/\\(id)" }
        }
    '''),
    _file("Core/Net/NetworkManager.swift", "Swift", '''
        import Alamofire

        final class NetworkManager {
            static let shared = NetworkManager()
            func request(_ path: String, method: HTTPMethod = .get) {
                AF.request(Cfg.base + path, method: method).response { _ in }
            }
        }
    '''),
    _file("Core/Data/AccountStore.swift", "Swift", '''
        final class AccountStore {
            func load(id: String) { NetworkManager.shared.request(Cfg.account(id)) }
            func login() { NetworkManager.shared.request(Endpoint.login.rawValue, method: .post) }
        }
    '''),
]


def _calls(cat):
    return {(i.details["method"], i.details["path"]): i for i in cat.items.values() if i.kind in ("api_client", "integration")}


def test_android_calls_resolve_through_constants_wrappers_and_build_config():
    cat = build(ANDROID, [{"name": "androidx.appcompat"}, {"name": "okhttp"}], [], [])
    calls = _calls(cat)
    account = calls[("GET", "/v2/accounts/{id}")]
    assert account.details["base_url"] == "https://api.acme.com/spl" and account.details["client"] in ("AccountRepository", "ApiClient")
    assert ("GET", "/v2/accounts/{id}/statements/{month}") in calls  # through fetch(path) -> api.get(path): a wrapper of a wrapper
    assert ("POST", "/auth/login") in calls and ("GET", "/v2/me") in calls
    assert len(calls) == 4  # cache.get(key) is not an HTTP call
    # the view model reaches the call through the repository
    links = {(link.source, link.target) for link in cat.links}
    repo_keys = [k for k, i in cat.items.items() if i.name == "AccountRepository"]
    assert repo_keys and any((repo_keys[0], account.key) == pair for pair in links)


def test_ios_wrapper_call_sites():
    cat = build(IOS, [], [], [])
    calls = _calls(cat)
    assert ("GET", "/v2/accounts/{id}") in calls and ("POST", "/auth/login") in calls
    assert calls[("GET", "/v2/accounts/{id}")].details["base_url"] == "https://api.acme.com/spl"
    assert calls[("GET", "/v2/accounts/{id}")].details["confidence"] == 1.0 and len(calls) == 2


def test_retrofit_paths_from_constants_and_builder_base_url():
    files = [
        ANDROID[0], ANDROID[1], ANDROID[2],
        _file("core/src/main/java/com/acme/core/net/Services.kt", "Kotlin", '''
            package com.acme.core.net

            import retrofit2.Retrofit
            import retrofit2.http.GET
            import retrofit2.http.POST
            import retrofit2.http.Path

            interface AccountService {
                @GET(ApiPaths.ACCOUNTS + "/{id}/balance")
                suspend fun balance(@Path("id") id: String): String

                @POST(Ep.LOGIN.path)
                suspend fun login(): String
            }

            object Net {
                val retrofit = Retrofit.Builder().baseUrl(BuildConfig.API_URL + "/").build()
                val accounts: AccountService = retrofit.create(AccountService::class.java)
            }
        '''),
    ]
    cat = build(files, [{"name": "androidx.appcompat"}, {"name": "retrofit"}], [], [])
    calls = {(i.details["method"], i.details["path"]): i for i in cat.items.values() if i.kind == "api_client"}
    balance = calls[("GET", "/v2/accounts/{id}/balance")]
    assert balance.details["base_url"].startswith("https://api.acme.com/spl")
    assert ("POST", "/auth/login") in calls
