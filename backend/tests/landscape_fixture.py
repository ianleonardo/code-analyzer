"""Repositories for landscape tests: the polyglot shop split into backend / web / mobile / ios, plus an audit service
(Kafka consumer, shared ORDERS table, Feign client to the shop, external partner API) and a near-copy of the backend."""

import textwrap
from pathlib import Path

from tests.test_catalog import FILES

AUDIT = {
    "pom.xml": """
        <project><modelVersion>4.0.0</modelVersion><groupId>com.acme</groupId><artifactId>audit-service</artifactId>
        <dependencies>
          <dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-web</artifactId></dependency>
          <dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-data-jpa</artifactId></dependency>
          <dependency><groupId>org.springframework.cloud</groupId><artifactId>spring-cloud-starter-openfeign</artifactId></dependency>
          <dependency><groupId>org.springframework.kafka</groupId><artifactId>spring-kafka</artifactId></dependency>
          <dependency><groupId>com.oracle.database.jdbc</groupId><artifactId>ojdbc11</artifactId></dependency>
        </dependencies></project>
    """,
    "src/main/resources/application.yml": """
        spring:
          application:
            name: audit-service
          datasource:
            url: jdbc:oracle:thin:@//db:1521/SHOP
        shop:
          url: http://shop-backend:8080
        partner:
          url: https://api.partner.com
    """,
    "src/main/java/com/acme/audit/OrderAuditListener.java": """
        package com.acme.audit;

        import org.springframework.kafka.annotation.KafkaListener;
        import org.springframework.stereotype.Component;

        @Component
        public class OrderAuditListener {
            private final AuditService service;
            public OrderAuditListener(AuditService service) { this.service = service; }

            @KafkaListener(topics = "order-events", groupId = "audit-trail")
            public void onOrderEvent(String payload) {
                service.record(payload);
            }
        }
    """,
    "src/main/java/com/acme/audit/AuditService.java": """
        package com.acme.audit;

        import org.springframework.stereotype.Service;
        import org.springframework.web.client.RestTemplate;

        @Service
        public class AuditService {
            private final ShopClient shop;
            private final OrderReadRepository orders;
            private final RestTemplate restTemplate = new RestTemplate();
            public AuditService(ShopClient shop, OrderReadRepository orders) { this.shop = shop; this.orders = orders; }

            public void record(String payload) {
                shop.order(1L);
                orders.findById(1L);
                restTemplate.postForObject("https://api.partner.com/v1/notify", payload, String.class);
            }
        }
    """,
    "src/main/java/com/acme/audit/ShopClient.java": """
        package com.acme.audit;

        import org.springframework.cloud.openfeign.FeignClient;
        import org.springframework.web.bind.annotation.GetMapping;
        import org.springframework.web.bind.annotation.PathVariable;

        @FeignClient(name = "shop", url = "${shop.url}")
        public interface ShopClient {
            @GetMapping("/shop/api/orders/{id}")
            String order(@PathVariable("id") Long id);
        }
    """,
    "src/main/java/com/acme/audit/OrderView.java": """
        package com.acme.audit;

        import jakarta.persistence.*;

        @Entity
        @Table(name = "ORDERS")
        public class OrderView {
            @Id
            private Long id;
            private String status;
        }
    """,
    "src/main/java/com/acme/audit/OrderReadRepository.java": """
        package com.acme.audit;

        import org.springframework.data.jpa.repository.JpaRepository;

        public interface OrderReadRepository extends JpaRepository<OrderView, Long> {
        }
    """,
}


def write_repo(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8")
    return root


def split_polyglot(base: Path) -> dict[str, Path]:
    out = {}
    for top, name in (("backend", "shop-backend"), ("web", "shop-web"), ("mobile", "shop-android"), ("ios", "shop-ios")):
        files = {rel[len(top) + 1:]: text for rel, text in FILES.items() if rel.startswith(top + "/")}
        out[name] = write_repo(base / name, files)
    out["audit-service"] = write_repo(base / "audit-service", AUDIT)
    copy = {rel[len("backend") + 1:]: text for rel, text in FILES.items() if rel.startswith("backend/") and ("OrderController" in rel or rel.endswith("pom.xml")
                                                                                                          or "/dto/" in rel or "ApiPaths" in rel or rel.endswith("application.yml"))}
    out["shop-backend-v2"] = write_repo(base / "shop-backend-v2", copy)
    return out
