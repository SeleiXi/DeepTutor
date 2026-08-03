#!/usr/bin/env python
"""Run the independent DeepTutor feature branches side by side.

The lab keeps each variant in a detached Git worktree and gives it an isolated
``DEEPTUTOR_HOME``.  A small state file records only processes created by this
script; stop operations verify the process command before terminating a tree.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
from typing import Iterable, Sequence
from urllib import error as urlerror
from urllib import request as urlrequest
import webbrowser


@dataclass(frozen=True, slots=True)
class Feature:
    key: str
    branch: str
    label: str
    backend_port: int
    frontend_port: int


FEATURES: tuple[Feature, ...] = (
    Feature(
        "answer-feedback",
        "feat/answer-effectiveness-feedback",
        "Answer effectiveness feedback",
        8811,
        3811,
    ),
    Feature(
        "diagnostic-follow-up",
        "feat/diagnostic-follow-up",
        "Diagnostic follow-up",
        8812,
        3812,
    ),
    Feature(
        "memory-reconciliation",
        "feat/memory-evidence-reconciliation",
        "Memory evidence reconciliation",
        8813,
        3813,
    ),
    Feature(
        "teacher-techniques",
        "feat/teacher-exam-techniques",
        "Teacher and exam techniques",
        8814,
        3814,
    ),
    Feature(
        "guided-question",
        "feat/ask-questions-capability",
        "Ask Questions capability",
        8815,
        3815,
    ),
    Feature(
        "chat-parent-fix",
        "feat/fix-stale-chat-parent",
        "Stale chat parent fix",
        8816,
        3816,
    ),
    Feature(
        "workbuddy",
        "feat/workbuddy-support",
        "WorkBuddy AgentOS support",
        8817,
        3817,
    ),
    Feature(
        "antigravity",
        "feat/antigravity-support",
        "Google Antigravity support",
        8818,
        3818,
    ),
)

FEATURE_BY_KEY = {feature.key: feature for feature in FEATURES}
FEATURE_BY_BRANCH = {feature.branch: feature for feature in FEATURES}
STATE_VERSION = 1


class LabError(RuntimeError):
    """A user-actionable feature-lab error."""


def check_python_dependencies() -> None:
    required = {
        "fastapi": "fastapi",
        "multipart": "python-multipart",
        "uvicorn": "uvicorn[standard]",
    }
    missing = [
        package
        for module, package in required.items()
        if importlib.util.find_spec(module) is None
    ]
    if missing:
        packages = " ".join(f'"{package}"' for package in missing)
        raise LabError(
            "The selected Python interpreter is missing Web runtime dependencies. "
            f"Install the project first, or run: {sys.executable} -m pip install {packages}"
        )


def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def default_lab_root(repo_root: Path) -> Path:
    return (repo_root.parent / f"{repo_root.name}-feature-lab").resolve()


def worktree_path(lab_root: Path, feature: Feature) -> Path:
    return lab_root / "worktrees" / feature.key


def runtime_home(lab_root: Path, feature: Feature) -> Path:
    return lab_root / "runtime" / feature.key


def state_path(lab_root: Path, feature: Feature) -> Path:
    return lab_root / "state" / f"{feature.key}.json"


def log_path(lab_root: Path, feature: Feature) -> Path:
    return lab_root / "logs" / f"{feature.key}.log"


def select_features(values: Sequence[str] | None) -> list[Feature]:
    if not values or values == ["all"]:
        return list(FEATURES)
    if "all" in values:
        raise LabError("'all' cannot be combined with individual variants.")

    selected: list[Feature] = []
    for value in values:
        feature = FEATURE_BY_KEY.get(value) or FEATURE_BY_BRANCH.get(value)
        if feature is None:
            choices = ", ".join(FEATURE_BY_KEY)
            raise LabError(f"Unknown variant '{value}'. Choose one of: {choices}")
        if feature not in selected:
            selected.append(feature)
    return selected


def _run(
    command: Sequence[str],
    *,
    cwd: Path,
    capture: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            cwd=str(cwd),
            check=check,
            capture_output=capture,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        raise LabError(f"Required command is unavailable: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        suffix = f"\n{detail}" if detail else ""
        raise LabError(f"Command failed: {' '.join(command)}{suffix}") from exc


def _git(repo_root: Path, *arguments: str, capture: bool = False) -> str:
    result = _run(["git", *arguments], cwd=repo_root, capture=capture)
    return result.stdout.strip() if capture else ""


def _git_at(path: Path, *arguments: str, capture: bool = False) -> str:
    result = _run(["git", "-C", str(path), *arguments], cwd=path, capture=capture)
    return result.stdout.strip() if capture else ""


def ensure_worktree(repo_root: Path, lab_root: Path, feature: Feature) -> Path:
    target = worktree_path(lab_root, feature)
    remote_ref = f"refs/remotes/origin/{feature.branch}"
    result = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", remote_ref],
        cwd=str(repo_root),
        check=False,
    )
    if result.returncode != 0:
        raise LabError(
            f"origin/{feature.branch} is missing. Push the feature branch before starting the lab."
        )

    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        print(f"[setup] {feature.key}: creating detached worktree")
        _git(repo_root, "worktree", "add", "--detach", str(target), f"origin/{feature.branch}")
        return target

    if not (target / ".git").exists():
        raise LabError(f"Refusing to reuse non-worktree directory: {target}")
    dirty = _git_at(target, "status", "--porcelain", capture=True)
    current = _git_at(target, "rev-parse", "HEAD", capture=True)
    expected = _git(repo_root, "rev-parse", f"origin/{feature.branch}", capture=True)
    if current != expected:
        if dirty:
            raise LabError(
                f"{target} has local changes and cannot be updated safely. "
                "Commit or move those changes first."
            )
        print(f"[setup] {feature.key}: updating worktree to {expected[:12]}")
        _git_at(target, "checkout", "--detach", expected)
    else:
        print(f"[setup] {feature.key}: worktree is current ({current[:12]})")
    return target


def _is_directory_link(path: Path) -> bool:
    is_junction = getattr(os.path, "isjunction", lambda _path: False)
    return path.is_symlink() or bool(is_junction(path))


def ensure_frontend_dependencies(target: Path) -> None:
    target_modules = target / "web" / "node_modules"
    if target_modules.exists() and _is_directory_link(target_modules):
        # Turbopack rejects a node_modules link that points outside its project
        # filesystem root.  rmdir removes only the link/junction, not its target.
        target_modules.rmdir()
        print(f"[setup] {target.name}: replacing incompatible linked node_modules")
    if target_modules.exists():
        return

    npm = shutil.which("npm")
    if not npm:
        raise LabError("npm is required to install each worktree's frontend dependencies.")
    print(f"[setup] {target.name}: installing frontend dependencies")
    _run([npm, "ci", "--legacy-peer-deps"], cwd=target / "web")


def setup_variants(
    repo_root: Path,
    lab_root: Path,
    features: Iterable[Feature],
    *,
    fetch: bool = True,
) -> None:
    lab_root.mkdir(parents=True, exist_ok=True)
    if fetch:
        print("[setup] fetching SeleiXi feature branches from origin")
        _git(repo_root, "fetch", "--prune", "origin")
    for feature in features:
        status = variant_status(lab_root, feature)["status"]
        if status in {"ready", "starting"}:
            print(f"[setup] {feature.key}: currently {status}; leaving live worktree untouched")
            continue
        target = ensure_worktree(repo_root, lab_root, feature)
        ensure_frontend_dependencies(target)


def _read_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _atomic_write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def prepare_runtime(
    repo_root: Path,
    lab_root: Path,
    feature: Feature,
    *,
    seed_home: Path | None,
    seed_settings: bool,
) -> Path:
    home = runtime_home(lab_root, feature)
    settings_dir = home / "data" / "user" / "settings"
    source_home = (seed_home or repo_root).resolve()
    source_settings = source_home / "data" / "user" / "settings"
    if seed_settings and not settings_dir.exists() and source_settings.is_dir():
        settings_dir.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source_settings, settings_dir)
        print(f"[config] {feature.key}: copied initial settings from {source_home}")
    settings_dir.mkdir(parents=True, exist_ok=True)

    system_path = settings_dir / "system.json"
    system = _read_json(system_path)
    system.update(
        {
            "version": 1,
            "backend_port": feature.backend_port,
            "frontend_port": feature.frontend_port,
            "next_public_api_base_external": "",
            "next_public_api_base": "",
            "cors_origin": "",
            "cors_origins": [],
        }
    )
    _atomic_write_json(system_path, system)
    return home


def _load_state(lab_root: Path, feature: Feature) -> dict[str, object]:
    return _read_json(state_path(lab_root, feature))


def _pid(value: object) -> int | None:
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def pid_alive(pid: int | None) -> bool:
    if pid is None:
        return False
    if os.name == "nt":
        # ``os.kill(pid, 0)`` can report an already-exited detached process as
        # alive on Windows. Query the kernel process exit code directly.
        import ctypes
        from ctypes import wintypes

        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(
            process_query_limited_information,
            False,
            pid,
        )
        if not handle:
            return False
        try:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def process_command(pid: int) -> str:
    if os.name == "nt":
        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        if not powershell:
            return ""
        expression = (
            f"(Get-CimInstance Win32_Process -Filter \"ProcessId = {pid}\").CommandLine"
        )
        result = subprocess.run(
            [powershell, "-NoProfile", "-NonInteractive", "-Command", expression],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
        )
        return result.stdout.strip()

    proc_path = Path("/proc") / str(pid) / "cmdline"
    try:
        return proc_path.read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
    except OSError:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            check=False,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()


def process_matches_state(pid: int, state: dict[str, object]) -> bool:
    command = process_command(pid).casefold()
    expected_script = str(state.get("script") or "").casefold()
    expected_home = str(state.get("runtime_home") or "").casefold()
    return bool(
        command
        and "start_web.py" in command
        and expected_script
        and expected_script in command
        and expected_home
        and expected_home in command
    )


def port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.25):
            return True
    except OSError:
        return False


def http_ready(port: int) -> bool:
    try:
        with urlrequest.urlopen(f"http://127.0.0.1:{port}/", timeout=1):  # noqa: S310
            return True
    except (urlerror.URLError, TimeoutError, OSError):
        return False


def variant_status(lab_root: Path, feature: Feature) -> dict[str, object]:
    state = _load_state(lab_root, feature)
    launcher_pid = _pid(state.get("launcher_pid"))
    alive = pid_alive(launcher_pid)
    owned = bool(alive and launcher_pid and process_matches_state(launcher_pid, state))
    backend = port_open(feature.backend_port)
    frontend = port_open(feature.frontend_port)
    if owned and backend and frontend:
        status = "ready"
    elif owned:
        status = "starting"
    elif backend or frontend:
        status = "port-conflict"
    elif state:
        status = "stopped"
    else:
        status = "not-started"
    return {
        "key": feature.key,
        "branch": feature.branch,
        "status": status,
        "launcher_pid": launcher_pid,
        "backend_port": feature.backend_port,
        "frontend_port": feature.frontend_port,
        "url": f"http://localhost:{feature.frontend_port}",
        "log": str(log_path(lab_root, feature)),
    }


def print_status(rows: Sequence[dict[str, object]]) -> None:
    print(f"{'VARIANT':<24} {'STATUS':<14} {'PID':<8} {'BACKEND':<10} URL")
    for row in rows:
        pid_text = str(row["launcher_pid"] or "-")
        print(
            f"{row['key']:<24} {row['status']:<14} {pid_text:<8} "
            f"{row['backend_port']:<10} {row['url']}"
        )


def _spawn_launcher(
    lab_root: Path,
    feature: Feature,
    home: Path,
) -> int:
    worktree = worktree_path(lab_root, feature)
    script = (worktree / "scripts" / "start_web.py").resolve()
    log = log_path(lab_root, feature)
    log.parent.mkdir(parents=True, exist_ok=True)

    environment = os.environ.copy()
    current_pythonpath = environment.get("PYTHONPATH", "")
    environment["PYTHONPATH"] = str(worktree.resolve()) + (
        os.pathsep + current_pythonpath if current_pythonpath else ""
    )
    environment["DEEPTUTOR_HOME"] = str(home.resolve())
    environment["PYTHONUNBUFFERED"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8:replace"
    command = [sys.executable, str(script), "--home", str(home.resolve())]

    kwargs: dict[str, object] = {
        "cwd": str(worktree),
        "env": environment,
        "stdin": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS  # type: ignore[attr-defined]
        )
    else:
        kwargs["start_new_session"] = True

    with log.open("a", encoding="utf-8") as output:
        output.write(
            f"\n=== {datetime.now(timezone.utc).isoformat()} "
            f"{feature.branch} @ {feature.frontend_port} ===\n"
        )
        output.flush()
        process = subprocess.Popen(  # noqa: S603
            command,
            stdout=output,
            stderr=subprocess.STDOUT,
            **kwargs,  # type: ignore[arg-type]
        )

    commit = _git_at(worktree, "rev-parse", "HEAD", capture=True)
    state = {
        "version": STATE_VERSION,
        "key": feature.key,
        "branch": feature.branch,
        "commit": commit,
        "launcher_pid": process.pid,
        "script": str(script),
        "runtime_home": str(home.resolve()),
        "worktree": str(worktree.resolve()),
        "backend_port": feature.backend_port,
        "frontend_port": feature.frontend_port,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "log": str(log.resolve()),
    }
    _atomic_write_json(state_path(lab_root, feature), state)
    print(f"[start] {feature.key}: PID {process.pid}, http://localhost:{feature.frontend_port}")
    return process.pid


def _tail(path: Path, lines: int = 12) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(content[-lines:])


def start_variants(
    repo_root: Path,
    lab_root: Path,
    features: Sequence[Feature],
    *,
    seed_home: Path | None,
    seed_settings: bool,
    timeout: int,
    open_browser: bool,
    fetch: bool,
) -> bool:
    check_python_dependencies()
    setup_variants(repo_root, lab_root, features, fetch=fetch)

    states = {feature.key: variant_status(lab_root, feature) for feature in features}
    conflicts = [
        feature for feature in features if states[feature.key]["status"] == "port-conflict"
    ]
    if conflicts:
        details = ", ".join(
            f"{feature.key} ({feature.backend_port}/{feature.frontend_port})"
            for feature in conflicts
        )
        raise LabError(f"Required ports are already owned by other processes: {details}")

    waiting: list[Feature] = []
    for feature in features:
        if states[feature.key]["status"] in {"ready", "starting"}:
            print(f"[start] {feature.key}: already {states[feature.key]['status']}")
            waiting.append(feature)
            continue
        home = prepare_runtime(
            repo_root,
            lab_root,
            feature,
            seed_home=seed_home,
            seed_settings=seed_settings,
        )
        _spawn_launcher(lab_root, feature, home)
        waiting.append(feature)

    deadline = time.monotonic() + timeout
    pending = {feature.key: feature for feature in waiting}
    failed: dict[str, str] = {}
    identity_checked_at: dict[str, float] = {}
    while pending and time.monotonic() < deadline:
        for key, feature in list(pending.items()):
            state = _load_state(lab_root, feature)
            launcher_pid = _pid(state.get("launcher_pid"))
            if not pid_alive(launcher_pid):
                failed[key] = "launcher exited"
                pending.pop(key)
                continue
            now = time.monotonic()
            last_identity_check = identity_checked_at.get(key, 0.0)
            if launcher_pid is not None and now - last_identity_check >= 3:
                if not process_matches_state(launcher_pid, state):
                    failed[key] = "launcher process identity was lost"
                    pending.pop(key)
                    continue
                identity_checked_at[key] = now
            if http_ready(feature.backend_port) and http_ready(feature.frontend_port):
                print(f"[ready] {feature.key}: http://localhost:{feature.frontend_port}")
                pending.pop(key)
        if pending:
            time.sleep(0.75)

    for key in pending:
        failed[key] = f"not ready after {timeout}s"

    rows = [variant_status(lab_root, feature) for feature in features]
    print()
    print_status(rows)
    if failed:
        print("\nSome variants did not become ready:")
        for key, reason in failed.items():
            log = log_path(lab_root, FEATURE_BY_KEY[key])
            print(f"\n[{key}] {reason}; log: {log}")
            tail = _tail(log)
            if tail:
                print(tail)
        return False

    if open_browser:
        for feature in features:
            webbrowser.open(f"http://localhost:{feature.frontend_port}", new=2)
    return True


def _wait_for_exit(pid: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.2)
    return not pid_alive(pid)


def _stop_process_tree(pid: int) -> bool:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if _wait_for_exit(pid, 6):
            return True
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return _wait_for_exit(pid, 3)

    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except OSError:
        return not pid_alive(pid)
    if _wait_for_exit(pid, 6):
        return True
    try:
        os.killpg(os.getpgid(pid), signal.SIGKILL)
    except OSError:
        pass
    return _wait_for_exit(pid, 3)


def stop_variants(lab_root: Path, features: Sequence[Feature]) -> bool:
    success = True
    for feature in features:
        path = state_path(lab_root, feature)
        state = _load_state(lab_root, feature)
        launcher_pid = _pid(state.get("launcher_pid"))
        if not pid_alive(launcher_pid):
            path.unlink(missing_ok=True)
            print(f"[stop] {feature.key}: already stopped")
            continue
        assert launcher_pid is not None
        if not process_matches_state(launcher_pid, state):
            success = False
            print(
                f"[stop] {feature.key}: PID {launcher_pid} no longer matches the recorded "
                "feature-lab process; leaving it untouched"
            )
            continue
        if _stop_process_tree(launcher_pid):
            path.unlink(missing_ok=True)
            print(f"[stop] {feature.key}: stopped PID {launcher_pid}")
        else:
            success = False
            print(f"[stop] {feature.key}: could not stop PID {launcher_pid}")
    return success


def _add_common_arguments(parser: argparse.ArgumentParser, repo_root: Path) -> None:
    parser.add_argument(
        "variants",
        nargs="*",
        metavar="VARIANT",
        help="Variant key or branch name. Default: all.",
    )
    parser.add_argument(
        "--lab-root",
        type=Path,
        default=default_lab_root(repo_root),
        help="Worktree/runtime parent directory.",
    )


def build_parser(repo_root: Path | None = None) -> argparse.ArgumentParser:
    root = (repo_root or repository_root()).resolve()
    parser = argparse.ArgumentParser(
        description="Run the SeleiXi DeepTutor feature branches on isolated ports."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List variants and assigned ports.")
    _add_common_arguments(list_parser, root)

    setup_parser = subparsers.add_parser("setup", help="Create/update detached worktrees.")
    _add_common_arguments(setup_parser, root)
    setup_parser.add_argument("--no-fetch", action="store_true", help="Skip git fetch origin.")

    start_parser = subparsers.add_parser("start", help="Start selected variants.")
    _add_common_arguments(start_parser, root)
    start_parser.add_argument(
        "--seed-home",
        type=Path,
        default=root,
        help="Copy initial data/user/settings from this DeepTutor home.",
    )
    start_parser.add_argument(
        "--no-seed-settings",
        action="store_true",
        help="Create fresh settings instead of copying initial local settings.",
    )
    start_parser.add_argument(
        "--timeout",
        type=int,
        default=180,
        help="Seconds to wait for every selected variant.",
    )
    start_parser.add_argument("--open", action="store_true", help="Open ready variants in tabs.")
    start_parser.add_argument("--no-fetch", action="store_true", help="Skip git fetch origin.")

    stop_parser = subparsers.add_parser("stop", help="Stop selected feature-lab processes.")
    _add_common_arguments(stop_parser, root)

    status_parser = subparsers.add_parser("status", help="Show process and port status.")
    _add_common_arguments(status_parser, root)
    status_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    return parser


def _normalized_argv(argv: Sequence[str] | None) -> list[str]:
    values = list(sys.argv[1:] if argv is None else argv)
    commands = {"list", "setup", "start", "stop", "status"}
    if not values or values[0] not in commands:
        values.insert(0, "start")
    return values


def main(argv: Sequence[str] | None = None) -> int:
    repo_root = repository_root()
    parser = build_parser(repo_root)
    args = parser.parse_args(_normalized_argv(argv))
    try:
        features = select_features(args.variants)
        lab_root = args.lab_root.expanduser().resolve()
        if args.command == "list":
            print_status([variant_status(lab_root, feature) for feature in features])
            return 0
        if args.command == "setup":
            setup_variants(
                repo_root,
                lab_root,
                features,
                fetch=not args.no_fetch,
            )
            return 0
        if args.command == "start":
            return (
                0
                if start_variants(
                    repo_root,
                    lab_root,
                    features,
                    seed_home=args.seed_home,
                    seed_settings=not args.no_seed_settings,
                    timeout=max(1, args.timeout),
                    open_browser=args.open,
                    fetch=not args.no_fetch,
                )
                else 1
            )
        if args.command == "stop":
            return 0 if stop_variants(lab_root, features) else 1
        rows = [variant_status(lab_root, feature) for feature in features]
        if args.json:
            print(json.dumps(rows, indent=2, ensure_ascii=False))
        else:
            print_status(rows)
        return 0
    except LabError as exc:
        print(f"feature-lab: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
