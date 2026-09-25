#!/usr/bin/env python
"""Validate the canonical LLMWiki skills library."""
from __future__ import annotations

import json
import os
import re
import sys
import argparse
from pathlib import Path
from typing import Any

from project_paths import resolve_path

KB_ROOT = resolve_path("project.root", start=Path(__file__))
ROOT = resolve_path("skills.root", start=KB_ROOT)
REGISTRY = ROOT / "registry.yaml"
REQUIRED_CARD_FIELDS = ["use_when", "do_not_use_when", "input", "output", "next_skills"]
ALLOWED_CACHE = ROOT / "_ops" / "runtime" / "state" / "skill-retrieval"
MCP_CATALOG = ROOT / "_registry" / "mcp_servers.catalog.yaml"
SKILL_CATALOG = ROOT / "_registry" / "skills.catalog.yaml"
SCRIPT_CATALOG = ROOT / "_registry" / "scripts.catalog.yaml"
TAXONOMY_REGISTRY = ROOT / "taxonomy" / "exercise-knowledge-tags" / "references" / "knowledge-points.yaml"
ROLE_D_MODEL_PROFILES = ROOT / "learn" / "_shared" / "references" / "model-profiles.json"
ROLE_D_RESPONSE_VALIDATOR = ROOT / "learn" / "_shared" / "scripts" / "validate_role_d_response.py"
ROLE_D_STRONG_SUITE = ROOT / "learn" / "_shared" / "tests" / "fixtures" / "role_d_strong_eval_cases.json"
SHARED_MODEL_TOOLS = ROOT / "_shared" / "model-tools"
SHARED_MODEL_REGISTRY = SHARED_MODEL_TOOLS / "registry.yaml"
SHARED_MODEL_PROFILES = SHARED_MODEL_TOOLS / "profiles.yaml"
ROLE_D_PATHS = {
    "learn/physics-question-tutoring/SKILL.md",
    "learn/physics-diagram-toolkit/SKILL.md",
    "learn/exercise-solution-curation/SKILL.md",
    "learn/physics-knowledge-graph/SKILL.md",
    "taxonomy/exercise-knowledge-tags/SKILL.md",
}
GRAPH_REQUIRED_IMPORTS = [
    "import/textbook-import/SKILL.md",
    "import/standards-import/SKILL.md",
    "import/exercise-bank-import/SKILL.md",
    "import/video-transcript-import/SKILL.md",
]


def parse_registry(text: str) -> list[dict[str, Any]]:
    skills: list[dict[str, Any]] = []
    cur: dict[str, Any] | None = None
    in_triggers = False
    for line in text.splitlines():
        if re.match(r"\s*-\s+name:\s*", line):
            if cur:
                skills.append(cur)
            cur = {"name": line.split(":", 1)[1].strip(), "triggers": []}
            in_triggers = False
        elif cur and re.match(r"\s+category:\s*", line):
            cur["category"] = line.split(":", 1)[1].strip()
        elif cur and re.match(r"\s+role:\s*", line):
            cur["role"] = line.split(":", 1)[1].strip()
        elif cur and re.match(r"\s+path:\s*", line):
            cur["path"] = line.split(":", 1)[1].strip()
        elif cur and re.match(r"\s+manifest:\s*", line):
            cur["manifest"] = line.split(":", 1)[1].strip()
        elif cur and re.match(r"\s+triggers:\s*", line):
            in_triggers = True
        elif cur and in_triggers and re.match(r"\s+-\s+", line):
            cur["triggers"].append(re.sub(r"^\s+-\s+", "", line).strip())
        elif cur and in_triggers and line.strip() and not line.startswith(" "):
            in_triggers = False
    if cur:
        skills.append(cur)
    return skills


