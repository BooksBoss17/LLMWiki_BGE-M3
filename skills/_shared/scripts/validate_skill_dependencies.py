#!/usr/bin/env python
"""Audit skill manifests, MCP catalog and portable runtime registry as one contract."""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from check_mcp_requirements import parse_catalog
from project_paths import find_project_root, resolve_path


def audit_registry(registry: dict[str, Any]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    components = registry.get("components", {})
    environments = registry.get("environments", {})
    package = registry.get("portable_package", {})
    for component_id in package.get("component_ids", []):
        component = components.get(component_id)
        if not isinstance(component, dict):
            issues.append({"code": "unknown_portable_component", "item": str(component_id)})
            continue
        if str(component.get("redistribution", "")).startswith("blocked"):
            issues.append({"code": "redistribution_blocked", "item": str(component_id)})
        if not component.get("license"):
            issues.append({"code": "license_missing", "item": str(component_id)})
        for dependency in component.get("dependencies", []):
            if dependency not in components:
                issues.append({"code": "unknown_component_dependency", "item": f"{component_id}:{dependency}"})
    for env_id in package.get("environment_ids", []):
        environment = environments.get(env_id)
        if not isinstance(environment, dict):
            issues.append({"code": "unknown_portable_environment", "item": str(env_id)})
            continue
        if not environment.get("license") or str(environment.get("license")).lower() == "unknown":
            issues.append({"code": "environment_license_missing", "item": str(env_id)})
        if str(environment.get("redistribution", "")).startswith("blocked"):
            issues.append({"code": "environment_redistribution_blocked", "item": str(env_id)})
    return issues


def manifest_runtime_ids(manifest: Path) -> list[str]:
    ids: list[str] = []
    active = False
    base_indent = 0
    for raw in manifest.read_text(encoding="utf-8", errors="ignore").splitlines():
        match = re.match(r"^(\s*)(model_ids|tool_ids):\s*$", raw)
        if match:
            active = True
            base_indent = len(match.group(1))
            continue
        if active:
            item = re.match(r"^(\s*)-\s+([^#]+?)\s*$", raw)
            if item and len(item.group(1)) > base_indent:
                ids.append(item.group(2).strip().strip('"\''))
                continue
            if raw.strip() and len(raw) - len(raw.lstrip()) <= base_indent:
                active = False
    return ids


def host_command_candidates(name: str) -> list[str]:
    if name == "powershell":
        return ["pwsh", "powershell"]
    if name == "nvidia-driver":
        return ["nvidia-smi"]
    return [name]


def audit(kb_root: Path, *, allow_missing_runtime: bool) -> dict[str, Any]:
    skills_root = kb_root / "skills"
    registry_path = skills_root / "_shared" / "model-tools" / "registry.yaml"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    components = registry.get("components", {})
    runtime_root = resolve_path("skills.model-tools.runtime", start=kb_root)
    issues = audit_registry(registry)
    declarations: dict[str, list[str]] = {}
    for manifest in skills_root.glob("*/*/manifest.yaml"):
        ids = manifest_runtime_ids(manifest)
        if not ids:
            continue
        rel = manifest.relative_to(skills_root).as_posix()
        declarations[rel] = ids
        for component_id in ids:
            if component_id not in components:
                issues.append({"code": "manifest_unknown_component", "item": f"{rel}:{component_id}"})
    catalog = parse_catalog(skills_root / "_registry" / "mcp_servers.catalog.yaml")
    for name, server in catalog.items():
        for key in ("runtime_node_component", "runtime_server_component", "runtime_python_component"):
            component_id = str(server.get(key, "")).strip()
            if not component_id or component_id not in components:
                issues.append({"code": "mcp_runtime_component_missing", "item": f"{name}:{key}:{component_id}"})
    if "npx bilibili-mcp-js" in (skills_root / "_registry" / "mcp_servers.catalog.yaml").read_text(encoding="utf-8"):
        issues.append({"code": "forbidden_global_mcp_launcher", "item": "npx bilibili-mcp-js"})
    missing_runtime: list[str] = []
    for component_id in registry.get("portable_package", {}).get("component_ids", []):
        component = components.get(component_id, {})
        rel = component.get("path")
        if rel and not (runtime_root / rel).exists():
            missing_runtime.append(component_id)
    for environment_id in registry.get("portable_package", {}).get("environment_ids", []):
        environment = registry.get("environments", {}).get(environment_id, {})
        rel = environment.get("path")
        if rel and not (runtime_root / rel).exists():
            missing_runtime.append(f"environment:{environment_id}")
        requirements = environment.get("requirements")
        if requirements and not (registry_path.parent / requirements).is_file():
            issues.append({"code": "environment_requirements_missing", "item": f"{environment_id}:{requirements}"})
    if missing_runtime and not allow_missing_runtime:
        issues.extend({"code": "portable_artifact_missing", "item": item} for item in missing_runtime)
    host = {}
    for name, requirement in registry.get("host_requirements", {}).items():
        candidates = host_command_candidates(name)
        present = any(shutil.which(candidate) for candidate in candidates)
        host[name] = {**requirement, "present": present}
        if requirement.get("required") and not present:
            issues.append({"code": "required_host_command_missing", "item": name})
    licenses = {
        component_id: {
            "license": components[component_id].get("license"),
            "redistribution": components[component_id].get("redistribution"),
            "official_url": components[component_id].get("official_url"),
        }
        for component_id in registry.get("portable_package", {}).get("component_ids", [])
        if component_id in components
    }
    environment_licenses = {
        environment_id: {
            "license": registry["environments"][environment_id].get("license"),
            "redistribution": registry["environments"][environment_id].get("redistribution"),
            "requirements": registry["environments"][environment_id].get("requirements"),
        }
        for environment_id in registry.get("portable_package", {}).get("environment_ids", [])
        if environment_id in registry.get("environments", {})
    }
    return {
        "ok": not issues,
        "registry": str(registry_path),
        "runtime_root": str(runtime_root),
        "manifest_declarations": declarations,
        "mcp_servers": sorted(catalog),
        "missing_runtime": missing_runtime,
        "host_requirements": host,
        "licenses": licenses,
        "environment_licenses": environment_licenses,
        "issues": issues,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kb-root")
    parser.add_argument("--allow-missing-runtime", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    root = Path(args.kb_root).resolve() if args.kb_root else find_project_root(__file__)
    try:
        payload = audit(root, allow_missing_runtime=args.allow_missing_runtime)
    except Exception as exc:
        payload = {"ok": False, "issues": [{"code": "audit_error", "item": str(exc)}]}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
