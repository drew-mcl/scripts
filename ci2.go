#!/usr/bin/env python3
"""pipeline_generator.py

Re-implementation of the Go pipeline generator in idiomatic, dependency-free Python 3.11.
The script reads a Gradle dependency graph (exported as JSON), analyses which deployable
applications under the *apps/* directory are affected by a set of changed files, and
prints GitLab CI YAML that triggers the downstream pipelines for those apps.

The design follows the same high-level flow as the original code but embraces Python
features:
  * argparse for clearer CLI UX (still compatible with the original single-argument mode)
  * pathlib for cross-platform path handling
  * json + built-in logging (JSON-encoded records) for structured output usable in GitLab
  * functional decomposition mirroring the Go functions

Run ``python pipeline_generator.py --help`` for usage.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Dict, List, Set

# ---------------------------------------------------------------------------
# Logging setup ––– JSON per line on stderr, similar to slog.NewJSONHandler
# ---------------------------------------------------------------------------
class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        payload = {
            "level": record.levelname.lower(),
            "msg": record.getMessage(),
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
        }
        if record.args:
            payload.update(record.args if isinstance(record.args, dict) else {"args": record.args})
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)

logger = logging.getLogger("pipeline-generator")
_handler = logging.StreamHandler()
_handler.setFormatter(JsonFormatter())
logger.addHandler(_handler)
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
class Project:  # mirrors the Go struct
    def __init__(self, project_dir: str, dependencies: List[str]):
        self.project_dir = project_dir.rstrip("/")
        self.dependencies = dependencies

    @classmethod
    def from_raw(cls, raw: Dict[str, object]):  # type: ignore[override]
        return cls(
            project_dir=str(raw["projectDir"]),
            dependencies=list(raw.get("dependencies", [])),
        )

# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

def load_projects(path: Path) -> Dict[str, Project]:
    with path.open("r", encoding="utf-8") as fp:
        raw_projects = json.load(fp)
    return {name: Project.from_raw(pdata) for name, pdata in raw_projects.items()}


def find_deployable_apps(apps_dir: Path, projects: Dict[str, Project]) -> Set[str]:
    apps: Set[str] = set()
    if not apps_dir.exists():
        logger.warning("apps directory not found, assuming no deployable applications", extra={"path": str(apps_dir)})
        return apps

    for entry in apps_dir.iterdir():
        if entry.is_dir():
            project_path = f":{apps_dir.name}:{entry.name}"
            if project_path in projects:
                apps.add(project_path)
            else:
                logger.warning("directory in apps/ does not match any known Gradle project", extra={"directory": entry.name, "expected_project_path": project_path})
    return apps


def build_reverse_graph(projects: Dict[str, Project]) -> Dict[str, List[str]]:
    reverse: Dict[str, List[str]] = {}
    for path, pdata in projects.items():
        for dep in pdata.dependencies:
            reverse.setdefault(dep, []).append(path)
    return reverse


def find_changed_modules(changed_files: List[str], projects: Dict[str, Project], deployable_apps: Set[str]) -> Set[str]:
    # Special case: version catalog changed
    if "versions.toml" in changed_files:
        logger.info("'versions.toml' changed, triggering all deployable applications.")
        return set(deployable_apps)

    changed: Set[str] = set()
    for file in changed_files:
        best_match: str = ""
        for project_path, pdata in projects.items():
            if file.startswith(pdata.project_dir) and len(pdata.project_dir) > len(best_match):
                best_match = project_path
        if best_match:
            logger.info("file change detected", extra={"file": file, "module": best_match})
            changed.add(best_match)
    return changed


def find_affected_apps(initial_modules: Set[str], reverse_graph: Dict[str, List[str]], deployable_apps: Set[str]) -> List[str]:
    affected: Set[str] = set()
    queue: List[str] = list(initial_modules)

    while queue:
        current = queue.pop(0)
        if current in affected:
            continue
        affected.add(current)
        logger.debug("traversing dependency", extra={"module": current})
        queue.extend(reverse_graph.get(current, []))

    return sorted(app for app in affected if app in deployable_apps)


def generate_pipeline_yaml(affected_apps: List[str]) -> str:
    lines: List[str] = [
        "# This pipeline was dynamically generated by the pipeline-generator tool.",
    ]

    if not affected_apps:
        logger.info("no applications affected, generating an empty pipeline.")
        return "\n".join(lines) + "\n"

    ci_project_path = os.getenv("CI_PROJECT_PATH", "$CI_PROJECT_PATH")
    ci_ref = os.getenv("CI_COMMIT_REF_NAME", "$CI_COMMIT_REF_NAME")

    for app in affected_apps:
        app_name = app.split(":")[-1]  # ":apps:refdata" -> "refdata"
        job_name = f"trigger:{app_name}"
        include_path = f".gitlab/{app_name}.yml"
        lines.append(
            f"""{job_name}:
  stage: downstream-pipelines
  trigger:
    include:
      - project: '{ci_project_path}'
        ref: '{ci_ref}'
        file: '{include_path}'"""
        )
    return "\n\n".join(lines) + "\n"

# ---------------------------------------------------------------------------
# Main orchestration – closely mirrors the Go `main`/`run`.
# ---------------------------------------------------------------------------

def _parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate downstream-pipeline YAML based on changed files and a Gradle dependency graph.")
    parser.add_argument("changed_files", nargs=argparse.REMAINDER, help="List of changed files *or* a single quoted string of space-separated paths (for compatibility)")
    parser.add_argument("--graph-file", default=Path("build/dependency-graph.json"), type=Path, help="Path to dependency-graph JSON exported from Gradle (default: build/dependency-graph.json)")
    parser.add_argument("--apps-dir", default=Path("apps"), type=Path, help="Directory that contains deployable apps (default: apps)")
    return parser.parse_args(argv)


def main(argv: List[str] | None = None) -> None:  # noqa: D401
    args = _parse_args(argv or sys.argv[1:])

    # Flatten changed_files: support both positional list and single string with spaces
    changed_files: List[str]
    if len(args.changed_files) == 1 and " " in args.changed_files[0]:
        changed_files = args.changed_files[0].split()
    else:
        changed_files = args.changed_files

    logger.info("starting pipeline analysis", extra={
        "changed_files": changed_files,
        "graph_file": str(args.graph_file),
        "apps_dir": str(args.apps_dir),
    })

    try:
        projects = load_projects(args.graph_file)
        deployable_apps = find_deployable_apps(args.apps_dir, projects)
        reverse_graph = build_reverse_graph(projects)
        changed_modules = find_changed_modules(changed_files, projects, deployable_apps)
        affected_apps = find_affected_apps(changed_modules, reverse_graph, deployable_apps)
        yaml_output = generate_pipeline_yaml(affected_apps)
        print(yaml_output, end="")
        logger.info("pipeline generation completed successfully")
    except Exception as exc:  # noqa: BLE001
        logger.error("pipeline generator failed", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
