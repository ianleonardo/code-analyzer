"""File-type knowledge: language by extension, category, comment syntax, and what never gets indexed."""

from __future__ import annotations

from pathlib import PurePosixPath

# extension -> (language, category). Categories: code, markup, style, config, doc, data, script
EXTENSIONS: dict[str, tuple[str, str]] = {
    ".py": ("Python", "code"), ".pyi": ("Python", "code"), ".pyx": ("Python", "code"),
    ".js": ("JavaScript", "code"), ".jsx": ("JavaScript", "code"), ".mjs": ("JavaScript", "code"), ".cjs": ("JavaScript", "code"),
    ".ts": ("TypeScript", "code"), ".tsx": ("TypeScript", "code"), ".mts": ("TypeScript", "code"), ".cts": ("TypeScript", "code"),
    ".vue": ("Vue", "code"), ".svelte": ("Svelte", "code"),
    ".java": ("Java", "code"), ".kt": ("Kotlin", "code"), ".kts": ("Kotlin", "code"), ".scala": ("Scala", "code"),
    ".groovy": ("Groovy", "code"), ".gradle": ("Gradle", "config"),
    ".go": ("Go", "code"), ".rs": ("Rust", "code"),
    ".cs": ("C#", "code"), ".vb": ("VB.NET", "code"), ".fs": ("F#", "code"),
    ".c": ("C", "code"), ".h": ("C/C++ Header", "code"), ".cc": ("C++", "code"), ".cpp": ("C++", "code"),
    ".cxx": ("C++", "code"), ".hpp": ("C++", "code"), ".hh": ("C++", "code"),
    ".m": ("Objective-C", "code"), ".mm": ("Objective-C", "code"), ".swift": ("Swift", "code"),
    ".rb": ("Ruby", "code"), ".php": ("PHP", "code"), ".dart": ("Dart", "code"),
    ".ex": ("Elixir", "code"), ".exs": ("Elixir", "code"), ".erl": ("Erlang", "code"),
    ".clj": ("Clojure", "code"), ".hs": ("Haskell", "code"), ".lua": ("Lua", "code"),
    ".pl": ("Perl", "code"), ".pm": ("Perl", "code"), ".r": ("R", "code"), ".jl": ("Julia", "code"),
    ".sql": ("SQL", "code"), ".ddl": ("SQL", "code"), ".pls": ("PL/SQL", "code"), ".pks": ("PL/SQL", "code"), ".pkb": ("PL/SQL", "code"),
    ".sh": ("Shell", "script"), ".bash": ("Shell", "script"), ".zsh": ("Shell", "script"),
    ".ps1": ("PowerShell", "script"), ".bat": ("Batch", "script"), ".cmd": ("Batch", "script"),
    ".cbl": ("COBOL", "code"), ".cob": ("COBOL", "code"), ".cobol": ("COBOL", "code"), ".cblle": ("COBOL", "code"),
    ".sqlcblle": ("COBOL", "code"), ".sqlcbl": ("COBOL", "code"), ".pco": ("COBOL", "code"), ".ccp": ("COBOL", "code"),
    ".cpy": ("COBOL Copybook", "code"), ".copy": ("COBOL Copybook", "code"), ".cpybk": ("COBOL Copybook", "code"),
    ".dcl": ("COBOL Copybook", "code"), ".dclgen": ("COBOL Copybook", "code"),
    ".rpg": ("RPG", "code"), ".rpgle": ("RPG", "code"), ".sqlrpgle": ("RPG", "code"), ".rpglec": ("RPG", "code"), ".sqlrpg": ("RPG", "code"),
    ".rpg38": ("RPG", "code"), ".rpgleinc": ("RPG", "code"), ".rpginc": ("RPG", "code"),
    ".clle": ("CL", "code"), ".clp": ("CL", "code"), ".cl38": ("CL", "code"),
    ".dspf": ("DDS", "code"), ".pf": ("DDS", "code"), ".lf": ("DDS", "code"), ".prtf": ("DDS", "code"), ".dds": ("DDS", "code"),
    ".jcl": ("JCL", "script"), ".prc": ("JCL", "script"), ".jclproc": ("JCL", "script"),
    ".bms": ("BMS", "markup"), ".csd": ("CICS CSD", "config"),
    ".jsp": ("JSP", "markup"), ".jspx": ("JSP", "markup"), ".cshtml": ("Razor", "markup"), ".razor": ("Razor", "markup"), ".xaml": ("XAML", "markup"),
    ".html": ("HTML", "markup"), ".htm": ("HTML", "markup"), ".hbs": ("Handlebars", "markup"), ".ejs": ("EJS", "markup"),
    ".ftl": ("FreeMarker", "markup"), ".ftlh": ("FreeMarker", "markup"), ".mustache": ("Mustache", "markup"),
    ".vm": ("Velocity", "markup"), ".peb": ("Pebble", "markup"), ".jte": ("JTE", "markup"),
    ".j2": ("Jinja", "markup"), ".jinja": ("Jinja", "markup"), ".jinja2": ("Jinja", "markup"), ".njk": ("Nunjucks", "markup"),
    ".storyboard": ("XML", "markup"), ".xib": ("XML", "markup"),
    ".css": ("CSS", "style"), ".scss": ("SCSS", "style"), ".sass": ("Sass", "style"), ".less": ("Less", "style"),
    ".proto": ("Protobuf", "code"), ".graphql": ("GraphQL", "code"), ".gql": ("GraphQL", "code"),
    ".tf": ("Terraform", "config"), ".hcl": ("HCL", "config"),
    ".json": ("JSON", "config"), ".yaml": ("YAML", "config"), ".yml": ("YAML", "config"), ".toml": ("TOML", "config"),
    ".xml": ("XML", "config"), ".plist": ("XML", "config"), ".xcconfig": ("Config", "config"),
    ".ini": ("INI", "config"), ".cfg": ("INI", "config"), ".conf": ("Config", "config"),
    ".properties": ("Properties", "config"), ".env": ("Env", "config"),
    ".md": ("Markdown", "doc"), ".mdx": ("Markdown", "doc"), ".rst": ("reStructuredText", "doc"),
    ".adoc": ("AsciiDoc", "doc"), ".txt": ("Text", "doc"),
    ".csv": ("CSV", "data"), ".tsv": ("CSV", "data"),
}

