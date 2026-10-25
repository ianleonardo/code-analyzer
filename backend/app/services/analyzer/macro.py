"""Stage 2: macro discovery from manifests, configuration and documentation.

Frameworks come only from exact dependency keys in manifests (never guessed from variable names). Each folder with a
primary manifest becomes a Container; documentation claims are kept as hypotheses and checked against manifests.
"""

from __future__ import annotations

import json
import re
import tomllib
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import PurePosixPath

import yaml

from app.services.analyzer.ingest import FileEntry

# dependency key (exact or prefix*) -> (display name, category)
FRAMEWORKS: dict[str, dict[str, tuple[str, str]]] = {
    "npm": {
        "next": ("Next.js", "web"), "nuxt": ("Nuxt", "web"), "react": ("React", "ui"), "react-dom": ("React", "ui"),
        "vue": ("Vue", "ui"), "@angular/core": ("Angular", "ui"), "svelte": ("Svelte", "ui"), "@sveltejs/kit": ("SvelteKit", "web"),
        "solid-js": ("SolidJS", "ui"), "react-native": ("React Native", "ui"), "expo": ("Expo", "ui"), "electron": ("Electron", "ui"),
        "express": ("Express", "web"), "@nestjs/core": ("NestJS", "web"), "fastify": ("Fastify", "web"), "koa": ("Koa", "web"),
        "hapi": ("hapi", "web"), "@hapi/hapi": ("hapi", "web"), "apollo-server": ("Apollo Server", "web"), "@apollo/server": ("Apollo Server", "web"),
        "graphql": ("GraphQL", "web"), "socket.io": ("Socket.IO", "web"), "@trpc/server": ("tRPC", "web"),
        "prisma": ("Prisma", "orm"), "@prisma/client": ("Prisma", "orm"), "typeorm": ("TypeORM", "orm"), "sequelize": ("Sequelize", "orm"),
        "mongoose": ("Mongoose", "orm"), "drizzle-orm": ("Drizzle", "orm"), "knex": ("Knex", "orm"), "mikro-orm": ("MikroORM", "orm"),
        "pg": ("PostgreSQL", "database"), "mysql2": ("MySQL", "database"), "mysql": ("MySQL", "database"), "mongodb": ("MongoDB", "database"),
        "redis": ("Redis", "cache"), "ioredis": ("Redis", "cache"), "oracledb": ("Oracle", "database"), "mssql": ("SQL Server", "database"),
        "sqlite3": ("SQLite", "database"), "better-sqlite3": ("SQLite", "database"), "duckdb": ("DuckDB", "database"),
        "kafkajs": ("Kafka", "messaging"), "amqplib": ("RabbitMQ", "messaging"), "@aws-sdk/client-sqs": ("AWS SQS", "messaging"),
        "bullmq": ("BullMQ", "messaging"), "bull": ("Bull", "messaging"), "@google-cloud/pubsub": ("GCP Pub/Sub", "messaging"),
        "aws-sdk": ("AWS SDK", "cloud"), "@aws-sdk/*": ("AWS SDK", "cloud"), "@google-cloud/*": ("Google Cloud SDK", "cloud"), "@azure/*": ("Azure SDK", "cloud"),
        "axios": ("Axios", "http"), "jest": ("Jest", "test"), "vitest": ("Vitest", "test"), "mocha": ("Mocha", "test"),
        "cypress": ("Cypress", "test"), "@playwright/test": ("Playwright", "test"), "tailwindcss": ("Tailwind CSS", "ui"),
        "vite": ("Vite", "build"), "webpack": ("webpack", "build"), "typescript": ("TypeScript", "build"), "zod": ("Zod", "validation"),
        "joi": ("Joi", "validation"), "yup": ("Yup", "validation"), "class-validator": ("class-validator", "validation"),
        "passport": ("Passport", "security"), "jsonwebtoken": ("JWT", "security"), "next-auth": ("NextAuth", "security"),
        "@reduxjs/toolkit": ("Redux", "ui"), "redux": ("Redux", "ui"), "zustand": ("Zustand", "ui"), "mobx": ("MobX", "ui"),
        "@tanstack/react-query": ("TanStack Query", "ui"), "langchain": ("LangChain", "ai"), "openai": ("OpenAI SDK", "ai"),
        "@anthropic-ai/sdk": ("Anthropic SDK", "ai"), "serverless": ("Serverless Framework", "cloud"),
    },
    "python": {
        "fastapi": ("FastAPI", "web"), "django": ("Django", "web"), "flask": ("Flask", "web"), "starlette": ("Starlette", "web"),
        "tornado": ("Tornado", "web"), "aiohttp": ("aiohttp", "web"), "sanic": ("Sanic", "web"), "djangorestframework": ("Django REST Framework", "web"),
        "streamlit": ("Streamlit", "ui"), "gradio": ("Gradio", "ui"), "dash": ("Dash", "ui"),
        "click": ("Click", "cli"), "typer": ("Typer", "cli"), "celery": ("Celery", "messaging"), "dramatiq": ("Dramatiq", "messaging"),
        "pydantic": ("Pydantic", "validation"), "sqlalchemy": ("SQLAlchemy", "orm"), "alembic": ("Alembic", "orm"), "peewee": ("Peewee", "orm"),
        "tortoise-orm": ("Tortoise ORM", "orm"), "sqlmodel": ("SQLModel", "orm"), "psycopg2": ("PostgreSQL", "database"),
        "psycopg2-binary": ("PostgreSQL", "database"), "psycopg": ("PostgreSQL", "database"), "asyncpg": ("PostgreSQL", "database"),
        "pymysql": ("MySQL", "database"), "mysqlclient": ("MySQL", "database"), "pymongo": ("MongoDB", "database"), "motor": ("MongoDB", "database"),
        "redis": ("Redis", "cache"), "cx-oracle": ("Oracle", "database"), "oracledb": ("Oracle", "database"), "duckdb": ("DuckDB", "database"),
        "pyodbc": ("ODBC", "database"), "ibm-db": ("Db2", "database"), "kafka-python": ("Kafka", "messaging"),
        "confluent-kafka": ("Kafka", "messaging"), "pika": ("RabbitMQ", "messaging"), "boto3": ("AWS SDK", "cloud"),
        "google-cloud-*": ("Google Cloud SDK", "cloud"), "azure-*": ("Azure SDK", "cloud"), "requests": ("Requests", "http"),
        "httpx": ("HTTPX", "http"), "pytest": ("pytest", "test"), "pandas": ("pandas", "data"), "numpy": ("NumPy", "data"),
        "polars": ("Polars", "data"), "pyspark": ("PySpark", "data"), "scikit-learn": ("scikit-learn", "ai"), "torch": ("PyTorch", "ai"),
        "tensorflow": ("TensorFlow", "ai"), "transformers": ("Hugging Face Transformers", "ai"), "langchain": ("LangChain", "ai"),
        "langchain-*": ("LangChain", "ai"), "openai": ("OpenAI SDK", "ai"), "anthropic": ("Anthropic SDK", "ai"),
        "llama-index": ("LlamaIndex", "ai"), "uvicorn": ("Uvicorn", "web"), "gunicorn": ("Gunicorn", "web"),
        "pyjwt": ("JWT", "security"), "ldap3": ("LDAP", "security"), "airflow": ("Apache Airflow", "data"), "apache-airflow": ("Apache Airflow", "data"),
    },
    "maven": {
        "spring-boot-starter-web": ("Spring Boot (Web MVC)", "web"), "spring-boot-starter-webflux": ("Spring WebFlux", "web"),
        "spring-boot-starter": ("Spring Boot", "web"), "spring-boot-starter-*": ("Spring Boot", "web"),
        "spring-boot-starter-data-jpa": ("Spring Data JPA", "orm"), "spring-boot-starter-security": ("Spring Security", "security"),
        "spring-kafka": ("Kafka", "messaging"), "kafka-clients": ("Kafka", "messaging"), "spring-boot-starter-amqp": ("RabbitMQ", "messaging"),
        "amqp-client": ("RabbitMQ", "messaging"), "spring-rabbit*": ("RabbitMQ", "messaging"),
        "activemq-*": ("ActiveMQ", "messaging"), "spring-boot-starter-activemq": ("ActiveMQ", "messaging"),
        "artemis-*": ("ActiveMQ Artemis", "messaging"), "spring-boot-starter-artemis": ("ActiveMQ Artemis", "messaging"),
        "com.ibm.mq*": ("IBM MQ", "messaging"), "mq-jms-spring-boot-starter": ("IBM MQ", "messaging"),
        "ibm-mq*": ("IBM MQ", "messaging"), "com.ibm.mq.allclient": ("IBM MQ", "messaging"),
        "spring-jms": ("JMS", "messaging"), "jakarta.jms-api": ("JMS", "messaging"), "javax.jms-api": ("JMS", "messaging"),
        "aws-java-sdk-sqs": ("AWS SQS", "messaging"), "amazon-sqs-java-messaging-lib": ("AWS SQS", "messaging"),
        "camel-kafka*": ("Kafka", "messaging"), "camel-activemq*": ("ActiveMQ", "messaging"),
        "camel-artemis*": ("ActiveMQ Artemis", "messaging"), "camel-rabbitmq*": ("RabbitMQ", "messaging"),
        "camel-amqp*": ("RabbitMQ", "messaging"), "camel-jms*": ("JMS", "messaging"),
        "camel-sjms*": ("JMS", "messaging"), "camel-paho*": ("MQTT", "messaging"),
        "camel-aws2-sqs*": ("AWS SQS", "messaging"), "camel-aws-sqs*": ("AWS SQS", "messaging"),
        "camel-google-pubsub*": ("Google Cloud Pub/Sub", "messaging"), "camel-azure-servicebus*": ("Azure Service Bus", "messaging"),
        "camel-pulsar*": ("Apache Pulsar", "messaging"), "camel-rocketmq*": ("RocketMQ", "messaging"),
        "camel-stomp*": ("ActiveMQ", "messaging"), "camel-ibmmq*": ("IBM MQ", "messaging"),
        "camel-*": ("Apache Camel", "messaging"),
        "spring-cloud-*": ("Spring Cloud", "cloud"), "hibernate-core": ("Hibernate", "orm"), "mybatis": ("MyBatis", "orm"),
        "mybatis-spring-boot-starter": ("MyBatis", "orm"), "micronaut-*": ("Micronaut", "web"), "quarkus-*": ("Quarkus", "web"),
        "jakarta.*": ("Jakarta EE", "web"), "javax.servlet-api": ("Servlet API", "web"), "jersey-*": ("Jersey (JAX-RS)", "web"),
        "postgresql": ("PostgreSQL", "database"), "mysql-connector-java": ("MySQL", "database"), "mysql-connector-j": ("MySQL", "database"),
        "ojdbc*": ("Oracle", "database"), "jt400": ("IBM i (AS/400) DB2", "database"), "db2jcc*": ("Db2", "database"),
        "mssql-jdbc": ("SQL Server", "database"), "h2": ("H2", "database"), "mongodb-driver-*": ("MongoDB", "database"),
        "jedis": ("Redis", "cache"), "lettuce-core": ("Redis", "cache"), "flyway-core": ("Flyway", "orm"), "liquibase-core": ("Liquibase", "orm"),
        "junit-jupiter*": ("JUnit 5", "test"), "junit": ("JUnit", "test"), "mockito-*": ("Mockito", "test"), "testng": ("TestNG", "test"),
        "lombok": ("Lombok", "build"), "jackson-databind": ("Jackson", "data"), "log4j-core": ("Log4j", "logging"), "slf4j-api": ("SLF4J", "logging"),
        "resilience4j-*": ("Resilience4j", "resilience"), "feign-*": ("OpenFeign", "http"), "spring-cloud-starter-openfeign": ("OpenFeign", "http"),
    },
    "go": {
        "github.com/gin-gonic/gin": ("Gin", "web"), "github.com/labstack/echo*": ("Echo", "web"), "github.com/gofiber/fiber*": ("Fiber", "web"),
        "github.com/go-chi/chi*": ("chi", "web"), "github.com/gorilla/mux": ("Gorilla mux", "web"), "google.golang.org/grpc": ("gRPC", "web"),
        "gorm.io/gorm": ("GORM", "orm"), "github.com/jmoiron/sqlx": ("sqlx", "orm"), "github.com/lib/pq": ("PostgreSQL", "database"),
        "github.com/jackc/pgx*": ("PostgreSQL", "database"), "github.com/go-sql-driver/mysql": ("MySQL", "database"),
        "go.mongodb.org/mongo-driver": ("MongoDB", "database"), "github.com/redis/go-redis*": ("Redis", "cache"), "github.com/go-redis/redis*": ("Redis", "cache"),
        "github.com/spf13/cobra": ("Cobra", "cli"), "github.com/spf13/viper": ("Viper", "config"), "github.com/segmentio/kafka-go": ("Kafka", "messaging"),
        "github.com/confluentinc/confluent-kafka-go*": ("Kafka", "messaging"), "github.com/aws/aws-sdk-go*": ("AWS SDK", "cloud"),
        "github.com/stretchr/testify": ("Testify", "test"), "go.uber.org/zap": ("Zap", "logging"),
    },
    "cargo": {
        "axum": ("Axum", "web"), "actix-web": ("Actix Web", "web"), "rocket": ("Rocket", "web"), "warp": ("warp", "web"),
        "tokio": ("Tokio", "runtime"), "serde": ("Serde", "data"), "diesel": ("Diesel", "orm"), "sqlx": ("SQLx", "orm"),
        "sea-orm": ("SeaORM", "orm"), "clap": ("clap", "cli"), "tonic": ("tonic (gRPC)", "web"), "reqwest": ("reqwest", "http"),
        "redis": ("Redis", "cache"), "rdkafka": ("Kafka", "messaging"), "tauri": ("Tauri", "ui"), "leptos": ("Leptos", "ui"), "yew": ("Yew", "ui"),
    },
    "nuget": {
        "Microsoft.AspNetCore.*": ("ASP.NET Core", "web"), "Microsoft.EntityFrameworkCore*": ("Entity Framework Core", "orm"),
        "Dapper": ("Dapper", "orm"), "MediatR": ("MediatR", "messaging"), "Serilog*": ("Serilog", "logging"), "AutoMapper*": ("AutoMapper", "data"),
        "FluentValidation*": ("FluentValidation", "validation"), "Npgsql*": ("PostgreSQL", "database"), "Oracle.ManagedDataAccess*": ("Oracle", "database"),
        "Microsoft.Data.SqlClient": ("SQL Server", "database"), "System.Data.SqlClient": ("SQL Server", "database"),
        "IBM.Data.DB2*": ("Db2", "database"), "StackExchange.Redis": ("Redis", "cache"), "Confluent.Kafka": ("Kafka", "messaging"),
        "RabbitMQ.Client": ("RabbitMQ", "messaging"), "MassTransit*": ("MassTransit", "messaging"), "xunit*": ("xUnit", "test"),
        "NUnit*": ("NUnit", "test"), "MSTest.*": ("MSTest", "test"), "Moq": ("Moq", "test"), "Polly*": ("Polly", "resilience"),
        "Swashbuckle.AspNetCore*": ("Swagger", "web"), "Newtonsoft.Json": ("Json.NET", "data"),
    },
    "gem": {
        "rails": ("Ruby on Rails", "web"), "sinatra": ("Sinatra", "web"), "sidekiq": ("Sidekiq", "messaging"), "pg": ("PostgreSQL", "database"),
        "mysql2": ("MySQL", "database"), "redis": ("Redis", "cache"), "rspec": ("RSpec", "test"), "rspec-rails": ("RSpec", "test"),
        "devise": ("Devise", "security"), "grape": ("Grape", "web"), "activerecord": ("Active Record", "orm"),
    },
    "composer": {
        "laravel/framework": ("Laravel", "web"), "symfony/*": ("Symfony", "web"), "doctrine/orm": ("Doctrine", "orm"),
        "slim/slim": ("Slim", "web"), "phpunit/phpunit": ("PHPUnit", "test"), "guzzlehttp/guzzle": ("Guzzle", "http"),
    },
    "hex": {"phoenix": ("Phoenix", "web"), "ecto": ("Ecto", "orm"), "ecto_sql": ("Ecto", "orm"), "postgrex": ("PostgreSQL", "database")},
}

