"""Layer catalogue: per-stack extraction and cross-layer links on a small polyglot repository, plus the API."""

import textwrap
from pathlib import Path

import pytest
from app.services.analyzer import classify, graph, ingest, macro, parsers
from app.services.analyzer.catalog import SourceFile, build
from app.services.analyzer.catalog import sql as sqlmod
from app.services.analyzer.catalog.config import mask
from app.services.analyzer.catalog.jvm import describe_cron

FILES = {
    "backend/pom.xml": """
        <project><modelVersion>4.0.0</modelVersion><groupId>com.acme</groupId><artifactId>shop</artifactId>
        <dependencies>
          <dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-web</artifactId></dependency>
          <dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-data-jpa</artifactId></dependency>
          <dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-security</artifactId></dependency>
          <dependency><groupId>com.oracle.database.jdbc</groupId><artifactId>ojdbc11</artifactId></dependency>
          <dependency><groupId>org.mariadb.jdbc</groupId><artifactId>mariadb-java-client</artifactId></dependency>
          <dependency><groupId>org.springframework.kafka</groupId><artifactId>spring-kafka</artifactId></dependency>
        </dependencies></project>
    """,
    "backend/src/main/resources/application.yml": """
        server:
          servlet:
            context-path: /shop
        spring:
          datasource:
            url: ${DB_URL:jdbc:oracle:thin:@//db:1521/SHOP}
            username: shop
            password: s3cret
          jpa:
            hibernate:
              ddl-auto: validate
        shop:
          orders:
            cleanup-cron: "0 30 2 * * *"
            topic: order-events
    """,
    "backend/src/main/resources/application-maria.properties": "spring.datasource.url=jdbc:mariadb://maria:3306/shop\nspring.datasource.password=${MARIA_PASSWORD}\n",
    "backend/src/main/java/com/acme/shop/api/ApiPaths.java": """
        package com.acme.shop.api;
        public final class ApiPaths {
            public static final String ORDERS = "/api/orders";
            public static final String VIEW_FORM = "orders/orderForm";
        }
    """,
    "backend/src/main/java/com/acme/shop/api/OrderController.java": """
        package com.acme.shop.api;

        import com.acme.shop.service.OrderService;
        import com.acme.shop.api.dto.CreateOrderRequest;
        import com.acme.shop.api.dto.OrderResponse;
        import jakarta.validation.Valid;
        import org.springframework.http.ResponseEntity;
        import org.springframework.security.access.prepost.PreAuthorize;
        import org.springframework.web.bind.annotation.*;

        @RestController
        @RequestMapping(ApiPaths.ORDERS)
        public class OrderController {
            private final OrderService orderService;

            public OrderController(OrderService orderService) { this.orderService = orderService; }

            @PostMapping
            @PreAuthorize("hasRole('CLERK')")
            public ResponseEntity<OrderResponse> create(@Valid @RequestBody CreateOrderRequest request) {
                if (request.getQuantity() > 100) {
                    throw new OrderLimitException("Quantity above 100 needs approval");
                }
                return ResponseEntity.status(HttpStatus.CREATED).body(orderService.place(request));
            }

            @GetMapping("/{orderId}")
            public OrderResponse get(@PathVariable("orderId") long orderId, @RequestParam(name = "full", required = false) Boolean full) {
                return orderService.find(orderId);
            }
        }
    """,
    "backend/src/main/java/com/acme/shop/api/OrderPageController.java": """
        package com.acme.shop.api;

        import org.springframework.stereotype.Controller;
        import org.springframework.validation.BindingResult;
        import org.springframework.web.bind.annotation.*;

        @Controller
        public class OrderPageController {
            @GetMapping("/orders/new")
            public String form() { return ApiPaths.VIEW_FORM; }

            @PostMapping("/orders/new")
            public String submit(@Valid CreateOrderRequest form, BindingResult result) {
                if (result.hasErrors()) {
                    return "orders/orderForm";
                }
                return "redirect:/orders/new";
            }
        }
    """,
    "backend/src/main/java/com/acme/shop/api/dto/CreateOrderRequest.java": """
        package com.acme.shop.api.dto;
        import jakarta.validation.constraints.*;
        public class CreateOrderRequest {
            @NotBlank(message = "Customer is required") private String customerCode;
            @Min(1) @Max(1000) private int quantity;
            @Email private String contactEmail;
            public int getQuantity() { return quantity; }
        }
    """,
    "backend/src/main/java/com/acme/shop/api/dto/OrderResponse.java": """
        package com.acme.shop.api.dto;
        public record OrderResponse(long id, String status, java.math.BigDecimal total) {}
    """,
    "backend/src/main/java/com/acme/shop/api/OrderLimitException.java": """
        package com.acme.shop.api;
        import org.springframework.http.HttpStatus;
        import org.springframework.web.bind.annotation.ResponseStatus;
        @ResponseStatus(HttpStatus.UNPROCESSABLE_ENTITY)
        public class OrderLimitException extends RuntimeException {
            public OrderLimitException(String message) { super(message); }
        }
    """,
    "backend/src/main/java/com/acme/shop/service/OrderService.java": """
        package com.acme.shop.service;

        import com.acme.shop.data.OrderRepository;
        import org.springframework.kafka.core.KafkaTemplate;
        import org.springframework.scheduling.annotation.Scheduled;
        import org.springframework.stereotype.Service;
        import org.springframework.transaction.annotation.Transactional;

        @Service
        public class OrderService {
            private final OrderRepository orders;
            private final KafkaTemplate<String, String> kafkaTemplate;

            public OrderService(OrderRepository orders, KafkaTemplate<String, String> kafkaTemplate) {
                this.orders = orders;
                this.kafkaTemplate = kafkaTemplate;
            }

            @Transactional
            public OrderResponse place(CreateOrderRequest request) {
                Assert.isTrue(request.getQuantity() > 0, "Quantity must be positive");
                OrderEntity saved = orders.save(new OrderEntity());
                kafkaTemplate.send("order-events", saved.toString());
                return null;
            }

            @Transactional(readOnly = true)
            public OrderResponse find(long id) {
                return orders.findById(id).orElseThrow(() -> new IllegalArgumentException("Unknown order"));
            }

            @Scheduled(cron = "${shop.orders.cleanup-cron}")
            public void purgeCancelled() {
                orders.deleteByStatus("CANCELLED");
            }
        }
    """,
    "backend/src/main/java/com/acme/shop/service/OrderEventsListener.java": """
        package com.acme.shop.service;
        import org.springframework.kafka.annotation.KafkaListener;
        import org.springframework.stereotype.Component;
        @Component
        public class OrderEventsListener {
            @KafkaListener(topics = "order-events", groupId = "audit")
            public void onEvent(String payload) { }
        }
    """,
    "backend/src/main/java/com/acme/shop/data/OrderEntity.java": """
        package com.acme.shop.data;
        import jakarta.persistence.*;
        @Entity
        @Table(name = "ORDERS")
        public class OrderEntity {
            @Id @GeneratedValue(strategy = GenerationType.SEQUENCE) private Long id;
            @Column(name = "CUSTOMER_CODE", nullable = false, length = 20) private String customerCode;
            @Column(name = "STATUS") private String status;
            @ManyToOne @JoinColumn(name = "PRODUCT_ID") private ProductEntity product;
        }
    """,
    "backend/src/main/java/com/acme/shop/data/ProductEntity.java": """
        package com.acme.shop.data;
        import jakarta.persistence.*;
        @Entity
        @Table(name = "PRODUCTS")
        public class ProductEntity {
            @Id private Long id;
            private String name;
        }
    """,
    "backend/src/main/java/com/acme/shop/data/OrderRepository.java": """
        package com.acme.shop.data;
        import org.springframework.data.jpa.repository.*;
        import java.util.List;
        public interface OrderRepository extends JpaRepository<OrderEntity, Long> {
            List<OrderEntity> findByCustomerCodeOrderByIdDesc(String customerCode);
            @Modifying
            @Query("DELETE FROM OrderEntity o WHERE o.status = :status")
            int deleteByStatus(@Param("status") String status);
            @Query(value = "SELECT * FROM ORDERS WHERE STATUS = 'OPEN'", nativeQuery = true)
            List<OrderEntity> openOrders();
        }
    """,
    "backend/src/main/java/com/acme/shop/config/SecurityConfig.java": """
        package com.acme.shop.config;
        import org.springframework.context.annotation.*;
        import org.springframework.security.config.annotation.web.builders.HttpSecurity;
        import org.springframework.security.config.annotation.web.configuration.EnableWebSecurity;
        import org.springframework.security.web.SecurityFilterChain;
        @Configuration
        @EnableWebSecurity
        public class SecurityConfig {
            @Bean
            public SecurityFilterChain filterChain(HttpSecurity http) throws Exception {
                http.authorizeHttpRequests(auth -> auth
                        .requestMatchers("/orders/**").permitAll()
                        .requestMatchers(HttpMethod.POST, "/api/orders/**").hasRole("CLERK")
                        .anyRequest().authenticated())
                    .httpBasic(Customizer.withDefaults())
                    .csrf(AbstractHttpConfigurer::disable);
                return http.build();
            }
            @Bean
            public PasswordEncoder passwordEncoder() { return new BCryptPasswordEncoder(); }
        }
    """,
    "backend/src/main/resources/db/oracle/V1__orders.sql": """
        CREATE TABLE PRODUCTS (
          ID NUMBER(19) PRIMARY KEY,
          NAME VARCHAR2(100 CHAR) NOT NULL
        );
        CREATE TABLE ORDERS (
          ID NUMBER(19) NOT NULL,
          CUSTOMER_CODE VARCHAR2(20) NOT NULL,
          STATUS VARCHAR2(10) DEFAULT 'OPEN',
          PRODUCT_ID NUMBER(19),
          CONSTRAINT PK_ORDERS PRIMARY KEY (ID),
          CONSTRAINT FK_ORD_PROD FOREIGN KEY (PRODUCT_ID) REFERENCES PRODUCTS (ID)
        );
        COMMENT ON COLUMN ORDERS.STATUS IS 'OPEN, PAID or CANCELLED';
        CREATE SEQUENCE ORDER_SEQ START WITH 1;
        CREATE OR REPLACE PACKAGE BODY ORDER_PKG AS
          PROCEDURE CLOSE_ORDER(P_ID IN NUMBER) IS
          BEGIN
            UPDATE ORDERS SET STATUS = 'CLOSED' WHERE ID = P_ID;
          END CLOSE_ORDER;
        END ORDER_PKG;
        /
    """,
    "backend/src/main/resources/db/maria/audit.sql": """
        CREATE TABLE `order_audit` (
          `id` BIGINT NOT NULL AUTO_INCREMENT,
          `order_id` BIGINT NOT NULL COMMENT 'ORDERS.ID',
          PRIMARY KEY (`id`)
        ) ENGINE=InnoDB;
    """,
    "backend/src/main/resources/mapper/ReportMapper.xml": """
        <?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE mapper PUBLIC "-//mybatis.org//DTD Mapper 3.0//EN" "http://mybatis.org/dtd/mybatis-3-mapper.dtd">
        <mapper namespace="com.acme.shop.data.ReportMapper">
          <select id="dailyTotals" resultType="map">
            SELECT o.STATUS, COUNT(*) FROM ORDERS o JOIN PRODUCTS p ON p.ID = o.PRODUCT_ID WHERE o.CUSTOMER_CODE = #{code} GROUP BY o.STATUS
          </select>
        </mapper>
    """,
    "backend/src/main/resources/templates/orders/orderForm.html": """
        <html xmlns:th="http://www.thymeleaf.org"><body>
          <h2>New order</h2>
          <form th:object="${form}" method="post">
            <input th:field="*{customerCode}" type="text" required maxlength="20"/>
            <input th:field="*{quantity}" type="number"/>
            <button type="submit">Place order</button>
          </form>
        </body></html>
    """,
    "web/package.json": '{"name": "shop-web", "dependencies": {"react": "18.3.1", "react-router-dom": "6.26.0", "axios": "1.7.0"}}',
    "web/src/App.tsx": """
        import { Routes, Route } from "react-router-dom";
        import OrderPage from "./pages/OrderPage";
        export default function App() {
          return (<Routes><Route path="/orders/:id" element={<OrderPage />} /></Routes>);
        }
    """,
    "web/src/api/orders.ts": """
        import axios from "axios";
        const api = axios.create({ baseURL: "/shop" });
        export function createOrder(body: CreateOrder) { return api.post("/api/orders", body); }
        export async function getOrder(id: string) { return api.get(`/api/orders/${id}`); }
    """,
    "web/src/pages/OrderPage.tsx": """
        import { useForm } from "react-hook-form";
        import { createOrder, getOrder } from "../api/orders";
        export default function OrderPage() {
          const { register, handleSubmit } = useForm();
          return (
            <form onSubmit={handleSubmit((v) => createOrder(v))}>
              <h1>Order</h1>
              <input {...register("customerCode", { required: "Customer is required", maxLength: 20 })} placeholder="Customer" />
              <input type="number" {...register("quantity", { min: 1 })} />
              <button type="submit" onClick={() => getOrder("1")}>Save order</button>
            </form>
          );
        }
    """,
    "mobile/app/build.gradle.kts": 'dependencies { implementation("androidx.room:room-runtime:2.6.1"); implementation("com.squareup.retrofit2:retrofit:2.11.0") }\nandroid { defaultConfig { buildConfigField("String", "API_URL", "\\"https://api.acme.com/shop/\\"") } }\n',
    "mobile/app/src/main/AndroidManifest.xml": """
        <manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.acme.mobile">
          <uses-permission android:name="android.permission.INTERNET"/>
          <application android:name=".ShopApp">
            <activity android:name=".MainActivity" android:exported="true">
              <intent-filter><action android:name="android.intent.action.MAIN"/><category android:name="android.intent.category.LAUNCHER"/></intent-filter>
            </activity>
          </application>
        </manifest>
    """,
    "mobile/app/src/main/java/com/acme/mobile/ShopApi.kt": """
        package com.acme.mobile
        import retrofit2.http.*
        interface ShopApi {
            @GET("api/orders/{orderId}")
            suspend fun order(@Path("orderId") id: Long): OrderDto

            @POST("api/orders")
            suspend fun create(@Body body: NewOrder): OrderDto
        }
        data class NewOrder(val customerCode: String, val quantity: Int)
        data class OrderDto(val id: Long, val status: String?)
    """,
    "mobile/app/src/main/java/com/acme/mobile/OrderRepository.kt": """
        package com.acme.mobile
        class OrderRepository @Inject constructor(private val api: ShopApi, private val dao: CachedOrderDao) {
            suspend fun load(id: Long) = api.order(id)
            suspend fun place(order: NewOrder) { dao.insert(api.create(order)) }
        }
    """,
    "mobile/app/src/main/java/com/acme/mobile/OrderViewModel.kt": """
        package com.acme.mobile
        @HiltViewModel
        class OrderViewModel @Inject constructor(private val repository: OrderRepository) : ViewModel() {
            val state = MutableStateFlow("")
            fun submit(code: String) { viewModelScope.launch { repository.place(NewOrder(code, 1)) } }
        }
    """,
    "mobile/app/src/main/java/com/acme/mobile/OrderScreen.kt": """
        package com.acme.mobile
        @Composable
        fun OrderScreen(viewModel: OrderViewModel = hiltViewModel()) {
            var code by remember { mutableStateOf("") }
            Column {
                OutlinedTextField(value = code, onValueChange = { code = it }, label = { Text("Customer code") }, isError = code.isBlank())
                Button(onClick = { viewModel.submit(code) }, enabled = code.isNotBlank()) { Text("Place order") }
            }
        }
    """,
    "mobile/app/src/main/java/com/acme/mobile/CachedOrder.kt": """
        package com.acme.mobile
        import androidx.room.*
        @Entity(
            tableName = "cached_orders"
        )
        data class CachedOrder(@PrimaryKey val id: Long, @ColumnInfo(name = "status_code") val status: String?)

        @Dao
        interface CachedOrderDao {
            @Query("SELECT * FROM cached_orders WHERE id = :id")
            suspend fun get(id: Long): CachedOrder?
            @Insert
            suspend fun insert(order: OrderDto)
        }
    """,
    "ios/Shop/OrderView.swift": """
        import SwiftUI
        struct OrderView: View {
            @StateObject private var vm = OrderStore()
            @State private var code = ""
            var body: some View {
                NavigationStack {
                    Form {
                        TextField("Customer code", text: $code)
                        Button("Place order") { Task { await vm.place(code) } }
                            .disabled(code.isEmpty)
                    }
                    .navigationTitle("New order")
                }
            }
        }
    """,
    "ios/Shop/OrderStore.swift": """
        import Foundation
        @MainActor
        final class OrderStore: ObservableObject {
            @Published var status: String = ""
            func place(_ code: String) async {
                var request = URLRequest(url: URL(string: "https://api.acme.com/shop/api/orders")!)
                request.httpMethod = "POST"
                _ = try? await URLSession.shared.data(for: request)
            }
        }
    """,
}