def card_fields(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in text.splitlines():
        m = re.match(r"\s*-\s+([a-z_]+):\s*(.*)", line)
        if m:
            fields[m.group(1)] = m.group(2).strip()
    return fields


def is_canonical_skill_path(path: Path) -> bool:
    ignored_parts = {"_archive", "_incoming", "_runtime", "_ops", "_registry"}
    return not any(part in ignored_parts for part in path.parts) and "runtime" not in path.parts


def iter_canonical_skill_files(root: Path | None = None):
    """Yield canonical SKILL.md files without traversing runtime/vendor trees."""
    scan_root = root or ROOT
    ignored_dirs = {"_archive", "_incoming", "_runtime", "_ops", "_registry", "runtime"}
    for current, dirs, files in os.walk(scan_root):
        dirs[:] = sorted(name for name in dirs if name not in ignored_dirs)
        if "SKILL.md" in files:
            path = Path(current) / "SKILL.md"
            if is_canonical_skill_path(path):
                yield path


def check_skill_frontmatter(issues: list[str]) -> None:
    for skill_path in iter_canonical_skill_files():
        text = skill_path.read_text(encoding="utf-8", errors="ignore")
        if not text.startswith("---\n"):
            issues.append(f"{skill_path}: missing frontmatter")
        if "\n---\n" not in text[4:]:
            issues.append(f"{skill_path}: unclosed frontmatter")
        if len(text) > 100000:
            issues.append(f"{skill_path}: too large {len(text)} chars")


def check_root_clean(issues: list[str]) -> None:
    for md_path in ROOT.glob("*.md"):
        if md_path.name not in {"README.md", "CHANGELOG.md", "PACKAGE_RUNTIME_NOTE.md"}:
            issues.append(f"root stray markdown: {md_path.name} (move to package, _incoming, or _archive)")


def check_registry_and_cards(skills: list[dict[str, Any]], issues: list[str]) -> None:
    for skill in skills:
        path_value = skill.get("path", "")
        if not path_value or path_value.startswith("C:"):
            issues.append(f"registry path invalid: {path_value}")
            continue
        skill_path = ROOT / path_value
        if not skill_path.exists():
            issues.append(f"registry path missing: {path_value}")
        manifest = skill.get("manifest", "")
        if manifest and not (ROOT / manifest).exists():
            issues.append(f"registry manifest missing: {manifest}")
        card_path = skill_path.parent / "card.md"
        if not card_path.exists():
            issues.append(f"card missing for {path_value}: {card_path.relative_to(ROOT)}")
            continue
        fields = card_fields(card_path.read_text(encoding="utf-8", errors="ignore"))
        for field in REQUIRED_CARD_FIELDS:
            if not fields.get(field):
                issues.append(f"{card_path.relative_to(ROOT)}: missing card field {field}")


def check_registry_and_catalog_shape(registry_text: str, skills: list[dict[str, Any]], issues: list[str]) -> None:
    for lineno, line in enumerate(registry_text.splitlines(), start=1):
        if re.match(r"^  -\s+", line) and not re.match(r"^  -\s+name:\s*", line):
            issues.append(f"registry.yaml line {lineno}: malformed top-level skill item or trigger indentation")
    registry_names = [str(skill.get("name", "")) for skill in skills]
    if len(registry_names) != len(set(registry_names)):
        issues.append("registry.yaml contains duplicate skill names")
    if not SKILL_CATALOG.exists():
        issues.append("skills catalog missing")
    else:
        catalog_names = re.findall(r"(?m)^  - name:\s*(.+?)\s*$", SKILL_CATALOG.read_text(encoding="utf-8", errors="ignore"))
        if len(catalog_names) != len(set(catalog_names)):
            issues.append("skills catalog contains duplicate skill names")
        if set(catalog_names) != set(registry_names):
            issues.append(
                "skills catalog differs from registry: "
                f"missing={sorted(set(registry_names) - set(catalog_names))}, "
                f"extra={sorted(set(catalog_names) - set(registry_names))}"
            )
    if SCRIPT_CATALOG.exists():
        script_paths = re.findall(r"(?m)^  - path:\s*(.+?)\s*$", SCRIPT_CATALOG.read_text(encoding="utf-8", errors="ignore"))
        if len(script_paths) != len(set(script_paths)):
            issues.append("scripts catalog contains duplicate paths")


def yaml_scalar(value: str) -> str:
    return value.strip().strip('"').strip("'")


def parse_mcp_catalog_names() -> set[str]:
    if not MCP_CATALOG.exists():
        return set()
    names: set[str] = set()
    for line in MCP_CATALOG.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = re.match(r"\s*-\s+name:\s*(.+)$", line)
        if m:
            names.add(yaml_scalar(m.group(1)))
    return names


def resolve_manifest_path(base: Path, raw_value: str) -> Path:
    value = yaml_scalar(raw_value).split("#", 1)[0].strip()
    return (base / value).resolve()


def check_manifests(skills: list[dict[str, Any]], issues: list[str], allow_missing_runtime: bool = False) -> None:
    catalog_names = parse_mcp_catalog_names()
    for skill in skills:
        manifest_value = skill.get("manifest", "")
        if not manifest_value:
            continue
        manifest_path = ROOT / manifest_value
        if not manifest_path.exists():
            continue  # already reported by registry/card check
        base = manifest_path.parent
        text = manifest_path.read_text(encoding="utf-8", errors="ignore")
        rel_manifest = manifest_path.relative_to(ROOT)

        entry_match = re.search(r"^entry_skill:\s*(.+)$", text, flags=re.MULTILINE)
        entry = yaml_scalar(entry_match.group(1)) if entry_match else "SKILL.md"
        if not (base / entry).exists():
            issues.append(f"{rel_manifest}: entry_skill missing: {entry}")

        section = ""
        runtime_subsection = ""
        for raw in text.splitlines():
            top = re.match(r"^([a-zA-Z0-9_]+):\s*(.*)$", raw)
            if top:
                section = top.group(1)
                runtime_subsection = ""
                continue
            if section == "runtime":
                sub = re.match(r"\s{2}([a-zA-Z0-9_]+):\s*(.*)$", raw)
                if sub:
                    runtime_subsection = sub.group(1)
                    continue
            if section == "scripts":
                m = re.match(r"\s*-\s+path:\s*(.+)$", raw)
                if m:
                    p = resolve_manifest_path(base, m.group(1))
                    if not p.exists():
                        issues.append(f"{rel_manifest}: script missing: {m.group(1)}")
            elif section == "runtime":
                if runtime_subsection in {"models", "envs"}:
                    m = re.match(r"\s+path:\s*(.+)$", raw)
                    if m:
                        p = resolve_manifest_path(base, m.group(1))
                        if not p.exists() and not allow_missing_runtime:
                            issues.append(f"{rel_manifest}: runtime path missing: {m.group(1)}")
                elif runtime_subsection == "requirements":
                    m = re.match(r"\s*-\s+(.+)$", raw)
                    if m and ":" not in m.group(1):
                        p = resolve_manifest_path(base, m.group(1))
                        if not p.exists():
                            issues.append(f"{rel_manifest}: requirements file missing: {m.group(1)}")
            elif section == "mcp_servers":
                name = re.match(r"\s*-\s+name:\s*(.+)$", raw)
                if name:
                    server_name = yaml_scalar(name.group(1))
                    if not MCP_CATALOG.exists():
                        issues.append(f"{rel_manifest}: mcp server {server_name} declared but mcp catalog missing")
                    elif server_name not in catalog_names:
                        issues.append(f"{rel_manifest}: mcp server {server_name} not in _registry/mcp_servers.catalog.yaml")
                catalog = re.match(r"\s+catalog:\s*(.+)$", raw)
                if catalog:
                    p = resolve_manifest_path(base, catalog.group(1))
                    if not p.exists():
                        issues.append(f"{rel_manifest}: mcp catalog path missing: {catalog.group(1)}")


def check_skill_local_references(skills: list[dict[str, Any]], issues: list[str]) -> None:
    ref_pattern = re.compile(r"`((?:references|scripts)/[^`]+)`|\]\(((?:references|scripts)/[^)#]+)")
    for skill in skills:
        path_value = skill.get("path", "")
        skill_path = ROOT / path_value
        if not skill_path.exists():
            continue
        text = skill_path.read_text(encoding="utf-8", errors="ignore")
        base = skill_path.parent
        for match in ref_pattern.finditer(text):
            ref = match.group(1) or match.group(2)
            ref = ref.strip().strip(".,，。；;:").split("#", 1)[0]
            p = (base / ref).resolve()
            if not p.exists():
                issues.append(f"{skill_path.relative_to(ROOT)}: local reference missing: {ref}")


def check_shared_model_runtime(skills: list[dict[str, Any]], issues: list[str], allow_missing_runtime: bool) -> None:
    if not SHARED_MODEL_REGISTRY.exists():
        issues.append("shared model runtime registry is missing")
        return
    try:
        registry = json.loads(SHARED_MODEL_REGISTRY.read_text(encoding="utf-8"))
    except Exception as exc:
        issues.append(f"cannot parse shared model registry: {exc}")
        return
    components = registry.get("components", {})
    environments = registry.get("environments", {})
    if registry.get("schema_version") != 1 or not components or not environments:
        issues.append("shared model registry must be schema v1 with components and environments")
    for component_id, component in components.items():
        if component.get("environment") and component["environment"] not in environments:
            issues.append(f"shared model component {component_id}: unknown environment {component['environment']}")
        if component.get("kind") in {"model", "model_bundle"} and not component.get("path"):
            issues.append(f"shared model component {component_id}: model path missing")
        if not component.get("official_url"):
            issues.append(f"shared model component {component_id}: official_url missing")
    if not SHARED_MODEL_PROFILES.exists():
        issues.append("shared model runtime profiles are missing")
    else:
        try:
            profiles = json.loads(SHARED_MODEL_PROFILES.read_text(encoding="utf-8"))
            for profile_name, profile in profiles.get("profiles", {}).items():
                referenced: list[str] = []
                for key in ("primary", "detector"):
                    if profile.get(key):
                        referenced.append(str(profile[key]))
                for key in ("required_dependencies", "candidates", "fallbacks"):
                    referenced.extend(str(item) for item in profile.get(key, []))
                for component_id in referenced:
                    if component_id not in components:
                        issues.append(f"shared model profile {profile_name}: unknown component {component_id}")
        except Exception as exc:
            issues.append(f"cannot parse shared model profiles: {exc}")

    for skill in skills:
        manifest_value = str(skill.get("manifest", ""))
        if not manifest_value:
            continue
        manifest_path = ROOT / manifest_value
        if not manifest_path.exists():
            continue
        text = manifest_path.read_text(encoding="utf-8", errors="ignore")
        in_runtime = False
        in_model_ids = False
        for raw in text.splitlines():
            if raw == "runtime:":
                in_runtime = True
                in_model_ids = False
                continue
            if in_runtime and re.match(r"^[A-Za-z0-9_]+:", raw):
                in_runtime = False
                in_model_ids = False
            if in_runtime and re.match(r"^  model_ids:\s*$", raw):
                in_model_ids = True
                continue
            if in_model_ids:
                item = re.match(r"^    -\s+(.+?)\s*$", raw)
                if item:
                    component_id = yaml_scalar(item.group(1))
                    if component_id not in components:
                        issues.append(f"{manifest_value}: unknown shared model id: {component_id}")
                    continue
                if raw.strip():
                    in_model_ids = False

    tracked_runtime_files = [
        SHARED_MODEL_TOOLS / "README.md",
        SHARED_MODEL_TOOLS / "CHANGELOG.md",
        SHARED_MODEL_TOOLS / "scripts" / "model_runtime.py",
        SHARED_MODEL_TOOLS / "scripts" / "runtime_env.ps1",
        SHARED_MODEL_TOOLS / "scripts" / "setup_runtime.ps1",
    ]
    for path in tracked_runtime_files:
        if not path.exists():
            issues.append(f"shared model runtime file missing: {path.relative_to(ROOT)}")
    if not allow_missing_runtime:
        runtime = SHARED_MODEL_TOOLS / "runtime"
        if not runtime.exists():
            issues.append("shared model runtime assets are missing; install or migrate runtime first")

    target_roots = [
        ROOT / "import" / "bemarkdown",
        ROOT / "import" / "video-transcript-import",
        ROOT / "import" / "chinese-handwriting-formula-transcriber",
    ]
    forbidden = [
        re.compile(r"runtime[/\\\\]models"),
        re.compile(r"[/\\\\]\.venvs[/\\\\]"),
        re.compile(r"\.cache[/\\\\]huggingface"),
        re.compile(r"\.paddlex[/\\\\]official_models"),
    ]
    for target_root in target_roots:
        if not target_root.exists():
            continue
        for path in target_root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in {".py", ".ps1", ".bat", ".yaml", ".yml"}:
                continue
            if "runtime" in path.parts or ".runtime" in path.parts or ".venvs" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            text = text.replace("_shared/model-tools/runtime/models", "SHARED_MODELS")
            text = text.replace("_shared\\model-tools\\runtime\\models", "SHARED_MODELS")
            for pattern in forbidden:
                if pattern.search(text):
                    issues.append(f"{path.relative_to(ROOT)}: forbidden skill-local model/runtime reference: {pattern.pattern}")
                    break


def check_skill_cache_location(issues: list[str]) -> None:
    if (ROOT / "_runtime").exists():
        issues.append("legacy skills/_runtime must be migrated to skills/_ops/runtime")
    if ALLOWED_CACHE.exists() and not ALLOWED_CACHE.is_dir():
        issues.append("skill retrieval cache path must be a directory")


def check_role_d_contracts(skills: list[dict[str, Any]], issues: list[str]) -> None:
    by_path = {str(skill.get("path", "")): skill for skill in skills}
    for path in sorted(ROLE_D_PATHS):
        skill = by_path.get(path)
        if not skill:
            issues.append(f"Role D skill missing from registry: {path}")
            continue
        if skill.get("role") != "role-d-physics-learning":
            issues.append(f"Role D ownership mismatch for {path}: {skill.get('role')}")
        manifest_value = str(skill.get("manifest", ""))
        manifest_path = ROOT / manifest_value
        if manifest_path.exists():
            manifest_text = manifest_path.read_text(encoding="utf-8", errors="ignore").lower()
            if "package_export: false" in manifest_text or "distribution: local_only" in manifest_text:
                issues.append(f"Role D skill must be package-exportable: {manifest_value}")
            if "model_profiles:" not in manifest_text or "supported: [weak, strong]" not in manifest_text:
                issues.append(f"Role D skill missing weak/strong model profile declaration: {manifest_value}")

    if not ROLE_D_MODEL_PROFILES.exists():
        issues.append("Role D model profile contract is missing")
    else:
        try:
            profile_contract = json.loads(ROLE_D_MODEL_PROFILES.read_text(encoding="utf-8"))
            profiles = profile_contract.get("profiles", {})
            if profile_contract.get("schema_version") != 1 or profile_contract.get("default_profile") != "weak":
                issues.append("Role D model profile contract must be schema v1 with weak default")
            if set(profiles) != {"weak", "strong"}:
                issues.append("Role D model profile contract must contain exactly weak and strong")
            invariants = profile_contract.get("invariants", {})
            if invariants.get("curator_apply_requires_teacher_authorization") is not True or invariants.get("destructive_apply_requires_allow_destructive") is not True:
                issues.append("Role D model profiles weakened curator authorization invariants")
        except Exception as exc:
            issues.append(f"cannot parse Role D model profile contract: {exc}")
    if not ROLE_D_RESPONSE_VALIDATOR.exists():
        issues.append("Role D structured response validator is missing")
    if not ROLE_D_STRONG_SUITE.exists():
        issues.append("Role D strong forward-test suite is missing")
    else:
        try:
            strong_suite = json.loads(ROLE_D_STRONG_SUITE.read_text(encoding="utf-8"))
            cases = strong_suite.get("cases", [])
            counts = {name: sum(1 for item in cases if item.get("category") == name) for name in ("text_physics", "image_vision", "diagram", "governance")}
            if strong_suite.get("model_profile") != "strong" or strong_suite.get("case_count") != 62 or counts != {"text_physics": 30, "image_vision": 12, "diagram": 8, "governance": 12}:
                issues.append("Role D strong forward-test suite has invalid counts or profile")
        except Exception as exc:
            issues.append(f"cannot parse Role D strong forward-test suite: {exc}")

    if not TAXONOMY_REGISTRY.exists():
        issues.append("Role D stable knowledge-point registry is missing")
    else:
        text = TAXONOMY_REGISTRY.read_text(encoding="utf-8", errors="ignore")
        blocks = re.split(r"(?m)^- kp_id:\s*", text)[1:]
        ids: list[str] = []
        level3 = 0
        required_fields = [
            "title",
            "parent_id",
            "level",
            "sort_order",
            "path",
            "aliases",
            "status",
            "replacement_ids",
            "wiki_path",
        ]
        for block in blocks:
            lines = block.splitlines()
            kp_id = lines[0].strip() if lines else ""
            ids.append(kp_id)
            for field in required_fields:
                if not re.search(rf"(?m)^  {re.escape(field)}:", block):
                    issues.append(f"knowledge-points.yaml {kp_id}: missing field {field}")
            if re.search(r"(?m)^  level:\s*3\s*$", block):
                level3 += 1
        if len(ids) != len(set(ids)) or any(not re.fullmatch(r"kp_[0-9]{6}", item) for item in ids):
            issues.append("knowledge-points.yaml contains invalid or duplicate kp_id")
        if level3 < 168:
            issues.append(f"knowledge-points.yaml lost baseline third-level tags: {level3} < 168")

    for path_value in GRAPH_REQUIRED_IMPORTS:
        skill_path = ROOT / path_value
        if not skill_path.exists():
            continue
        text = skill_path.read_text(encoding="utf-8", errors="ignore")
        if "import_handoff.py" not in text or "physics-knowledge-graph" not in text:
            issues.append(f"{path_value}: missing mandatory Role A -> Role D graph handoff gate")


def source_points_to_skills(source: str) -> bool:
    norm = source.replace("\\", "/").lower()
    return "../skills" in norm or "/skills/" in norm or norm.endswith("skill.md") or "skill.md" in norm


def source_points_to_student_data(source: str) -> bool:
    norm = source.replace("\\", "/").lower()
    student_terms = ["studentdatasql", "student_data", "student-data", "student_data_sql"]
    sensitive_terms = ["student_identities", "student_data.sqlite3", "student_data.dev.sqlite3"]
    return any(term in norm for term in student_terms + sensitive_terms)


def source_points_to_role_d_runtime(source: str) -> bool:
    norm = source.replace("\\", "/").lower()
    terms = [
        "output/learning_sessions",
        "learning_sessions/",
        "role_d_eval",
        "role_d_strong_eval",
        "role_d_graph",
        "role_d_curation",
        "role_d_taxonomy",
    ]
    return any(term in norm for term in terms)


def check_teaching_rag_not_polluted(issues: list[str]) -> None:
    metadata_path = resolve_path("rag.index", start=KB_ROOT) / "metadata.json"
    chunks_path = resolve_path("rag.data", start=KB_ROOT) / "chunks.jsonl"
    if metadata_path.exists():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            for item in metadata.get("chunks", []):
                source = str(item.get("source", ""))
                if source_points_to_skills(source):
                    issues.append(f"teaching RAG metadata contains skills source: {item.get('source')}")
                if source_points_to_student_data(source):
                    issues.append(f"teaching RAG metadata contains StudentDataSQL source: {item.get('source')}")
                if source_points_to_role_d_runtime(source):
                    issues.append(f"teaching RAG metadata contains Role D runtime source: {item.get('source')}")
        except Exception as exc:
            issues.append(f"cannot parse teaching RAG metadata: {exc}")
    if chunks_path.exists():
        try:
            with chunks_path.open("r", encoding="utf-8") as f:
                for lineno, line in enumerate(f, start=1):
                    if not line.strip():
                        continue
                    item = json.loads(line)
                    source = str(item.get("source", ""))
                    if source_points_to_skills(source):
                        issues.append(f"teaching RAG chunks line {lineno} contains skills source: {item.get('source')}")
                        break
                    if source_points_to_student_data(source):
                        issues.append(f"teaching RAG chunks line {lineno} contains StudentDataSQL source: {item.get('source')}")
                        break
                    if source_points_to_role_d_runtime(source):
                        issues.append(f"teaching RAG chunks line {lineno} contains Role D runtime source: {item.get('source')}")
                        break
        except Exception as exc:
            issues.append(f"cannot parse teaching RAG chunks: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--allow-missing-runtime",
        action="store_true",
        help="allow package/template checks before large runtime models and envs are installed",
    )
    args = parser.parse_args()

    issues: list[str] = []
    if not REGISTRY.exists():
        issues.append(f"missing registry: {REGISTRY}")
        skills: list[dict[str, Any]] = []
    else:
        registry_text = REGISTRY.read_text(encoding="utf-8")
        skills = parse_registry(registry_text)
        check_registry_and_catalog_shape(registry_text, skills, issues)
    check_skill_frontmatter(issues)
    check_root_clean(issues)
    check_registry_and_cards(skills, issues)
    check_manifests(skills, issues, allow_missing_runtime=args.allow_missing_runtime)
    check_skill_local_references(skills, issues)
    check_shared_model_runtime(skills, issues, allow_missing_runtime=args.allow_missing_runtime)
    check_skill_cache_location(issues)
    check_role_d_contracts(skills, issues)
    check_teaching_rag_not_polluted(issues)
    print(json.dumps({"ok": not issues, "issue_count": len(issues), "issues": issues}, ensure_ascii=False, indent=2))
    return 1 if issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
