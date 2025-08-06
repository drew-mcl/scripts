"""
Maven → Gradle Kotlin DSL converter
==================================
• Creates/updates *build.gradle.kts* for every leaf `pom.xml`.
• Keeps **gradle/libs.versions.toml** in sync.
• Registers new **internal modules** in *settings.gradle.kts* automatically.

Typical usage
-------------
```bash
# Preview changes in a specific subdirectory
python maven_to_gradle.py /repo/root/services/my-service --dry-run

# Convert the entire repository recursively and write changes
python maven_to_gradle.py /repo/root -r --cleanup
```
Flags:
* `-r / --recursive`   – Walk sub-directories from the target path.
* `--dry-run`          – Print changes, don’t write to disk.
* `--overwrite`        – Replace existing build.gradle.kts files.
* `--cleanup`          – Remove pom.xml files after successful conversion.

Assumptions
-----------
* A Gradle wrapper and `gradle/libs.versions.toml` exist at the repository root.
* The root `pom.xml` holds shared `<properties>` and `<dependencyManagement>` for the monorepo.
* Internal module paths in Gradle (e.g., `:services:auth`) are derived from their
  directory structure relative to the repository root.
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
        print("ERROR: `tomllib` (Python 3.11+) or `toml` package is required.", file=sys.stderr)
        sys.exit(1)

# --------------------------------------------------------------------------------------
# Constants & Data Classes
# --------------------------------------------------------------------------------------

ScopeMapping = {
    "compile": "implementation",
    "runtime": "runtimeOnly",
    "provided": "compileOnly",
    "test": "testImplementation",
    "testCompile": "testImplementation",
}
XML_NS = {"m": "http://maven.apache.org/POM/4.0.0"}


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
    path: pathlib.Path
    group: str
    artifact: str
    version: str
    packaging: str
    dependencies: List[Dependency] = field(default_factory=list)


# --------------------------------------------------------------------------------------
# File System & Config Loaders
# --------------------------------------------------------------------------------------

def find_repo_root(start_path: pathlib.Path) -> Optional[pathlib.Path]:
    """Find the repository root by looking for a 'gradlew' file."""
    current = start_path.resolve()
    while current != current.parent:
        if (current / "gradlew").exists():
            return current
        current = current.parent
    return None


def load_settings(repo_root: pathlib.Path) -> Tuple[pathlib.Path, set, List[str]]:
    """Loads settings.gradle.kts, returning its path, a set of existing modules, and its lines."""
    settings_path = repo_root / "settings.gradle.kts"
    if not settings_path.exists():
        settings_path.write_text('rootProject.name = "monorepo"\n', encoding="utf-8")
    lines = settings_path.read_text(encoding="utf-8").splitlines()
    # Maps the full include path (e.g., ":services:auth") to itself for quick lookups.
    includes = {
        m.group(1)
        for line in lines
        if (m := re.match(r'\s*include\(\s*"(:.+)"\s*\)', line))
    }
    return settings_path, includes, lines


def add_module_to_settings(lines: List[str], artifact: str, path_rel: str):
    """Add a module to settings.gradle.kts."""
    include_line = f'include(":{artifact}")'
    dir_line = f'project(":{artifact}").projectDir = file("{path_rel}")'
    lines.extend([include_line, dir_line])


def load_catalog(repo_root: pathlib.Path) -> Tuple[pathlib.Path, Dict[str, str], List[str]]:
    """Loads libs.versions.toml, returning its path, a map of modules to aliases, and its lines."""
    catalog_path = repo_root / "gradle" / "libs.versions.toml"
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    if not catalog_path.exists():
        catalog_path.write_text("[versions]\n\n[libraries]\n", encoding="utf-8")

    content = catalog_path.read_text(encoding="utf-8")
    lines = content.splitlines()
    data = tomllib.loads(content)
    mapping = {
        entry["module"]: alias
        for alias, entry in data.get("libraries", {}).items()
        if isinstance(entry, dict) and "module" in entry
    }
    return catalog_path, mapping, lines


def load_root_config(repo_root: pathlib.Path) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Loads properties and managed dependency versions from the root pom.xml."""
    root_pom = repo_root / "pom.xml"
    if not root_pom.exists():
        return {}, {}

    try:
        tree = ET.parse(root_pom)
    except ET.ParseError:
        return {}, {}

    props = {
        p.tag.split("}")[-1]: (p.text or "").strip()
        for p in tree.findall("m:properties/*", XML_NS)
    }
    managed_deps = {
        f"{(d.findtext('m:groupId', '', XML_NS) or '').strip()}:{(d.findtext('m:artifactId', '', XML_NS) or '').strip()}":
        (d.findtext('m:version', '', XML_NS) or '').strip()
        for d in tree.findall("m:dependencyManagement/m:dependencies/m:dependency", XML_NS)
    }
    return props, managed_deps