def _write(root: Path) -> Path:
    for rel, text in FILES.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8")
    return root


@pytest.fixture(scope="module")
def polyglot(tmp_path_factory) -> Path:
    return _write(tmp_path_factory.mktemp("polyglot"))


@pytest.fixture(scope="module")
def cat(polyglot):
    ws = ingest.walk(polyglot, lambda: False, lambda n: None)
    m = macro.discover(ws.files)
    parsed = {f.path: parsers.parse(f.text or "", f.language) for f in ws.text_files if f.category in ("code", "markup", "script")}
    g = graph.build(parsed, {f.path: f.language for f in ws.text_files}, m.path_aliases, {})
    files = []
    for f in ws.files:
        if f.text is None:
            continue
        cls = classify.classify(f.path, f.language, f.category, f.text, parsed.get(f.path))
        container = max((c.path for c in m.containers if c.path and f.path.startswith(c.path + "/")), key=len, default="")
        files.append(SourceFile(f.path, f.language, f.category, cls.layer, cls.role, cls.is_test, container, f.text))
    deps = [dict(d, container=c.name, manifest=c.manifest, ecosystem=c.ecosystem) for c in m.containers for d in c.dependencies]
    result = build(files, deps, [(e.source, e.target, e.kind) for e in g.edges], [fw["name"] for fw in m.frameworks])
    assert result.extra["stats"]["errors"] == []
    return result


