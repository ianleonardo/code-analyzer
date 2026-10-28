"""Camel routes -> outbound HTTP calls with full URLs; ingress -> direct: hops -> egress; v1 / v2 sharing one route."""

import textwrap

from app.services.analyzer.catalog import SourceFile, build

POM = """
<project><dependencies>
  <dependency><groupId>org.apache.camel.springboot</groupId><artifactId>camel-spring-boot-starter</artifactId></dependency>
  <dependency><groupId>org.apache.camel.springboot</groupId><artifactId>camel-servlet-starter</artifactId></dependency>
</dependencies></project>
"""

PROPERTIES = """
server.servlet.context-path=/
camel.servlet.mapping.context-path=/spl/*
dbe.account.url=http://dbe-account:8080/api
dbe.customer.host=dbe-customer.internal
dbe.customer.port=8443
dbe.audit.url=http://user:s3cret@dbe-audit:8080
"""

PROD_PROPERTIES = """
dbe.account.url=https://account.prod.acme.com/api
"""

ROUTES_JAVA = '''
package com.acme.spl;

import org.apache.camel.Exchange;
import org.apache.camel.builder.RouteBuilder;
import org.springframework.stereotype.Component;

@Component
public class AccountRoutes extends RouteBuilder {
    @Override
    public void configure() {
        rest("/v1/accounts").get("/{id}").to("direct:getAccount");
        rest("/v2/accounts").get("/{id}").to("direct:getAccount")
            .post().to("direct:createAccount");

        from("direct:getAccount").routeId("get-account")
            .removeHeaders("CamelHttp*")
            .setHeader(Exchange.HTTP_METHOD, constant("GET"))
            .setHeader(Exchange.HTTP_PATH, simple("/accounts/${header.id}"))
            .to("{{dbe.account.url}}?bridgeEndpoint=true");

        from("direct:createAccount").routeId("create-account")
            .choice()
                .when(header("apiVersion").isEqualTo("v2"))
                    .to("http://{{dbe.customer.host}}:{{dbe.customer.port}}/customers/validate?httpMethod=POST")
                .otherwise()
                    .to("http://{{dbe.customer.host}}:{{dbe.customer.port}}/customers/legacy-validate?httpMethod=POST")
            .end()
            .setHeader(Exchange.HTTP_METHOD, constant("POST"))
            .toD("{{dbe.account.url}}/accounts?bridgeEndpoint=true")
            .to("direct:audit");

        from("direct:audit").routeId("audit")
            .recipientList(simple("{{dbe.audit.url}}/audit/${header.kind}?bridgeEndpoint=true&apiKey=abc123"));

        from("servlet:/spl/proxy?matchOnUriPrefix=true").routeId("legacy-proxy")
            .to("http://dbe-legacy:9000/legacy?bridgeEndpoint=true");

        from("direct:broken").routeId("broken")
            .to("http://{{missing.host}}/x/${body}");
    }
}
'''

CUSTOMER_XML = """
<routes xmlns="http://camel.apache.org/schema/spring">
  <rest path="/v1/customers">
    <get uri="/{customerId}"><to uri="direct:getCustomer"/></get>
  </rest>
  <route id="customer-by-id">
    <from uri="direct:getCustomer"/>
    <setHeader name="CamelHttpMethod"><constant>GET</constant></setHeader>
    <toD uri="https://{{dbe.customer.host}}:{{dbe.customer.port}}/customers/${header.customerId}"/>
  </route>
</routes>
"""


def _file(path: str, lang: str, text: str, category: str = "code") -> SourceFile:
    return SourceFile(path=path, language=lang, category=category, layer="service", role="service", is_test=False, container="", text=textwrap.dedent(text).strip())


def _catalog():
    files = [
        _file("pom.xml", "XML", POM, "config"),
        _file("src/main/resources/application.properties", "Properties", PROPERTIES, "config"),
        _file("src/main/resources/application-prod.properties", "Properties", PROD_PROPERTIES, "config"),
        _file("src/main/java/com/acme/spl/AccountRoutes.java", "Java", ROUTES_JAVA),
        _file("src/main/resources/routes/customer-routes.xml", "XML", CUSTOMER_XML, "config"),
    ]
    return build(files, [{"name": "camel-spring-boot-starter"}], [], ["Apache Camel"])


