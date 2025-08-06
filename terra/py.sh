#!/usr/bin/env python3
"""
Convert Maven POM projects to Gradle Kotlin DSL builds **and keep the repo-wide
`gradle/libs.versions.toml` in sync**.

Usage
-----
    python maven_to_gradle.py /path/to/repo -r --init

Key behaviour
-------------
* Walks the repo (optionally recursive), skipping the aggregator / root POM.
* For every leaf POM writes a `build.gradle.kts` that:
  * sets `group` + `version`
  * lists dependencies, resolving each coordinate to `libs.<alias>` **if the
    module already exists in `libs.versions.toml`**.
  * When the module is **missing** from the version catalog the script:
        1. Generates a deterministic alias (e.g. `jakarta_annotation_api`).
        2. **Appends** the new entry to the catalog.
        3. Uses that alias in the generated build file.
* `--init` bootstraps the Gradle wrapper (`gradle init`) once if absent.
* `--dry-run` prints would-be changes. `--overwrite` rewrites existing build
  files.

Limitations
-----------
* Maven plugin configuration is *not* migrated.
* Only scopes `compile`, `runtime`, `provided`, `test` are handled.
* Does not evaluate Maven profiles / properties.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
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
# Data classes / constants
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
# Catalog helpers
# --------------------------------------------------------------------------------------

def load_catalog(repo_root: pathlib.Path) -> Tuple[pathlib.Path, Dict[str, str], List[str]]:
    """Return (path, module→alias map, file lines). Creates catalog if missing."""
    catalog_path = repo_root / "gradle" / "libs.versions.toml"
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    if not catalog_path.exists():
        catalog_path.write_text("[libraries]\n", encoding="utf-8")
    lines = catalog_path.read_text(encoding="utf-8").splitlines()

    try:
        data = tomllib.loads("\n".join(lines))
    except Exception as exc:  # malformed TOML – bail out
        print(f"ERROR: Cannot parse libs.versions.toml: {exc}", file=sys.stderr)
        sys.exit(1)

    libraries = data.get("libraries", {})
    module_to_alias = {}
    for alias, entry in libraries.items():
        module = entry["module"] if isinstance(entry, dict) else entry.split(":", 2)[:2]
        if isinstance(module, list):
            module = ":".join(module)
        module_to_alias[module] = alias
    return catalog_path, module_to_alias, lines


def normalise_alias(dep: Dependency, existing_aliases: Dict[str, str]) -> str:
    """Generate a kebab-/snake-case alias unique in the catalog."""
    base = re.sub(r"[^a-z0-9]+", "_", dep.artifact.lower()).strip("_")
    alias = base
    i = 2
    while alias in existing_aliases.values():
        alias = f"{base}_{i}"
        i += 1
    return alias


def append_alias(catalog_lines: List[str], alias: str, dep: Dependency):
    """Append a TOML entry for the library using string shorthand."""
    # ensure [libraries] header exists
    if not any(line.strip() == "[libraries]" for line in catalog_lines):
        catalog_lines.append("[libraries]")
    catalog_lines.append(f"{alias} = \"{dep.group}:{dep.artifact}:{dep.version}\"")

# --------------------------------------------------------------------------------------
# POM parsing helpers
# --------------------------------------------------------------------------------------

def find_poms(root: pathlib.Path, recursive: bool) -> List[pathlib.Path]:
    pattern = "**/pom.xml" if recursive else "pom.xml"
    return list(root.glob(pattern))


def is_root_pom(pom_path: pathlib.Path, repo_root: pathlib.Path) -> bool:
    if pom_path.parent == repo_root:
        return True
    try:
        tree = ET.parse(pom_path)
        return tree.find("./{*}modules") is not None
    except ET.ParseError:
        return False


def parse_pom(path: pathlib.Path) -> PomInfo:
    ns = {"m": "http://maven.apache.org/POM/4.0.0"}
    tree = ET.parse(path)
    root_el = tree.getroot()

    def _get(tag: str, default: str = "") -> str:
        el = root_el.find(f"m:{tag}", ns)
        return el.text.strip() if el is not None and el.text else default

    group = _get("groupId") or _get("parent/m:groupId", "")
    artifact = _get("artifactId")
    version = _get("version") or _get("parent/m:version", "")
    packaging = _get("packaging", "jar")

    deps: List[Dependency] = []
    for dep_el in root_el.findall("m:dependencies/m:dependency", ns):
        d_group = dep_el.findtext("m:groupId", default="", namespaces=ns).strip()
        d_art = dep_el.findtext("m:artifactId", default="", namespaces=ns).strip()
        d_ver = dep_el.findtext("m:version", default="", namespaces=ns)
        d_scope = dep_el.findtext("m:scope", default="compile", namespaces=ns).strip()
        deps.append(Dependency(d_group, d_art, d_ver.strip() if d_ver else None, d_scope))

    return PomInfo(group, artifact, version, packaging, deps)

# --------------------------------------------------------------------------------------
# Gradle build generation
# --------------------------------------------------------------------------------------

def gradle_line(dep: Dependency, module_to_alias: Dict[str, str], catalog_lines: List[str]) -> Tuple[str, str]:
    conf = ScopeMapping.get(dep.scope, "implementation")
    module = dep.module_notation()

    if module in module_to_alias:
        return conf, f"libs.{module_to_alias[module]}"

    # missing → create alias & update catalog
    if dep.version is None:
        raise ValueError(f"No version specified for dependency {module}")

    alias = normalise_alias(dep, module_to_alias)
    append_alias(catalog_lines, alias, dep)
    module_to_alias[module] = alias  # update mapping for subsequent deps
    return conf, f"libs.{alias}"


def generate_build_kts(info: PomInfo, module_to_alias: Dict[str, str], catalog_lines: List[str]) -> str:
    dep_lines: Dict[str, List[str]] = {}
    for dep in info.dependencies:
        conf, notation = gradle_line(dep, module_to_alias, catalog_lines)
        dep_lines.setdefault(conf, []).append(notation)

    deps_block = "\n".join(
        f"    {conf}({notation})" for conf, notations in dep_lines.items() for notation in notations
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
# Core processing
# --------------------------------------------------------------------------------------

def process_repo(repo_root: pathlib.Path, recursive: bool, dry_run: bool, overwrite: bool):
    catalog_path, module_to_alias, catalog_lines = load_catalog(repo_root)

    poms = find_poms(repo_root, recursive)
    for pom in poms:
        if is_root_pom(pom, repo_root):
            continue
        info = parse_pom(pom)
        try:
            build_kts = generate_build_kts(info, module_to_alias, catalog_lines)
        except ValueError as err:
            print(f"WARN  {pom.relative_to(repo_root)} → {err}")
            continue

        target_file = pom.parent / "build.gradle.kts"
        if target_file.exists() and not overwrite:
            print(f"SKIP  {target_file.relative_to(repo_root)} (exists)")
        else:
            if dry_run:
                print(f"----- {target_file.relative_to(repo_root)} -----")
                print(build_kts)
                print()
            else:
                target_file.write_text(build_kts, encoding="utf-8")
                print(f"WRITE {target_file.relative_to(repo_root)}")

    # flush catalog changes
    if not dry_run:
        catalog_path.write_text("\n".join(catalog_lines) + "\n", encoding="utf-8")
        print(f"UPDATED {catalog_path.relative_to(repo_root)}")


def ensure_gradle_wrapper(repo_root: pathlib.Path):
    if (repo_root / "gradlew").exists():
        return
    print("Running 'gradle init' to bootstrap wrapper…")
    subprocess.run(["gradle", "--no-daemon", "init", "--type", "java-library"], cwd=repo_root, check=True)

# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Convert Maven POMs to Gradle build.gradle.kts files and maintain version catalog.")
    parser.add_argument("path", type=pathlib.Path, help="Path to repo root")
    parser.add_argument("-r", "--recursive", action="store_true", help="Recurse into sub-directories")
    parser.add_argument("--init", action="store_true", help="Run 'gradle init' if wrapper missing")
    parser.add_argument("--dry-run", action="store_true", help="Print files instead of writing")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing build files")
    args = parser.parse_args()

    repo_root = args.path.expanduser().resolve()
    if not repo_root.exists():
        parser.error(f"Path {repo_root} does not exist")

    if args.init:
        ensure_gradle_wrapper(repo_root)

    process_repo(repo_root, args.recursive, args.dry_run, args.overwrite)

if __name__ == "__main__":
    main()