def _links(cat, source=None, target=None, kind=None):
    return [lk for lk in cat.links if (source is None or lk.source == source) and (target is None or lk.target == target) and (kind is None or lk.kind == kind)]


def test_rest_endpoint_details(cat):
    ep = cat.items["api:POST /api/orders"]
    d = ep.details
    assert d["handler"]["class"] == "OrderController" and d["style"] == "REST"
    fields = {f["name"]: f for f in d["request"]["fields"]}
    assert fields["customerCode"]["required"] and any("Customer is required" in v for v in fields["customerCode"]["validations"])
    assert "≥ 1" in fields["quantity"]["validations"] and "valid e-mail address" in fields["contactEmail"]["validations"]
    assert d["request"]["validated"] and {"201", "400", "422"} <= {str(c) for c in d["status_codes"]}
    assert d["security"]["roles"] == ["CLERK"] and d["security"]["http_rule"]["access"] == "hasRole"
    assert any(r["outcome"].startswith("throws OrderLimitException") for r in d["rules"])
    assert d["errors"][0]["status"] == "422"
    assert {f["name"] for f in d["response"]["fields"]} == {"id", "status", "total"}
    assert d["url"] == "/shop/api/orders"
    get = cat.items["api:GET /api/orders/{orderId}"].details
    assert [(p["in"], p["name"], p["required"]) for p in get["params"]] == [("path", "orderId", True), ("query", "full", False)]


