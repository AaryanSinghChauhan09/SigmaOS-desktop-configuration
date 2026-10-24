"""Declarative system configurator for SigmaOS-configurator /
SigmaOS-control-center / desktop-configuration.

Settings live in a TOML-like file; the engine exposes pure get/set/list, a
plan-then-apply diff engine with a schema (types + allowed keys), and named
profiles that merge deterministically. Applying is a plan first -- the write
step is thin and opt-in.
"""
import argparse, json, re, sys

ENGINE = {
    "name": "sigma-configurator",
    "summary": "Declarative get/set and profile-driven system configuration",
    "language": "python",
    "verbs": ["get", "set", "list", "apply-plan", "profile"],
    "principles": ["Declarative over Imperative", "Plan Before Apply",
                   "Fail-Fast", "KISS", "Deterministic Testing"],
}

cli_doc = """\
$ sigma-configurator get --file system.conf --key appearance.theme
$ sigma-configurator set --file system.conf --key appearance.theme --value "Adwaita-dark"
$ sigma-configurator list --file system.conf
$ sigma-configurator apply-plan --file system.conf --desired desired.json --schema schema.json
$ sigma-configurator profile --file system.conf --profile base=laptop.json --profile work=work.json
"""


class ConfiguratorError(Exception):
    pass


# --- minimal TOML-like layer: [section] headers, key = value, # comments ---


def parse_toml(text):
    """Pure: parse a TOML subset into a nested dict."""
    data, section = {}, None
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            header = line[1:-1].strip()
            if not header or not re.match(r"^[A-Za-z0-9_.-]+$", header):
                raise ConfiguratorError(f"line {lineno}: bad section {header!r}")
            section = data
            for part in header.split("."):
                section = section.setdefault(part, {})
                if not isinstance(section, dict):
                    raise ConfiguratorError(f"line {lineno}: section conflict")
            continue
        if "=" not in line:
            raise ConfiguratorError(f"line {lineno}: expected key = value")
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if section is None:
            raise ConfiguratorError(f"line {lineno}: key outside a section")
        if key in section:
            raise ConfiguratorError(f"line {lineno}: duplicate key {key!r}")
        section[key] = _parse_value(value, lineno)
    return data


def _parse_value(value, lineno):
    if value.startswith('"') and value.endswith('"') and len(value) >= 2:
        return value[1:-1]
    if value.startswith("'") and value.endswith("'") and len(value) >= 2:
        return value[1:-1]
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_parse_value(v.strip(), lineno) for v in inner.split(",")]
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    raise ConfiguratorError(f"line {lineno}: unquoted string {value!r}")


def _format_value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, str):
        return f'"{v}"'
    if isinstance(v, list):
        return "[" + ", ".join(_format_value(x) for x in v) + "]"
    return str(v)


def format_toml(data):
    """Pure inverse of parse_toml (deterministic ordering)."""
    lines = []
    for section in sorted(data):
        node = data[section]
        if not isinstance(node, dict):
            raise ConfiguratorError(f"top-level key must be a table: {section!r}")
        lines.append(f"[{section}]")
        scalars = {k: v for k, v in node.items() if not isinstance(v, dict)}
        tables = {k: v for k, v in node.items() if isinstance(v, dict)}
        for k in sorted(scalars):
            lines.append(f"{k} = {_format_value(scalars[k])}")
        for sub in sorted(tables):
            lines.append(f"[{section}.{sub}]")
            for k in sorted(tables[sub]):
                lines.append(f"{k} = {_format_value(tables[sub][k])}")
        lines.append("")
    return "\n".join(lines)


def get(data, dotted):
    """Pure: dotted lookup 'a.b.c'."""
    node = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            raise ConfiguratorError(f"no such key: {dotted}")
        node = node[part]
    return node


def set_key(data, dotted, value):
    """Pure: dotted assignment; returns the mutated dict."""
    parts = dotted.split(".")
    if any(not re.match(r"^[A-Za-z0-9_-]+$", p) for p in parts):
        raise ConfiguratorError(f"invalid key: {dotted!r}")
    node = data
    for part in parts[:-1]:
        nxt = node.setdefault(part, {})
        if not isinstance(nxt, dict):
            raise ConfiguratorError(f"key conflict at {dotted}")
        node = nxt
    node[parts[-1]] = value
    return data


def list_keys(data, prefix=""):
    """Pure: flatten to sorted dotted keys with values."""
    out = {}
    for k in sorted(data):
        key = f"{prefix}.{k}" if prefix else k
        v = data[k]
        if isinstance(v, dict):
            out.update(list_keys(v, key))
        else:
            out[key] = v
    return out


# --- schema-checked apply planning ---

_TYPEMAP = {"str": str, "int": int, "float": (int, float), "bool": bool,
            "list": list}


