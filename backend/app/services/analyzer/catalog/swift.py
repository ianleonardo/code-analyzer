"""iOS (Swift): SwiftUI views and UIKit view controllers (fields, buttons, navigation, titles), storyboards, view models
(ObservableObject / @Observable), networking (URLSession, Alamofire, Moya-style endpoint enums) and persistence models
(Core Data .xcdatamodel, SwiftData @Model, Realm Object).
"""

from __future__ import annotations

import re

from app.services.analyzer.catalog import jvm
from app.services.analyzer.catalog import sql as sqlmod
from app.services.analyzer.catalog.data import _register, _safe_xml
from app.services.analyzer.catalog.model import Catalog, Context, Item, TypeRef, snippet
from app.services.analyzer.catalog.structure import (
    match_close,
    simple_type,
    split_top,
    string_values,
)

SWIFTUI_FIELDS = {"TextField": "text", "SecureField": "password", "TextEditor": "textarea", "Toggle": "switch", "Picker": "select", "DatePicker": "date",
                  "Stepper": "number", "Slider": "slider", "ColorPicker": "color", "PhotosPicker": "photo", "MultiDatePicker": "date"}
UIKIT_FIELDS = {"UITextField": "text", "UITextView": "textarea", "UISwitch": "switch", "UISegmentedControl": "select", "UIDatePicker": "date",
                "UIPickerView": "select", "UISlider": "slider", "UIStepper": "number", "UISearchBar": "search"}
VIEW_CONTROLLERS = {"UIViewController", "UITableViewController", "UICollectionViewController", "UIPageViewController", "UITabBarController",
                    "UINavigationController", "UIHostingController", "BaseViewController"}


def _label(args: str) -> str:
    parts = split_top(args)
    if not parts:
        return ""
    first = parts[0]
    values = string_values(first)
    if values and first.strip().startswith('"'):
        return values[0]
    m = re.match(r"(?:LocalizedStringKey|NSLocalizedString|String\(localized:)\s*\(?\s*\"([^\"]*)\"", first)
    return m.group(1) if m else ""


def swiftui_widgets(body: str, base_line: int) -> dict:
    fields, actions, navigation = [], [], []
    for m in re.finditer(r"(?<![\w.])(" + "|".join(SWIFTUI_FIELDS) + r")\s*\(", body):
        args = body[m.end() : match_close(body, m.end() - 1)]
        binding = re.search(r"(?:text|isOn|selection|value|date)\s*:\s*\$([\w.]+)", args)
        prompt = re.search(r"prompt\s*:\s*Text\(\s*\"([^\"]*)\"", args)
        label = _label(args) or (prompt.group(1) if prompt else "")
        kind = SWIFTUI_FIELDS[m.group(1)]
        tail = body[m.end() : m.end() + 400]
        km = re.search(r"\.keyboardType\(\s*\.(\w+)", tail.split("\n\n")[0])
        if km and kind == "text":
            kind = {"emailAddress": "email", "numberPad": "number", "decimalPad": "number", "phonePad": "phone", "URL": "url"}.get(km.group(1), kind)
        fields.append({"name": (binding.group(1) if binding else label)[:60] or m.group(1), "widget": m.group(1), "type": kind, "label": label,
                       "line": base_line + body.count("\n", 0, m.start())})
    for m in re.finditer(r"(?<![\w.])Button\s*(\(|\{)", body):
        if m.group(1) == "(":
            close = match_close(body, m.end() - 1)
            args = body[m.end() : close]
            label = _label(args)
            action_text = ""
            am = re.search(r"action\s*:\s*([\w.]+)", args)
            if am:
                action_text = am.group(1)
            trailing = body[close + 1 : close + 600].lstrip()
            if trailing.startswith("{"):
                start = body.find("{", close)
                end = match_close(body, start)
                block = body[start : end + 1]
                if not action_text and "action:" not in args:
                    action_text = re.sub(r"\s+", " ", block[1:-1]).strip()[:200]
                if not label:
                    label = (string_values(block) or [""])[0]
        else:
            start = m.end() - 1
            end = match_close(body, start)
            action_text = re.sub(r"\s+", " ", body[start + 1 : end]).strip()[:200]
            label_block = body[end + 1 : end + 300]
            label = (string_values(label_block.split("}")[0]) or [""])[0]
        disabled = re.search(r"\.disabled\(([^)]*(?:\([^)]*\)[^)]*)*)\)", body[m.end() : m.end() + 800])
        actions.append({"label": label or "Button", "widget": "Button", "handler": action_text, "enabled": f"not ({disabled.group(1)})" if disabled else "",
                        "line": base_line + body.count("\n", 0, m.start())})
    for m in re.finditer(r"\.searchable\s*\(\s*text\s*:\s*\$([\w.]+)(?:\s*,\s*prompt\s*:\s*(?:Text\()?\s*\"([^\"]*)\")?", body):
        fields.append({"name": m.group(1), "widget": "searchable", "type": "search", "label": m.group(2) or "Search",
                       "line": base_line + body.count("\n", 0, m.start())})
    for m in re.finditer(r"NavigationLink\s*\(([^{]*?)(?:destination\s*:\s*([A-Z]\w*)|value\s*:\s*([^)]*))", body):
        navigation.append({"target": m.group(2) or (m.group(3) or "").strip(), "via": "NavigationLink", "line": base_line + body.count("\n", 0, m.start())})
    for m in re.finditer(r"NavigationLink\s*(?:\([^)]*\))?\s*\{\s*([A-Z]\w*)\s*\(", body):
        if m.group(1) not in ("Text", "Label", "Image", "HStack", "VStack", "ZStack"):
            navigation.append({"target": m.group(1), "via": "NavigationLink", "line": base_line + body.count("\n", 0, m.start())})
    for m in re.finditer(r"\.(?:sheet|fullScreenCover|navigationDestination|popover)\s*\([^{]*\{\s*(?:\w+\s+in\s+)?([A-Z]\w*)\s*\(", body):
        navigation.append({"target": m.group(1), "via": "presentation", "line": base_line + body.count("\n", 0, m.start())})
    for m in re.finditer(r"([A-Z]\w*(?:View|Screen|Page))\s*\([^)]*\)\s*\.\s*tabItem\b", body):
        navigation.append({"target": m.group(1), "via": "TabView", "line": base_line + body.count("\n", 0, m.start())})
    title = ""
    tm = re.search(r"\.navigationTitle\(\s*(?:Text\()?\s*\"([^\"]*)\"", body)
    if tm:
        title = tm.group(1)
    return {"fields": fields, "actions": actions, "navigation": navigation, "title": title}


