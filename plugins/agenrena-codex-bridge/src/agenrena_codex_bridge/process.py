from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

from .config import (
    ConfigurationError,
    Settings,
    bridge_config_path,
    bridge_state_dir,
    load_bridge_config,
)


PLUGIN_ROOT = Path(__file__).resolve().parents[2]
_CHILD_PROCESSES: dict[int, subprocess.Popen[Any]] = {}


def _runtime_paths(
    environ: Mapping[str, str] = os.environ,
    home: Optional[Path] = None,
) -> tuple[Path, Path, Path]:
    config = load_bridge_config(environ, home=home)
    directory = bridge_state_dir(environ, home=home, bridge_config=config)
    return (
        directory,
        directory / "process.json",
        directory / "bridge.log",
    )


def _pid_is_running(pid: Any) -> bool:
    try:
        resolved_pid = int(pid)
    except (TypeError, ValueError):
        return False
    if resolved_pid <= 0:
        return False
    try:
        waited_pid, _ = os.waitpid(resolved_pid, os.WNOHANG)
        if waited_pid == resolved_pid:
            return False
    except ChildProcessError:
        pass
    try:
        os.kill(resolved_pid, 0)
        return True
    except OSError:
        return False


def _pid_is_bridge(pid: Any) -> bool:
    if not _pid_is_running(pid):
        return False
    try:
        result = subprocess.run(
            ["ps", "-p", str(int(pid)), "-o", "command="],
            capture_output=True,
            text=True,
        )
    except OSError:
        # Sandboxed plugin hosts may allow signaling a child while denying
        # process-table inspection. The process file is private (0600), so PID
        # liveness remains a reasonable fallback there.
        return True
    return (
        result.returncode == 0
        and "agenrena_codex_bridge.main" in result.stdout
    )


def _read_process_file(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_process_file(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp_path.write_text(
        json.dumps(dict(value), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(temp_path, 0o600)
    os.replace(temp_path, path)


def get_process_status(
    environ: Mapping[str, str] = os.environ,
    home: Optional[Path] = None,
) -> dict[str, Any]:
    runtime_dir, process_file, log_file = _runtime_paths(environ, home=home)
    state = _read_process_file(process_file)
    running = _pid_is_bridge(state.get("pid"))
    if not running and state:
        try:
            process_file.unlink()
        except FileNotFoundError:
            pass
    return {
        "running": running,
        "pid": state.get("pid") if running else None,
        "started_at": state.get("started_at") if running else None,
        "runtime_dir": str(runtime_dir),
        "log_file": str(log_file),
    }


def _runtime_python() -> Path:
    return Path(sys.executable)


def _validate_runtime(settings: Settings) -> None:
    codex_path = (
        Path(settings.codex_bin)
        if Path(settings.codex_bin).is_absolute()
        else Path(shutil.which(settings.codex_bin) or "")
    )
    if not codex_path.is_file():
        raise ConfigurationError(
            f"Codex executable was not found: {settings.codex_bin}"
        )


def start_daemon(
    environ: Mapping[str, str] = os.environ,
    home: Optional[Path] = None,
) -> dict[str, Any]:
    current = get_process_status(environ, home=home)
    if current["running"]:
        return current

    settings = Settings.from_env(environ=environ, home=home, cwd=PLUGIN_ROOT)
    python = _runtime_python()
    _validate_runtime(settings)
    runtime_dir, process_file, log_file = _runtime_paths(environ, home=home)
    runtime_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    child_env = dict(os.environ)
    child_env.update(dict(environ))
    existing_python_path = child_env.get("PYTHONPATH")
    child_env["PYTHONPATH"] = (
        f"{PLUGIN_ROOT / 'src'}{os.pathsep}{existing_python_path}"
        if existing_python_path
        else str(PLUGIN_ROOT / "src")
    )
    child_env["AGENRENA_BRIDGE_CONFIG_FILE"] = str(
        bridge_config_path(environ, home=home)
    )

    with log_file.open("a", encoding="utf-8") as log:
        child = subprocess.Popen(
            [str(python), "-m", "agenrena_codex_bridge.main"],
            cwd=str(PLUGIN_ROOT),
            env=child_env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
    _CHILD_PROCESSES[child.pid] = child

    _write_process_file(
        process_file,
        {
            "pid": child.pid,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "workspace": str(settings.codex_workspace),
        },
    )
    for _ in range(20):
        time.sleep(0.1)
        if child.poll() is not None:
            _CHILD_PROCESSES.pop(child.pid, None)
            tail = tail_log(log_file, lines=20)
            try:
                process_file.unlink()
            except FileNotFoundError:
                pass
            raise RuntimeError(
                "Agenrena bridge exited during startup."
                + (f"\n\n{tail}" if tail else "")
            )
        status = get_process_status(environ, home=home)
        if status["running"]:
            return status
    raise RuntimeError(f"Agenrena bridge did not report startup. Check {log_file}.")


def stop_daemon(
    environ: Mapping[str, str] = os.environ,
    home: Optional[Path] = None,
) -> dict[str, Any]:
    current = get_process_status(environ, home=home)
    if not current["running"]:
        return current

    pid = int(current["pid"])
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return get_process_status(environ, home=home)

    for _ in range(50):
        time.sleep(0.1)
        if not _pid_is_bridge(pid):
            child = _CHILD_PROCESSES.pop(pid, None)
            if child is not None:
                child.poll()
            return get_process_status(environ, home=home)

    os.kill(pid, signal.SIGKILL)
    child = _CHILD_PROCESSES.pop(pid, None)
    if child is not None:
        child.wait(timeout=5)
    return get_process_status(environ, home=home)


def tail_log(path: Path, lines: int = 20) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return ""
    return "\n".join(content.splitlines()[-lines:])