MANIFESTS = {
    "package.json": "Node.js", "pyproject.toml": "Python", "setup.py": "Python", "requirements.txt": "Python",
    "pipfile": "Python", "pom.xml": "JVM", "build.gradle": "JVM", "build.gradle.kts": "JVM", "go.mod": "Go",
    "cargo.toml": "Rust", "gemfile": "Ruby", "composer.json": "PHP", "mix.exs": "Elixir", "pubspec.yaml": "Dart",
}
DOC_NAMES = ("readme", "architecture", "design", "prd", "trd", "spec", "roadmap", "agents", "contributing", "claude")

INFRA_IMAGES = {
    "postgres": ("PostgreSQL", "database"), "mysql": ("MySQL", "database"), "mariadb": ("MariaDB", "database"),
    "mongo": ("MongoDB", "database"), "redis": ("Redis", "cache"), "rabbitmq": ("RabbitMQ", "messaging"),
    "kafka": ("Kafka", "messaging"), "confluentinc/cp-kafka": ("Kafka", "messaging"), "bitnami/kafka": ("Kafka", "messaging"),
    "activemq": ("ActiveMQ", "messaging"), "apache/activemq": ("ActiveMQ", "messaging"),
    "artemis": ("ActiveMQ Artemis", "messaging"), "apache/activemq-artemis": ("ActiveMQ Artemis", "messaging"),
    "ibmcom/mq": ("IBM MQ", "messaging"), "icr.io/ibm-messaging/mq": ("IBM MQ", "messaging"),
    "elasticsearch": ("Elasticsearch", "search"), "opensearch": ("OpenSearch", "search"), "minio": ("MinIO", "storage"),
    "nginx": ("NGINX", "gateway"), "traefik": ("Traefik", "gateway"), "keycloak": ("Keycloak", "security"),
    "oracle": ("Oracle", "database"), "mcr.microsoft.com/mssql": ("SQL Server", "database"), "zookeeper": ("ZooKeeper", "messaging"),
    "memcached": ("Memcached", "cache"), "localstack": ("LocalStack", "cloud"), "temporalio": ("Temporal", "workflow"),
}


