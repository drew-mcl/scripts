#!/usr/bin/env python3
"""
Maven → Gradle Kotlin DSL converter
==================================
• Creates/updates *build.gradle.kts* for every leaf `pom.xml`.
• Keeps **gradle/libs.versions.toml** in sync.
• Registers new **internal modules** (`com.barclays.asgard.*`) in
  *settings.gradle.kts* automatically.

Typical usage
-------------
```bash
python maven_to_gradle.py /repo/root -r --dry-run   # preview
python maven_to_gradle.py /repo/root -r             # write changes
```
Flags:
* `-r / --recursive`   – walk sub-directories.
* `--dry-run`           – print changes, don’t write.
* `--overwrite`         – replace existing build files.

Assumptions
-----------
* Gradle wrapper & catalog already exist under `gradle/`.
* Root `pom.xml` holds shared `<properties>` for version placeholders.
* Internal module path defaults to its directory location; you can tweak the
  logic if your naming differs.
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
        print("ERROR: tomllib (Py3.11+) or toml package required", file=sys.stderr)
        sys.exit(1)

# --------------------------------------------------------------------------------------
# Constants & data classes
# --------------------------------------------------------------------------------------

INTERNAL_PREFIX = "com.barclays.asgard"
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
# settings.gradle.kts helpers
# --------------------------------------------------------------------------------------

def load_settings(repo_root: pathlib.Path) -> Tuple[pathlib.Path, Dict[str, str], List[str]]:
    """Return (path, artifact→include-path, file-lines). Creates file if absent."""
    settings_path = repo_root / "settings.gradle.kts"
    if not settings_path.exists():
        settings_path.write_text("", encoding="utf-8")
    lines = settings_path.read_text(encoding="utf-8").splitlines()

    includes = {}
    for line in lines:
        m = re.match(r'\s*include\(\s*"(:[^"]+)"\s*\)', line)
        if m:
            path = m.group(1)
            # Handle both simple module names and monorepo module names (e.g., "remote-compute:module")
            if ":" in path:
                # Monorepo module: extract the full module name
                artifact = path.strip(":")
            else:
                # Simple module: extract just the artifact name
                artifact = path.strip(":").split(":")[-1]
            includes[artifact] = path
    return settings_path, includes, lines


def add_module_to_settings(lines: List[str], artifact: str, path_rel: str):
    include_line = f'include(":{artifact}")'
    # Use relative path from root for projectDir in monorepo
    dir_line = f'project(":{artifact}").projectDir = file("{path_rel}")'
    lines.extend([include_line, dir_line])

# --------------------------------------------------------------------------------------
# Root POM properties
# --------------------------------------------------------------------------------------

def load_root_properties(repo_root: pathlib.Path) -> Dict[str, str]:
    root_pom = repo_root / "pom.xml"
    if not root_pom.exists():
        return {}
    ns = {"m": "http://maven.apache.org/POM/4.0.0"}
    props = {}
    try:
        tree = ET.parse(root_pom)
        for p in tree.findall("m:properties/*", ns):
            if p.text:
                props[p.tag.split("}")[-1]] = p.text.strip()
    except ET.ParseError:
        pass
    return props

# --------------------------------------------------------------------------------------
# Version catalog helpers
# --------------------------------------------------------------------------------------

def load_catalog(repo_root: pathlib.Path) -> Tuple[pathlib.Path, Dict[str, str], List[str]]:
    catalog = repo_root / "gradle" / "libs.versions.toml"
    catalog.parent.mkdir(parents=True, exist_ok=True)
    if not catalog.exists():
        catalog.write_text("[versions]\n[libraries]\n", encoding="utf-8")
    raw_lines = catalog.read_text(encoding="utf-8").splitlines()
    data = tomllib.loads("\n".join(raw_lines))
    mapping = {}
    for alias, entry in data.get("libraries", {}).items():
        if isinstance(entry, dict) and "module" in entry:
            module = entry["module"]
        else:
            # Handle old format for backward compatibility
            module = ":".join(entry.split(":")[:2]) if isinstance(entry, str) else ""
        mapping[module] = alias
    return catalog, mapping, raw_lines


def normalise_alias(artifact: str, taken: Dict[str, str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", artifact.lower()).strip("_") or "lib"
    alias = base
    i = 2
    while alias in taken.values():
        alias = f"{base}_{i}" if base else f"lib_{i}"
        i += 1
    return alias


def append_library(lines: List[str], alias: str, dep: Dependency):
    # Ensure we have both [versions] and [libraries] sections
    has_versions = any(l.strip() == "[versions]" for l in lines)
    has_libraries = any(l.strip() == "[libraries]" for l in lines)
    
    if not has_versions:
        lines.append("[versions]")
    if not has_libraries:
        lines.append("[libraries]")
    
    # Add version entry
    version_alias = f"{alias}_version"
    lines.append(f'{version_alias} = "{dep.version}"')
    
    # Add library entry with version reference
    lines.append(f'{alias} = {{ module = "{dep.group}:{dep.artifact}", version.ref = "{version_alias}" }}')

# --------------------------------------------------------------------------------------
# POM parsing
# --------------------------------------------------------------------------------------

def find_poms(root: pathlib.Path, recursive: bool) -> List[pathlib.Path]:
    return list(root.glob("**/pom.xml" if recursive else "pom.xml"))


def is_aggregator(pom: pathlib.Path, repo_root: pathlib.Path) -> bool:
    if pom.parent == repo_root:
        return True
    try:
        tree = ET.parse(pom)
        return tree.find("./{*}modules") is not None
    except ET.ParseError:
        return True


def parse_pom(path: pathlib.Path, root_props: Dict[str, str]) -> PomInfo:
    ns = {"m": "http://maven.apache.org/POM/4.0.0"}
    root = ET.parse(path).getroot()

    def t(selector: str, default: str = "") -> str:
        el = root.find(f"m:{selector}", ns)
        return el.text.strip() if el is not None and el.text else default

    group = t("groupId") or t("parent/m:groupId")
    artifact = t("artifactId")
    version = t("version") or t("parent/m:version")
    packaging = t("packaging", "jar")

    # property resolution
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

def gradle_line(dep: Dependency, mod_map: Dict[str, str], cat_lines: List[str], settings_map: Dict[str, str], current_module_name: str) -> Tuple[str, str]:
    conf = ScopeMapping.get(dep.scope, "implementation")

    # Internal module → project dependency
    if dep.group.startswith(INTERNAL_PREFIX):
        # Try to find the module in settings_map, fallback to simple artifact name
        path = settings_map.get(dep.artifact, f":{dep.artifact}")
        return conf, f"project(\"{path}\")"

    module = dep.module_notation()
    if module in mod_map:
        return conf, f"libs.{mod_map[module]}"

    if dep.version is None:
        # Version-less external dependency not in catalog – skip with warning
        raise ValueError(f"Missing version and not found in catalog for {module}")

    alias = normalise_alias(dep.artifact, mod_map)
    append_library(cat_lines, alias, dep)
    mod_map[module] = alias
    return conf, f"libs.{alias}"


def build_script(info: PomInfo, mod_map: Dict[str, str], cat_lines: List[str], settings_map: Dict[str, str], module_name: str) -> str:
    buckets: Dict[str, List[str]] = {}
    for dep in info.dependencies:
        try:
            conf, line = gradle_line(dep, mod_map, cat_lines, settings_map, module_name)
        except ValueError as err:
            print(f"WARN  {info.artifact}: {err}")
            continue
        buckets.setdefault(conf, []).append(line)

    deps_block = "\n".join(f"    {c}({l})" for c, lst in buckets.items() for l in lst)

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
# Main orchestration
# --------------------------------------------------------------------------------------

def process(repo: pathlib.Path, recursive: bool, dry: bool, overwrite: bool):
    # Always use root repo for catalog and settings
    catalog_path, mod_map, cat_lines = load_catalog(repo)
    settings_path, settings_map, settings_lines = load_settings(repo)
    root_props = load_root_properties(repo)

    new_module_lines: List[str] = []

    for pom in find_poms(repo, recursive):
        if is_aggregator(pom, repo):
            continue
        info = parse_pom(pom, root_props)

        # Calculate module name for monorepo structure
        # If pom is in a subdirectory of repo, use that as prefix
        rel_to_repo = pom.parent.relative_to(repo)
        if rel_to_repo == pathlib.Path("."):
            module_name = info.artifact
        else:
            # For monorepo: use subdirectory as prefix
            module_name = f"{rel_to_repo.parts[0]}:{info.artifact}"
        
        # ensure current module is in settings (internal project itself)
        if info.group.startswith(INTERNAL_PREFIX) and module_name not in settings_map:
            rel_path = pom.parent.relative_to(repo).as_posix()
            add_module_to_settings(settings_lines, module_name, rel_path)
            settings_map[module_name] = f":{module_name}"
            new_module_lines.append(f'include(":{module_name}")')
            new_module_lines.append(f'project(":{module_name}").projectDir = file("{rel_path}")')

        script_text = build_script(info, mod_map, cat_lines, settings_map, module_name)
        out_file = pom.parent / "build.gradle.kts"
        if out_file.exists() and not overwrite:
            print(f"SKIP  {out_file.relative_to(repo)} (exists)")
        else:
            if dry:
                print(f"----- {out_file.relative_to(repo)} -----\n{script_text}\n")
            else:
                out_file.write_text(script_text, encoding="utf-8")
                print(f"WRITE {out_file.relative_to(repo)}")

    # Flush catalog & settings updates
    if dry:
        if new_module_lines:
            print("----- settings.gradle.kts additions -----")
            for l in new_module_lines:
                print(l)
            print()
    else:
        catalog_path.write_text("\n".join(cat_lines) + "\n", encoding="utf-8")
        if new_module_lines:
            settings_path.write_text("\n".join(settings_lines) + "\n", encoding="utf-8")
            print(f"UPDATED {settings_path.relative_to(repo)}")
        print(f"UPDATED {catalog_path.relative_to(repo)}")

# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description="Convert Maven POMs → Gradle, sync catalog & settings.")
    p.add_argument("path", type=pathlib.Path, help="Repo root")
    p.add_argument("-r", "--recursive", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--overwrite", action="store_true")
    args = p.parse_args()

    root = args.path.expanduser().resolve()
    if not root.exists():
        p.error(f"Path {root} does not exist")

    process(root, args.recursive, args.dry_run, args.overwrite)

if __name__ == "__main__":
    main()
