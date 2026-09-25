#!/usr/bin/env python
"""在不访问网络的情况下，解析并诊断共享 OCR/VLM/STT 运行库。"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

MODEL_RUNTIME_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = MODEL_RUNTIME_ROOT / "registry.yaml"
DEFAULT_RUNTIME = MODEL_RUNTIME_ROOT / "runtime"


def load_registry() -> dict[str, Any]:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def runtime_root() -> Path:
    raw = os.environ.get("LLMWIKI_MODEL_RUNTIME_ROOT")
    # Do not resolve an explicit Windows override: resolving can expand an
    # existing 8.3 path to its long-name form, breaking callers that retain the
    # supplied path for containment checks.  abspath still makes a relative
    # override deterministic without dereferencing the user-provided spelling.
    return Path(os.path.abspath(os.path.expanduser(raw))) if raw else DEFAULT_RUNTIME.resolve()


def native_runtime_root() -> Path:
    """Return an ASCII Windows alias for native libraries that reject Unicode paths."""
    root = runtime_root()
    if os.name != "nt" or str(root).isascii():
        return root
    import ctypes

    size = 32768
    buffer = ctypes.create_unicode_buffer(size)
    written = ctypes.windll.kernel32.GetShortPathNameW(str(root), buffer, size)
    if 0 < written < size and buffer.value.isascii():
        return Path(buffer.value)

    logical_drives = ctypes.windll.kernel32.GetLogicalDrives()
    normalized_root = os.path.normcase(os.path.abspath(str(root))).rstrip("\\")
    for letter in "ZYXWVUTSR":
        if not logical_drives & (1 << (ord(letter) - ord("A"))):
            continue
        target_buffer = ctypes.create_unicode_buffer(size)
        if ctypes.windll.kernel32.QueryDosDeviceW(f"{letter}:", target_buffer, size):
            target = target_buffer.value
            if target.startswith("\\??\\"):
                target = target[4:]
            if os.path.normcase(os.path.abspath(target)).rstrip("\\") == normalized_root:
                return Path(f"{letter}:\\")
    for letter in "ZYXWVUTSR":
        if logical_drives & (1 << (ord(letter) - ord("A"))):
            continue
        result = subprocess.run(
            ["subst", f"{letter}:", str(root)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode == 0:
            return Path(f"{letter}:\\")
    raise RuntimeError(f"无法为含非 ASCII 字符的共享运行库创建本机别名：{root}")


def inside_runtime(relative_path: str | os.PathLike[str]) -> Path:
    root = Path(os.path.abspath(runtime_root()))
    candidate = Path(os.path.abspath(root / relative_path))
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"组件路径位于共享运行库之外：{relative_path}") from exc
    return candidate


def native_inside_runtime(relative_path: str | os.PathLike[str]) -> Path:
    inside_runtime(relative_path)
    return Path(os.path.abspath(native_runtime_root() / relative_path))


def resolve_component(component_id: str) -> dict[str, Any]:
    registry = load_registry()
    components = registry.get("components", {})
    if component_id not in components:
        raise KeyError(f"未知的共享模型组件：{component_id}")
    component = dict(components[component_id])
    root = runtime_root()
    component["id"] = component_id
    component["runtime_root"] = str(root)
    raw_path = component.get("path")
    component["resolved_path"] = str(native_inside_runtime(raw_path)) if raw_path else None
    env_id = component.get("environment")
    env = registry.get("environments", {}).get(env_id, {})
    if env:
        env_root = native_inside_runtime(env["path"])
        component["environment_path"] = str(env_root)
        component["python"] = str(env_root / env["python"])
    else:
        component["environment_path"] = None
        component["python"] = None
    return component


def environment_variables() -> dict[str, str]:
    root = native_runtime_root()
    models = root / "models"
    return {
        "LLMWIKI_MODEL_RUNTIME_ROOT": str(root),
        "HF_HOME": str(models / "huggingface"),
        "HF_HUB_CACHE": str(models / "huggingface" / "hub"),
        "TRANSFORMERS_CACHE": str(models / "huggingface" / "transformers"),
        "PADDLE_HOME": str(models / "paddle"),
        "PADDLEOCR_HOME": str(models / "paddleocr"),
        "PADDLE_PDX_CACHE_HOME": str(models / "paddlex"),
        "PADDLE_PDX_MODEL_SOURCE": "bos",
        "CNOCR_HOME": str(models / "cnocr"),
        "CNSTD_HOME": str(models / "cnstd"),
        "TOKENIZERS_PARALLELISM": "false",
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    }


def file_summary(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "file_count": 0, "bytes": 0}
    if path.is_file():
        return {"exists": True, "file_count": 1, "bytes": path.stat().st_size}
    count = 0
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            count += 1
            total += item.stat().st_size
    return {"exists": True, "file_count": count, "bytes": total}


def installed_version(python: Path, package: str) -> dict[str, Any]:
    if not python.exists():
        return {"installed": False, "error": f"缺少解释器：{python}"}
    code = (
        "import importlib.metadata as m,json;"
        f"p={package!r};"
        "\ntry: print(json.dumps({'installed':True,'version':m.version(p)}))"
        "\nexcept Exception as e: print(json.dumps({'installed':False,'error':repr(e)}))"
    )
    proc = subprocess.run([str(python), "-c", code], capture_output=True, text=True, encoding="utf-8")
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception:
        return {"installed": False, "error": (proc.stderr or proc.stdout).strip()}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def find_portable_manifest(root: Path) -> tuple[Path | None, dict[str, Any]]:
    for parent in (root, *root.parents):
        candidate = parent / "PORTABLE_RUNTIME_MANIFEST.json"
        if candidate.is_file():
            return candidate, json.loads(candidate.read_text(encoding="utf-8"))
    return None, {"files": [], "validation_lock": {"components": {}}}


def build_inventory(write: bool, component_id: str | None = None) -> dict[str, Any]:
    registry = load_registry()
    root = runtime_root()
    target = root / "inventory.json"
    existing = json.loads(target.read_text(encoding="utf-8")) if component_id and target.exists() else {"components": {}}
    known_components = registry.get("components", {})
    rows: dict[str, Any] = {
        item_id: row for item_id, row in existing.get("components", {}).items() if item_id in known_components
    }
    for existing_id, existing_row in rows.items():
        existing_component = known_components.get(existing_id, {})
        if existing_component.get("path"):
            existing_row["resolved_path"] = str((root / existing_component["path"]).resolve())
    selected = registry.get("components", {})
    if component_id:
        if component_id not in selected:
            raise KeyError(f"未知的共享模型组件：{component_id}")
        selected = {component_id: selected[component_id]}
    for item_id, raw in selected.items():
        rel = raw.get("path")
        if not rel:
            continue
        path = (root / rel).resolve()
        files = []
        if path.exists():
            candidates = [path] if path.is_file() else sorted(item for item in path.rglob("*") if item.is_file())
            for item in candidates:
                files.append({"path": item.relative_to(root).as_posix(), "bytes": item.stat().st_size, "sha256": sha256(item)})
        rows[item_id] = {"resolved_path": str(path), "files": files, "weights_verified": bool(files)}
    payload = {"schema_version": 1, "runtime_root": str(root), "components": rows}
    if write:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        payload["inventory_path"] = str(target)
    return payload


def doctor(all_components: bool, component_id: str | None) -> dict[str, Any]:
    registry = load_registry()
    root = runtime_root()
    inventory_path = root / "inventory.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8")) if inventory_path.exists() else {"components": {}}
    portable_manifest_path, portable_manifest = find_portable_manifest(root)
    if all_components:
        excluded = set(registry.get("portable_package", {}).get("excluded_component_ids", [])) if portable_manifest_path else set()
        selected = [item for item in registry.get("components", {}) if item not in excluded]
    else:
        selected = [str(component_id)]
    portable_files = {str(item.get("path")) for item in portable_manifest.get("files", [])}
    portable_root = portable_manifest_path.parent if portable_manifest_path else None
    runtime_prefix = root.relative_to(portable_root).as_posix() if portable_root else ""
    validation_path = root / "validation-lock.json"
    validation = (
        json.loads(validation_path.read_text(encoding="utf-8"))
        if validation_path.exists()
        else portable_manifest.get("validation_lock", {"components": {}})
    )
    rows = []
    for item_id in selected:
        item = resolve_component(item_id)
        row: dict[str, Any] = {"id": item_id, "kind": item.get("kind"), "environment": item.get("environment")}
        python = Path(item["python"]) if item.get("python") else None
        package = item.get("installed_package")
        if package and python:
            row.update(installed_version(python, package))
            row["expected_version"] = item.get("version")
        elif package:
            row.update({"installed": False, "error": "组件未声明运行环境"})
        elif item.get("resolved_path"):
            row["installed"] = Path(item["resolved_path"]).exists()
        else:
            row["installed"] = bool(python and python.exists())
        if item.get("resolved_path"):
            resolved_path = Path(item["resolved_path"])
            summary = file_summary(resolved_path)
            row["path"] = item["resolved_path"]
            row.update(summary)
            expected_hash = item.get("artifact_sha256")
            if expected_hash and resolved_path.is_file():
                row["expected_sha256"] = expected_hash
                row["actual_sha256"] = sha256(resolved_path)
                row["hash_verified"] = row["actual_sha256"] == expected_hash
            else:
                row["hash_verified"] = None
            inv = inventory.get("components", {}).get(item_id, {})
            relative = str(registry.get("components", {}).get(item_id, {}).get("path") or "").replace("\\", "/").rstrip("/")
            manifest_prefix = f"{runtime_prefix}/{relative}".strip("/")
            portable_verified = bool(
                manifest_prefix
                and any(path == manifest_prefix or path.startswith(manifest_prefix + "/") for path in portable_files)
            )
            row["weights_verified"] = bool(
                summary["exists"] and summary["file_count"] and (inv.get("weights_verified") or portable_verified)
            )
            row["weights_status"] = "portable_package_manifest" if portable_verified else "sha256_inventory"
        else:
            row["weights_verified"] = True
            row["weights_status"] = "not_applicable_package_component"
        proof = validation.get("components", {}).get(item_id, {})
        row["inference_required"] = item.get("kind") in {"model", "model_bundle"}
        row["inference_verified"] = bool(proof.get("inference_verified"))
        if proof:
            row["validated_device"] = proof.get("device")
            row["validated_at"] = proof.get("validated_at")
            row["validation_suite"] = proof.get("suite")
            row["gpu_verified"] = str(proof.get("device", "")).startswith("gpu") or str(proof.get("device", "")).startswith("cuda")
            raw_warnings = proof.get("warnings") or []
            if isinstance(raw_warnings, str):
                raw_warnings = [raw_warnings]
            row["runtime_warnings"] = [str(warning) for warning in raw_warnings]
            row["warning_count"] = len(row["runtime_warnings"])
        rows.append(row)
    ok = all(
        row.get("installed")
        and row.get("hash_verified") is not False
        and (row.get("path") is None or row.get("weights_verified"))
        and (not row.get("inference_required") or row.get("inference_verified"))
        for row in rows
    )
    return {
        "ok": ok,
        "runtime_root": str(root),
        "inventory_present": inventory_path.exists() or portable_manifest_path is not None,
        "portable_manifest_present": portable_manifest_path is not None,
        "validation_lock_present": validation_path.exists() or bool(portable_manifest.get("validation_lock")),
        "warnings_present": any(row.get("runtime_warnings") for row in rows),
        "components": rows,
    }


def run_component(component_id: str, script: str, arguments: list[str]) -> int:
    component = resolve_component(component_id)
    python = Path(component.get("python") or "")
    if not python.exists():
        raise FileNotFoundError(f"缺少共享运行库解释器：{python}")
    target = Path(script).expanduser().resolve()
    if not target.exists():
        raise FileNotFoundError(f"缺少目标脚本：{target}")
    forwarded = list(arguments)
    if forwarded and forwarded[0] == "--":
        forwarded = forwarded[1:]
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env.update(environment_variables())
    return subprocess.run([str(python), str(target), *forwarded], env=env).returncode


def execute_component(component_id: str, arguments: list[str]) -> subprocess.CompletedProcess[str]:
    """Execute a registered portable tool by component ID.

    Callers only need the stable component ID.  Runtime-root overrides,
    executable discovery and environment isolation stay behind this interface.
    """
    component = resolve_component(component_id)
    executable = Path(component.get("resolved_path") or "")
    if not executable.is_file():
        raise FileNotFoundError(f"缺少共享工具入口：{executable}")
    forwarded = list(arguments)
    if forwarded and forwarded[0] == "--":
        forwarded = forwarded[1:]
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env.update(environment_variables())
    env["PATH"] = os.pathsep.join([str(executable.parent), env.get("PATH", "")])
    return subprocess.run([str(executable), *forwarded], env=env, text=True, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="解析、诊断或调用共享 OCR/VLM/STT 运行库。")
    sub = parser.add_subparsers(dest="command", required=True)
    resolve_parser = sub.add_parser("resolve", help="按组件 ID 解析模型和解释器绝对路径。")
    resolve_parser.add_argument("--id", required=True, help="模型注册表中的组件 ID。")
    resolve_parser.add_argument("--json", action="store_true", help="以 JSON 格式输出（为兼容保留参数）。")
    env_parser = sub.add_parser("env", help="输出共享运行库环境变量。")
    env_parser.add_argument("--json", action="store_true", help="以 JSON 格式输出（为兼容保留参数）。")
    doctor_parser = sub.add_parser("doctor", help="检查安装、权重清单和推理验证状态。")
    doctor_parser.add_argument("--all", action="store_true", help="检查全部注册组件。")
    doctor_parser.add_argument("--id", help="只检查指定组件 ID。")
    doctor_parser.add_argument("--json", action="store_true", help="以 JSON 格式输出（为兼容保留参数）。")
    inventory_parser = sub.add_parser("inventory", help="生成模型文件和 SHA-256 清单。")
    inventory_parser.add_argument("--write", action="store_true", help="将清单写入运行库。")
    inventory_parser.add_argument("--id", help="只更新指定组件的清单，其余组件沿用现有记录。")
    inventory_parser.add_argument("--json", action="store_true", help="以 JSON 格式输出（为兼容保留参数）。")
    run_parser = sub.add_parser("run", help="使用指定组件的共享 Python 解释器运行脚本。")
    run_parser.add_argument("--id", required=True, help="模型注册表中的组件 ID。")
    run_parser.add_argument("--script", required=True, help="要运行的目标 Python 脚本。")
    run_parser.add_argument("arguments", nargs=argparse.REMAINDER, help="透传给目标脚本的参数。")
    exec_parser = sub.add_parser("exec", help="按组件 ID 运行 portable executable。")
    exec_parser.add_argument("--id", required=True, help="注册表中的工具组件 ID。")
    exec_parser.add_argument("arguments", nargs=argparse.REMAINDER, help="透传给工具的参数。")
    args = parser.parse_args(argv)

    try:
        if args.command == "run":
            return run_component(args.id, args.script, args.arguments)
        if args.command == "exec":
            return execute_component(args.id, args.arguments).returncode
        if args.command == "resolve":
            payload = resolve_component(args.id)
        elif args.command == "env":
            payload = environment_variables()
        elif args.command == "doctor":
            if not args.all and not args.id:
                parser.error("doctor 需要 --all 或 --id")
            payload = doctor(args.all, args.id)
        else:
            payload = build_inventory(args.write, args.id)
    except Exception as exc:
        payload = {"ok": False, "error": str(exc)}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