@dataclass
class Container:
    name: str
    path: str  # "" for repository root
    ecosystem: str
    manifest: str
    kind: str = "service"  # service, frontend, library, cli, infrastructure, database, application
    frameworks: list[dict] = field(default_factory=list)
    dependencies: list[dict] = field(default_factory=list)
    entry_points: list[str] = field(default_factory=list)
    scripts: dict = field(default_factory=dict)
    description: str = ""
    deployable: bool = False


@dataclass
class MacroResult:
    containers: list[Container]
    frameworks: list[dict]
    languages: dict[str, int]
    architecture_style: str
    style_evidence: list[str]
    monorepo: bool
    entry_points: list[str]
    external_systems: list[dict]
    infrastructure: list[dict]
    docs: dict
    readme_excerpt: str
    doc_claims: list[str]
    drift: list[dict]
    path_aliases: dict[str, list[str]]
    ci: list[str]


def _match(table: dict[str, tuple[str, str]], name: str) -> tuple[str, str] | None:
    key = name.lower()
    for pattern, value in table.items():
        lowered = pattern.lower()
        if lowered.endswith("*"):
            if key.startswith(lowered[:-1]):
                return value
        elif key == lowered:
            return value
    return None


def _dep(name: str, version: str = "", scope: str = "runtime") -> dict:
    return {"name": name.strip(), "version": str(version or "").strip()[:100], "scope": scope}