def test_service_job_listener_and_topic(cat):
    svc = cat.items["svc:OrderService"]
    place = next(m for m in svc.details["methods"] if m["name"] == "place")
    assert place["transactional"] and place["calls"][0] == "OrderRepository.save"
    assert any(r["kind"] == "assert" and "Quantity must be positive" in r["outcome"] for r in place["rules"])
    assert _links(cat, "api:POST /api/orders", "svc:OrderService", "calls")
    job = cat.items["job:OrderService.purgeCancelled"]
    assert job.details["schedule"]["cron"] == "0 30 2 * * *" and job.details["human"] == "at 02:30 every day"
    assert _links(cat, "svc:OrderService", "topic:kafka:order-events", "publishes")
    assert _links(cat, "topic:kafka:order-events", "listener:OrderEventsListener.onEvent", "delivers to")


def test_tables_merge_ddl_and_jpa(cat):
    orders = cat.items["table:orders"].details
    columns = {c["name"]: c for c in orders["columns"]}
    assert columns["CUSTOMER_CODE"]["type"] == "VARCHAR2(20)" and columns["CUSTOMER_CODE"]["field"] == "customerCode"
    assert columns["STATUS"]["comment"] == "OPEN, PAID or CANCELLED" and columns["ID"]["pk"]
    assert orders["entity"] == "OrderEntity" and "JPA @Entity" in orders["sources"]
    assert columns["PRODUCT_ID"]["references"]["table"] == "PRODUCTS"
    assert _links(cat, "table:orders", "table:products", "references")
    assert set(orders["crud"]) >= {"C", "R", "D"}
    assert any("endpoint:POST /api/orders" in names for names in orders["crud"].values())
    assert cat.items["table:order_audit"].details["columns"][1]["comment"] == "ORDERS.ID"


