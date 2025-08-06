"""
Maven → Gradle Kotlin DSL converter **with live version-catalog sync** and
internal-project detection.

After merging Maven modules into your mono-repo, run:

    python maven_to_gradle.py /path/to/repo -r --dry-run

What it does
============
1. **Walks** the repo (optionally recursive) and skips aggregator / root POMs.
2. **Parses** each leaf `pom.xml`, resolving:
   * *property placeholders* (e.g. `${junit.version}`) against the **root** POM
     `<properties>` section.
   * internal modules whose group starts with **`com.barclays.asgard`** – these
     become Gradle project dependencies via the `settings.gradle.kts` include
     path (e.g. `project(":core:fix")`).
3. **Generates** a `build.gradle.kts` beside each POM:
   * sets `group` + `version`.
   * converts dependencies:
        * internal → `project("…")`.
        * external → `libs.<alias>` (looked up or appended to
          `gradle/libs.versions.toml`).
4. **Writes/updates** `gradle/libs.versions.toml` in one pass.

CLI flags
---------
-r, --recursive   recurse into sub-directories.
--dry-run         show what would change without writing.
--overwrite       replace existing `build.gradle.kts` files.

(No more `--init`: the script assumes your Gradle wrapper + catalog already
exist.)

Limitations
-----------
* Maven plugin configs aren’t migrated.
* Profiles are ignored; only property placeholders in `<dependencies>` are
  resolved.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys
import textwrap
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

try:
    import tomllib  # Python ≥3.11
except ModuleNotFoundError:  # pragma: no cover
    try:
        import toml as tomllib  # type: ignore
    except ModuleNotFoundError:
        print("ERROR: tomllib (3.11+) or toml package required", file=sys.stderr)
        sys.exit(1)

# --------------------------------------------------------------------------------------
# Dataclasses / constants
# --------------------------------------------------------------------------------------

ScopeMapping = {
    "compile": "implementation",
    "runtime": "runtimeOnly",
    "provided": "compileOnly",
    "test": "testImplementation",
    "testCompile": "testImplementation",
}

@dataclass
class Dependency:
    group: str
    artifact: str
    version: Optional[str]
    scope: str = "compile"

    def module_notation(self) -> str:
        return f"{self.group}:{self.artifact}"

@dataclass
class PomInfo:
    group: str
    artifact: str
    version: str
    packaging: str
    dependencies: List[Dependency] = field(default_factory=list)

# --------------------------------------------------------------------------------------
# Helpers: settings.gradle.kts → internal module map
# --------------------------------------------------------------------------------------

def load_internal_modules(repo_root: pathlib.Path) -> Dict[str, str]:
    """Return artifactId → include-path (":foo:bar") for internal modules."""
    settings = repo_root / "settings.gradle.kts"
    if not settings.exists():
        return {}
    paths = re.findall(r"include\((?:\s*)\"([^\"]+)\"\s*\)", settings.read_text())
    mapping = {}
    for path in paths:
        artifact = path.strip(":").split(":")[-1]
        mapping[artifact] = f":{path.strip(":")}"  # ensure leading colon
    return mapping

# --------------------------------------------------------------------------------------
# Helpers: root POM properties
# --------------------------------------------------------------------------------------

def load_root_properties(repo_root: pathlib.Path) -> Dict[str, str]:
    root_pom = repo_root / "pom.xml"
    if not root_pom.exists():
        return {}
    ns = {"m": "http://maven.apache.org/POM/4.0.0"}
    try:
        props = {}
        tree = ET.parse(root_pom)
        for prop in tree.findall("m:properties/*", ns):
            if prop.text:
                props[prop.tag.split("}")[-1]] = prop.text.strip()
        return props
    except ET.ParseError:
        return {}

# --------------------------------------------------------------------------------------
# Helpers: version catalog
# --------------------------------------------------------------------------------------

def load_catalog(repo_root: pathlib.Path) -> Tuple[pathlib.Path, Dict[str, str], List[str]]:
    catalog = repo_root / "gradle" / "libs.versions.toml"
    catalog.parent.mkdir(parents=True, exist_ok=True)
    if not catalog.exists():
        catalog.write_text("[libraries]\n", encoding="utf-8")
    lines = catalog.read_text(encoding="utf-8").splitlines()
    data = tomllib.loads("\n".join(lines))
    libs = data.get("libraries", {})
    mod_to_alias = {}
    for alias, entry in libs.items():
        module = entry["module"] if isinstance(entry, dict) else entry.split(":", 2)[:2]
        if isinstance(module, list):
            module = ":".join(module)
        mod_to_alias[module] = alias
    return catalog, mod_to_alias, lines


def normalise_alias(artifact: str, existing: Dict[str, str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", artifact.lower()).strip("_")
    alias = base or "lib"
    i = 2
    while alias in existing.values():
        alias = f"{base}_{i}" if base else f"lib_{i}"
        i += 1
    return alias


def append_alias(lines: List[str], alias: str, dep: Dependency):
    if not any(line.strip() == "[libraries]" for line in lines):
        lines.append("[libraries]")
    lines.append(f"{alias} = \"{dep.group}:{dep.artifact}:{dep.version}\"")

# --------------------------------------------------------------------------------------
# POM parsing & build generation
# --------------------------------------------------------------------------------------

def find_poms(root: pathlib.Path, recursive: bool) -> List[pathlib.Path]:
    glob = "**/pom.xml" if recursive else "pom.xml"
    return list(root.glob(glob))


def is_root_or_aggregator(pom: pathlib.Path, repo_root: pathlib.Path) -> bool:
    if pom.parent == repo_root:
        return True
    try:
        tree = ET.parse(pom)
        return tree.find("./{*}modules") is not None
    except ET.ParseError:
        return True


def parse_pom(path: pathlib.Path, root_props: Dict[str, str]) -> PomInfo:
    ns = {"m": "http://maven.apache.org/POM/4.0.0"}
    tree = ET.parse(path)
    root = tree.getroot()

    def txt(tag: str, default: str = "") -> str:
        el = root.find(f"m:{tag}", ns)
        return el.text.strip() if el is not None and el.text else default

    group = txt("groupId") or txt("parent/m:groupId")
    artifact = txt("artifactId")
    version = txt("version") or txt("parent/m:version")
    packaging = txt("packaging", "jar")

    # local property overrides
    local_props = {p.tag.split("}")[-1]: (p.text or "").strip() for p in root.findall("m:properties/*", ns)}
    props = {**root_props, **local_props}

    deps: List[Dependency] = []
    for d in root.findall("m:dependencies/m:dependency", ns):
        g = (d.findtext("m:groupId", namespaces=ns) or "").strip()
        a = (d.findtext("m:artifactId", namespaces=ns) or "").strip()
        v_raw = d.findtext("m:version", default="", namespaces=ns)
        v = v_raw.strip() if v_raw else None
        if v and v.startswith("${") and v.endswith("}"):
            v = props.get(v[2:-1])
        s = (d.findtext("m:scope", default="compile", namespaces=ns) or "compile").strip()
        deps.append(Dependency(g, a, v, s))
    return PomInfo(group, artifact, version, packaging, deps)

# --------------------------------------------------------------------------------------
# Dependency translation
# --------------------------------------------------------------------------------------

def gradle_line(dep: Dependency, mod_to_alias: Dict[str, str], cat_lines: List[str], internal: Dict[str, str]) -> Tuple[str, str]:
    conf = ScopeMapping.get(dep.scope, "implementation")

    # 1) internal project?
    if dep.group.startswith("com.barclays.asgard"):
        path = internal.get(dep.artifact)
        if path:
            return conf, f"project(\"{path}\")"
        # fallthrough to external handling if path unknown

    module = dep.module_notation()
    if module in mod_to_alias:
        return conf, f"libs.{mod_to_alias[module]}"

    if dep.version is None:
        raise ValueError(f"No version for {module}")

    alias = normalise_alias(dep.artifact, mod_to_alias)
    append_alias(cat_lines, alias, dep)
    mod_to_alias[module] = alias
    return conf, f"libs.{alias}"


def build_script(info: PomInfo, mod_to_alias: Dict[str, str], cat_lines: List[str], internal: Dict[str, str]) -> str:
    bucket: Dict[str, List[str]] = {}
    for d in info.dependencies:
        conf, line = gradle_line(d, mod_to_alias, cat_lines, internal)
        bucket.setdefault(conf, []).append(line)

    deps_block = "\n".join(
        f"    {c}({l})" for c, lst in bucket.items() for l in lst
    )

    return textwrap.dedent(
        f"""
        plugins {{
            java
        }}

        group = \"{info.group}\"
        version = \"{info.version}\"

        dependencies {{
        {deps_block}
        }}
        """
    ).strip() + "\n"

# --------------------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------------------

def process(repo_root: pathlib.Path, recursive: bool, dry: bool, overwrite: bool):
    catalog_path, mod_to_alias, cat_lines = load_catalog(repo_root)
    internal = load_internal_modules(repo_root)
    root_props = load_root_properties(repo_root)

    for pom in find_poms(repo_root, recursive):
        if is_root_or_aggregator(pom, repo_root):
            continue
        info = parse_pom(pom, root_props)
        try:
            script = build_script(info, mod_to_alias, cat_lines, internal)
        except ValueError as e:
            print(f"WARN  {pom.relative_to(repo_root)} → {e}")
            continue

        out = pom.parent / "build.gradle.kts"
        if out.exists() and not overwrite:
            print(f"SKIP  {out.relative_to(repo_root)} (exists)")
            continue

        if dry:
            print(f"----- {out.relative_to(repo_root)} -----\n{script}\n")
        else:
            out.write_text(script, encoding="utf-8")
            print(f"WRITE {out.relative_to(repo_root)}")

    if not dry:
        catalog_path.write_text("\n".join(cat_lines) + "\n", encoding="utf-8")
        print(f"UPDATED {catalog_path.relative_to(repo_root)}")

# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Convert Maven POMs to Gradle build scripts & sync catalog.")
    ap.add_argument("path", type=pathlib.Path, help="Repo root")
    ap.add_argument("-r", "--recursive", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()

    root = args.path.expanduser().resolve()
    if not root.exists():
        ap.error(f"Path {root} does not exist")

    process(root, args.recursive, args.dry_run, args.overwrite)

if __name__ == "__main__":
    main()