# --- manifest readers ---------------------------------------------------------------------------------------------


def _read_package_json(text: str, container: Container) -> list[str]:
    try:
        data = json.loads(text)
    except ValueError:
        return []
    for key, scope in (("dependencies", "runtime"), ("devDependencies", "dev"), ("peerDependencies", "peer"), ("optionalDependencies", "optional")):
        for name, version in (data.get(key) or {}).items():
            container.dependencies.append(_dep(name, version, scope))
    container.name = data.get("name") or container.name
    container.description = data.get("description") or ""
    container.scripts = {k: str(v)[:200] for k, v in (data.get("scripts") or {}).items()}
    if data.get("main"):
        container.entry_points.append(str(data["main"]))
    if isinstance(data.get("bin"), dict):
        container.entry_points += [str(v) for v in data["bin"].values()]
        container.kind = "cli"
    elif isinstance(data.get("bin"), str):
        container.entry_points.append(data["bin"])
        container.kind = "cli"
    workspaces = data.get("workspaces")
    if isinstance(workspaces, dict):
        workspaces = workspaces.get("packages")
    if workspaces:
        container.kind = "aggregator"
    return list(workspaces or [])


def _read_requirements(text: str, container: Container, scope: str = "runtime") -> None:
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith(("-", "git+", "http")):
            continue
        match = re.match(r"^([A-Za-z0-9_.\-\[\]]+)\s*([<>=!~].*)?$", line)
        if match:
            container.dependencies.append(_dep(re.sub(r"\[.*\]", "", match.group(1)), match.group(2) or "", scope))


def _read_pyproject(text: str, container: Container) -> None:
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError):
        return
    project = data.get("project") or {}
    container.name = project.get("name") or container.name
    container.description = project.get("description") or container.description
    for item in project.get("dependencies") or []:
        _read_requirements(str(item), container)
    for group, items in (project.get("optional-dependencies") or {}).items():
        for item in items:
            _read_requirements(str(item), container, "dev" if group in ("dev", "test", "tests") else "optional")
    if project.get("scripts"):
        container.entry_points += list(project["scripts"].values())
        container.kind = "cli" if container.kind == "service" else container.kind
    poetry = (data.get("tool") or {}).get("poetry") or {}
    for name, version in (poetry.get("dependencies") or {}).items():
        if name.lower() != "python":
            container.dependencies.append(_dep(name, version if isinstance(version, str) else json.dumps(version)))
    for name, version in ((poetry.get("group") or {}).get("dev", {}).get("dependencies") or {}).items():
        container.dependencies.append(_dep(name, version if isinstance(version, str) else "", "dev"))


def _read_setup_py(text: str, container: Container) -> None:
    match = re.search(r"install_requires\s*=\s*\[([^\]]*)\]", text, re.S)
    if match:
        for item in re.findall(r"['\"]([^'\"]+)['\"]", match.group(1)):
            _read_requirements(item, container)


def _xml_text(node, path: str, ns: dict) -> str:
    found = node.find(path, ns)
    return (found.text or "").strip() if found is not None and found.text else ""


def _read_pom(text: str, container: Container) -> list[str]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return []
    ns = {"m": root.tag.split("}")[0].strip("{")} if root.tag.startswith("{") else {}
    prefix = "m:" if ns else ""
    container.name = _xml_text(root, f"{prefix}artifactId", ns) or container.name
    container.description = _xml_text(root, f"{prefix}description", ns)
    parent = root.find(f"{prefix}parent", ns)
    if parent is not None:
        artifact = _xml_text(parent, f"{prefix}artifactId", ns)
        if artifact:
            container.dependencies.append(_dep(artifact, _xml_text(parent, f"{prefix}version", ns), "parent"))
    for dep in root.iter(f"{{{ns['m']}}}dependency" if ns else "dependency"):
        artifact = _xml_text(dep, f"{prefix}artifactId", ns)
        group = _xml_text(dep, f"{prefix}groupId", ns)
        scope = _xml_text(dep, f"{prefix}scope", ns) or "runtime"
        if artifact:
            entry = _dep(artifact, _xml_text(dep, f"{prefix}version", ns), "dev" if scope == "test" else scope)
            entry["group"] = group
            container.dependencies.append(entry)
    packaging = _xml_text(root, f"{prefix}packaging", ns)
    if packaging == "pom":
        container.kind = "aggregator"
    modules = root.find(f"{prefix}modules", ns)
    return [(m.text or "").strip() for m in modules] if modules is not None else []