def _storyboards(ctx: Context) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for file in ctx.files:
        if not file.text or not file.path.endswith(".storyboard"):
            continue
        root = _safe_xml(file.text)
        if root is None:
            continue
        ids = {}
        for element in root.iter():
            if isinstance(element.tag, str) and element.tag.endswith("Controller") and element.attrib.get("id"):
                ids[element.attrib["id"]] = element.attrib.get("customClass") or element.attrib.get("storyboardIdentifier") or element.tag
        for element in root.iter():
            if not (isinstance(element.tag, str) and element.tag.endswith("Controller") and element.attrib.get("customClass")):
                continue
            fields, actions, segues = [], [], []
            for child in element.iter():
                tag = child.tag if isinstance(child.tag, str) else ""
                if tag in ("textField", "textView", "switch", "segmentedControl", "datePicker", "pickerView", "slider", "stepper", "searchBar"):
                    fields.append({"name": child.attrib.get("placeholder") or child.attrib.get("userLabel") or tag, "widget": tag, "type": "password" if child.attrib.get("secureTextEntry") == "YES" else tag,
                                   "label": child.attrib.get("placeholder", "")})
                elif tag == "button":
                    state = child.find("state")
                    actions.append({"label": (state.attrib.get("title") if state is not None else "") or child.attrib.get("title", "") or "Button", "widget": "UIButton", "handler": ""})
                elif tag == "segue":
                    segues.append({"target": ids.get(child.attrib.get("destination", ""), child.attrib.get("destination", "")), "via": f"segue {child.attrib.get('kind', '')} {child.attrib.get('identifier', '')}".strip()})
            out[element.attrib["customClass"]] = {"fields": fields, "actions": actions, "navigation": segues, "storyboard": file.path,
                                                  "title": (element.find(".//navigationItem").attrib.get("title", "") if element.find(".//navigationItem") is not None else "")}
    return out