# --------------------------------------------------------------------------------------
# POM Parsing
# --------------------------------------------------------------------------------------

def find_poms(root: pathlib.Path, recursive: bool) -> List[pathlib.Path]:
    """Find all pom.xml files in the given directory."""
    return list(root.glob("**/pom.xml" if recursive else "pom.xml"))


def is_aggregator(pom_path: pathlib.Path) -> bool:
    """Check if a POM is just an aggregator (<packaging>pom</packaging>)."""
    try:
        tree = ET.parse(pom_path)
        return tree.find("./{*}modules") is not None or tree.findtext("{*}packaging") == "pom"
    except ET.ParseError:
        return True


def parse_pom(path: pathlib.Path, root_props: Dict[str, str], managed_deps: Dict[str, str]) -> PomInfo:
    """Parses a single pom.xml file into a PomInfo object."""
    root = ET.parse(path).getroot()

    def resolve_prop(value: str) -> str:
        if value and value.startswith("${") and value.endswith("}"):
            key = value[2:-1]
            return root_props.get(key, value)
        return value

    def find_text(element, selector: str, default: str = "") -> str:
        el = element.find(f"m:{selector}", XML_NS)
        return (el.text or "").strip() if el is not None else default

    group = resolve_prop(find_text(root, "groupId") or find_text(root, "parent/m:groupId"))
    artifact = find_text(root, "artifactId")
    version = resolve_prop(find_text(root, "version") or find_text(root, "parent/m:version"))
    packaging = find_text(root, "packaging", "jar")

    deps: List[Dependency] = []
    for d_el in root.findall("m:dependencies/m:dependency", XML_NS):
        g = resolve_prop(find_text(d_el, "groupId"))
        a = resolve_prop(find_text(d_el, "artifactId"))
        v_raw = find_text(d_el, "version")
        v = resolve_prop(v_raw) if v_raw else managed_deps.get(f"{g}:{a}")
        s = find_text(d_el, "scope", "compile")
        if g and a:
            deps.append(Dependency(g, a, v, s))

    return PomInfo(path, group, artifact, version, packaging, deps)


# --------------------------------------------------------------------------------------
# Gradle Script Generation
# --------------------------------------------------------------------------------------

def get_gradle_path(pom_path: pathlib.Path, repo_root: pathlib.Path) -> str:
    """Calculates the Gradle project path (e.g., :services:api) from a pom.xml path."""
    relative_path = pom_path.parent.relative_to(repo_root)
    return ":" + str(relative_path).replace(pathlib.os.sep, ":") if relative_path != pathlib.Path(".") else f":{pom_path.parent.name}"


def append_to_catalog(lines: List[str], existing_aliases: Dict[str, str], dep: Dependency) -> str:
    """Adds a new library to the TOML catalog and returns its alias."""
    base_alias = re.sub(r"[^a-zA-Z0-9]+", "-", dep.artifact.lower()).strip("-")
    alias = base_alias
    i = 2
    while alias in existing_aliases.values():
        alias = f"{base_alias}-{i}"
        i += 1

    try:
        lib_idx = lines.index("[libraries]")
    except ValueError:
        lines.append("\n[libraries]")
        lib_idx = len(lines) - 1

    version_ref = alias.replace("-", ".")
    lines.insert(lib_idx + 1, f'{alias} = {{ module = "{dep.module_notation()}", version.ref = "{version_ref}" }}')

    try:
        ver_idx = lines.index("[versions]")
    except ValueError:
        lines.insert(0, "\n[versions]")
        ver_idx = 0
    lines.insert(ver_idx + 1, f'{version_ref} = "{dep.version}"')
    return alias


def build_script(info: PomInfo, catalog_map: Dict[str, str], catalog_lines: List[str], internal_modules: Dict[str, str]) -> str:
    """Generates the content for a build.gradle.kts file."""
    buckets: Dict[str, List[str]] = {}
    for dep in info.dependencies:
        conf = ScopeMapping.get(dep.scope, "implementation")

        # Case 1: Internal project dependency
        if dep.artifact in internal_modules:
            line = f'project("{internal_modules[dep.artifact]}")'
            buckets.setdefault(conf, []).append(line)
            continue

        # Case 2: External dependency in catalog
        module = dep.module_notation()
        if module in catalog_map:
            line = f"libs.{catalog_map[module].replace('-', '.')}"
            buckets.setdefault(conf, []).append(line)
            continue

        # Case 3: New external dependency to add to catalog
        if dep.version:
            new_alias = append_to_catalog(catalog_lines, catalog_map, dep)
            catalog_map[module] = new_alias  # Update map for subsequent lookups
            line = f"libs.{new_alias.replace('-', '.')}"
            buckets.setdefault(conf, []).append(line)
        else:
            print(f"WARN  [{info.artifact}] Skipping dependency {module}: No version found in POM or dependencyManagement.")

    deps_block = "\n".join(
        f"    {conf}({line})" for conf, lines in sorted(buckets.items()) for line in sorted(lines)
    )

    return textwrap.dedent(f"""
        plugins {{
            `java-library` // Or `application` if it's an executable
        }}

        group = "{info.group}"
        version = "{info.version}"

        dependencies {{
        {deps_block}
        }}
        """
    ).strip() + "\n"