def _read_gradle(text: str, container: Container) -> None:
    for match in re.finditer(r"""\b(implementation|api|compileOnly|runtimeOnly|testImplementation|annotationProcessor|kapt)\s*\(?\s*["']([^:"']+):([^:"']+)(?::([^"']+))?["']""", text):
        scope = "dev" if match.group(1).startswith("test") else "runtime"
        entry = _dep(match.group(3), match.group(4) or "", scope)
        entry["group"] = match.group(2)
        container.dependencies.append(entry)
    for match in re.finditer(r"""id\s*\(?\s*["']org\.springframework\.boot["']""", text):
        container.dependencies.append(_dep("spring-boot-starter", "", "plugin"))


def _read_go_mod(text: str, container: Container) -> str:
    module = re.search(r"^module\s+(\S+)", text, re.M)
    for match in re.finditer(r"^\s*([\w.\-/]+\.[\w.\-/]+)\s+(v[\w.\-+]+)", text, re.M):
        container.dependencies.append(_dep(match.group(1), match.group(2)))
    if module:
        container.name = module.group(1).split("/")[-1]
        return module.group(1)
    return ""


def _read_cargo(text: str, container: Container) -> list[str]:
    try:
        data = tomllib.loads(text)
    except (tomllib.TOMLDecodeError, ValueError):
        return []
    package = data.get("package") or {}
    container.name = package.get("name") or container.name
    container.description = package.get("description") or ""
    for key, scope in (("dependencies", "runtime"), ("dev-dependencies", "dev")):
        for name, version in (data.get(key) or {}).items():
            container.dependencies.append(_dep(name, version if isinstance(version, str) else (version or {}).get("version", ""), scope))
    if data.get("bin"):
        container.kind = "cli"
    return list((data.get("workspace") or {}).get("members") or [])


def _read_csproj(text: str, container: Container) -> None:
    for match in re.finditer(r"""<PackageReference\s+Include="([^"]+)"(?:\s+Version="([^"]*)")?""", text):
        container.dependencies.append(_dep(match.group(1), match.group(2) or ""))
    if "Microsoft.NET.Sdk.Web" in text:
        container.dependencies.append(_dep("Microsoft.AspNetCore.App", "", "sdk"))
    if re.search(r"<OutputType>\s*Exe\s*</OutputType>", text, re.I):
        container.kind = "cli" if "Microsoft.NET.Sdk.Web" not in text else container.kind


def _read_gemfile(text: str, container: Container) -> None:
    for match in re.finditer(r"""^\s*gem\s+["']([^"']+)["'](?:\s*,\s*["']([^"']+)["'])?""", text, re.M):
        container.dependencies.append(_dep(match.group(1), match.group(2) or ""))


def _read_composer(text: str, container: Container) -> None:
    try:
        data = json.loads(text)
    except ValueError:
        return
    container.name = (data.get("name") or container.name).split("/")[-1]
    container.description = data.get("description") or ""
    for key, scope in (("require", "runtime"), ("require-dev", "dev")):
        for name, version in (data.get(key) or {}).items():
            if name != "php" and not name.startswith("ext-"):
                container.dependencies.append(_dep(name, version, scope))


def _read_mix(text: str, container: Container) -> None:
    for match in re.finditer(r"\{:(\w+),\s*\"([^\"]+)\"", text):
        container.dependencies.append(_dep(match.group(1), match.group(2)))


ECOSYSTEM_TABLE = {"Node.js": "npm", "Python": "python", "JVM": "maven", "Go": "go", "Rust": "cargo", ".NET": "nuget", "Ruby": "gem", "PHP": "composer", "Elixir": "hex"}


def _detect_frameworks(container: Container) -> None:
    table = FRAMEWORKS.get(ECOSYSTEM_TABLE.get(container.ecosystem, ""), {})
    seen: dict[str, dict] = {}
    for dep in container.dependencies:
        hit = _match(table, dep["name"]) or (_match(table, f"{dep.get('group', '')}.*") if dep.get("group") else None)
        if not hit and container.ecosystem == "JVM" and dep.get("group", "").startswith("jakarta"):
            hit = ("Jakarta EE", "web")
        if hit and hit[0] not in seen:
            seen[hit[0]] = {"name": hit[0], "category": hit[1], "evidence": f"{container.manifest} → {dep['name']}", "version": dep.get("version", "")}
    container.frameworks = list(seen.values())


# --- main entry ---------------------------------------------------------------------------------------------------


