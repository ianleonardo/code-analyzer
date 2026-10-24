"""Android (Kotlin / Java) UI: Activities, Fragments and Jetpack Compose screens with their fields, buttons and actions
(layout XML resolved through strings.xml, view binding, click listeners), navigation graphs, NavHost routes,
AndroidManifest (launcher, deep links, permissions) and Room @Database declarations.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from app.services.analyzer.catalog import jvm
from app.services.analyzer.catalog.data import _safe_xml
from app.services.analyzer.catalog.model import Catalog, Context, Item, TypeRef, snippet
from app.services.analyzer.catalog.structure import (
    match_close,
    simple_type,
    split_top,
    string_values,
)

INPUTS = {"EditText", "TextInputEditText", "AutoCompleteTextView", "MaterialAutoCompleteTextView", "Spinner", "CheckBox", "MaterialCheckBox", "RadioButton",
          "RadioGroup", "Switch", "SwitchCompat", "SwitchMaterial", "MaterialSwitch", "ToggleButton", "SeekBar", "RatingBar", "DatePicker", "TimePicker",
          "NumberPicker", "Slider", "RangeSlider", "ChipGroup", "AppCompatEditText", "AppCompatSpinner", "AppCompatCheckBox"}
ACTIONS = {"Button", "MaterialButton", "ImageButton", "FloatingActionButton", "ExtendedFloatingActionButton", "AppCompatButton", "Chip", "TextView_clickable"}
LISTS = {"RecyclerView", "ListView", "GridView", "ViewPager", "ViewPager2", "TabLayout", "BottomNavigationView", "NavigationView", "WebView", "MapView"}
ACTIVITY_BASES = {"AppCompatActivity", "ComponentActivity", "Activity", "FragmentActivity", "BaseActivity", "PreferenceActivity"}
FRAGMENT_BASES = {"Fragment", "DialogFragment", "BottomSheetDialogFragment", "PreferenceFragmentCompat", "BaseFragment", "ListFragment"}
COMPOSE_FIELDS = {"TextField": "text", "OutlinedTextField": "text", "BasicTextField": "text", "SecureTextField": "password", "OutlinedSecureTextField": "password",
                  "Checkbox": "checkbox", "TriStateCheckbox": "checkbox", "Switch": "switch", "RadioButton": "radio", "Slider": "slider", "RangeSlider": "slider",
                  "DatePicker": "date", "TimePicker": "time", "ExposedDropdownMenuBox": "select", "DropdownMenu": "menu", "SearchBar": "search", "DockedSearchBar": "search"}
COMPOSE_BUTTONS = ("Button", "TextButton", "OutlinedButton", "ElevatedButton", "FilledTonalButton", "IconButton", "FloatingActionButton", "ExtendedFloatingActionButton",
                   "FilledIconButton", "OutlinedIconButton")


def _strings(ctx: Context) -> dict[str, str]:
    out: dict[str, str] = {}
    for file in ctx.files:
        if file.text and re.search(r"/res/values[\w-]*/strings[\w-]*\.xml$", "/" + file.path):
            is_default = "/values/" in "/" + file.path
            for m in re.finditer(r"<string\s+name=\"(\w+)\"[^>]*>([\s\S]*?)</string>", file.text):
                val = re.sub(r"<[^>]+>", "", m.group(2)).strip().strip('"')[:200]
                if is_default or m.group(1) not in out:
                    out[m.group(1)] = val
    return out


def _res(value: str, strings: dict[str, str]) -> str:
    value = value or ""
    m = re.match(r"@string/(\w+)", value)
    return strings.get(m.group(1), value) if m else value


def _layouts(ctx: Context) -> dict[str, object]:
    out = {}
    for file in ctx.files:
        if file.text and re.search(r"(^|/)res/layout[\w-]*/[\w]+\.xml$", file.path):
            out.setdefault(PurePosixPath(file.path).stem, file)
    return out


def layout_widgets(name: str, layouts: dict, strings: dict[str, str], depth: int = 0) -> dict:
    file = layouts.get(name)
    if file is None or depth > 2:
        return {"fields": [], "actions": [], "lists": [], "labels": [], "fragments": [], "path": ""}
    root = _safe_xml(file.text)
    out = {"fields": [], "actions": [], "lists": [], "labels": [], "fragments": [], "path": file.path}
    if root is None:
        return out
    parents = {child: parent for parent in root.iter() for child in parent}
    for element in root.iter():
        tag = element.tag.split(".")[-1] if isinstance(element.tag, str) else ""
        attrs = element.attrib
        wid = (attrs.get("id") or "").replace("@+id/", "").replace("@id/", "")
        line = file.text.count("\n", 0, file.text.find(f'"@+id/{wid}"')) + 1 if wid and f'"@+id/{wid}"' in file.text else 0
        if tag == "include" and attrs.get("layout"):
            nested = layout_widgets(attrs["layout"].replace("@layout/", ""), layouts, strings, depth + 1)
            for key in ("fields", "actions", "lists", "labels", "fragments"):
                out[key] += nested[key]
            continue
        if tag in ("fragment", "FragmentContainerView") and attrs.get("name"):
            out["fragments"].append({"class": attrs["name"].split(".")[-1], "id": wid, "nav_graph": attrs.get("navGraph", "").replace("@navigation/", "")})
            continue
        if tag in INPUTS:
            hint = _res(attrs.get("hint", ""), strings)
            parent = parents.get(element)
            if not hint and parent is not None and parent.tag.split(".")[-1] in ("TextInputLayout",):
                hint = _res(parent.attrib.get("hint", ""), strings)
            input_type = attrs.get("inputType", "")
            kind = "password" if "Password" in input_type else "email" if "Email" in input_type else "number" if re.search(r"number|phone", input_type, re.I) else (
                "date" if "date" in input_type.lower() else "text") if tag.endswith(("EditText", "TextView")) else tag.lower()
            out["fields"].append({"name": wid or hint or tag, "widget": tag, "type": kind, "label": hint or _res(attrs.get("text", ""), strings), "input_type": input_type,
                                  "max_length": attrs.get("maxLength", ""), "binding": attrs.get("text", "") if attrs.get("text", "").startswith("@{") or attrs.get("text", "").startswith("@={") else "",
                                  "entries": attrs.get("entries", "").replace("@array/", ""), "line": line})
        elif tag in ACTIONS or (attrs.get("onClick") and tag not in INPUTS):
            out["actions"].append({"label": _res(attrs.get("text", "") or attrs.get("contentDescription", ""), strings) or wid, "id": wid, "widget": tag,
                                   "handler": attrs.get("onClick", ""), "line": line})
        elif tag in LISTS:
            out["lists"].append({"id": wid, "widget": tag, "line": line, "item_layout": attrs.get("listitem", "").replace("@layout/", "")})
        elif tag in ("TextView", "MaterialTextView") and attrs.get("text") and len(out["labels"]) < 25:
            out["labels"].append(_res(attrs["text"], strings))
    return out


def _nav_graphs(ctx: Context, strings: dict[str, str]) -> dict[str, dict]:
    """Fragment / activity class -> {id, label, actions:[target class], arguments:[...], graph}"""
    out: dict[str, dict] = {}
    for file in ctx.files:
        if not (file.text and re.search(r"(^|/)res/navigation/[\w]+\.xml$", file.path)):
            continue
        root = _safe_xml(file.text)
        if root is None:
            continue
        ids = {}
        for element in root.iter():
            if isinstance(element.tag, str) and element.attrib.get("name") and element.attrib.get("id"):
                ids[element.attrib["id"].replace("@+id/", "").replace("@id/", "")] = element.attrib["name"].split(".")[-1]
        start = root.attrib.get("startDestination", "").replace("@id/", "").replace("@+id/", "")
        for element in root.iter():
            if not isinstance(element.tag, str) or element.tag not in ("fragment", "activity", "dialog") or not element.attrib.get("name"):
                continue
            cls = element.attrib["name"].split(".")[-1]
            dest_id = element.attrib.get("id", "").replace("@+id/", "")
            actions = []
            for action in element.findall("action"):
                target = action.attrib.get("destination", "").replace("@id/", "").replace("@+id/", "")
                actions.append({"id": action.attrib.get("id", "").replace("@+id/", ""), "target": ids.get(target, target)})
            out[cls] = {"id": dest_id, "label": _res(element.attrib.get("label", ""), strings), "actions": actions, "graph": file.path,
                        "start": dest_id == start, "layout": element.attrib.get("layout", "").replace("@layout/", ""),
                        "arguments": [{"name": a.attrib.get("name", ""), "type": a.attrib.get("argType", ""), "default": a.attrib.get("defaultValue", "")} for a in element.findall("argument")]}
    return out


def _manifest(ctx: Context, cat: Catalog, strings: dict[str, str]) -> dict[str, dict]:
    activities: dict[str, dict] = {}
    for file in ctx.files:
        if file.name != "AndroidManifest.xml" or not file.text or file.is_test:
            continue
        root = _safe_xml(file.text)
        if root is None:
            continue
        package = root.attrib.get("package", "")
        permissions = sorted({p.attrib.get("name", "").split(".")[-1] for p in root.iter("uses-permission") if p.attrib.get("name")})
        components = []
        for tag in ("activity", "activity-alias", "service", "receiver", "provider"):
            for element in root.iter(tag):
                name = element.attrib.get("name", "").split(".")[-1]
                filters = []
                launcher = False
                for intent in element.findall("intent-filter"):
                    actions = [a.attrib.get("name", "").split(".")[-1] for a in intent.findall("action")]
                    categories = [c.attrib.get("name", "").split(".")[-1] for c in intent.findall("category")]
                    data = [{k: v for k, v in d.attrib.items() if k in ("scheme", "host", "path", "pathPrefix", "pathPattern", "mimeType")} for d in intent.findall("data")]
                    if "MAIN" in actions and "LAUNCHER" in categories:
                        launcher = True
                    filters.append({"actions": actions, "categories": categories, "data": data})
                entry = {"type": tag, "name": name, "exported": element.attrib.get("exported", ""), "label": _res(element.attrib.get("label", ""), strings),
                         "launcher": launcher, "intent_filters": filters, "permission": element.attrib.get("permission", "")}
                components.append(entry)
                if tag in ("activity", "activity-alias"):
                    activities[name] = entry
        app = root.find("application")
        cat.add(Item("shared", "manifest", f"manifest:{file.path}", f"AndroidManifest ({package or file.container or 'app'})", file.path, 1, file.text.count("\n") + 1, "XML", "Android",
                     f"package {package}", {"package": package, "application": (app.attrib.get("name", "") if app is not None else "").split(".")[-1],
                                             "permissions": permissions, "components": components,
                                             "uses_cleartext": (app.attrib.get("usesCleartextTraffic", "") if app is not None else ""),
                                             "network_security_config": (app.attrib.get("networkSecurityConfig", "") if app is not None else "")}))
    return activities


def _call_args(text: str, open_index: int) -> tuple[str, int]:
    close = match_close(text, open_index)
    return text[open_index + 1 : close], close


def _named_arg(args: str, name: str) -> str:
    for part in split_top(args):
        m = re.match(rf"^{name}\s*=\s*([\s\S]*)$", part)
        if m:
            return m.group(1).strip()
    return ""


def _text_label(fragment: str) -> str:
    m = re.search(r"\bText\s*\(\s*(?:text\s*=\s*)?(\"(?:\\.|[^\"\\])*\"|stringResource\s*\(\s*(?:id\s*=\s*)?R\.string\.(\w+)\s*\))", fragment)
    if not m:
        values = string_values(fragment)
        return values[0] if values else ""
    if m.group(2):
        return "@string/" + m.group(2)
    return string_values(m.group(1))[0] if string_values(m.group(1)) else ""


def compose_widgets(body: str, base_line: int, strings: dict[str, str]) -> dict:
    fields, actions, navigation = [], [], []
    for m in re.finditer(r"(?<![\w.])(" + "|".join(COMPOSE_FIELDS) + r")\s*\(", body):
        args, close = _call_args(body, m.end() - 1)
        label = _text_label(_named_arg(args, "label")) or _text_label(_named_arg(args, "placeholder")) or _text_label(_named_arg(args, "text"))
        label = _res(label, strings)
        value = _named_arg(args, "value") or _named_arg(args, "checked") or _named_arg(args, "selected") or (split_top(args)[0] if split_top(args) else "")
        kind = COMPOSE_FIELDS[m.group(1)]
        if "PasswordVisualTransformation" in args:
            kind = "password"
        km = re.search(r"KeyboardType\.(\w+)", args)
        if km:
            kind = {"Email": "email", "Number": "number", "Phone": "phone", "Password": "password", "Decimal": "number", "NumberPassword": "pin", "Uri": "url"}.get(km.group(1), kind)
        fields.append({"name": re.sub(r"[^\w.]", "", value.split(".")[-1] if value else "")[:60] or label or m.group(1), "widget": m.group(1), "type": kind, "label": label,
                       "required": bool(re.search(r"isError\s*=", args)), "validation": _named_arg(args, "isError")[:120], "line": base_line + body.count("\n", 0, m.start())})
    for m in re.finditer(r"(?<![\w.])((?:[A-Z]\w*)?(?:Button|FloatingActionButton|Fab))\s*\(", body):
        args, close = _call_args(body, m.end() - 1)
        on_click = _named_arg(args, "onClick")
        trailing = body[close + 1 : close + 400]
        label = ""
        if trailing.lstrip().startswith("{"):
            start = close + 1 + (len(trailing) - len(trailing.lstrip()))
            end = match_close(body, start)
            label = _text_label(body[start : end + 1])
        if not label:
            res = re.search(r"stringResource\s*\(\s*(?:id\s*=\s*)?R\.string\.(\w+)", trailing[:300])
            icon = re.search(r"Icons\.\w+\.(\w+)", trailing[:300] + args)
            label = ("@string/" + res.group(1)) if res else icon.group(1) if icon else ""
        actions.append({"label": _res(label, strings) or m.group(1), "widget": m.group(1), "handler": re.sub(r"\s+", " ", on_click)[:200],
                        "enabled": _named_arg(args, "enabled")[:120], "line": base_line + body.count("\n", 0, m.start())})
    for m in re.finditer(r"navigate\s*\(\s*(\"[^\"]*\"|[\w.]+(?:\.route)?|\w+\([^)]*\))", body):
        navigation.append({"target": m.group(1).strip('"'), "via": "navController.navigate", "line": base_line + body.count("\n", 0, m.start())})
    return {"fields": fields, "actions": actions, "navigation": navigation}


def _nav_host_routes(ctx: Context) -> dict[str, list[str]]:
    """Composable function name -> routes declared in NavHost graphs."""
    out: dict[str, list[str]] = {}
    for file in ctx.code_files("Kotlin"):
        if "composable" not in file.text:
            continue
        for m in re.finditer(r"\bcomposable\s*(?:<\s*(\w+)\s*>)?\s*\(\s*(?:route\s*=\s*)?([^,){]*)", file.text):
            route = m.group(1) or ctx.resolve(m.group(2).strip()) or m.group(2).strip().strip('"')
            open_paren = file.text.rfind("(", 0, m.end())
            close_paren = match_close(file.text, open_paren)
            brace = file.text.find("{", close_paren)
            if brace < 0 or brace - close_paren > 20:
                continue
            end = match_close(file.text, brace)
            block = file.text[brace : end + 1]
            for call in re.finditer(r"(?<![\w.])([A-Z]\w*(?:Screen|Page|Route|View|Content))\s*\(", block):
                out.setdefault(call.group(1), []).append(route)
    return out


def _click_handlers(text: str, base_line: int) -> list[dict]:
    out = []
    for m in re.finditer(r"(?:binding\??\.)?(\w+)\??\.setOnClickListener\s*(?:\{|\(\s*(?:v\s*->\s*)?)", text):
        start = m.end() - 1
        if text[start] == "{":
            end = match_close(text, start)
        else:
            end = match_close(text, start)
        block = text[start : end + 1]
        calls = re.findall(r"(\w+)\s*\.\s*(\w+)\s*\(", block)
        out.append({"id": m.group(1), "calls": [f"{a}.{b}" for a, b in calls if a not in ("it", "Log")][:6], "line": base_line + text.count("\n", 0, m.start()),
                    "messages": [s for s in string_values(block) if len(s) > 2][:3]})
    return out


def _screen_navigation(text: str, base_line: int) -> list[dict]:
    out = []
    for m in re.finditer(r"Intent\s*\(\s*[\w@.]+\s*,\s*(\w+)::class\.java|Intent\s*\(\s*[\w.]+\s*,\s*(\w+)\.class\)|startActivity<(\w+)>", text):
        out.append({"target": m.group(1) or m.group(2) or m.group(3), "via": "Intent", "line": base_line + text.count("\n", 0, m.start())})
    for m in re.finditer(r"navigate\s*\(\s*(?:R\.id\.(\w+)|(\w+)Directions\.(\w+)\s*\(|\"([^\"]+)\")", text):
        out.append({"target": m.group(1) or (m.group(3) and f"{m.group(2)}.{m.group(3)}") or m.group(4), "via": "Navigation", "line": base_line + text.count("\n", 0, m.start())})
    return out


def extract(ctx: Context, cat: Catalog) -> None:
    if not ctx.android:
        return
    strings = _strings(ctx)
    layouts = _layouts(ctx)
    nav = _nav_graphs(ctx, strings)
    manifest_activities = _manifest(ctx, cat, strings)
    nav_routes = _nav_host_routes(ctx)
    for file in ctx.code_files("Kotlin", "Java"):
        fs = ctx.structure(file)
        if fs is None:
            continue
        for decl in fs.all_types():
            bases = set(decl.base_names())
            ref = TypeRef(decl, fs, file)
            if bases & ACTIVITY_BASES or bases & FRAGMENT_BASES or decl.name.endswith(("Activity", "Fragment")) and decl.kind == "class" and bases:
                _class_screen(ctx, cat, ref, "Activity" if bases & ACTIVITY_BASES or decl.name.endswith("Activity") else "Fragment", layouts, strings, nav, manifest_activities)
            if decl.has("Database") and "RoomDatabase" in bases:
                ann = decl.ann("Database")
                cat.extra.setdefault("room_databases", []).append({"class": decl.name, "entities": ann.idents("entities"), "version": ann.raw("version") or "", "path": file.path,
                                                                    "line": decl.line, "name": _room_name(ctx, decl.name)})
        if file.language == "Kotlin":
            screen_groups: dict[str, list] = {}
            for function in fs.functions:
                if not function.has("Composable"):
                    continue
                if re.search(r"Preview", function.name) or any("Preview" in a.name for a in function.annotations):
                    continue
                routes = nav_routes.get(function.name, [])
                has_vm = any(simple_type(p.type).endswith("ViewModel") or "ViewModel" in (p.default or "") for p in function.params)
                is_screen_name = bool(re.search(r"(Screen|Page|Route|Dialog|BottomSheet|App|Catalog|Dashboard|Home)$", function.name)) and not re.search(r"(Preview|SectionTitle|ChooserRow|Placeholder|Header|Toolbar|Icon|Button|Chip|Tag|Background|Wheel|Scrollbar|Theme|State)$", function.name)
                if not (is_screen_name or routes or has_vm):
                    continue
                screen_groups.setdefault(function.name, []).append(function)
            for fn_name, overloads in screen_groups.items():
                routes = nav_routes.get(fn_name, [])
                helpers = [f for f in fs.functions if f.has("Composable") and f.name != fn_name and not re.search(r"Preview", f.name)]
                _compose_screen(ctx, cat, file, overloads, routes, strings, helpers)


def _room_name(ctx: Context, name: str) -> str:
    for file in ctx.code_files("Kotlin", "Java"):
        m = re.search(rf"databaseBuilder\s*\([^,]+,\s*{name}(?:::class\.java|\.class)\s*,\s*\"([^\"]+)\"", file.text)
        if m:
            return m.group(1)
    return ""


def _class_screen(ctx: Context, cat: Catalog, ref: TypeRef, kind: str, layouts: dict, strings: dict, nav: dict, manifest: dict) -> None:
    decl, file = ref.decl, ref.file
    text = decl.body
    layout = ""
    m = re.search(r"setContentView\s*\(\s*R\.layout\.(\w+)|inflate\s*\(\s*R\.layout\.(\w+)|DataBindingUtil\.setContentView\s*\([^,]+,\s*R\.layout\.(\w+)", text)
    if m:
        layout = next(g for g in m.groups() if g)
    if not layout:
        m = re.search(r"\b(\w+)Binding\.(?:inflate|bind)\b", text) or re.search(r":\s*(\w+)Binding\b", text)
        if m:
            layout = re.sub(r"(?<!^)([A-Z])", r"_\1", m.group(1)).lower()
    if not layout:
        m = re.search(r"(?:Fragment|AppCompatActivity|ComponentActivity)\s*\(\s*R\.layout\.(\w+)", decl.header)
        if m:
            layout = m.group(1)
    if not layout and decl.name in nav and nav[decl.name].get("layout"):
        layout = nav[decl.name]["layout"]
    widgets = layout_widgets(layout, layouts, strings) if layout else {"fields": [], "actions": [], "lists": [], "labels": [], "fragments": [], "path": ""}
    compose = compose_widgets(text, decl.body_line, strings) if "setContent" in text else {"fields": [], "actions": [], "navigation": []}
    handlers = _click_handlers(text, decl.body_line)
    by_id = {h["id"]: h for h in handlers}
    actions = widgets["actions"] + compose["actions"]
    for action in actions:
        handler = by_id.get(action.get("id", ""))
        if handler:
            action["handler"] = ", ".join(handler["calls"])
            action["handler_line"] = handler["line"]
    var_types = jvm.field_types(ref, ctx)
    view_models = sorted({t for t in var_types.values() if t.endswith("ViewModel")})
    navigation = _screen_navigation(text, decl.body_line) + compose["navigation"]
    nav_entry = nav.get(decl.name, {})
    for action in nav_entry.get("actions", []):
        navigation.append({"target": action["target"], "via": f"nav action {action['id']}", "line": 0})
    title = nav_entry.get("label") or manifest.get(decl.name, {}).get("label", "") or re.sub(r"(Activity|Fragment)$", "", decl.name)
    rules = jvm.rules_in(text, decl.body_line, file.language)
    for m in re.finditer(r"(\w+)\.error\s*=\s*(\"[^\"]*\"|getString\(R\.string\.(\w+)\))", text):
        message = string_values(m.group(2))[0] if string_values(m.group(2)) else strings.get(m.group(3) or "", m.group(3) or "")
        rules.append({"kind": "validation", "condition": f"{m.group(1)} invalid", "outcome": f"shows error \"{message}\"", "line": decl.body_line + text.count("\n", 0, m.start())})
    entry = manifest.get(decl.name, {})
    details = {
        "platform": "Android", "type": kind, "title": title, "layout": widgets["path"], "fields": widgets["fields"] + compose["fields"], "actions": actions,
        "lists": widgets["lists"], "labels": widgets["labels"][:15], "navigation": navigation, "view_models": view_models,
        "fragments": [f["class"] for f in widgets["fragments"]], "rules": rules[:20], "launcher": entry.get("launcher", False),
        "deep_links": [d for f in entry.get("intent_filters", []) for d in f.get("data", []) if d.get("scheme")], "nav_arguments": nav_entry.get("arguments", []),
        "routes": [f"nav:{nav_entry['id']}"] if nav_entry.get("id") else [], "start_destination": nav_entry.get("start", False),
    }
    item = cat.add(Item("ui", "screen", f"ui:{decl.name}", title if title != decl.name else decl.name, file.path, decl.line, decl.end_line, file.language, f"Android {kind}",
                        decl.header.split("{")[0].strip()[:240], details, code=jvm._class_excerpt(file.text, decl, 120)))
    cat.type_keys.setdefault(decl.name, item.key)
    for vm in view_models:
        cat.pending_types.append((item.key, vm, "uses"))
    for name, t in var_types.items():
        if t not in view_models and t in ctx.types and not t.endswith("Binding"):
            cat.pending_types.append((item.key, t, "uses"))
    for target in navigation:
        cat.pending_screens.append((item.key, target["target"], "navigates to"))
    for fragment in details["fragments"]:
        cat.pending_screens.append((item.key, fragment, "hosts"))
    calls = jvm.calls_in(text, var_types, decl.body_line)
    cat.pending_calls += [(item.key, c["type"], c["method"], c["line"]) for c in calls]


def _compose_screen(ctx: Context, cat: Catalog, file, overloads: list, routes: list[str], strings: dict, helpers: list | None = None) -> None:
    primary = overloads[0]
    fn_name = primary.name
    widgets = {"fields": [], "actions": [], "navigation": []}
    frontier = []
    for fn in overloads:
        w = compose_widgets(fn.body, fn.body_line or fn.line, strings)
        for key in ("fields", "actions", "navigation"):
            widgets[key] += w[key]
        frontier.append(fn.body)
    seen = {fn_name}
    helper_list = helpers or []
    while frontier and len(seen) < 25:
        text = frontier.pop(0)
        for helper in helper_list:
            hname = helper.name.split(".")[-1]
            if hname not in seen and re.search(rf"(?<![\w.]){re.escape(hname)}\s*\(", text) and not re.search(r"(Screen|Page|Route)$", hname):
                seen.add(hname)
                frontier.append(helper.body)
                extra = compose_widgets(helper.body, helper.body_line or helper.line, strings)
                for key in ("fields", "actions", "navigation"):
                    widgets[key] += [dict(w, section=hname) for w in extra[key]]
    combined_body = "\n".join(fn.body for fn in overloads)
    all_params = [p for fn in overloads for p in fn.params]
    view_models = sorted({simple_type(p.type) for p in all_params if simple_type(p.type).endswith("ViewModel")} |
                         set(re.findall(r"(?:hiltViewModel|viewModel|koinViewModel)\s*<\s*(\w+)\s*>", combined_body)))
    title = ""
    tm = re.search(r"(?:TopAppBar|CenterAlignedTopAppBar|MediumTopAppBar|LargeTopAppBar|AlertDialog)\s*\([\s\S]{0,300}?title\s*=\s*\{([^}]*)\}", combined_body)
    if tm:
        title = _res(_text_label(tm.group(1)), strings)
    if not title:
        tsm = re.search(r"TrackScreenViewEvent\s*\(\s*screenName\s*=\s*\"([^\"]+)\"", combined_body)
        if tsm:
            title = tsm.group(1)
    state = re.findall(r"(?:val|var)\s+(\w+)\s+by\s+(?:remember|rememberSaveable)\s*\{\s*mutableStateOf\(([^)]*)\)", combined_body)
    collected = re.findall(r"(?:val|var)\s+(\w+)\s+by\s+(\w+)\.(\w+)\.collectAsState", combined_body) + re.findall(r"(?:val|var)\s+(\w+)\s+by\s+(\w+)\.(\w+)\.collectAsStateWithLifecycle", combined_body)
    rules = []
    for fn in overloads:
        rules += jvm.rules_in(fn.body, fn.body_line or fn.line, "Kotlin")
    for action in widgets["actions"]:
        if action.get("enabled"):
            rules.append({"kind": "validation", "condition": action["enabled"], "outcome": f"enables \"{action['label']}\"", "line": action["line"]})
    line_start = min(fn.line for fn in overloads)
    line_end = max(fn.end_line for fn in overloads)
    details = {
        "platform": "Android", "type": "Composable", "title": title or re.sub(r"(Screen|Page|Route|Dialog)$", "", fn_name), "fields": widgets["fields"], "actions": widgets["actions"],
        "navigation": widgets["navigation"], "view_models": view_models, "routes": routes, "rules": rules[:20],
        "state": [{"name": n, "initial": v[:60]} for n, v in state] + [{"name": n, "from": f"{vm}.{prop}"} for n, vm, prop in collected],
        "params": [{"name": p.name, "type": p.type} for p in primary.params],
    }
    item = cat.add(Item("ui", "screen", f"ui:{fn_name}", fn_name, file.path, line_start, line_end, "Kotlin", "Jetpack Compose",
                        f"@Composable fun {fn_name}(...)", details, code=snippet(file.text, line_start, line_end, 120)))
    cat.type_keys.setdefault(fn_name, item.key)
    for vm in view_models:
        cat.pending_types.append((item.key, vm, "uses"))
    for target in widgets["navigation"]:
        cat.pending_screens.append((item.key, target["target"], "navigates to"))
    var_types = {p.name: simple_type(p.type) for p in all_params if p.type}
    for fn in overloads:
        for m in re.finditer(r"(?:val|var)\s+(\w+)\s*(?::\s*(\w+))?\s*=\s*(?:hiltViewModel|viewModel|koinViewModel)\s*<?\s*(\w*)", fn.body):
            var_types[m.group(1)] = m.group(2) or m.group(3)
        calls = jvm.calls_in(fn.body, var_types, fn.body_line or fn.line)
        cat.pending_calls += [(item.key, c["type"], c["method"], c["line"]) for c in calls]