def _core_data(ctx: Context, cat: Catalog) -> None:
    for file in ctx.files:
        if not file.text or ".xcdatamodel" not in file.path:
            continue
        root = _safe_xml(file.text)
        if root is None:
            continue
        for entity in root.iter("entity"):
            name = entity.attrib.get("name", "")
            line = file.text.count("\n", 0, file.text.find(f'name="{name}"')) + 1
            entry = _register(cat, name, file.path, line, "Core Data model")
            entry.update({"entity": entity.attrib.get("representedClassName", name), "entity_path": file.path, "entity_line": line, "orm": "Core Data"})
            columns = [{"name": a.attrib.get("name", ""), "type": a.attrib.get("attributeType", ""), "nullable": a.attrib.get("optional") == "YES", "pk": False, "source": "coredata",
                        "default": a.attrib.get("defaultValueString", "")} for a in entity.findall("attribute")]
            relations = [{"field": r.attrib.get("name", ""), "kind": "OneToMany" if r.attrib.get("toMany") == "YES" else "ManyToOne", "target": r.attrib.get("destinationEntity", ""),
                          "delete_rule": r.attrib.get("deletionRule", "")} for r in entity.findall("relationship")]
            entry["entity_columns"] = columns
            entry["relations"] = relations
            cat.entity_tables[entry["entity"]] = sqlmod.table_key(name)


def _persistence_models(ctx: Context, cat: Catalog, ref: TypeRef) -> bool:
    decl, file = ref.decl, ref.file
    swiftdata = decl.has("Model")
    realm = "Object" in decl.base_names() and re.search(r"@Persisted|@objc dynamic", decl.body or "")
    if not (swiftdata or realm):
        return False
    entry = _register(cat, decl.name, file.path, decl.line, "SwiftData @Model" if swiftdata else "Realm Object")
    entry.update({"entity": decl.name, "entity_path": file.path, "entity_line": decl.line, "orm": "SwiftData" if swiftdata else "Realm"})
    columns, relations = [], []
    for member in decl.fields():
        if member.body or "static" in member.modifiers or member.has("Transient", "Ignored"):
            continue
        t = member.type or ""
        inner = simple_type(t.strip("[]?"))
        if inner and ctx.type(inner, file) and ctx.type(inner, file).decl.kind == "class":
            relations.append({"field": member.name, "kind": "OneToMany" if t.strip().startswith("[") or "List<" in t else "ManyToOne", "target": inner})
            continue
        attribute = member.ann("Attribute")
        columns.append({"name": member.name, "field": member.name, "java_type": t, "nullable": t.strip().endswith("?"), "pk": bool(member.ann("Persisted") and "primaryKey" in member.ann("Persisted").args) or (attribute is not None and ".unique" in attribute.args),
                        "unique": attribute is not None and ".unique" in attribute.args, "source": "swiftdata" if swiftdata else "realm"})
    entry["entity_columns"] = columns
    entry["relations"] = relations
    cat.entity_tables[decl.name] = sqlmod.table_key(decl.name)
    return True


_URL = re.compile(r"(?:URL\s*\(\s*string\s*:\s*|URLComponents\s*\(\s*string\s*:\s*|AF\.request\s*\(\s*|\.request\s*\(\s*|appendingPathComponent\s*\(\s*|baseURL\.appending\s*\(\s*path\s*:\s*|URLRequest\s*\(\s*url\s*:\s*URL\(string:\s*)(\"(?:\\.|[^\"\\])*\"(?:\s*\+\s*[\w.\"\\()]+)*|[\w.]+\s*\+\s*\"[^\"]*\")")


