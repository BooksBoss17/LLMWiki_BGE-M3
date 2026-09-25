#!/usr/bin/env python
"""Codex-only Windows UTF-8 command guard for this repository.

This helper does not change the shared skills workflow. It exists to keep
Codex from passing Chinese path literals through PowerShell/inline Python in
ways that can become question marks on Windows consoles.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path


SKIP_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    "node_modules",
    ".mypy_cache",
    ".pytest_cache",
}

REPO_ROOT = Path(__file__).resolve().parents[2]
PROJECT_SCRIPTS = REPO_ROOT / "skills" / "_shared" / "scripts"
if str(PROJECT_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(PROJECT_SCRIPTS))

from project_paths import resolve_path  # noqa: E402

TASK_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def guarded_env(clear_python_env: bool = False) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    if clear_python_env:
        env.pop("PYTHONPATH", None)
        env.pop("PYTHONHOME", None)
    return env


def find_paths(root: Path, token: str, max_results: int) -> list[str]:
    matches: list[str] = []
    for current, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        cur = Path(current)
        for name in dirs + files:
            if token in name:
                matches.append(str((cur / name).resolve()))
                if len(matches) >= max_results:
                    return matches
    return matches


def resolve_path_arg(path: str | None, root: str, token: str | None, index: int) -> Path:
    if path:
        return Path(path).resolve()
    if not token:
        raise SystemExit("provide --path or --token")
    matches = find_paths(Path(root).resolve(), token, max(index + 1, 20))
    if not matches:
        raise SystemExit(f"no path matched token: {token}")
    if index < 0 or index >= len(matches):
        raise SystemExit(f"--index {index} out of range for {len(matches)} matches")
    return Path(matches[index]).resolve()


def task_path(task_id: str) -> Path:
    if not TASK_ID_RE.match(task_id):
        raise SystemExit(
            "task id must be ASCII and match [A-Za-z0-9][A-Za-z0-9_.-]{0,79}"
        )
    return resolve_path("workspace.tmp", start=REPO_ROOT) / "tasks" / "codex" / f"{task_id}.json"


def command_find(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    payload = {
        "root": str(root),
        "token": args.token,
        "matches": find_paths(root, args.token, args.max_results),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def command_read(args: argparse.Namespace) -> int:
    path = resolve_path_arg(args.path, args.root, args.token, args.index)
    payload: dict[str, object] = {
        "path": str(path),
        "exists": path.exists(),
    }
    if not path.exists():
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 1
    data = path.read_bytes()
    text = data.decode(args.encoding, errors=args.errors)
    if args.tail:
        excerpt = text[-args.max_chars :]
    else:
        excerpt = text[: args.max_chars]
    payload.update(
        {
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "encoding": args.encoding,
            "truncated": len(text) > len(excerpt),
            "excerpt": repr(excerpt) if args.repr else excerpt,
        }
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def command_run(args: argparse.Namespace) -> int:
    if not args.command:
        raise SystemExit("missing command after --")
    cwd = Path(args.cwd).resolve() if args.cwd else None
    completed = subprocess.run(
        args.command,
        cwd=str(cwd) if cwd else None,
        env=guarded_env(clear_python_env=args.clear_python_env),
        text=True,
    )
    return completed.returncode


def command_git_status(args: argparse.Namespace) -> int:
    command = ["git", "-c", "core.quotepath=false", "status"]
    if args.porcelain:
        command.append(f"--porcelain={args.porcelain}")
    elif not args.long:
        command.append("--short")
    completed = subprocess.run(
        command,
        cwd=str(Path(args.cwd).resolve()),
        env=guarded_env(clear_python_env=False),
        text=True,
    )
    return completed.returncode


def command_rag(args: argparse.Namespace) -> int:
    kb_root = Path(args.kb_root).resolve()
    rag_root = resolve_path("library.rag", start=kb_root, must_exist=True)
    rag_env = resolve_path("rag.env", start=kb_root, must_exist=True)
    python_exe = rag_env / "Scripts" / "python.exe"
    script = rag_root / "scripts" / "rag_pipeline.py"
    if not python_exe.exists():
        raise SystemExit(f"missing RAG python: {python_exe}")
    if not script.exists():
        raise SystemExit(f"missing RAG script: {script}")
    completed = subprocess.run(
        [str(python_exe), str(script)],
        cwd=str(rag_root),
        env=guarded_env(clear_python_env=True),
        text=True,
    )
    return completed.returncode


def command_task(args: argparse.Namespace) -> int:
    path = task_path(args.task_id)
    if args.action == "path":
        print(str(path.resolve()))
        return 0
    if args.action == "init":
        if args.json_file:
            data = json.loads(Path(args.json_file).read_text(encoding="utf-8"))
        elif not sys.stdin.isatty():
            raw = sys.stdin.read().strip()
            try:
                data = json.loads(raw) if raw else {}
            except json.JSONDecodeError as exc:
                raise SystemExit(
                    "task init expects UTF-8 JSON on stdin; "
                    "prefer --json-file for Chinese paths or long payloads "
                    f"({exc})"
                ) from exc
        else:
            data = {}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"task_id": args.task_id, "path": str(path.resolve())}, ensure_ascii=False, indent=2))
        return 0
    if args.action == "show":
        if not path.exists():
            raise SystemExit(f"missing task file: {path}")
        print(path.read_text(encoding="utf-8"))
        return 0
    raise SystemExit(f"unsupported task action: {args.action}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command_name", required=True)

    find = sub.add_parser("find", help="Find paths by an ASCII token.")
    find.add_argument("--root", default=".")
    find.add_argument("--token", required=True)
    find.add_argument("--max-results", type=int, default=20)
    find.set_defaults(func=command_find)

    read = sub.add_parser("read", help="Read a UTF-8 file by path or discovered token.")
    read.add_argument("--path", default=None)
    read.add_argument("--root", default=".")
    read.add_argument("--token", default=None)
    read.add_argument("--index", type=int, default=0)
    read.add_argument("--encoding", default="utf-8")
    read.add_argument("--errors", default="replace")
    read.add_argument("--max-chars", type=int, default=4000)
    read.add_argument("--tail", action="store_true")
    read.add_argument("--repr", action="store_true")
    read.set_defaults(func=command_read)

    run = sub.add_parser("run", help="Run a command with UTF-8 Python env.")
    run.add_argument("--cwd", default=None)
    run.add_argument("--clear-python-env", action="store_true")
    run.add_argument("command", nargs=argparse.REMAINDER)
    run.set_defaults(func=command_run)

    git_status = sub.add_parser("git-status", help="Run git status with UTF-8 path output.")
    git_status.add_argument("--cwd", default=".")
    git_status.add_argument("--long", action="store_true")
    git_status.add_argument("--porcelain", default=None)
    git_status.set_defaults(func=command_git_status)

    rag = sub.add_parser("rag", help="Run the BGE-M3 RAG rebuild safely.")
    rag.add_argument("--kb-root", default=".")
    rag.set_defaults(func=command_rag)

    task = sub.add_parser("task", help="Manage ASCII-id JSON task files under tmp/tasks/codex/.")
    task.add_argument("action", choices=["init", "show", "path"])
    task.add_argument("--task-id", required=True)
    task.add_argument("--json-file", default=None)
    task.set_defaults(func=command_task)

    return parser


def main() -> int:
    configure_stdio()
    parser = build_parser()
    args = parser.parse_args()
    if getattr(args, "command", None) and args.command[:1] == ["--"]:
        args.command = args.command[1:]
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