FILENAMES: dict[str, tuple[str, str]] = {
    "dockerfile": ("Dockerfile", "config"),
    "containerfile": ("Dockerfile", "config"),
    "makefile": ("Makefile", "script"),
    "jenkinsfile": ("Groovy", "config"),
    "gemfile": ("Ruby", "config"),
    "rakefile": ("Ruby", "code"),
    "procfile": ("Config", "config"),
    "vagrantfile": ("Ruby", "config"),
    ".gitignore": ("Config", "config"),
    ".dockerignore": ("Config", "config"),
    ".cursorrules": ("Markdown", "doc"),
    "license": ("Text", "doc"),
}

# Directories never walked: build outputs, dependencies, caches, VCS metadata.
EXCLUDED_DIRS = {
    "node_modules", "dist", "build", "out", "target", ".next", ".nuxt", ".svelte-kit", ".output", "__pycache__",
    "coverage", ".nyc_output", ".git", ".svn", ".hg", ".idea", ".vscode", ".vs", ".venv", "venv", ".tox",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".gradle", ".terraform", "bower_components", ".cache",
    ".parcel-cache", "storybook-static", "site-packages", "Pods", "DerivedData", ".angular", ".turbo", ".yarn",
    ".pnpm-store", "jspm_packages", "elm-stuff", "_build", "deps", ".dart_tool", ".expo", "vendor",
}
# Build outputs only when the folder sits next to a .NET project file.
DOTNET_OUTPUT_DIRS = {"bin", "obj"}

LOCKFILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "pipfile.lock", "cargo.lock", "go.sum",
    "composer.lock", "gemfile.lock", "mix.lock", "packages.lock.json", "npm-shrinkwrap.json", "bun.lockb", "uv.lock",
}

BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".svgz", ".tiff", ".psd", ".mp3", ".mp4", ".wav",
    ".ogg", ".mov", ".avi", ".webm", ".woff", ".woff2", ".ttf", ".otf", ".eot", ".zip", ".gz", ".tgz", ".bz2",
    ".xz", ".7z", ".rar", ".tar", ".jar", ".war", ".ear", ".class", ".exe", ".dll", ".so", ".dylib", ".a", ".lib",
    ".o", ".obj", ".pdb", ".pyc", ".pyo", ".whl", ".egg", ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".bin", ".dat", ".db", ".sqlite", ".duckdb", ".parquet", ".avro", ".orc", ".pkl", ".h5", ".onnx", ".pt", ".ckpt",
    ".keystore", ".jks", ".p12", ".pfx", ".der", ".iso", ".dmg", ".apk", ".ipa", ".nupkg",
}