def _networking(ctx: Context, cat: Catalog, ref: TypeRef) -> None:
    decl, file = ref.decl, ref.file
    body = decl.body or ""
    if not re.search(r"URLSession|URLRequest|URLComponents|AF\.request|Alamofire|TargetType|Moya|httpMethod|HTTPMethod|var\s+path\s*:", body):
        return
    calls = []
    for method in decl.methods():
        text = method.body or ""
        verb = (re.search(r"httpMethod\s*=\s*\"(\w+)\"|method\s*:\s*\.(\w+)|HTTPMethod\.(\w+)", text) or [None])
        verb_value = next((g for g in verb.groups() if g), "") if hasattr(verb, "groups") else ""
        for m in _URL.finditer(text):
            url = "".join(string_values(m.group(1))) or m.group(1)
            url = re.sub(r"\\\(([^)]*)\)", r"{\1}", url)
            calls.append({"operation": method.name, "method": (verb_value or "GET").upper(), "path": url, "line": (method.body_line or method.line) + text.count("\n", 0, m.start())})
    # enum-based endpoints (Moya TargetType or a custom APICall protocol): var path / var method switch over the cases
    path_prop = next((m for m in decl.fields() if m.name == "path" and m.body and "switch" in m.body), None)
    method_prop = next((m for m in decl.fields() if m.name in ("method", "httpMethod") and m.body), None)
    if path_prop is not None:
        def cases(text: str) -> list[tuple[list[str], str]]:
            out = []
            parts = re.split(r"\bcase\b|\bdefault\s*:", text)
            for part in parts[1:]:
                head, _, rest = part.partition(":")
                names = re.findall(r"\.(\w+)", head) or ["*"]
                value = re.search(r"return\s+(\"(?:\\.|[^\"\\])*\"|\.\w+)", rest) or re.search(r"^\s*(\"(?:\\.|[^\"\\])*\"|\.\w+)", rest)
                if value:
                    out.append((names, value.group(1)))
            if not out:
                single = re.search(r"return\s+(\"(?:\\.|[^\"\\])*\"|\.\w+)", text) or re.fullmatch(r"\s*(\"(?:\\.|[^\"\\])*\"|\.\w+)\s*", text)
                if single:
                    out.append((["*"], single.group(1)))
            return out

        methods_map = {}
        if method_prop is not None:
            for names, value in cases(method_prop.body):
                verb = (string_values(value) or [value.lstrip(".")])[0].upper()
                for name in names:
                    methods_map[name] = verb
        for names, value in cases(path_prop.body):
            path = re.sub(r"\\\(([^)]*)\)", r"{\1}", (string_values(value) or [""])[0])
            for name in names:
                calls.append({"operation": name, "method": methods_map.get(name, methods_map.get("*", "GET")), "path": path, "line": path_prop.line})
    for call in calls:
        if not call["path"] or not re.search(r"/|https?:", call["path"]):
            continue
        key = f"client:{call['method']} {call['path']}"
        item = cat.add(Item("ui", "api_client", key, f"{call['method']} {call['path']}", file.path, call["line"], call["line"], "Swift",
                            "Alamofire" if "AF.request" in body or "Alamofire" in body else "Moya" if path_prop is not None else "URLSession",
                            f"{decl.name}.{call['operation']}", {"client": decl.name.split(".")[0], "method": call["method"], "path": call["path"], "operation": call["operation"], "params": []},
                            code=snippet(file.text, call["line"] - 3, call["line"] + 12, 16)))
        cat.type_members.setdefault(decl.name.split(".")[0], {})[call["operation"]] = item.key
        cat.type_keys.setdefault(decl.name.split(".")[0], item.key)


def _swift_url_configs(ctx: Context) -> tuple[str, dict[str, str]]:
    prefix = ""
    base_urls = {}
    for f in ctx.code_files("Swift"):
        t = f.text
        pm = re.search(r'(?:apiPrefix|prefix|basePath)\s*=\s*"([^"]+)"', t)
        if pm and not prefix:
            prefix = pm.group(1)
        for m in re.finditer(r'(?:var|let)\s+(\w+?)(?:BaseURL|Url|URL)\b[^=]*=\s*[^"\n]*"(https?://[^"]+)"', t):
            base_urls[m.group(1).lower()] = m.group(2)
        for m in re.finditer(r'(?:var|let)\s+(\w+?)(?:BaseURL|Url|URL)\b[^"\n]*URL\s*\(\s*string:\s*"([^"]+)"', t):
            base_urls[m.group(1).lower()] = m.group(2)
        for m in re.finditer(r'url\s*\(\s*forKey\s*:\s*"(\w+)"\s*,\s*env\s*:\s*"(\w+)"\s*,\s*fallback\s*:\s*"(https?://[^"]+)"\)', t):
            key = m.group(1).lower().replace("baseurl", "").replace("url", "")
            env = m.group(2).lower().replace("_base_url", "").replace("_url", "")
            fallback = m.group(3)
            base_urls[key] = fallback
            base_urls[env] = fallback
        for m in re.finditer(r'case\s+(\w+)\s*=\s*"(https?://[^"]+)"', t):
            base_urls[m.group(1).lower()] = m.group(2)
        for m in re.finditer(r'(?:baseURL|baseUrl)\s*=\s*"(https?://[^"]+)"', t):
            base_urls.setdefault("default", m.group(1))
    return prefix, base_urls