def discover(files: list[FileEntry]) -> MacroResult:
    by_path = {item.path: item for item in files}
    text_of = {item.path: item.text for item in files if item.text is not None}
    containers: dict[str, Container] = {}
    workspace_globs: list[str] = []
    go_modules: dict[str, str] = {}

    for item in files:
        pure = PurePosixPath(item.path)
        name = pure.name.lower()
        directory = str(pure.parent) if str(pure.parent) != "." else ""
        text = text_of.get(item.path)
        ecosystem = MANIFESTS.get(name)
        if name.endswith((".csproj", ".vbproj", ".fsproj")):
            ecosystem = ".NET"
        if not ecosystem or text is None:
            continue
        if name == "requirements.txt" and directory in containers:
            _read_requirements(text, containers[directory])
            continue
        if name in ("requirements-dev.txt", "requirements_test.txt"):
            continue
        container = containers.get(directory)
        if container is None or container.ecosystem != ecosystem:
            if container is not None and container.ecosystem != ecosystem:
                # two ecosystems in one folder: keep both, second one named by ecosystem
                key = f"{directory}#{ecosystem}"
            else:
                key = directory
            container = Container(name=PurePosixPath(directory).name or "root", path=directory, ecosystem=ecosystem, manifest=item.path)
            containers[key] = container
        if name == "package.json":
            workspace_globs += [f"{directory}/{glob}".strip("/") for glob in _read_package_json(text, container)]
        elif name == "pyproject.toml":
            _read_pyproject(text, container)
        elif name == "setup.py":
            _read_setup_py(text, container)
        elif name in ("requirements.txt", "pipfile"):
            _read_requirements(text, container)
        elif name == "pom.xml":
            _read_pom(text, container)
        elif name.startswith("build.gradle"):
            _read_gradle(text, container)
        elif name == "go.mod":
            module = _read_go_mod(text, container)
            if module:
                go_modules[directory] = module
        elif name == "cargo.toml":
            _read_cargo(text, container)
        elif ecosystem == ".NET":
            _read_csproj(text, container)
        elif name == "gemfile":
            _read_gemfile(text, container)
        elif name == "composer.json":
            _read_composer(text, container)
        elif name == "mix.exs":
            _read_mix(text, container)

    # extra requirements files feed the nearest Python container
    for item in files:
        name = PurePosixPath(item.path).name.lower()
        if re.match(r"requirements[-_](dev|test|tests)\.txt$", name) and item.text:
            directory = str(PurePosixPath(item.path).parent)
            directory = "" if directory == "." else directory
            if directory in containers:
                _read_requirements(item.text, containers[directory], "dev")

    if not containers:
        containers[""] = Container(name="root", path="", ecosystem=_dominant_language(files), manifest="")

    pnpm = text_of.get("pnpm-workspace.yaml")
    if pnpm and "" in containers:
        containers[""].kind = "aggregator"
    if pnpm:
        try:
            workspace_globs += list((yaml.safe_load(pnpm) or {}).get("packages") or [])
        except yaml.YAMLError:
            pass

    for container in containers.values():
        _detect_frameworks(container)
        _classify_container(container, files)
    nested = [c for c in containers.values() if c.path]
    for container in containers.values():
        if not container.path and nested and container.kind in ("application", "library") and not any(fw["category"] in ("web", "ui", "cli") for fw in container.frameworks):
            container.kind = "aggregator"  # root manifest that only holds tooling for the nested packages

    # Docker / compose / IaC
    infrastructure, compose_services = _infrastructure(files, text_of)
    for container in containers.values():
        prefix = f"{container.path}/" if container.path else ""
        if any(path.startswith(prefix) and PurePosixPath(path).name.lower().startswith("dockerfile") for path in by_path):
            container.deployable = True

    container_list = sorted(containers.values(), key=lambda c: (c.path.count("/"), c.path))
    monorepo = bool(workspace_globs) or len([c for c in container_list if c.kind != "aggregator"]) > 1 or "go.work" in by_path
    frameworks = _merge_frameworks(container_list)
    languages = _language_loc(files)
    entry_points = _entry_points(files, text_of, container_list)
    style, evidence = _architecture_style(container_list, frameworks, files, compose_services, monorepo, text_of)
    external = _external_systems(frameworks, infrastructure, compose_services, text_of)
    docs, readme, claims = _documentation(files, text_of)
    drift = _drift(claims, frameworks, infrastructure, readme)
    aliases = _path_aliases(files, text_of)
    ci = [path for path in by_path if path.startswith(".github/workflows/") or PurePosixPath(path).name.lower() in ("jenkinsfile", ".gitlab-ci.yml", "azure-pipelines.yml", "bitbucket-pipelines.yml", ".travis.yml")]
    for container in container_list:
        container.dependencies = _dedupe_deps(container.dependencies)
    return MacroResult(
        containers=container_list, frameworks=frameworks, languages=languages, architecture_style=style,
        style_evidence=evidence, monorepo=monorepo, entry_points=entry_points, external_systems=external,
        infrastructure=infrastructure, docs=docs, readme_excerpt=readme, doc_claims=claims, drift=drift,
        path_aliases=aliases, ci=sorted(ci),
    )


def _dedupe_deps(deps: list[dict]) -> list[dict]:
    seen: dict[tuple[str, str], dict] = {}
    for dep in deps:
        key = (dep["name"].lower(), dep["scope"])
        if key not in seen:
            seen[key] = dep
    return list(seen.values())


def _dominant_language(files: list[FileEntry]) -> str:
    counts: dict[str, int] = {}
    for item in files:
        if item.category == "code" and item.text:
            counts[item.language] = counts.get(item.language, 0) + item.text.count("\n")
    return max(counts, key=counts.get) if counts else "Unknown"


def _classify_container(container: Container, files: list[FileEntry]) -> None:
    categories = {fw["category"] for fw in container.frameworks}
    names = {fw["name"] for fw in container.frameworks}
    if container.kind in ("cli", "aggregator"):
        return
    if categories & {"ui"} and not names & {"Express", "NestJS", "Fastify", "Koa"}:
        container.kind = "frontend" if not names & {"Next.js", "Nuxt", "SvelteKit"} else "web application"
    elif categories & {"web"}:
        container.kind = "service"
    elif "cli" in categories:
        container.kind = "cli"
    else:
        prefix = f"{container.path}/" if container.path else ""
        has_main = any(
            item.path.startswith(prefix) and PurePosixPath(item.path).name in ("main.py", "__main__.py", "main.go", "Program.cs", "main.rs", "index.js", "server.js", "app.py")
            for item in files
        )
        container.kind = "application" if has_main else "library"


def _merge_frameworks(containers: list[Container]) -> list[dict]:
    merged: dict[str, dict] = {}
    for container in containers:
        for fw in container.frameworks:
            item = merged.setdefault(fw["name"], {**fw, "containers": []})
            item["containers"].append(container.name)
    return sorted(merged.values(), key=lambda fw: (fw["category"], fw["name"]))


