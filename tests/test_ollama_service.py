import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from lyric_analyzer import local_ai, ollama_service as service
from lyric_analyzer.windows_job import ProcessJob


@pytest.fixture(autouse=True)
def isolated_service(monkeypatch):
    monkeypatch.setattr(service, "_managed_process", None)
    monkeypatch.setattr(service, "_managed_job", None)


def test_running_service_is_not_adopted_or_launched(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "ready", lambda: True)
    launch = Mock()
    monkeypatch.setattr(service, "launch", launch)
    assert service.start(tmp_path) == "already_running"
    service.shutdown()
    launch.assert_not_called()


def test_unresponsive_service_does_not_spawn_duplicate(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "ready", Mock(side_effect=local_ai.LocalAIError("busy")))
    launch = Mock()
    monkeypatch.setattr(service, "launch", launch)
    with pytest.raises(local_ai.LocalAIError):
        service.start(tmp_path)
    launch.assert_not_called()


def test_crashed_service_can_restart_and_be_owned(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "ready", Mock(side_effect=[False, False, True]))
    monkeypatch.setattr(service, "find_executable", lambda: Path("ollama.exe"))
    job = Mock(assigned=True)
    process = Mock(pid=123)
    process.poll.return_value = None
    monkeypatch.setattr(service, "ProcessJob", lambda: job)
    monkeypatch.setattr(service, "launch", Mock(return_value=process))
    assert service.restart(tmp_path) == "started"
    job.assign.assert_called_once_with(123)
    service.shutdown()
    job.close.assert_called_once()
    process.wait.assert_called_once_with(timeout=3)
    process.terminate.assert_not_called()


def test_assignment_failure_cleans_up_new_process(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "ready", lambda: False)
    monkeypatch.setattr(service, "find_executable", lambda: Path("ollama.exe"))
    job = Mock(assigned=False)
    job.assign.side_effect = OSError("cannot assign")
    process = Mock(pid=123)
    process.poll.return_value = None
    monkeypatch.setattr(service, "ProcessJob", lambda: job)
    monkeypatch.setattr(service, "launch", lambda *args: process)
    with pytest.raises(local_ai.LocalAIError):
        service.start(tmp_path)
    process.terminate.assert_called_once()
    job.close.assert_called_once()


@pytest.mark.parametrize("row", [
    {"ProcessId": 123, "ExecutablePath": "other.exe", "CommandLine": "other serve"},
    {"ProcessId": 123, "ExecutablePath": "ollama.exe", "CommandLine": "ollama run model"},
    {"ProcessId": True, "ExecutablePath": "ollama.exe", "CommandLine": "ollama serve"},
    [{"ProcessId": 123}, {"ProcessId": 456}],
])
def test_unknown_listener_cannot_be_stopped(monkeypatch, row):
    monkeypatch.setattr(service.sys, "platform", "win32")
    monkeypatch.setattr(service.subprocess, "CREATE_NO_WINDOW", 0, raising=False)
    monkeypatch.setattr(service.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=json.dumps(row)))
    with pytest.raises(local_ai.LocalAIError, match="停止を中止"):
        service.external_server_pid()


def test_manual_stop_targets_only_verified_pid(monkeypatch):
    monkeypatch.setattr(service, "ready", Mock(side_effect=[True, False]))
    monkeypatch.setattr(service, "external_server_pid", lambda: 123)
    monkeypatch.setattr(service.subprocess, "CREATE_NO_WINDOW", 0, raising=False)
    run = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(service.subprocess, "run", run)
    assert service.stop() == "stopped"
    assert run.call_args.args[0][1:] == ["/PID", "123", "/T", "/F"]


def test_stopped_service_stop_is_harmless(monkeypatch):
    monkeypatch.setattr(service, "ready", lambda: False)
    lookup = Mock()
    monkeypatch.setattr(service, "external_server_pid", lookup)
    assert service.stop() == "not_running"
    lookup.assert_not_called()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows job lifecycle")
def test_closing_job_terminates_owned_process():
    job = ProcessJob()
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        job.assign(process.pid)
        job.close()
        assert process.wait(timeout=5) is not None
    finally:
        job.close()
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