def validate(data, schema):
    """Pure: check a config dict against {dotted_key: {type, choices?, default?}}."""
    problems = []
    for key, rule in schema.items():
        expected = rule.get("type", "str")
        pytype = _TYPEMAP.get(expected)
        if pytype is None:
            problems.append(f"{key}: unknown schema type {expected!r}")
            continue
        try:
            value = get(data, key)
        except ConfiguratorError:
            if "default" in rule:
                continue
            problems.append(f"{key}: missing required key")
            continue
        if isinstance(value, bool) and expected in ("int", "float"):
            problems.append(f"{key}: expected {expected}, got bool")
            continue
        if not isinstance(value, pytype):
            problems.append(f"{key}: expected {expected}, got {type(value).__name__}")
            continue
        choices = rule.get("choices")
        if choices is not None and value not in choices:
            problems.append(f"{key}: {value!r} not in choices {choices}")
    return problems


def plan_apply(current, desired, schema):
    """Pure: ordered set/unset operations to reach desired from current."""
    problems = validate(desired, schema)
    if problems:
        raise ConfiguratorError("schema violations: " + "; ".join(problems))
    cur_flat, des_flat = list_keys(current), list_keys(desired)
    steps = []
    for key in sorted(des_flat):
        if key not in cur_flat or cur_flat[key] != des_flat[key]:
            steps.append({"op": "set", "key": key,
                          "from": cur_flat.get(key), "to": des_flat[key]})
    for key in sorted(cur_flat):
        rule = schema.get(key, {})
        if key not in des_flat and "default" in rule and cur_flat[key] != rule["default"]:
            steps.append({"op": "reset", "key": key,
                          "from": cur_flat[key], "to": rule["default"]})
    return steps


def apply_steps(data, steps):
    """Pure mutation: apply planned steps (idempotent by construction)."""
    for step in steps:
        if step["op"] == "set":
            set_key(data, step["key"], step["to"])
        elif step["op"] == "reset":
            set_key(data, step["key"], step["to"])
        else:
            raise ConfiguratorError(f"unknown op {step['op']!r}")
    return data