def _integrations(cat):
    return {i.key: i for i in cat.by_kind("integration") if i.framework == "Apache Camel"}


def _calls(cat, source_key):
    return {link.target for link in cat.links if link.source == source_key and link.kind == "calls"}


def test_full_urls_methods_and_placeholders():
    cat = _catalog()
    outs = _integrations(cat)
    by_name = {i.name + " " + i.details.get("base_url", ""): i for i in outs.values()}
    # {{dbe.account.url}} + HTTP_PATH header with ${header.id} -> /api/accounts/{id}; method from the setHeader
    get = next(i for i in outs.values() if i.details["method"] == "GET" and i.details["path"].startswith("/api") and "accounts" in i.details["path"])
    assert get.details["base_url"] == "http://dbe-account:8080" and get.details["path"] == "/api/accounts/{id}"
    assert get.details["variants"] == ["https://account.prod.acme.com/api"] or "prod" in str(get.details["variants"]) or get.details["variants"] == []
    assert get.details["target"] == "dbe-account" and get.details["client"] == "Camel"
    # POST: toD with the property host + port and the plain path
    post = next(i for i in outs.values() if i.details["method"] == "POST" and i.details["path"] == "/api/accounts")
    assert post.details["base_url"] == "http://dbe-account:8080"
    # when / otherwise branches keep their condition
    validate = next(i for i in outs.values() if i.details["path"] == "/customers/validate")
    legacy = next(i for i in outs.values() if i.details["path"] == "/customers/legacy-validate")
    assert validate.details["base_url"] == "http://dbe-customer.internal:8443" and validate.details["method"] == "POST"
    assert "v2" in validate.details["condition"] and legacy.details["condition"] == "otherwise"
    # XML: https host:port placeholders, method from <setHeader>, ${header.customerId} -> {customerId}
    xml = next(i for i in outs.values() if i.details["path"] == "/customers/{customerId}")
    assert xml.details["base_url"] == "https://dbe-customer.internal:8443" and xml.details["method"] == "GET"
    assert by_name  # sanity


def test_credentials_are_masked_and_unresolved_parts_reported():
    cat = _catalog()
    outs = _integrations(cat)
    audit = next(i for i in outs.values() if "/audit/" in i.details["path"])
    blob = str(audit.details) + str(audit.signature)
    assert "s3cret" not in blob and "abc123" not in blob
    assert audit.details["path"] == "/audit/{kind}" and audit.details["method"] == ""
    broken = next(i for i in outs.values() if i.details["path"].startswith("/x"))
    assert "missing.host" in broken.details["unresolved"] and broken.details["confidence"] < 1


def test_versioned_ingress_shares_one_route_and_reaches_every_downstream_call():
    cat = _catalog()
    endpoints = {i.details["path"]: i for i in cat.by_kind("endpoint") if i.framework == "Apache Camel"}
    v1, v2 = endpoints["/v1/accounts/{id}"], endpoints["/v2/accounts/{id}"]
    assert v1.details["url"] == "/spl/v1/accounts/{id}" and v1.details["version"] == "v1"
    assert v2.details["url"] == "/spl/v2/accounts/{id}" and v2.details["version"] == "v2"
    get = next(i for i in _integrations(cat).values() if i.details["path"] == "/api/accounts/{id}")
    # both versions end at the same downstream call, and the HTTP method is inherited
    assert get.key in _calls(cat, v1.key) and get.key in _calls(cat, v2.key)
    # POST /v2/accounts reaches validate + create + (through direct:audit) the audit call
    post = next(i for i in cat.by_kind("endpoint") if i.name == "POST /v2/accounts")
    reached = {cat.items[k].details["path"] for k in _calls(cat, post.key) if k in cat.items}
    assert {"/customers/validate", "/api/accounts", "/audit/{kind}"} <= reached
    customer = endpoints["/v1/customers/{customerId}"]
    assert any(cat.items[k].details["path"] == "/customers/{customerId}" for k in _calls(cat, customer.key))


def test_bridge_endpoint_pass_through_inherits_ingress_path():
    cat = _catalog()
    proxy = next(i for i in _integrations(cat).values() if i.details["host"] == "dbe-legacy:9000")
    assert proxy.details["passthrough"] is True and proxy.details["base_url"] == "http://dbe-legacy:9000"
    assert proxy.details["path"] == "/legacy/{path}"