def test_queries_procedures_and_databases(cat):
    derived = cat.items["query:OrderRepository.findByCustomerCodeOrderByIdDesc"].details
    assert derived["dialect"] == "derived" and "ORDER BY id DESC" in derived["sql"] and "customer_code" in derived["sql"].lower()
    jpql = cat.items["query:OrderRepository.deleteByStatus"].details
    assert jpql["dialect"] == "JPQL" and jpql["tables"] == {"orders": ["D"]} and jpql["modifying"]
    assert cat.items["query:OrderRepository.openOrders"].details["tables"] == {"orders": ["R"]}
    assert cat.items["query:ReportMapper.dailyTotals"].details["tables"] == {"orders": ["R"], "products": ["R"]}
    package = cat.items["routine:order_pkg"].details
    assert package["members"] == ["CLOSE_ORDER"] and package["tables"] == {"orders": ["U"]}
    engines = {i.name for i in cat.by_kind("database")}
    assert {"Oracle", "MariaDB", "SQLite (Android Room)"} <= engines
    oracle = cat.items["db:oracle"].details
    assert oracle["connections"][0]["url"] == "${DB_URL:jdbc:oracle:thin:@//db:1521/SHOP}"
    assert any(s["name"] == "ORDER_SEQ" for s in oracle["sequences"])


def test_mvc_template_form_flow(cat):
    screen = next(i for i in cat.by_kind("screen") if i.path.endswith("orderForm.html"))
    assert _links(cat, "api:GET /orders/new", screen.key, "renders")
    assert _links(cat, screen.key, "api:POST /orders/new", "submits")
    field = next(f for f in screen.details["fields"] if f["name"] == "customerCode")
    assert field["required"] and "required, not blank — \"Customer is required\"" in field["server_validations"]
    rule = cat.items["api:POST /orders/new"].details["rules"][0]
    assert rule["condition"] == "result.hasErrors()" and rule["outcome"] == "renders view orders/orderForm"
    assert 302 in cat.items["api:POST /orders/new"].details["status_codes"]


