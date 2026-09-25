#!/usr/bin/env python
"""Rebind copied Windows venvs to the bundled CPython runtime."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


def configure_utf8_stdout(stream=None) -> None:
    target = stream or sys.stdout
    if hasattr(target, "reconfigure"):
        target.reconfigure(encoding="utf-8", errors="replace")


def render_pyvenv_cfg(text: str, python_home: Path, env_root: Path) -> str:
    replacements = {
        "home": str(python_home),
        "executable": str(python_home / "python.exe"),
        "command": f"{python_home / 'python.exe'} -m venv --upgrade {env_root}",
    }
    output: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in replacements:
            output.append(f"{key} = {replacements[key]}")
            seen.add(key)
        else:
            output.append(line)
    for key in ("home", "executable", "command"):
        if key not in seen:
            output.append(f"{key} = {replacements[key]}")
    return "\n".join(output).rstrip() + "\n"


def rewrite_pyvenv_cfg(config: Path, python_home: Path, env_root: Path) -> bool:
    original = config.read_text(encoding="utf-8")
    updated = render_pyvenv_cfg(original, python_home, env_root)
    if updated == original:
        return False
    backup = config.with_name("pyvenv.cfg.pre-portable")
    if not backup.exists():
        shutil.copy2(config, backup)
    config.write_text(updated, encoding="utf-8", newline="\n")
    return True


def target_configs(kb_root: Path) -> list[Path]:
    model_tools = kb_root / "skills" / "_shared" / "model-tools"
    runtime = model_tools / "runtime"
    registry = json.loads((model_tools / "registry.yaml").read_text(encoding="utf-8"))
    targets = []
    for environment_id in registry.get("portable_package", {}).get("environment_ids", []):
        environment = registry.get("environments", {}).get(environment_id, {})
        python_rel = str(environment.get("python", "")).replace("\\", "/")
        if python_rel != "Scripts/python.exe":
            continue
        targets.append(runtime / environment["path"] / "pyvenv.cfg")
    targets.append(kb_root / "BGE-M3" / "runtime" / "env" / "pyvenv.cfg")
    return targets


def upgrade_venv(python_home: Path, env_root: Path) -> dict[str, str | int | bool]:
    base_python = python_home / "python.exe"
    upgraded = subprocess.run(
        [str(base_python), "-m", "venv", "--upgrade", str(env_root)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    env_python = env_root / "Scripts" / "python.exe"
    verified = subprocess.run(
        [str(env_python), "-c", "import sys;print(sys.base_prefix)"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    expected = str(python_home.resolve()).casefold()
    actual = verified.stdout.strip().casefold()
    return {
        "ok": upgraded.returncode == 0 and verified.returncode == 0 and actual == expected,
        "upgrade_exit": upgraded.returncode,
        "verify_exit": verified.returncode,
        "base_prefix": verified.stdout.strip(),
        "error": (upgraded.stderr or verified.stderr).strip(),
    }


def main(argv: list[str] | None = None) -> int:
    configure_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kb-root", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--user-authorized", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if args.apply and not args.user_authorized:
        payload = {"ok": False, "mode": "blocked", "error": "--apply requires --user-authorized"}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 2
    root = Path(args.kb_root).expanduser().resolve()
    python_home = root / "skills" / "_shared" / "model-tools" / "runtime" / "python" / "cpython-3.11.15-windows-x86_64-none"
    if not (python_home / "python.exe").is_file():
        payload = {"ok": False, "mode": "error", "error": f"缺少 bundled CPython：{python_home}"}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1
    rows = []
    for config in target_configs(root):
        if not config.is_file():
            rows.append({"config": str(config), "status": "missing"})
            continue
        original = config.read_text(encoding="utf-8")
        changed = render_pyvenv_cfg(original, python_home, config.parent) != original
        verification = None
        if args.apply:
            if changed:
                rewrite_pyvenv_cfg(config, python_home, config.parent)
            verification = upgrade_venv(python_home, config.parent)
        row = {"config": str(config), "status": "updated" if args.apply and changed else ("planned" if changed else "unchanged")}
        if verification is not None:
            row["verification"] = verification
        rows.append(row)
    payload = {
        "ok": not any(row["status"] == "missing" or (row.get("verification") and not row["verification"]["ok"]) for row in rows),
        "mode": "applied" if args.apply else "dry-run",
        "python_home": str(python_home),
        "environments": rows,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
