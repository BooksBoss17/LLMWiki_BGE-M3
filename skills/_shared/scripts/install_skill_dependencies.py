#!/usr/bin/env python
"""Bootstrap skill-owned external dependencies for a new LLMWiki deployment.

Stdlib-only wrapper.  By default it only checks and prints a plan.  Pass
--install to create environments, install packages, download BGE-M3, and add
Hermes MCP servers declared in skills/_registry/mcp_servers.catalog.yaml.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

SCRIPT = Path(__file__).resolve()
SKILLS_ROOT = SCRIPT.parents[2]
DEFAULT_KB_ROOT = SKILLS_ROOT.parent
DEFAULT_PACKAGE_ROOT = SKILLS_ROOT.parent if (SKILLS_ROOT.parent / "requirements").exists() else None

CATALOG = SKILLS_ROOT / "_registry" / "mcp_servers.catalog.yaml"
DEFAULT_SCOPES = ["core", "mcp", "rag"]
ALL_SCOPES = ["core", "mcp", "rag", "skill-runtimes"]

SKILL_RUNTIMES = [
    {
        "name": "video-transcript-import",
        "requirements": "_shared/model-tools/requirements/document-stt-py310.txt",
        "venv": "_shared/model-tools/runtime/envs/document-stt-py310",
        "notes": "Shared document/STT environment. Models resolve through _shared/model-tools/registry.yaml.",
    },
    {
        "name": "bemarkdown",
        "requirements": "_shared/model-tools/requirements/formula-py311.txt",
        "venv": "_shared/model-tools/runtime/envs/formula-py311",
        "notes": "Shared OCR/formula environment. Models resolve through _shared/model-tools/registry.yaml.",
        "runtime_components": [
            "temurin-jre-21.0.11+10",
            "jruby-complete-9.3.8.0",
            "apache-batik-1.19",
            "wmf-native-adapter-1.0.0",
            "transpect-mathtype-0.0.7.5",
            "olefile-0.47",
        ],
    },
    {
        "name": "role-d-learning-tools",
        "requirements": "_shared/model-tools/requirements/role-d-learning-py311.txt",
        "venv": "_shared/model-tools/runtime/envs/role-d-learning",
        "notes": "Lightweight deterministic question parsing, SVG/PNG rendering, taxonomy, and graph tooling. OCR models live in the shared model runtime.",
    },
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Install/check LLMWiki skill dependencies.")
    parser.add_argument("--kb-root", default=os.environ.get("KB_ROOT") or str(DEFAULT_KB_ROOT))
    parser.add_argument("--package-root", default=str(DEFAULT_PACKAGE_ROOT) if DEFAULT_PACKAGE_ROOT else "")
    parser.add_argument("--install", action="store_true", help="perform install/download actions")
    parser.add_argument("--all", action="store_true", help="include all scopes, including heavy skill runtimes")
    parser.add_argument(
        "--scope",
        action="append",
        help="comma-separated scopes: core,mcp,rag,skill-runtimes; may be repeated",
    )
    parser.add_argument("--skip-bge-model", action="store_true", help="do not download BGE-M3 model")
    parser.add_argument(
        "--hf-endpoint",
        default="auto",
        help="HuggingFace endpoint: auto, direct, https://hf-mirror.com, or any custom endpoint",
    )
    parser.add_argument("--timeout", type=int, default=600, help="subprocess timeout seconds")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def selected_scopes(args: argparse.Namespace) -> list[str]:
    if args.all:
        return ALL_SCOPES
    if not args.scope:
        return DEFAULT_SCOPES
    scopes: list[str] = []
    for item in args.scope:
        for scope in item.split(","):
            scope = scope.strip()
            if scope:
                scopes.append(scope)
    invalid = [scope for scope in scopes if scope not in ALL_SCOPES]
    if invalid:
        raise SystemExit(f"Unknown scope(s): {', '.join(invalid)}")
    return scopes


def run_cmd(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: int = 600,
) -> dict[str, Any]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout,
        )
        return {
            "cmd": cmd,
            "returncode": proc.returncode,
            "stdout": proc.stdout[-4000:],
            "stderr": proc.stderr[-4000:],
            "ok": proc.returncode == 0,
        }
    except Exception as exc:  # noqa: BLE001 - surface deployment failures as data
        return {"cmd": cmd, "returncode": None, "stdout": "", "stderr": str(exc), "ok": False}


def clean_python_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def exe_in_venv(venv: Path, name: str) -> Path:
    if os.name == "nt":
        return venv / "Scripts" / f"{name}.exe"
    return venv / "bin" / name


def python_in_venv(venv: Path) -> Path:
    return exe_in_venv(venv, "python")


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def parse_mcp_catalog(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    servers: dict[str, dict[str, Any]] = {}
    current: dict[str, Any] | None = None
    list_key: str | None = None
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = re.match(r"\s*-\s+name:\s*(.+)$", line)
        if m:
            current = {"name": m.group(1).strip()}
            servers[current["name"]] = current
            list_key = None
            continue
        if current is None:
            continue
        indent = len(line) - len(line.lstrip(" "))
        kv = re.match(r"\s+([A-Za-z0-9_]+):\s*(.*)$", line)
        if kv and indent == 4:
            key, value = kv.group(1), kv.group(2).strip()
            if value:
                current[key] = value.strip('"').strip("'")
                list_key = None
            else:
                current[key] = []
                list_key = key
            continue
        li = re.match(r"\s+-\s+(.+)$", line)
        if li and list_key and indent >= 6:
            current.setdefault(list_key, []).append(li.group(1).strip())
    return servers


def hermes_mcp_installed() -> tuple[set[str], str | None]:
    if not command_exists("hermes"):
        return set(), "hermes CLI not found"
    result = run_cmd(["hermes", "mcp", "list"], timeout=60)
    if not result["ok"]:
        return set(), result["stderr"] or result["stdout"] or "hermes mcp list failed"
    names: set[str] = set()
    for line in str(result["stdout"]).splitlines():
        m = re.match(r"\s*([A-Za-z0-9_.-]+)\s+", line)
        if m and m.group(1).lower() not in {"name", "mcp"}:
            names.add(m.group(1))
    return names, None


def huggingface_direct_available(timeout: int = 8) -> bool:
    try:
        request = urllib.request.Request("https://huggingface.co", method="HEAD")
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed public endpoint
            return 200 <= int(response.status) < 500
    except Exception:
        return False


def hf_endpoint_env(value: str) -> tuple[dict[str, str], str]:
    env = clean_python_env()
    current = os.environ.get("HF_ENDPOINT", "").strip()
    if value == "direct":
        env.pop("HF_ENDPOINT", None)
        return env, "direct"
    if value == "auto":
        if current:
            env["HF_ENDPOINT"] = current
            return env, current
        if huggingface_direct_available():
            return env, "direct"
        env["HF_ENDPOINT"] = "https://hf-mirror.com"
        return env, "https://hf-mirror.com"
    endpoint = value.strip()
    if endpoint:
        env["HF_ENDPOINT"] = endpoint
    return env, endpoint or "direct"


def add_result(results: list[dict[str, Any]], scope: str, name: str, status: str, detail: Any = None) -> None:
    results.append({"scope": scope, "name": name, "status": status, "detail": detail})


def ensure_venv(venv: Path, results: list[dict[str, Any]], scope: str, args: argparse.Namespace) -> Path:
    py = python_in_venv(venv)
    if py.exists():
        add_result(results, scope, f"venv:{venv}", "present")
        return py
    if not args.install:
        add_result(results, scope, f"venv:{venv}", "missing", "pass --install to create it")
        return py
    venv.parent.mkdir(parents=True, exist_ok=True)
    result = run_cmd([sys.executable, "-m", "venv", str(venv)], env=clean_python_env(), timeout=args.timeout)
    add_result(results, scope, f"create-venv:{venv}", "ok" if result["ok"] else "error", result)
    return py


def pip_install(py: Path, requirements: Path, results: list[dict[str, Any]], scope: str, args: argparse.Namespace) -> None:
    if not requirements.exists():
        add_result(results, scope, f"requirements:{requirements}", "missing")
        return
    if not args.install:
        add_result(results, scope, f"pip-install:{requirements}", "planned")
        return
    env = clean_python_env()
    upgrade = run_cmd([str(py), "-m", "pip", "install", "-U", "pip"], env=env, timeout=args.timeout)
    add_result(results, scope, f"pip-upgrade:{py}", "ok" if upgrade["ok"] else "warning", upgrade)
    install = run_cmd([str(py), "-m", "pip", "install", "-r", str(requirements)], env=env, timeout=args.timeout)
    add_result(results, scope, f"pip-install:{requirements}", "ok" if install["ok"] else "error", install)


def install_mcp(results: list[dict[str, Any]], args: argparse.Namespace) -> None:
    catalog = parse_mcp_catalog(CATALOG)
    add_result(results, "mcp", "catalog", "ok" if catalog else "missing", str(CATALOG))
    installed, live_error = hermes_mcp_installed()
    if live_error:
        add_result(results, "mcp", "hermes mcp list", "warning", live_error)
    else:
        add_result(results, "mcp", "hermes mcp list", "ok", sorted(installed))
    for name, server in catalog.items():
        component_ids = [server.get("runtime_node_component"), server.get("runtime_server_component")]
        for component_id in [str(item) for item in component_ids if item]:
            doctor = run_cmd(
                [
                    sys.executable,
                    str(SKILLS_ROOT / "_shared" / "model-tools" / "scripts" / "model_runtime.py"),
                    "doctor",
                    "--id",
                    component_id,
                    "--json",
                ],
                env=clean_python_env(),
                timeout=args.timeout,
            )
            add_result(results, "mcp", f"runtime:{component_id}", "ok" if doctor["ok"] else "error", doctor)
        status = "present" if name in installed else ("planned" if not args.install else "installing")
        add_result(results, "mcp", f"hermes:{name}", status)
        if args.install:
            configure = run_cmd(
                [
                    sys.executable,
                    str(SKILLS_ROOT / "_shared" / "scripts" / "configure_agent_mcp.py"),
                    "--agent",
                    "hermes",
                    "--kb-root",
                    str(args.kb_root),
                    "--apply",
                    "--user-authorized",
                    "--json",
                ],
                env=clean_python_env(),
                timeout=args.timeout,
            )
            add_result(results, "mcp", f"configure:{name}", "ok" if configure["ok"] else "error", configure)


def rag_runtime_paths(kb_root: Path) -> dict[str, Path]:
    runtime = kb_root / "BGE-M3" / "runtime"
    return {
        "venv": runtime / "env",
        "models": runtime / "models" / "BAAI" / "bge-m3",
        "requirements": kb_root / "skills" / "_shared" / "model-tools" / "requirements" / "rag-py311.txt",
    }


def install_rag(kb_root: Path, package_root: Path | None, results: list[dict[str, Any]], args: argparse.Namespace) -> None:
    paths = rag_runtime_paths(kb_root)
    py = ensure_venv(paths["venv"], results, "rag", args)
    pip_install(py, paths["requirements"], results, "rag", args)
    if args.skip_bge_model:
        add_result(results, "rag", "BGE-M3 model", "skipped")
        return
    model_dir = paths["models"]
    model_present = (model_dir / "config.json").exists() and any(
        (model_dir / name).exists() for name in ("pytorch_model.bin", "model.safetensors")
    )
    if model_present:
        add_result(results, "rag", "BGE-M3 model", "present", str(model_dir))
        return
    if not args.install:
        add_result(results, "rag", "BGE-M3 model", "planned", str(model_dir))
        return
    env, endpoint = hf_endpoint_env(args.hf_endpoint)
    run_cmd([str(py), "-m", "pip", "install", "-U", "huggingface_hub"], env=env, timeout=args.timeout)
    scripts_dir = py.parent
    hf = scripts_dir / ("hf.exe" if os.name == "nt" else "hf")
    huggingface_cli = scripts_dir / ("huggingface-cli.exe" if os.name == "nt" else "huggingface-cli")
    cli = hf if hf.exists() else huggingface_cli
    if not cli.exists():
        add_result(results, "rag", "huggingface-cli", "error", f"not found under {scripts_dir}")
        return
    model_dir.parent.mkdir(parents=True, exist_ok=True)
    result = run_cmd(
        [str(cli), "download", "BAAI/bge-m3", "--local-dir", str(model_dir)],
        env=env,
        timeout=args.timeout,
    )
    add_result(results, "rag", "BGE-M3 model download", "ok" if result["ok"] else "error", {"endpoint": endpoint, **result})


def install_skill_runtimes(results: list[dict[str, Any]], args: argparse.Namespace) -> None:
    for item in SKILL_RUNTIMES:
        req = SKILLS_ROOT / item["requirements"]
        venv = SKILLS_ROOT / item["venv"]
        add_result(results, "skill-runtimes", item["name"], "planned" if not args.install else "installing", item["notes"])
        py = ensure_venv(venv, results, "skill-runtimes", args)
        pip_install(py, req, results, "skill-runtimes", args)
        for component_id in item.get("runtime_components", []):
            doctor = run_cmd(
                [
                    sys.executable,
                    str(SKILLS_ROOT / "_shared" / "model-tools" / "scripts" / "model_runtime.py"),
                    "doctor",
                    "--id",
                    component_id,
                    "--json",
                ],
                env=clean_python_env(),
                timeout=args.timeout,
            )
            add_result(
                results,
                "skill-runtimes",
                f"runtime:{component_id}",
                "ok" if doctor["ok"] else "error",
                doctor,
            )


def main() -> int:
    args = parse_args()
    scopes = selected_scopes(args)
    kb_root = Path(args.kb_root).resolve()
    package_root = Path(args.package_root).resolve() if args.package_root else None
    results: list[dict[str, Any]] = []

    add_result(results, "core", "kb_root", "ok", str(kb_root))
    add_result(results, "core", "skills_root", "ok", str(SKILLS_ROOT))
    if package_root:
        add_result(results, "core", "package_root", "ok", str(package_root))
    add_result(results, "core", "python", "ok", sys.version.split()[0])
    for command in ("git", "powershell", "curl", "hermes"):
        status = "present" if command_exists(command) else "missing"
        add_result(results, "core", command, status)
    dependency_audit = run_cmd(
        [sys.executable, str(SKILLS_ROOT / "_shared" / "scripts" / "validate_skill_dependencies.py"), "--kb-root", str(kb_root), "--json"],
        env=clean_python_env(),
        timeout=args.timeout,
    )
    add_result(results, "core", "portable dependency audit", "ok" if dependency_audit["ok"] else "error", dependency_audit)

    if "mcp" in scopes:
        install_mcp(results, args)
    if "rag" in scopes:
        install_rag(kb_root, package_root, results, args)
    if "skill-runtimes" in scopes:
        install_skill_runtimes(results, args)

    errors = [row for row in results if row["status"] == "error"]
    payload = {
        "ok": not errors,
        "install": bool(args.install),
        "scopes": scopes,
        "results": results,
        "errors": errors,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