def test_react_screen_traces_to_spring(cat):
    screen = cat.items["ui:web/src/pages/OrderPage.tsx"]
    assert screen.details["routes"] == ["/orders/{id}"]
    fields = {f["name"]: f for f in screen.details["fields"]}
    assert fields["customerCode"]["required"] and "maxLength 20" in fields["customerCode"]["validations"]
    calls = {(c["method"], c["url"]) for c in screen.details["backend_calls"]}
    assert ("POST", "/api/orders") in calls and ("GET", "/api/orders/${id}") in calls
    assert _links(cat, screen.key, "api:POST /api/orders", "calls") and _links(cat, screen.key, "api:GET /api/orders/{orderId}", "calls")


def test_android_screen_to_backend(cat):
    screen = cat.items["ui:OrderScreen"].details
    assert screen["fields"][0]["label"] == "Customer code" and screen["actions"][0]["label"] == "Place order"
    assert any(r["outcome"] == 'enables "Place order"' for r in screen["rules"])
    assert ("POST", "/api/orders") in {(c["method"], c["url"]) for c in screen["backend_calls"]}
    client = cat.items["client:POST /api/orders"]
    assert client.framework == "Retrofit" and client.details["endpoint"] == "api:POST /api/orders"
    cached = cat.items["table:cached_orders"].details
    assert cached["orm"] == "Room" and {c["name"] for c in cached["columns"]} == {"id", "status_code"}
    assert cat.items["query:CachedOrderDao.get"].details["tables"] == {"cached_orders": ["R"]}
    manifest = next(iter(cat.by_kind("manifest"))).details
    assert manifest["permissions"] == ["INTERNET"] and manifest["components"][0]["launcher"]


def test_ios_screen_and_client(cat):
    screen = cat.items["ui:OrderView"].details
    assert screen["title"] == "New order" and screen["fields"][0]["label"] == "Customer code" and screen["view_models"] == ["OrderStore"]
    client = cat.items["client:POST https://api.acme.com/shop/api/orders"]
    assert client.details["endpoint"] == "api:POST /api/orders"


def test_shared_layer(cat):
    config = cat.items["config:spring.datasource.password"].details
    assert config["secret"] and next(v for v in config["values"] if v["profile"] == "default")["value"] == "••••••"
    assert next(v for v in config["values"] if v["profile"] == "maria")["value"] == "${MARIA_PASSWORD}"
    assert cat.items["config:BuildConfig.API_URL"].details["values"][0]["value"] == "https://api.acme.com/shop/"
    sec = cat.items["sec:SecurityConfig"].details
    assert {"HTTP Basic", "CSRF disabled"} <= set(sec["mechanisms"]) and sec["password_encoder"] == ["BCryptPasswordEncoder"]
    assert [r["access"] for r in sec["rules"]] == ["permitAll", "hasRole", "authenticated"]
    assert cat.items["exc:OrderLimitException"].details["status"] == 422
    assert "dto:CreateOrderRequest" in cat.items and _links(cat, "api:POST /api/orders", "dto:CreateOrderRequest", "request")


def test_sql_and_helpers():
    assert sqlmod.tables_in("INSERT INTO a (x) SELECT x FROM b JOIN c ON c.id=b.id") == {"a": ["C"], "b": ["R"], "c": ["R"]}
    assert sqlmod.derived_query("countByStatusIn", "orders")["sql"] == "SELECT COUNT(*) FROM orders WHERE status IN (:status)"
    statements = sqlmod.split_statements("DELIMITER //\nCREATE PROCEDURE p() BEGIN SELECT 1; SELECT 2; END //\nDELIMITER ;\nSELECT 3;")
    assert len(statements) == 2 and statements[0][1].startswith("CREATE PROCEDURE")
    assert describe_cron("0 0/15 * * * MON-FRI") == "every 15 minutes on Monday-Friday"
    assert mask("db.url", "jdbc:postgresql://user:pw@host/db")[0] == "jdbc:postgresql://user:••••@host/db"