def extract(ctx: Context, cat: Catalog) -> None:
    swift_files = ctx.code_files("Swift")
    if not swift_files and not any(".xcdatamodel" in f.path or f.path.endswith(".storyboard") for f in ctx.files):
        return
    cat.extra["swift_url_config"] = _swift_url_configs(ctx)
    _core_data(ctx, cat)
    boards = _storyboards(ctx)
    for file in swift_files:
        fs = ctx.structure(file)
        if fs is None:
            continue
        for decl in fs.all_types():
            ref = TypeRef(decl, fs, file)
            bases = set(decl.base_names())
            try:
                if _persistence_models(ctx, cat, ref):
                    continue
                _networking(ctx, cat, ref)
                if decl.kind == "struct" and "View" in bases:
                    _swiftui_screen(ctx, cat, ref)
                elif decl.kind == "class" and (bases & VIEW_CONTROLLERS or decl.name.endswith("ViewController")):
                    _uikit_screen(ctx, cat, ref, boards.get(decl.name, {}))
                elif decl.kind in ("class", "actor") and not decl.name.endswith(("Delegate", "AppDelegate")) and ("ObservableObject" in bases or decl.has("Observable", "MainActor") and decl.name.endswith(("ViewModel", "Store", "Model"))):
                    _view_model(ctx, cat, ref)
                elif decl.kind in ("class", "struct", "actor") and re.search(r"(Service|Manager|Repository|Client|API|Api|Store|UseCase|Interactor)$", decl.name):
                    _swift_service(ctx, cat, ref)
                elif decl.kind == "struct" and "App" in bases:
                    root = re.search(r"WindowGroup\s*\{\s*([A-Z]\w*)\s*\(", decl.body)
                    if root:
                        cat.extra.setdefault("ios_roots", []).append(root.group(1))
            except (IndexError, ValueError, KeyError, RecursionError):
                continue
    clients = [c for c in cat.by_kind("api_client") if c.language == "Swift"]
    for item in [i for i in cat.items.values() if i.language == "Swift" and i.kind in ("repository", "service", "view_model")]:
        file = ctx.by_path.get(item.path)
        text = snippet(file.text, item.line_start, item.line_end, 2000) if file else ""
        for client in clients:
            if client.details.get("client") == item.name and re.search(rf"\.{re.escape(client.details.get('operation', ''))}\b", text):
                cat.link(item.key, client.key, "calls", client.details.get("operation", ""))


def _var_types(ref: TypeRef) -> dict[str, str]:
    out = {}
    for member in ref.decl.fields():
        t = member.type
        if not t and member.initializer:
            m = re.match(r"([A-Z]\w*)\s*[.(]", member.initializer)
            t = m.group(1) if m else ""
        if t:
            out[member.name] = simple_type(t)
    for member in ref.decl.methods():
        if member.kind == "constructor":
            for p in member.params:
                out.setdefault(p.name, simple_type(p.type))
    return out


def _is_screen_name(name: str) -> bool:
    return bool(re.search(r"(Screen|Page|View|Scene)$", name)) and not re.search(r"(Row|Cell|Item|Button|Label|Badge|Header|Footer|Icon|Image|Card|Chip|Field|Bar)View$", name)