# --------------------------------------------------------------------------------------
# Main Orchestration
# --------------------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Convert Maven POMs to Gradle, syncing catalog & settings.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument("path", type=pathlib.Path, help="Path to the repository root or a subdirectory to process.")
    parser.add_argument("-r", "--recursive", action="store_true", help="Process target path and all subdirectories.")
    parser.add_argument("--dry-run", action="store_true", help="Print changes instead of writing them to disk.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing build.gradle.kts files.")
    parser.add_argument("--cleanup", action="store_true", help="Delete pom.xml files after successful conversion.")
    args = parser.parse_args()

    target_path = args.path.resolve()
    if not target_path.exists():
        parser.error(f"Path does not exist: {target_path}")

    repo_root = find_repo_root(target_path)
    if not repo_root:
        parser.error("Could not find repository root (searched for a 'gradlew' file).")

    print(f"Repository Root: {repo_root}")
    print(f"Processing Target: {target_path}")

    # --- Step 1: Discover all POMs and internal modules ---
    glob_pattern = "**/pom.xml" if args.recursive else "pom.xml"
    all_poms = [p for p in target_path.glob(glob_pattern) if not is_aggregator(p)]
    
    root_props, managed_deps = load_root_config(repo_root)
    parsed_poms = [parse_pom(p, root_props, managed_deps) for p in all_poms]
    
    # Map artifactId to its full Gradle project path (e.g., "my-api" -> ":services:my-api")
    internal_modules = {p.artifact: get_gradle_path(p.path, repo_root) for p in parsed_poms}
    
    # --- Step 2: Load Gradle configs ---
    catalog_path, catalog_map, catalog_lines = load_catalog(repo_root)
    settings_path, settings_set, settings_lines = load_settings(repo_root)
    original_catalog_lines = list(catalog_lines)
    original_settings_lines = list(settings_lines)

    # --- Step 3: Generate build scripts and update settings ---
    converted_poms = []
    for info in parsed_poms:
        gradle_path = internal_modules[info.artifact]
        
        # Add module to settings.gradle.kts if it's new
        if gradle_path not in settings_set:
            relative_dir = info.path.parent.relative_to(repo_root).as_posix()
            settings_lines.extend(['', f'include("{gradle_path}")', f'project("{gradle_path}").projectDir = file("{relative_dir}")'])
            settings_set.add(gradle_path) # Track new additions
            print(f"INFO  New module found: {gradle_path}")

        # Generate build.gradle.kts
        script_text = build_script(info, catalog_map, catalog_lines, internal_modules)
        out_file = info.path.parent / "build.gradle.kts"

        if out_file.exists() and not args.overwrite:
            print(f"SKIP  {out_file.relative_to(repo_root)} (exists)")
            continue

        if args.dry_run:
            print("-" * 60)
            print(f"DRY-RUN for {out_file.relative_to(repo_root)}")
            print("-" * 60)
            print(script_text)
        else:
            out_file.write_text(script_text, encoding="utf-8")
            print(f"WRITE {out_file.relative_to(repo_root)}")
            converted_poms.append(info.path)

    # --- Step 4: Write updated config files ---
    if args.dry_run:
        print("-" * 60)
        if set(catalog_lines) != set(original_catalog_lines):
            print(f"DRY-RUN for {catalog_path.relative_to(repo_root)} (updates pending)")
        if set(settings_lines) != set(original_settings_lines):
            print(f"DRY-RUN for {settings_path.relative_to(repo_root)} (updates pending)")
    else:
        if catalog_lines != original_catalog_lines:
            catalog_path.write_text("\n".join(catalog_lines) + "\n", encoding="utf-8")
            print(f"UPDATE {catalog_path.relative_to(repo_root)}")
        if settings_lines != original_settings_lines:
            settings_path.write_text("\n".join(settings_lines) + "\n", encoding="utf-8")
            print(f"UPDATE {settings_path.relative_to(repo_root)}")

        if args.cleanup:
            for pom_path in converted_poms:
                pom_path.unlink()
                print(f"DELETE {pom_path.relative_to(repo_root)}")

    print("\nConversion complete.")


if __name__ == "__main__":
    main()
