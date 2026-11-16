"""Checks the schemas and samples.

  uv run scripts/check.py               run every check
  uv run scripts/check.py --write-sums  rewrite SHA256SUMS after a change
"""

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import jsonschema_rs

ROOT = Path(__file__).resolve().parent.parent
SUMS = ROOT / "SHA256SUMS"
TAG = re.compile(r"^v\d+\.\d+\.\d+$")
SAMPLE = re.compile(r"^samples/(v\d+)/([a-z-]+)/valid/[^/]+\.json$")


def schema_path(major: str, name: str) -> Path:
    return ROOT / "schemas" / major / f"{name}.schema.json"


def load_schemas() -> dict[tuple[str, str], dict]:
    schemas = {}
    for path in sorted(ROOT.glob("schemas/v*/*.schema.json")):
        schemas[(path.parent.name, path.name.removesuffix(".schema.json"))] = json.loads(path.read_text())
    return schemas


def check_schemas(schemas, errors):
    for (major, name), schema in schemas.items():
        try:
            jsonschema_rs.meta.validate(schema)
        except jsonschema_rs.ValidationError as e:
            errors.append(f"{major}/{name}: not a valid JSON Schema: {e.message}")


def check_samples(schemas, errors):
    for (major, name), schema in schemas.items():
        validator = jsonschema_rs.validator_for(schema)
        valid = sorted((ROOT / "samples" / major / name / "valid").glob("*.json"))
        invalid = sorted((ROOT / "samples" / major / name / "invalid").glob("*.json"))
        if not valid or not invalid:
            errors.append(f"{major}/{name}: needs valid and invalid samples")
        for path in valid:
            for e in validator.iter_errors(json.loads(path.read_text())):
                errors.append(f"{path.relative_to(ROOT)}: {e.message} at /{'/'.join(map(str, e.instance_path))}")
        for path in invalid:
            if validator.is_valid(json.loads(path.read_text())):
                errors.append(f"{path.relative_to(ROOT)}: passes the schema")


def resolve(schema, node, location):
    while "$ref" in node:
        location = node["$ref"]
        node = schema
        for part in location.removeprefix("#/").split("/"):
            node = node[part]
    return node, location


def declared(schema, node, location, properties, enums, seen):
    node, location = resolve(schema, node, location)
    if location in seen:
        return
    seen.add(location)
    if "enum" in node:
        enums[location] = {json.dumps(v) for v in node["enum"]}
    for key, sub in node.get("properties", {}).items():
        properties.add(f"{location}/properties/{key}")
        declared(schema, sub, f"{location}/properties/{key}", properties, enums, seen)
    if "items" in node:
        declared(schema, node["items"], f"{location}/items", properties, enums, seen)


def used(schema, node, location, instance, properties, enums):
    node, location = resolve(schema, node, location)
    if "enum" in node:
        enums.setdefault(location, set()).add(json.dumps(instance))
    if isinstance(instance, dict):
        for key, value in instance.items():
            if key in node.get("properties", {}):
                properties.add(f"{location}/properties/{key}")
                used(schema, node["properties"][key], f"{location}/properties/{key}", value, properties, enums)
    if isinstance(instance, list) and "items" in node:
        for value in instance:
            used(schema, node["items"], f"{location}/items", value, properties, enums)


def check_coverage(schemas, errors):
    for (major, name), schema in schemas.items():
        all_properties, all_enums = set(), {}
        declared(schema, schema, "#", all_properties, all_enums, set())
        seen_properties, seen_enums = set(), {}
        for path in (ROOT / "samples" / major / name / "valid").glob("*.json"):
            used(schema, schema, "#", json.loads(path.read_text()), seen_properties, seen_enums)
        for location in sorted(all_properties - seen_properties):
            errors.append(f"{major}/{name}: property {location} is in no valid sample")
        for location, values in sorted(all_enums.items()):
            for value in sorted(values - seen_enums.get(location, set())):
                errors.append(f"{major}/{name}: value {value} of {location} is in no valid sample")


def git(*args) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout


def check_compatibility(errors):
    tags = [t for t in git("tag", "--list").split() if TAG.match(t)]
    validators = {}
    for tag in tags:
        for path in git("ls-tree", "-r", "--name-only", tag, "samples").split():
            match = SAMPLE.match(path)
            if not match:
                continue
            major, name = match.groups()
            current = schema_path(major, name)
            if not current.exists():
                errors.append(f"{tag}:{path}: {current.relative_to(ROOT)} is gone")
                continue
            if current not in validators:
                validators[current] = jsonschema_rs.validator_for(json.loads(current.read_text()))
            sample = json.loads(git("show", f"{tag}:{path}"))
            if not validators[current].is_valid(sample):
                errors.append(f"{tag}:{path}: fails the current {current.relative_to(ROOT)}")
    print(f"compatibility: {len(tags)} earlier tag(s)")


def check_version(errors):
    version = (ROOT / "VERSION").read_text().strip()
    if not re.match(r"^\d+\.\d+\.\d+$", version):
        errors.append(f"VERSION: {version!r} is not MAJOR.MINOR.PATCH")
    try:
        tags = git("tag", "--points-at", "HEAD").split()
    except subprocess.CalledProcessError:
        tags = []  # no commit yet
    for tag in tags:
        if TAG.match(tag) and tag != f"v{version}":
            errors.append(f"VERSION: {version} differs from the tag {tag} of this commit")
    majors = {p.name for p in (ROOT / "schemas").iterdir()}
    if f"v{version.split('.')[0]}" not in majors:
        errors.append(f"VERSION: {version} has no schemas/v{version.split('.')[0]}/")


def sums() -> str:
    files = [ROOT / "VERSION"]
    files += sorted(p for d in ("schemas", "samples") for p in (ROOT / d).rglob("*.json"))
    return "".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(ROOT)}\n" for p in files)


def main():
    if sys.argv[1:] == ["--write-sums"]:
        SUMS.write_text(sums())
        return
    errors = []
    schemas = load_schemas()
    check_schemas(schemas, errors)
    check_samples(schemas, errors)
    check_coverage(schemas, errors)
    check_compatibility(errors)
    check_version(errors)
    if not SUMS.exists() or SUMS.read_text() != sums():
        errors.append("SHA256SUMS is out of date: run scripts/check.py --write-sums")
    for error in errors:
        print(error)
    print(f"{len(schemas)} schema(s), {len(errors)} error(s)")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