def test_catalog_api(client, admin, polyglot):
    response = client.post("/api/repositories", json={"source_type": "local", "path": str(polyglot), "name": "polyglot"}, headers=admin)
    analysis_id = response.json()["started_analysis"]["analysis_id"]
    assert client.get(f"/api/analyses/{analysis_id}", headers=admin).json()["status"] == "completed"
    summary = client.get(f"/api/analyses/{analysis_id}/catalog", headers=admin).json()
    assert summary["built"] and summary["layers"]["api"]["total"] >= 4 and summary["layers"]["data"]["total"] >= 8
    listing = client.get(f"/api/analyses/{analysis_id}/catalog/items", params={"layer": "api", "kind": "endpoint"}, headers=admin).json()
    post = next(i for i in listing["items"] if i["name"] == "POST /api/orders")
    assert post["badge"]["method"] == "POST" and post["badge"]["secured"]
    detail = client.get(f"/api/analyses/{analysis_id}/catalog/items/{post['item_id']}", headers=admin).json()
    assert any(lk["relation"] == "calls" and lk["name"] == "OrderService" for lk in detail["links_out"]), detail["links_out"]
    assert any(lk["relation"] == "calls" and lk["layer"] == "ui" for lk in detail["links_in"]), detail["links_in"]
    trace = client.get(f"/api/analyses/{analysis_id}/catalog/items/{post['item_id']}/trace", headers=admin).json()
    names = {n["name"] for n in trace["nodes"]}
    assert {"OrderService", "ORDERS", "OrderPage"} <= names and trace["flowchart"].startswith("flowchart LR") and "sequenceDiagram" in trace["sequence"]
    erd = client.get(f"/api/analyses/{analysis_id}/catalog/erd", headers=admin).json()
    assert "PRODUCTS ||--o{ ORDERS" in erd["mermaid"]
    crud = client.get(f"/api/analyses/{analysis_id}/catalog/crud", headers=admin).json()
    assert any(row["table"] == "ORDERS" and row["cells"] for row in crud["rows"])
    md = client.get(f"/api/analyses/{analysis_id}/catalog/export.md", headers=admin)
    assert md.status_code == 200 and "## API layer" in md.text and "| body |" not in md.text
    xlsx = client.get(f"/api/analyses/{analysis_id}/catalog/export.xlsx", headers=admin)
    assert xlsx.status_code == 200 and xlsx.content[:2] == b"PK"
    # rebuild offline from stored files gives the same inventory
    rebuilt = client.post(f"/api/analyses/{analysis_id}/catalog/rebuild", headers=admin).json()
    assert rebuilt["layers"]["api"]["total"] == summary["layers"]["api"]["total"]
    # chat retrieval sees catalogue facts
    hits = client.get(f"/api/analyses/{analysis_id}/search", params={"q": "ORDERS"}, headers=admin)
    assert hits.status_code == 200
    viewer = __import__("tests.conftest", fromlist=["login"]).login(client, "viewer", "ViewerPass123!")
    assert client.post(f"/api/analyses/{analysis_id}/catalog/rebuild", headers=viewer).status_code == 403
    assert client.get(f"/api/analyses/{analysis_id}/catalog", headers=viewer).status_code == 200


def test_python_fastapi_catalog(client, admin, sample_repo):
    response = client.post(
        "/api/repositories",
        json={"source_type": "local", "path": str(sample_repo), "name": "orders-fastapi"},
        headers=admin,
    )
    analysis_id = response.json()["started_analysis"]["analysis_id"]
    assert client.get(f"/api/analyses/{analysis_id}", headers=admin).json()["status"] == "completed"

    summary = client.get(f"/api/analyses/{analysis_id}/catalog", headers=admin).json()
    assert summary["built"]
    assert summary["layers"]["api"]["total"] >= 1
    assert summary["layers"]["service"]["total"] >= 1
    assert summary["layers"]["data"]["total"] >= 2

    api_items = client.get(
        f"/api/analyses/{analysis_id}/catalog/items",
        params={"layer": "api", "kind": "endpoint"},
        headers=admin,
    ).json()["items"]
    ep = next(i for i in api_items if i["name"] == "GET /orders/{order_id}")
    assert ep["framework"] == "FastAPI"
    assert ep["badge"]["method"] == "GET"

    detail = client.get(f"/api/analyses/{analysis_id}/catalog/items/{ep['item_id']}", headers=admin).json()
    assert any(p["name"] == "order_id" and p["in"] == "path" for p in detail["details"]["params"])
    assert any(lk["relation"] == "calls" and lk["name"] == "OrderService" for lk in detail["links_out"])
    assert any(lk["relation"] == "calls" and lk["layer"] == "ui" for lk in detail["links_in"])

    trace = client.get(f"/api/analyses/{analysis_id}/catalog/items/{ep['item_id']}/trace", headers=admin).json()
    trace_names = {n["name"] for n in trace["nodes"]}
    assert {"GET /orders/{order_id}", "OrderService", "OrderRepository"} <= trace_names