# Line comment prefixes and block delimiters, for code/comment/blank line counts.
_C_STYLE = (("//",), ("/*", "*/"))
COMMENTS: dict[str, tuple[tuple[str, ...], tuple[str, str] | None]] = {
    "Python": (("#",), ('"""', '"""')),
    "Ruby": (("#",), ("=begin", "=end")),
    "Shell": (("#",), None), "PowerShell": (("#",), ("<#", "#>")), "Perl": (("#",), None), "R": (("#",), None),
    "YAML": (("#",), None), "TOML": (("#",), None), "Dockerfile": (("#",), None), "Makefile": (("#",), None),
    "Elixir": (("#",), None), "Terraform": (("#", "//"), ("/*", "*/")), "HCL": (("#", "//"), ("/*", "*/")),
    "Properties": (("#", "!"), None), "INI": ((";", "#"), None),
    "SQL": (("--",), ("/*", "*/")), "PL/SQL": (("--",), ("/*", "*/")), "Lua": (("--",), ("--[[", "]]")),
    "Haskell": (("--",), ("{-", "-}")), "Erlang": (("%",), None), "Clojure": ((";",), None),
    "Batch": (("REM", "rem", "::"), None), "VB.NET": (("'",), None),
    "HTML": ((), ("<!--", "-->")), "XML": ((), ("<!--", "-->")), "Vue": (("//",), ("<!--", "-->")),
    "CSS": ((), ("/*", "*/")), "SCSS": (("//",), ("/*", "*/")), "Less": (("//",), ("/*", "*/")),
    "COBOL": (("*>",), None), "COBOL Copybook": (("*>",), None), "RPG": (("//",), None), "CL": ((), ("/*", "*/")),
    "JCL": (("//*",), None), "DDS": ((), None), "BMS": (("*",), None), "CICS CSD": (("*",), None),
}
DEFAULT_COMMENTS = _C_STYLE


def classify_path(path: str) -> tuple[str, str]:
    """(language, category) for a repository-relative path. ('', 'other') when unknown."""
    pure = PurePosixPath(path)
    name = pure.name.lower()
    if name in FILENAMES:
        return FILENAMES[name]
    if name.startswith("dockerfile"):
        return ("Dockerfile", "config")
    if name.startswith(".env"):
        return ("Env", "config")
    if name.endswith((".d.ts",)):
        return ("TypeScript", "code")
    suffix = pure.suffix.lower()
    if suffix in EXTENSIONS:
        return EXTENSIONS[suffix]
    return ("", "other")


# Source-member exports from mainframe / IBM i libraries often carry no extension (or .mbr / .txt / .src).
SNIFF_SUFFIXES = ("", ".mbr", ".src", ".txt", ".mem")


def sniff_language(path: str, text: str) -> tuple[str, str]:
    """(language, category) from content for extension-less legacy members; ('', '') when nothing matches."""
    from app.services.analyzer import legacy

    suffix = PurePosixPath(path).suffix.lower()
    if suffix not in SNIFF_SUFFIXES or not text:
        return "", ""
    language = legacy.sniff(path if suffix in ("", ".mbr", ".src", ".mem") else "", text)
    if not language:
        return "", ""
    return language, EXTENSIONS.get({"COBOL": ".cbl", "COBOL Copybook": ".cpy", "RPG": ".rpgle", "CL": ".clle", "DDS": ".dds", "JCL": ".jcl", "BMS": ".bms"}[language], ("", "code"))[1]


def is_minified(name: str) -> bool:
    lowered = name.lower()
    return lowered.endswith((".min.js", ".min.css", ".map", ".bundle.js", ".chunk.js")) or "-bundle." in lowered


def count_lines(text: str, language: str) -> tuple[int, int, int]:
    """(code, comment, blank) line counts using the language's comment syntax."""
    line_prefixes, block = COMMENTS.get(language, DEFAULT_COMMENTS)
    code = comment = blank = 0
    in_block = False
    fixed_column7 = language in ("COBOL", "COBOL Copybook", "RPG", "DDS")
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            blank += 1
            continue
        if fixed_column7 and len(raw) > 6 and raw[6:7] in ("*", "/") and not raw[:6].strip(" 0123456789HFDICOPAhfdicopa"):
            comment += 1
            continue
        if in_block:
            comment += 1
            if block and block[1] in line:
                in_block = False
            continue
        if block and line.startswith(block[0]):
            comment += 1
            rest = line[len(block[0]) :]
            if block[1] not in rest:
                in_block = True
            continue
        if line_prefixes and line.startswith(line_prefixes):
            comment += 1
            continue
        code += 1
    return code, comment, blank