def merge_profiles(profiles):
    """Pure: merge (name, dict) pairs in order; later wins; report conflicts."""
    merged, conflicts = {}, []
    for name, prof in profiles:
        flat = list_keys(prof)
        for key, value in flat.items():
            if key in merged and merged[key] != value:
                conflicts.append({"key": key, "from": merged[key], "to": value,
                                  "profile": name})
            merged[key] = value
    data = {}
    for key in sorted(merged):
        set_key(data, key, merged[key])
    return data, conflicts


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog=ENGINE["name"], description=ENGINE["summary"])
    sub = p.add_subparsers(dest="cmd", required=True)
    gt = sub.add_parser("get", help="read a dotted key")
    gt.add_argument("--file", required=True)
    gt.add_argument("--key", required=True)
    st = sub.add_parser("set", help="write a dotted key")
    st.add_argument("--file", required=True)
    st.add_argument("--key", required=True)
    st.add_argument("--value", required=True)
    ls = sub.add_parser("list", help="list all keys")
    ls.add_argument("--file", required=True)
    ap = sub.add_parser("apply-plan", help="diff desired vs current against schema")
    ap.add_argument("--file", required=True)
    ap.add_argument("--desired", required=True, help="JSON desired state")
    ap.add_argument("--schema", default=None, help="JSON schema file")
    ap.add_argument("--execute", action="store_true", help="write the file")
    pf = sub.add_parser("profile", help="merge named profiles and show conflicts")
    pf.add_argument("--profile", action="append", required=True,
                    help="name=path.json (later overrides earlier)")
    pf.add_argument("--file", default=None, help="write merged result here")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.cmd in ("get", "set", "list", "apply-plan"):
            with open(args.file) as fh:
                data = parse_toml(fh.read())
        if args.cmd == "get":
            print(json.dumps(get(data, args.key)))
        elif args.cmd == "set":
            set_key(data, args.key, _parse_value(args.value, 0))
            with open(args.file, "w") as fh:
                fh.write(format_toml(data))
            print(f"set {args.key}")
        elif args.cmd == "list":
            for key, value in list_keys(data).items():
                print(f"{key} = {_format_value(value)}")
        elif args.cmd == "apply-plan":
            with open(args.desired) as fh:
                desired = json.load(fh)
            schema = {}
            if args.schema:
                with open(args.schema) as fh:
                    schema = json.load(fh)
            steps = plan_apply(data, desired, schema)
            print(json.dumps(steps, indent=2))
            if args.execute and steps:
                apply_steps(data, steps)
                with open(args.file, "w") as fh:
                    fh.write(format_toml(data))
                print(f"applied {len(steps)} step(s)")
        elif args.cmd == "profile":
            profiles = []
            for spec in args.profile:
                name, _, path = spec.partition("=")
                with open(path) as fh:
                    profiles.append((name, json.load(fh)))
            merged, conflicts = merge_profiles(profiles)
            print(json.dumps(merged, indent=2, sort_keys=True))
            for c in conflicts:
                print(f"conflict: {c['key']} {c['from']!r} -> {c['to']!r} "
                      f"(from profile {c['profile']})", file=sys.stderr)
            if args.file:
                with open(args.file, "w") as fh:
                    fh.write(format_toml(merged))
        return 0
    except (ConfiguratorError, OSError, json.JSONDecodeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


def self_test() -> None:
    doc = """\
# SigmaOS system configuration
[appearance]
theme = "Adwaita-dark"
icon_theme = "Papirus"
font_size = 11

[appearance.window]
titlebar = "compact"
cornerRadius = 8

[power]
[performance]
mode = "balanced"
autoSuspend = true
"""
    data = parse_toml(doc)
    assert data["appearance"]["theme"] == "Adwaita-dark"
    assert data["appearance"]["window"]["cornerRadius"] == 8
    assert data["power"] == {}
    assert data["performance"]["autoSuspend"] is True
    assert get(data, "appearance.window.titlebar") == "compact"
    try:
        get(data, "appearance.nope")
    except ConfiguratorError as e:
        assert "no such key" in str(e)
    else:
        raise AssertionError("missing key not caught")

    # round-trip
    text = format_toml(data)
    assert parse_toml(text) == data
    assert "[appearance.window]" in text

    # set + list
    set_key(data, "appearance.theme", "Adwaita")
    assert get(data, "appearance.theme") == "Adwaita"
    set_key(data, "network.vpn.endpoint", "vpn.example.com")
    assert get(data, "network.vpn.endpoint") == "vpn.example.com"
    flat = list_keys(data)
    assert "appearance.window.titlebar" in flat
    assert flat["network.vpn.endpoint"] == "vpn.example.com"

    for bad_doc in ("key_outside = 1\n", "[a]\nx\n", "[a]\nx = y\n",
                    "[a]\nx = 1\n[a]\nx = 2\n"):
        try:
            parse_toml(bad_doc)
        except ConfiguratorError:
            pass
        else:
            raise AssertionError(f"bad doc not caught: {bad_doc!r}")

    # schema validation + apply planning
    schema = {
        "appearance.theme": {"type": "str",
                             "choices": ["Adwaita", "Adwaita-dark", "HighContrast"]},
        "appearance.font_size": {"type": "int"},
        "performance.mode": {"type": "str", "choices": ["powersave", "balanced",
                                                        "performance"],
                             "default": "balanced"},
        "power.autoSuspend": {"type": "bool", "default": True},
    }
    current = parse_toml(doc)
    desired = {"appearance": {"theme": "HighContrast", "font_size": 13},
               "performance": {"mode": "performance"}}
    steps = plan_apply(current, desired, schema)
    by_key = {s["key"]: s for s in steps}
    assert by_key["appearance.theme"]["from"] == "Adwaita-dark"
    assert by_key["appearance.theme"]["to"] == "HighContrast"
    assert by_key["appearance.font_size"]["to"] == 13
    assert by_key["performance.mode"]["to"] == "performance"
    assert "power.autoSuspend" not in by_key  # already at default
    # applying is idempotent
    mutated = apply_steps(parse_toml(doc), steps)
    assert plan_apply(mutated, desired, schema) == []

    bad_desired = {"appearance": {"theme": "NoSuchTheme"}}
    try:
        plan_apply(current, bad_desired, schema)
    except ConfiguratorError as e:
        assert "choices" in str(e)
    else:
        raise AssertionError("bad choice not caught")
    try:
        plan_apply(current, {"appearance": {"font_size": "huge"}}, schema)
    except ConfiguratorError as e:
        assert "expected int" in str(e)
    else:
        raise AssertionError("bad type not caught")

    # profiles
    base = {"appearance": {"theme": "Adwaita"}, "power": {"autoSuspend": True}}
    work = {"appearance": {"theme": "HighContrast"},
            "network": {"proxy": "proxy.corp.example"}}
    merged, conflicts = merge_profiles([("base", base), ("work", work)])
    assert merged["appearance"]["theme"] == "HighContrast"  # later wins
    assert merged["network"]["proxy"] == "proxy.corp.example"
    assert len(conflicts) == 1
    assert conflicts[0]["key"] == "appearance.theme"
    assert conflicts[0]["from"] == "Adwaita" and conflicts[0]["profile"] == "work"
    m2, c2 = merge_profiles([("a", {"x": {"k": 1}}), ("b", {"x": {"k": 1}})])
    assert c2 == [] and m2["x"]["k"] == 1  # same value: no conflict


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