def _swiftui_screen(ctx: Context, cat: Catalog, ref: TypeRef) -> None:
    decl, file = ref.decl, ref.file
    body = decl.body
    layout_markers = r"NavigationStack|NavigationView|NavigationSplitView|TabView|ScrollView|LazyVStack|LazyVGrid|Form\s*\{|\.navigationTitle|\.toolbar"
    if not _is_screen_name(decl.name) and not re.search(layout_markers, body):
        return
    widgets = swiftui_widgets(body, decl.body_line)
    var_types = _var_types(ref)
    view_models = sorted({t for name, t in var_types.items() if t.endswith(("ViewModel", "Store", "Interactor", "Presenter")) or any(m.name == name and m.has("StateObject", "ObservedObject", "EnvironmentObject", "Bindable") for m in decl.fields())})
    if not (widgets["fields"] or widgets["actions"] or widgets["navigation"] or view_models or re.search(layout_markers + r"|List\s*[({]", body) or re.search(r"(Screen|Page)$", decl.name) or decl.name in cat.extra.get("ios_roots", [])):
        return
    state = [{"name": m.name, "type": m.type, "wrapper": next((a.name for a in m.annotations), ""), "initial": m.initializer[:60]} for m in decl.fields()
             if m.has("State", "Binding", "StateObject", "ObservedObject", "EnvironmentObject", "Environment", "AppStorage", "FocusState", "Query", "Bindable")]
    rules = jvm.rules_in(body, decl.body_line, "Swift")
    for m in re.finditer(r"guard\s+([^{]+?)\s+else\s*\{([^}]*)\}", body):
        outcome = jvm._outcome(m.group(2)) or re.sub(r"\s+", " ", m.group(2)).strip()[:120]
        rules.append({"kind": "guard", "condition": m.group(1).strip()[:200], "outcome": outcome or "stops", "line": decl.body_line + body.count("\n", 0, m.start())})
    for action in widgets["actions"]:
        if action.get("enabled"):
            rules.append({"kind": "validation", "condition": action["enabled"], "outcome": f"enables \"{action['label']}\"", "line": action["line"]})
    details = {"platform": "iOS", "type": "SwiftUI View", "title": widgets["title"] or re.sub(r"(Screen|Page|View|Scene)$", "", decl.name), "fields": widgets["fields"],
               "actions": widgets["actions"], "navigation": widgets["navigation"], "view_models": view_models, "state": state, "rules": rules[:20],
               "root": decl.name in cat.extra.get("ios_roots", [])}
    item = cat.add(Item("ui", "screen", f"ui:{decl.name}", decl.name, file.path, decl.line, decl.end_line, "Swift", "SwiftUI", decl.header.split("{")[0][:200], details,
                        code=jvm._class_excerpt(file.text, decl, 120)))
    cat.type_keys.setdefault(decl.name, item.key)
    for vm in view_models:
        cat.pending_types.append((item.key, vm, "uses"))
    for target in widgets["navigation"]:
        cat.pending_screens.append((item.key, target["target"], "navigates to"))
    calls = jvm.calls_in(body, var_types, decl.body_line)
    cat.pending_calls += [(item.key, c["type"], c["method"], c["line"]) for c in calls]


def _uikit_screen(ctx: Context, cat: Catalog, ref: TypeRef, board: dict) -> None:
    decl, file = ref.decl, ref.file
    fields, actions = list(board.get("fields", [])), list(board.get("actions", []))
    for member in decl.fields():
        t = simple_type(member.type)
        if member.has("IBOutlet") and t in UIKIT_FIELDS:
            fields.append({"name": member.name, "widget": t, "type": UIKIT_FIELDS[t], "label": "", "line": member.line})
        elif member.has("IBOutlet") and t == "UIButton":
            actions.append({"label": member.name, "widget": "UIButton", "handler": "", "line": member.line})
    body = decl.body
    for m in re.finditer(r"(\w+)\.placeholder\s*=\s*\"([^\"]*)\"", body):
        for f in fields:
            if f["name"] == m.group(1):
                f["label"] = m.group(2)
    for member in decl.methods():
        if member.has("IBAction", "objc") and (member.has("IBAction") or "sender" in " ".join(p.name for p in member.params)):
            calls = re.findall(r"(\w+)\s*\.\s*(\w+)\s*\(", member.body or "")
            actions.append({"label": member.name, "widget": "IBAction", "handler": ", ".join(f"{a}.{b}" for a, b in calls[:5]), "line": member.line})
    navigation = list(board.get("navigation", []))
    for m in re.finditer(r"(?:pushViewController|present)\s*\(\s*(?:\w+\s*\(\s*\)\s*|([A-Z]\w*)\s*\()", body):
        if m.group(1):
            navigation.append({"target": m.group(1), "via": "push/present", "line": decl.body_line + body.count("\n", 0, m.start())})
    for m in re.finditer(r"performSegue\s*\(\s*withIdentifier\s*:\s*\"([^\"]+)\"", body):
        navigation.append({"target": m.group(1), "via": "segue", "line": decl.body_line + body.count("\n", 0, m.start())})
    title = board.get("title") or (re.search(r"\btitle\s*=\s*\"([^\"]*)\"", body) or [None, ""])[1]
    var_types = _var_types(ref)
    view_models = sorted({t for t in var_types.values() if t.endswith(("ViewModel", "Presenter", "Interactor"))})
    details = {"platform": "iOS", "type": "UIKit " + next((b for b in decl.base_names() if b in VIEW_CONTROLLERS), "UIViewController"), "title": title or re.sub(r"ViewController$", "", decl.name),
               "fields": fields, "actions": actions, "navigation": navigation, "view_models": view_models, "storyboard": board.get("storyboard", ""),
               "rules": jvm.rules_in(body, decl.body_line, "Swift")[:20]}
    item = cat.add(Item("ui", "screen", f"ui:{decl.name}", decl.name, file.path, decl.line, decl.end_line, "Swift", "UIKit", decl.header.split("{")[0][:200], details,
                        code=jvm._class_excerpt(file.text, decl, 120)))
    cat.type_keys.setdefault(decl.name, item.key)
    for vm in view_models:
        cat.pending_types.append((item.key, vm, "uses"))
    for target in navigation:
        cat.pending_screens.append((item.key, target["target"], "navigates to"))
    calls = jvm.calls_in(body, var_types, decl.body_line)
    cat.pending_calls += [(item.key, c["type"], c["method"], c["line"]) for c in calls]


