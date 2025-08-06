#!/usr/bin/env python3
"""
Convert Maven POM projects to Gradle Kotlin DSL builds.

After you merge a folder containing Maven modules into your mono-repo, run:

    python maven_to_gradle.py /path/to/repo -r --init

Options
-------
-r | --recursive   : walk the directory tree; otherwise only root is scanned
--init             : call `gradle init` once at repo root (if Gradle wrapper not present)
--dry-run          : print the build scripts to stdout instead of writing them
--overwrite        : overwrite existing `build.gradle.kts` files (otherwise the file is skipped)

The script skips the top-level/root POM and any POMs that declare modules, and
writes `build.gradle.kts` next to every leaf-module `pom.xml` it finds.

For each dependency it tries to resolve an alias in `gradle/libs.versions.toml`.
If a matching alias is found it emits the canonical `libs.<alias>` notation; if
not, the literal GAV coordinate is used.

Known limitations
-----------------
* Maven plugin configuration is **not** migrated – add these manually.
* Only `compile`, `runtime`, `provided`, and `test` scopes are handled.
* No support for property interpolation (${...}) in POMs.
* Parent GAV inheritance is flattened, but profiles are ignored.
"""

from __future__ import annotations

import argparse
import pathlib
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
# Data classes
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
# Utility functions
# --------------------------------------------------------------------------------------

def find_poms(root: pathlib.Path, recursive: bool) -> List[pathlib.Path]:
    pattern = "**/pom.xml" if recursive else "pom.xml"
    return [p for p in root.glob(pattern)]


def is_root_pom(pom_path: pathlib.Path, repo_root: pathlib.Path) -> bool:
    """Treat the pom in repo root as root POM; also, any pom with <modules>."""
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


def load_version_aliases(repo_root: pathlib.Path) -> Dict[str, str]:
    libs_file = repo_root / "gradle" / "libs.versions.toml"
    if not libs_file.exists():
        return {}
    with libs_file.open("rb") as fh:
        data = tomllib.load(fh)

    alias_map: Dict[str, str] = {}
    libs_table = data.get("libraries", {})
    for alias, entry in libs_table.items():
        module = entry["module"] if isinstance(entry, dict) else entry
        alias_map[module] = alias
    return alias_map


def gradle_notation(dep: Dependency, alias_map: Dict[str, str]) -> Tuple[str, str]:
    """Return (configuration, dependency line)"""
    conf = ScopeMapping.get(dep.scope, "implementation")
    module = dep.module_notation()
    if module in alias_map:
        return conf, f"libs.{alias_map[module]}"
    elif dep.version:
        return conf, f'"{module}:{dep.version}"'
    else:
        # Fallback to versionless; assumes version comes transitively
        return conf, f'"{module}"'


def generate_build_kts(info: PomInfo, alias_map: Dict[str, str]) -> str:
    dep_lines: Dict[str, List[str]] = {}
    for dep in info.dependencies:
        conf, notation = gradle_notation(dep, alias_map)
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
    alias_map = load_version_aliases(repo_root)
    poms = find_poms(repo_root, recursive)
    for pom in poms:
        if is_root_pom(pom, repo_root):
            continue
        info = parse_pom(pom)
        build_kts = generate_build_kts(info, alias_map)
        target_file = pom.parent / "build.gradle.kts"
        if target_file.exists() and not overwrite:
            print(f"SKIP  {target_file.relative_to(repo_root)} (exists)")
            continue
        if dry_run:
            print(f"----- {target_file.relative_to(repo_root)} -----")
            print(build_kts)
            print()
        else:
            target_file.write_text(build_kts, encoding="utf-8")
            print(f"WRITE {target_file.relative_to(repo_root)}")


def ensure_gradle_wrapper(repo_root: pathlib.Path):
    if (repo_root / "gradlew").exists():
        return
    print("Running 'gradle init' to bootstrap wrapper…")
    subprocess.run(["gradle", "--no-daemon", "init", "--type", "java-library"], cwd=repo_root, check=True)


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Convert Maven POMs to Gradle build.gradle.kts files.")
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