def _language_loc(files: list[FileEntry]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in files:
        if item.text is not None and item.category in ("code", "markup", "style", "script"):
            counts[item.language] = counts.get(item.language, 0) + item.text.count("\n") + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


ENTRY_PATTERNS = re.compile(
    r"(^|/)(main\.py|__main__\.py|app\.py|manage\.py|wsgi\.py|asgi\.py|server\.(js|ts)|index\.(js|ts)|main\.(js|ts|tsx|jsx)|"
    r"app\.(js|ts)|main\.go|main\.rs|lib\.rs|Program\.cs|Startup\.cs|config\.ru|index\.php|artisan)$"
)


def _entry_points(files: list[FileEntry], text_of: dict[str, str], containers: list[Container]) -> list[str]:
    entries: list[str] = []
    for item in files:
        if item.text is None or item.category != "code":
            continue
        path = item.path
        if ENTRY_PATTERNS.search(path) and not re.search(r"(^|/)(tests?|__tests__|spec|examples?|docs?)/", path):
            entries.append(path)
        elif item.language in ("Java", "Kotlin") and "@SpringBootApplication" in item.text:
            entries.append(path)
        elif item.language == "Go" and re.search(r"(^|/)cmd/[^/]+/main\.go$", path):
            entries.append(path)
        elif item.language == "Python" and 'if __name__ == "__main__"' in item.text.replace("'", '"') and "/test" not in path:
            entries.append(path)
    for container in containers:
        for entry in container.entry_points:
            candidate = str(PurePosixPath(container.path) / entry).lstrip("./") if container.path else entry.lstrip("./")
            if candidate in text_of and candidate not in entries:
                entries.append(candidate)
    return sorted(set(entries))[:200]


def _infrastructure(files: list[FileEntry], text_of: dict[str, str]) -> tuple[list[dict], dict]:
    items: list[dict] = []
    services: dict = {}
    for item in files:
        name = PurePosixPath(item.path).name.lower()
        text = text_of.get(item.path)
        if text is None:
            continue
        if name.startswith("dockerfile"):
            base = re.findall(r"^\s*FROM\s+(\S+)", text, re.M | re.I)
            ports = re.findall(r"^\s*EXPOSE\s+([\d\s/a-z]+)", text, re.M | re.I)
            cmd = re.findall(r"^\s*(?:CMD|ENTRYPOINT)\s+(.+)$", text, re.M | re.I)
            items.append({"type": "Dockerfile", "path": item.path, "base_images": base[:5], "ports": " ".join(ports).split(), "command": cmd[-1][:200] if cmd else ""})
        elif name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml") or re.match(r"docker-compose\..+\.ya?ml$", name):
            try:
                data = yaml.safe_load(text) or {}
            except yaml.YAMLError:
                continue
            for service, spec in (data.get("services") or {}).items():
                if not isinstance(spec, dict):
                    continue
                image = str(spec.get("image") or "")
                services[service] = {
                    "image": image,
                    "build": bool(spec.get("build")),
                    "ports": [str(p) for p in spec.get("ports") or []][:6],
                    "depends_on": list(spec.get("depends_on") or []) if not isinstance(spec.get("depends_on"), dict) else list(spec["depends_on"]),
                    "file": item.path,
                }
            items.append({"type": "Docker Compose", "path": item.path, "services": list((data.get("services") or {}).keys())})
        elif name.endswith(".tf"):
            resources = re.findall(r'resource\s+"([\w-]+)"\s+"([\w-]+)"', text)
            providers = re.findall(r'provider\s+"([\w-]+)"', text)
            if resources or providers:
                items.append({"type": "Terraform", "path": item.path, "providers": sorted(set(providers)), "resources": [f"{a}.{b}" for a, b in resources[:30]]})
        elif name in ("chart.yaml",):
            items.append({"type": "Helm chart", "path": item.path})
        elif name in ("serverless.yml", "serverless.yaml"):
            items.append({"type": "Serverless Framework", "path": item.path, "functions": re.findall(r"handler:\s*(\S+)", text)[:30]})
        elif name in ("template.yaml", "template.yml") and "AWS::Serverless" in text:
            items.append({"type": "AWS SAM", "path": item.path, "functions": re.findall(r"Handler:\s*(\S+)", text)[:30]})
        elif item.path.endswith((".yaml", ".yml")) and re.search(r"^kind:\s*(Deployment|StatefulSet|Service|Ingress|CronJob)\b", text, re.M) and "apiVersion:" in text:
            kinds = re.findall(r"^kind:\s*(\w+)", text, re.M)
            items.append({"type": "Kubernetes", "path": item.path, "kinds": sorted(set(kinds))})
    return items, services


def _architecture_style(containers, frameworks, files, compose_services, monorepo, text_of) -> tuple[str, list[str]]:
    evidence: list[str] = []
    containers = [c for c in containers if c.kind != "aggregator"] or containers
    paths = [item.path.lower() for item in files]
    serverless = any(p.endswith(("serverless.yml", "serverless.yaml")) for p in paths) or any("AWS::Serverless" in (text_of.get(p) or "") for p in text_of if p.lower().endswith(("template.yaml", "template.yml")))
    services = [c for c in containers if c.kind in ("service", "web application")]
    built_services = [name for name, spec in compose_services.items() if spec.get("build")]
    if serverless:
        evidence.append("Serverless manifest (serverless.yml or AWS SAM template) found")
        return "Serverless", evidence
    if len(services) >= 2 or len(built_services) >= 3:
        evidence.append(f"{max(len(services), len(built_services))} independently deployable services (" + ", ".join((c.name for c in services[:6])) + ")")
        if built_services:
            evidence.append(f"docker-compose builds {len(built_services)} services: {', '.join(built_services[:6])}")
        return "Microservices" + (" (monorepo)" if monorepo else ""), evidence
    web = [fw for fw in frameworks if fw["category"] in ("web",)]
    ui = [fw for fw in frameworks if fw["category"] == "ui"]
    clis = [c for c in containers if c.kind == "cli"]
    if not web and not ui and (clis or all(c.kind == "library" for c in containers)):
        evidence.append("No web framework; packaged as " + ("a CLI" if clis else "a library"))
        return "Library / CLI", evidence
    dirs = {segment for p in paths for segment in p.split("/")[:-1]}
    if {"domain", "application", "infrastructure"} <= dirs:
        evidence.append("domain/, application/ and infrastructure/ folders present")
        style = "Monolith (Clean / Hexagonal layering)"
    elif {"controllers", "models", "views"} <= dirs or any(fw["name"] in ("Ruby on Rails", "Django", "Laravel") for fw in frameworks) or (web and "templates" in dirs and not ui):
        evidence.append("MVC folders or an MVC framework present")
        style = "Monolith (MVC)"
    elif any(c.kind == "frontend" for c in containers) and len(containers) == 1:
        evidence.append("Single front-end application")
        style = "Single-page application"
    elif any(c.kind == "frontend" for c in containers) and web:
        evidence.append("Separate front-end and back-end packages in one repository")
        style = "Full-stack (separate front-end and back-end)"
    else:
        style = "Monolith (layered)"
    evidence += [f"Web framework: {fw['name']}" for fw in web[:3]]
    if monorepo:
        evidence.append("Workspace / multiple manifests detected")
    return style, evidence


def _external_systems(frameworks, infrastructure, compose_services, text_of) -> list[dict]:
    systems: dict[str, dict] = {}
    for fw in frameworks:
        if fw["category"] in ("database", "cache", "messaging", "search", "storage", "cloud", "ai", "security") and fw["name"] not in ("JWT",):
            systems.setdefault(fw["name"], {"name": fw["name"], "type": fw["category"], "evidence": fw["evidence"]})
    for service, spec in compose_services.items():
        image = spec.get("image", "").lower()
        for key, (label, kind) in INFRA_IMAGES.items():
            if image.startswith(key) or f"/{key}" in image:
                systems.setdefault(label, {"name": label, "type": kind, "evidence": f"{spec['file']} → service '{service}' ({image})"})
                break
    hosts: dict[str, int] = {}
    code_ext = (".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".kt", ".go", ".rs", ".cs", ".rb", ".php", ".scala", ".swift", ".dart", ".properties", ".yml", ".yaml", ".env", ".conf")
    for path, text in text_of.items():
        lowered_path = path.lower()
        if "/test" in lowered_path or "test/" in lowered_path or not lowered_path.endswith(code_ext) or "/docs/" in lowered_path or lowered_path.endswith((".tsx", ".jsx")):
            continue
        text = re.sub(r"(?m)^\s*(#|//|\*|/\*).*$", "", text)
        for host in re.findall(r"https?://([a-z0-9.-]+\.[a-z]{2,})(?:[:/][^\s\"'`]*)?", text, re.I):
            host = host.lower()
            if any(skip in host for skip in ("example.", "localhost", "w3.org", "schema", "apache.org", "github.com", "githubusercontent", "mozilla.org", "fonts.", "npmjs", "pypi.org", "maven", "json-schema", "xml", "openapis", "swagger.io", "creativecommons", "opensource.org", "docs.", "readthedocs", "stackoverflow", "wikipedia", "spring.io", "gradle.org", "astral.sh", "pre-commit", "python.org", "nodejs.org", "golang.org", "microsoft.com/fwlink", "purl.org", "jquery", "cdn", "twitter.com", "x.com", "linkedin.com", "facebook.com", "youtube.com", "instagram.com", "discord", "medium.com", "shields.io", "badge")):
                continue
            hosts[host] = hosts.get(host, 0) + 1
    for host, count in sorted(hosts.items(), key=lambda kv: -kv[1])[:8]:
        systems.setdefault(host, {"name": host, "type": "external api", "evidence": f"{count} URL reference(s) in code"})
    return list(systems.values())


def _documentation(files, text_of) -> tuple[dict, str, list[str]]:
    docs: dict[str, str] = {}
    readme = ""
    for item in sorted(files, key=lambda f: f.path.count("/")):
        if item.text is None:
            continue
        stem = PurePosixPath(item.path).stem.lower()
        name = PurePosixPath(item.path).name.lower()
        if item.category == "doc" and (any(stem.startswith(prefix) for prefix in DOC_NAMES) or item.path.startswith("docs/")):
            docs[item.path] = item.text[:4000]
            if not readme and stem == "readme":
                readme = item.text[:6000]
        elif name in (".cursorrules", "agents.md", "claude.md"):
            docs[item.path] = item.text[:4000]
        if len(docs) > 30:
            break
    claims = []
    lowered = readme.lower()
    known = {fw[0] for table in FRAMEWORKS.values() for fw in table.values()}
    ambiguous = {"click", "dash", "express", "requests", "echo", "fiber", "rocket", "gin", "chi", "warp", "viper", "cobra", "zap",
                 "mocha", "moq", "polly", "yup", "joi", "bull", "serde", "tokio", "lombok", "jackson", "h2", "junit", "jest", "vite",
                 "typescript", "pydantic", "pandas", "numpy", "axios", "redux", "zod", "playwright", "cypress", "pytest"}
    for name in sorted(known):
        token = name.lower().split(" (")[0]
        if len(token) < 4 or token in ambiguous:
            continue
        if re.search(rf"(?<![\w-]){re.escape(token)}(?![\w-])", lowered):
            claims.append(name)
    return docs, readme, claims


def _drift(claims: list[str], frameworks: list[dict], infrastructure: list[dict], readme: str) -> list[dict]:
    found = {fw["name"].lower() for fw in frameworks}
    found_roots = {name.split(" (")[0] for name in found}
    infra_text = json.dumps(infrastructure).lower()
    drift = []
    for claim in claims:
        key = claim.lower()
        if key in found or key.split(" (")[0] in found_roots or key.split(" (")[0] in infra_text:
            continue
        if claim in ("TypeScript", "GraphQL", "JWT", "Docker"):
            continue
        drift.append({"claim": claim, "detail": f"README mentions {claim}, but no manifest dependency or infrastructure file confirms it."})
    return drift[:15]


def _path_aliases(files, text_of) -> dict[str, list[str]]:
    """tsconfig/jsconfig `paths` aliases -> folder prefixes, resolved before import edges are built."""
    aliases: dict[str, list[str]] = {}
    for item in files:
        name = PurePosixPath(item.path).name.lower()
        if not (name.startswith("tsconfig") and name.endswith(".json") or name == "jsconfig.json") or item.text is None:
            continue
        cleaned = re.sub(r"//[^\n]*|/\*[\s\S]*?\*/", "", item.text)
        cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
        try:
            data = json.loads(cleaned)
        except ValueError:
            continue
        options = data.get("compilerOptions") or {}
        base_dir = str(PurePosixPath(item.path).parent)
        base_dir = "" if base_dir == "." else base_dir
        base_url = options.get("baseUrl") or "."
        root = str(PurePosixPath(base_dir) / base_url) if base_dir else base_url
        root = str(PurePosixPath(root)).replace("/./", "/").removeprefix("./").removesuffix("/.")
        root = "" if root == "." else root
        for alias, targets in (options.get("paths") or {}).items():
            key = alias.rstrip("*").rstrip("/")
            for target in targets:
                folder = str(PurePosixPath(root) / target.rstrip("*").rstrip("/")) if root else target.rstrip("*").rstrip("/")
                folder = folder.removeprefix("./")
                aliases.setdefault(key, []).append(folder)
        if root and "@" not in aliases:
            aliases.setdefault("~baseUrl", []).append(root)
    return aliases