def _methods_summary(ref: TypeRef, var_types: dict[str, str]) -> tuple[list[dict], list[dict]]:
    methods, all_calls = [], []
    for member in ref.decl.methods():
        if member.kind == "constructor" or "private" in member.modifiers:
            continue
        calls = jvm.calls_in(member.body or "", var_types, member.body_line or member.line)
        all_calls += calls
        methods.append({"name": member.name, "signature": f"{member.name}({', '.join((p.label + ' ' if p.label and p.label != p.name else '') + p.name + ': ' + p.type for p in member.params)})" + (f" -> {member.type}" if member.type else ""),
                        "line": member.line, "end_line": member.end_line, "async": "async" in member.header, "throws": "throws" in member.header,
                        "calls": [f"{c['type']}.{c['method']}" for c in calls], "rules": jvm.rules_in(member.body or "", member.body_line or member.line, "Swift")})
    return methods, all_calls


def _extract_swift_http_calls(decl, member, file, cat: Catalog) -> list[dict]:
    body = member.body or ""
    if not body:
        return []
    prefix, base_urls = cat.extra.get("swift_url_config", ("", {}))

    verb = ""
    vm = re.search(r"\b(?:client|api|session|http)\s*\.\s*(get|post|postEmpty|put|patch|delete|request)\b", body)
    if vm:
        verb = "POST" if vm.group(1) == "postEmpty" else vm.group(1).upper()
    if not verb:
        vm2 = re.search(r"\bmethod:\s*\.(get|post|put|patch|delete)\b", body)
        if vm2:
            verb = vm2.group(1).upper()
    if not verb:
        vm3 = re.search(r"\.httpMethod\s*=\s*[\"'](GET|POST|PUT|PATCH|DELETE)[\"']", body, re.I)
        if vm3:
            verb = vm3.group(1).upper()
    if not verb:
        name_lower = member.name.lower()
        if re.match(r"^(?:fetch|get|list|find|load|read|show)\b", name_lower):
            verb = "GET"
        elif re.match(r"^(?:create|add|insert|save|post|submit|checkout)\b", name_lower):
            verb = "POST"
        elif re.match(r"^(?:update|edit|modify|patch)\b", name_lower):
            verb = "PATCH"
        elif re.match(r"^(?:put|replace)\b", name_lower):
            verb = "PUT"
        elif re.match(r"^(?:delete|remove|clear)\b", name_lower):
            verb = "DELETE"
        else:
            verb = "GET"

    found_calls = []
    for m in re.finditer(r'(?:(\w+)\.)?(\w+?)(?:URL|Url)\s*\(\s*"([^"]+)"\s*\)', body):
        helper_token = m.group(2).lower()
        raw_path = m.group(3)
        found_calls.append((helper_token, raw_path, m.start()))

    if not found_calls:
        for m in re.finditer(r'URL\s*\(\s*string:\s*"([^"]+)"\s*\)', body):
            found_calls.append(("", m.group(1), m.start()))
        for m in re.finditer(r'\b(?:client|api)\s*\.\s*(?:get|post|put|patch|delete|postEmpty)\s*\(\s*"([^"]+)"', body):
            found_calls.append(("", m.group(1), m.start()))

    if not found_calls and ("client." in body or "url" in body or "URLSession" in body):
        for m in re.finditer(r'"(/[\w\-/{}\\()]+)"', body):
            found_calls.append(("", m.group(1), m.start()))

    out = []
    seen = set()
    for helper_token, raw_path, pos in found_calls:
        norm = re.sub(r"\\\((\w+)\)", r"{\1}", raw_path)
        target = helper_token
        if not target:
            target = re.sub(r"(Service|Manager|Repository|Client|API|Api|Store|UseCase|Interactor)$", "", decl.name).lower()
        base_url = base_urls.get(target, "")
        if not base_url:
            for k, u in base_urls.items():
                if k in target or k in decl.name.lower():
                    base_url = u
                    target = k
                    break
        full_path = norm
        if prefix and not norm.startswith(prefix) and not norm.startswith("http"):
            full_path = f"{prefix.rstrip('/')}/{norm.lstrip('/')}"
        sig = (verb, full_path)
        if sig in seen:
            continue
        seen.add(sig)
        call_line = member.body_line + body.count("\n", 0, pos) if member.body_line else member.line
        out.append({
            "method": verb,
            "url": full_path,
            "raw_path": norm,
            "base": base_url,
            "target": target,
            "client": "APIClient",
            "line": call_line,
        })
    return out


