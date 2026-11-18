"""Explicit Windows-only startup of the local Ollama service; no lyric input."""
import os
import atexit
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error

from lyric_analyzer import local_ai
from lyric_analyzer.windows_job import ProcessJob


# This is a shared local service, not per-user or lyric state.
_start_lock = threading.RLock()
_managed_process = None
_managed_job = None


def ready():
    try:
        response = local_ai._json_request("/api/tags", timeout=3)
    except local_ai.LocalAIError as error:
        cause = error.__cause__
        reason = cause.reason if isinstance(cause, urllib.error.URLError) else cause
        if isinstance(reason, ConnectionRefusedError):
            return False
        raise
    if not isinstance(response.get("models"), list):
        raise local_ai.LocalAIError("接続先をOllamaとして確認できません。起動状態を確認してください。")
    return True


def find_executable():
    if sys.platform != "win32":
        raise local_ai.LocalAIError("この起動ボタンはWindows用です。Ollamaを手動で起動してください。")
    found = shutil.which("ollama.exe")
    roots = [Path.home() / "AppData" / "Local"]
    if os.environ.get("LOCALAPPDATA"):
        roots.insert(0, Path(os.environ["LOCALAPPDATA"]))
    candidates = ([Path(found)] if found else []) + [root / "Programs" / "Ollama" / "ollama.exe" for root in roots]
    for candidate in candidates:
        if candidate.name.lower() == "ollama.exe" and candidate.is_file():
            return candidate.resolve()
    raise local_ai.LocalAIError("Ollamaが見つかりません。Windows版Ollamaをインストールしてから再実行してください。自動インストールは行いません。")


def launch(executable, log_directory):
    directory = Path(log_directory)
    directory.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment.update(OLLAMA_HOST="127.0.0.1:11434", OLLAMA_NO_CLOUD="true", OLLAMA_DEBUG_LOG_REQUESTS="false")
    with (directory / "ollama-server.log").open("ab") as log:
        return subprocess.Popen([str(executable), "serve"], stdin=subprocess.DEVNULL,
                                stdout=log, stderr=log, env=environment,
                                creationflags=subprocess.CREATE_NO_WINDOW)


def start(log_directory):
    """Return started/already_running/starting without stopping any service."""
    global _managed_process, _managed_job
    with _start_lock:
        if ready():
            return "already_running"
        if _managed_process is None or _managed_process.poll() is not None:
            _close_managed()
            executable = find_executable()
            try:
                _managed_job = ProcessJob()
                _managed_process = launch(executable, log_directory)
                _managed_job.assign(_managed_process.pid)
            except OSError as error:
                _close_managed()
                raise local_ai.LocalAIError("Ollamaを起動できませんでした。Ollamaアプリを手動で起動してください。") from error
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                if ready():
                    return "started"
            except local_ai.LocalAIError as error:
                cause = error.__cause__
                reason = cause.reason if isinstance(cause, urllib.error.URLError) else cause
                if not isinstance(reason, TimeoutError):
                    raise
            if _managed_process.poll() is not None:
                raise local_ai.LocalAIError("Ollamaの起動処理が終了しました。Ollamaアプリを手動で起動して確認してください。")
            time.sleep(.2)
        return "starting"


def _close_managed():
    global _managed_process, _managed_job
    assigned = _managed_job is not None and _managed_job.assigned
    if _managed_job is not None:
        _managed_job.close()
        _managed_job = None
    if _managed_process is not None:
        if not assigned and _managed_process.poll() is None:
            _managed_process.terminate()
        try:
            _managed_process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            _managed_process.kill()
            _managed_process.wait(timeout=3)
        _managed_process = None


def external_server_pid():
    """Find only the verified ollama.exe serve listener on the fixed app port."""
    if sys.platform != "win32":
        raise local_ai.LocalAIError("停止・再起動ボタンはWindows用です。")
    system = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    script = ("[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new(); "
              "$ErrorActionPreference='Stop'; "
              "Get-NetTCPConnection -State Listen -LocalPort 11434 -ErrorAction SilentlyContinue | "
              "Where-Object { $_.LocalAddress -in @('127.0.0.1','0.0.0.0') } | "
              "Select-Object -ExpandProperty OwningProcess -Unique | "
              "ForEach-Object { Get-CimInstance Win32_Process -Filter ('ProcessId='+$_) } | "
              "Select-Object ProcessId,ExecutablePath,CommandLine | ConvertTo-Json -Compress")
    try:
        result = subprocess.run([str(system / "WindowsPowerShell/v1.0/powershell.exe"), "-NoProfile", "-NonInteractive", "-Command", script],
                                capture_output=True, text=True, encoding="utf-8", timeout=8,
                                creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired, UnicodeError) as error:
        raise local_ai.LocalAIError("Ollamaの起動元を確認できないため、停止を中止しました。") from error
    if result.returncode:
        raise local_ai.LocalAIError("Ollamaの起動元を確認できないため、停止を中止しました。")
    if not result.stdout.strip():
        return None
    try:
        value = json.loads(result.stdout)
        rows = value if isinstance(value, list) else [value]
        if len(rows) != 1:
            raise ValueError()
        row = rows[0]
        if (type(row["ProcessId"]) is not int or row["ProcessId"] <= 0
                or Path(row["ExecutablePath"]).name.lower() != "ollama.exe"
                or not re.search(r"(?:^|\s)serve(?:\s|$)", row["CommandLine"])):
            raise ValueError()
        return row["ProcessId"]
    except (ValueError, TypeError, KeyError):
        raise local_ai.LocalAIError("接続先をWindows版Ollamaと確認できないため、停止を中止しました。WSL・Docker内のOllamaは起動元で停止・再起動してください。") from None


def stop():
    with _start_lock:
        owned = _managed_process is not None or _managed_job is not None
        try:
            _close_managed()
        except (OSError, subprocess.TimeoutExpired) as error:
            raise local_ai.LocalAIError("Ollamaを停止できませんでした。起動状態を確認してください。") from error
        if not owned:
            try:
                if not ready():
                    return "not_running"
            except local_ai.LocalAIError:
                pass  # An unresponsive listener can still be verified by its process.
            pid = external_server_pid()
            if pid is None:
                return "not_running"
            system = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
            try:
                result = subprocess.run([str(system / "taskkill.exe"), "/PID", str(pid), "/T", "/F"],
                                        capture_output=True, timeout=8, creationflags=subprocess.CREATE_NO_WINDOW)
            except (OSError, subprocess.TimeoutExpired) as error:
                raise local_ai.LocalAIError("Ollamaを停止できませんでした。起動状態を確認してください。") from error
            if result.returncode:
                raise local_ai.LocalAIError("Ollamaを停止できませんでした。起動状態を確認してください。")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            try:
                if not ready():
                    return "stopped"
            except local_ai.LocalAIError:
                pass
            time.sleep(.2)
        raise local_ai.LocalAIError("Ollamaの停止を確認できません。別のOllamaアプリが自動起動していないか確認してください。")


def restart(log_directory):
    with _start_lock:
        stop()
        return start(log_directory)


def shutdown():
    """Only app-owned jobs are terminated automatically on backend exit."""
    try:
        with _start_lock:
            _close_managed()
    except (OSError, subprocess.TimeoutExpired):
        pass


atexit.register(shutdown)
