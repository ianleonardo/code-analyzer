import textwrap
from app.services.analyzer.catalog import SourceFile, build


def _make_file(path: str, lang: str, content: str) -> SourceFile:
    return SourceFile(
        path=path,
        language=lang,
        category="code" if lang in ("Java", "Kotlin") else "config",
        layer="service",
        role="service",
        is_test=False,
        container="",
        text=textwrap.dedent(content).strip(),
    )


def test_camel_java_dsl_extraction():
    files = [
        _make_file(
            "pom.xml",
            "XML",
            """
            <project>
                <dependencies>
                    <dependency><groupId>org.apache.camel.springboot</groupId><artifactId>camel-spring-boot-starter</artifactId></dependency>
                    <dependency><groupId>org.apache.camel</groupId><artifactId>camel-kafka</artifactId></dependency>
                </dependencies>
            </project>
            """,
        ),
        _make_file(
            "src/main/java/com/example/OrderRoute.java",
            "Java",
            """
            package com.example;

            import org.apache.camel.builder.RouteBuilder;
            import org.springframework.stereotype.Component;

            @Component
            public class OrderRoute extends RouteBuilder {
                @Override
                public void configure() {
                    // Kafka messaging listener
                    from("kafka:orders-topic?groupId=order-service")
                        .routeId("consume-orders")
                        .bean(OrderService.class, "processOrder")
                        .to("direct:auditOrder");

                    // Internal direct route
                    from("direct:auditOrder")
                        .routeId("audit-route")
                        .to("sql:INSERT INTO audit_log (msg) VALUES (:#msg)");

                    // Scheduled timer job
                    from("timer:cleanupTimer?period=60000")
                        .routeId("cleanup-job")
                        .to("direct:doCleanup");

                    // REST DSL endpoints
                    rest("/api/orders")
                        .get("/{id}")
                            .to("direct:getOrder")
                        .post()
                            .to("direct:createOrder");
                }
            }
            """,
        ),
        _make_file(
            "src/main/resources/schema.sql",
            "SQL",
            "CREATE TABLE audit_log (id BIGINT PRIMARY KEY, msg VARCHAR(255));",
        ),
        _make_file(
            "src/main/java/com/example/OrderService.java",
            "Java",
            """
            package com.example;

            import org.springframework.stereotype.Service;

            @Service
            public class OrderService {
                public void processOrder(String order) {}
            }
            """,
        ),
    ]

    dependencies = [{"name": "camel-spring-boot-starter"}]
    cat = build(files, dependencies, [], ["Apache Camel", "Spring"])

    # 1. Listener from kafka
    listener = cat.items.get("listener:camel:consume-orders")
    assert listener is not None
    assert listener.layer == "service"
    assert listener.kind == "listener"
    assert listener.framework == "Apache Camel"
    assert listener.details["transport"] == "kafka"
    assert listener.details["destinations"] == ["orders-topic"]

    # 2. Job from timer
    job = cat.items.get("job:camel:cleanup-job")
    assert job is not None
    assert job.layer == "service"
    assert job.kind == "job"
    assert job.details["trigger"] == "timer"
    assert job.details["schedule"]["period"] == "60000"

    # 3. Direct route
    direct = cat.items.get("svc:camel:audit-route")
    assert direct is not None
    assert direct.layer == "service"
    assert direct.kind == "service"

    # 4. REST DSL endpoints
    get_ep = cat.items.get("endpoint:camel:GET:/api/orders/{id}")
    assert get_ep is not None
    assert get_ep.layer == "api"
    assert get_ep.kind == "endpoint"
    assert get_ep.name == "GET /api/orders/{id}"

    post_ep = cat.items.get("endpoint:camel:POST:/api/orders")
    assert post_ep is not None
    assert post_ep.layer == "api"
    assert post_ep.kind == "endpoint"

    # Check links
    links = {(lk.source, lk.target, lk.kind) for lk in cat.links}
    # route -> OrderService
    assert ("listener:camel:consume-orders", "svc:OrderService", "calls") in links
    # route -> audit_log table
    assert any(lk.source == "svc:camel:audit-route" and "audit_log" in lk.target for lk in cat.links)


def test_camel_xml_and_yaml_dsl_extraction():
    files = [
        _make_file(
            "src/main/resources/camel-context.xml",
            "XML",
            """
            <camelContext xmlns="http://camel.apache.org/schema/spring">
                <rest path="/api/payments">
                    <post uri="/charge">
                        <to uri="direct:chargePayment"/>
                    </post>
                </rest>
                <route id="payment-route">
                    <from uri="jms:queue:payments"/>
                    <to uri="direct:chargePayment"/>
                </route>
            </camelContext>
            """,
        ),
        _make_file(
            "src/main/resources/routes.camel.yaml",
            "YAML",
            """
            - route:
                id: notification-route
                from:
                  uri: rabbitmq:events.exchange?routingKey=notify
                  steps:
                    - to:
                        uri: direct:sendEmail
            """,
        ),
    ]

    dependencies = [{"name": "camel-core"}]
    cat = build(files, dependencies, [], ["Apache Camel"])

    # XML Rest endpoint
    xml_ep = cat.items.get("endpoint:camel:POST:/api/payments/charge")
    assert xml_ep is not None
    assert xml_ep.layer == "api"

    # XML JMS Route
    xml_route = cat.items.get("listener:camel:payment-route")
    assert xml_route is not None
    assert xml_route.details["transport"] == "jms"
    assert xml_route.details["destinations"] == ["queue:payments"]

    # YAML RabbitMQ Route
    yaml_route = cat.items.get("listener:camel:notification-route")
    assert yaml_route is not None
    assert yaml_route.details["transport"] == "rabbitmq"
    assert yaml_route.details["destinations"] == ["events.exchange"]