def _attach_swift_http_calls(ctx: Context, cat: Catalog, ref: TypeRef, item: Item, methods: list[dict]) -> None:
    decl, file = ref.decl, ref.file
    method_by_name = {m["name"]: m for m in methods}
    all_http_calls = []
    for member in decl.methods():
        if member.kind == "constructor":
            continue
        m_calls = _extract_swift_http_calls(decl, member, file, cat)
        if not m_calls:
            continue
        if member.name in method_by_name:
            method_by_name[member.name]["http_calls"] = m_calls
        all_http_calls.extend(m_calls)
        for call in m_calls:
            client_key = f"client:{call['method']} {call['url']}"
            if client_key not in cat.items:
                cat.add(Item(
                    "ui", "api_client", client_key, f"{call['method']} {call['url']}",
                    file.path, call["line"], call["line"], "Swift", "APIClient",
                    f"{decl.name}.{member.name}",
                    {
                        "client": decl.name,
                        "method": call["method"],
                        "path": call["url"],
                        "raw_path": call["raw_path"],
                        "base_url": call["base"],
                        "target": call["target"],
                        "operation": member.name,
                        "params": [],
                    },
                    code=snippet(file.text, member.line, member.end_line or (member.line + 10), 20),
                ))
            cat.link(item.key, client_key, "calls", member.name)
    if all_http_calls:
        item.details["http_calls"] = all_http_calls


def _view_model(ctx: Context, cat: Catalog, ref: TypeRef) -> None:
    decl, file = ref.decl, ref.file
    var_types = _var_types(ref)
    methods, calls = _methods_summary(ref, var_types)
    state = [{"name": m.name, "type": m.type or m.initializer[:40]} for m in decl.fields() if m.has("Published") or (decl.has("Observable") and "private" not in m.modifiers and not m.body)]
    item = cat.add(Item("ui", "view_model", f"vm:{decl.name}", decl.name, file.path, decl.line, decl.end_line, "Swift", "SwiftUI", decl.header.split("{")[0][:200],
                        {"class": decl.name, "state": state[:30], "methods": methods, "dependencies": sorted({t for t in var_types.values() if t in ctx.types and t != decl.name})},
                        code=jvm._class_excerpt(file.text, decl, 120)))
    cat.type_keys.setdefault(decl.name, item.key)
    for m in methods:
        cat.type_members.setdefault(decl.name, {}).setdefault(m["name"], item.key)
    cat.pending_calls += [(item.key, c["type"], c["method"], c["line"]) for c in calls]
    for t in item.details["dependencies"]:
        cat.pending_types.append((item.key, t, "uses"))
    _attach_swift_http_calls(ctx, cat, ref, item, methods)


def _swift_service(ctx: Context, cat: Catalog, ref: TypeRef) -> None:
    decl, file = ref.decl, ref.file
    var_types = _var_types(ref)
    methods, calls = _methods_summary(ref, var_types)
    if not methods:
        return
    layer = "data" if decl.name.endswith(("Repository", "Store")) else "service"
    kind = "repository" if layer == "data" else "service"
    key = f"{'repo' if layer == 'data' else 'svc'}:{decl.name}"
    if key in cat.items:
        return
    item = cat.add(Item(layer, kind, key, decl.name, file.path, decl.line, decl.end_line, "Swift", "Swift", decl.header.split("{")[0][:200],
                        {"class": decl.name, "methods": methods, "dependencies": sorted({t for t in var_types.values() if t in ctx.types and t != decl.name})},
                        code=jvm._class_excerpt(file.text, decl, 120)))
    cat.type_keys.setdefault(decl.name, item.key)
    for m in methods:
        cat.type_members.setdefault(decl.name, {}).setdefault(m["name"], item.key)
    cat.pending_calls += [(item.key, c["type"], c["method"], c["line"]) for c in calls]
    for t in item.details["dependencies"]:
        cat.pending_types.append((item.key, t, "uses"))
    _attach_swift_http_calls(ctx, cat, ref, item, methods)