def test_ocp_java_kotlin_swift_html_react_enhancements():
    sources = [
        SourceFile(
            "ocp/template.yaml", "YAML", "config", "infra", "Configuration", False, "",
            textwrap.dedent("""
                apiVersion: template.openshift.io/v1
                kind: Template
                metadata:
                  name: order-service-template
                parameters:
                  - name: APP_REPLICAS
                    value: "3"
                    description: Number of pods
                objects:
                  - apiVersion: v1
                    kind: ConfigMap
                    metadata:
                      name: order-config
                    data:
                      MAX_RETRY: "5"
                      application.properties: |
                        feature.audit.enabled=true
                  - apiVersion: v1
                    kind: Secret
                    metadata:
                      name: order-secret
                    stringData:
                      DB_PASS: supersecret
                  - apiVersion: apps.openshift.io/v1
                    kind: DeploymentConfig
                    metadata:
                      name: order-dc
                    spec:
                      replicas: 3
                      template:
                        spec:
                          containers:
                            - name: app
                              image: registry.acme.io/orders:1.0
                              envFrom:
                                - configMapRef:
                                    name: order-config
                              env:
                                - name: DB_PASS
                                  valueFrom:
                                    secretKeyRef:
                                      name: order-secret
                                      key: DB_PASS
                  - apiVersion: route.openshift.io/v1
                    kind: Route
                    metadata:
                      name: order-route
                    spec:
                      host: orders.apps.ocp.acme.com
                      to:
                        kind: Service
                        name: order-svc
                      tls:
                        termination: edge
                  - apiVersion: v1
                    kind: Service
                    metadata:
                      name: order-svc
                    spec:
                      ports:
                        - port: 8080
                          targetPort: 8080
            """).strip(),
        ),
        SourceFile(
            "src/main/java/com/acme/PaymentsApi.java", "Java", "code", "api", "Controller", False, "",
            textwrap.dedent("""
                package com.acme;
                import org.springframework.web.bind.annotation.*;
                @RequestMapping("/api/payments")
                public interface PaymentsApi {
                    @PostMapping("/{paymentId}/capture")
                    String capture(@PathVariable("paymentId") String id);
                }
            """).strip(),
        ),
        SourceFile(
            "src/main/java/com/acme/PaymentsController.java", "Java", "code", "api", "Controller", False, "",
            textwrap.dedent("""
                package com.acme;
                import org.springframework.web.bind.annotation.RestController;
                @RestController
                public class PaymentsController implements PaymentsApi {
                    @Override
                    public String capture(String id) {
                        String retry = System.getenv("MAX_RETRY");
                        return retry;
                    }
                }
            """).strip(),
        ),
        SourceFile(
            "src/main/java/com/acme/JaxRsResource.java", "Java", "code", "api", "Controller", False, "",
            textwrap.dedent("""
                package com.acme;
                import jakarta.ws.rs.*;
                @Path("/api/invoices")
                public class JaxRsResource {
                    @GET
                    @Path("/{invoiceId}")
                    public String getInvoice(@PathParam("invoiceId") String invoiceId, @QueryParam("currency") String currency) {
                        return invoiceId;
                    }
                }
            """).strip(),
        ),
        SourceFile(
            "web/src/components/BillingTab.tsx", "TypeScript", "code", "ui", "Component", False, "",
            textwrap.dedent("""
                import React from 'react';
                export function BillingTab() {
                    return (
                        <div>
                            <h1>Billing</h1>
                            <button onClick={() => fetch('/api/payments/123/capture', { method: 'POST' })}>Capture</button>
                        </div>
                    );
                }
            """).strip(),
        ),
        SourceFile(
            "portal/index.html", "HTML", "markup", "ui", "Template", False, "",
            textwrap.dedent("""
                <!DOCTYPE html>
                <html>
                <head><title>Customer Portal</title></head>
                <body>
                    <h1>Customer Portal</h1>
                    <a href="details.html">View Details</a>
                    <button id="refreshBtn">Refresh</button>
                </body>
                </html>
            """).strip(),
        ),
        SourceFile(
            "portal/details.html", "HTML", "markup", "ui", "Template", False, "",
            textwrap.dedent("""
                <!DOCTYPE html>
                <html>
                <head><title>Invoice Details</title></head>
                <body>
                    <canvas id="chartCanvas"></canvas>
                    <button id="backBtn">Back</button>
                </body>
                </html>
            """).strip(),
        ),
    ]
    res = build(sources, [], [], ["Spring Boot", "React"])
    assert res.extra["stats"]["errors"] == []
    # 1. OCP config & manifests
    assert "config:APP_REPLICAS" in res.items
    assert "config:MAX_RETRY" in res.items
    assert "config:feature.audit.enabled" in res.items
    assert "config:ocp.route.order-route.host" in res.items
    assert res.items["config:DB_PASS"].details["secret"]
    assert "manifest:DeploymentConfig:order-dc" in res.items
    assert "manifest:Route:order-route" in res.items
    assert _links(res, "manifest:Route:order-route", "manifest:Service:order-svc", "routes to")
    assert _links(res, "manifest:DeploymentConfig:order-dc", "manifest:ConfigMap:order-config", "uses")
    # 2. Spring interface-inherited controller + config usage link + JAX-RS endpoint
    assert "api:POST /api/payments/{paymentId}/capture" in res.items
    assert _links(res, "api:POST /api/payments/{paymentId}/capture", "config:MAX_RETRY", "reads config")
    assert "api:GET /api/invoices/{invoiceId}" in res.items
    # 3. React *Tab.tsx screen & HTML screens
    assert "ui:web/src/components/BillingTab.tsx" in res.items
    assert _links(res, "ui:web/src/components/BillingTab.tsx", "api:POST /api/payments/{paymentId}/capture", "calls")
    assert "ui:portal/index.html" in res.items and "ui:portal/details.html" in res.items
    assert _links(res, "ui:portal/index.html", "ui:portal/details.html", "navigates to")


