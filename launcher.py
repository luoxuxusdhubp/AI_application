"""微信 AI 助手一键启动器。

默认启动后端和桌面小宠物，不需要打开网页。传入 --web 时保留旧的网页管理模式。
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BACKEND_URL = "http://127.0.0.1:8000/docs"
FRONTEND_URL = "http://127.0.0.1:3000"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


def find_python() -> str:
    candidates = [
        ROOT / "backend" / ".venv" / "Scripts" / "python.exe",
        ROOT / ".venv" / "Scripts" / "python.exe",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return sys.executable or "python"


def find_npm() -> str:
    for name in ("npm.cmd", "npm"):
        found = shutil.which(name)
        if found:
            return found
    raise RuntimeError("未找到 npm。请先安装 Node.js 18 或更高版本。")


def port_is_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def http_ready(url: str) -> bool:
    try:
        request = urllib.request.Request(url, headers={"Connection": "close"})
        with urllib.request.urlopen(request, timeout=1.5) as response:
            # 完整读取响应后再关闭连接，避免 Windows Proactor/Uvicorn
            # 在客户端提前断开时记录 WinError 10054。
            response.read()
            return 200 <= response.status < 500
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def wait_for_service(url: str, port: int, process: subprocess.Popen, timeout: float = 60) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        if http_ready(url):
            return True
        # 端口已打开但服务仍在加载时，减少无意义的请求频率。
        time.sleep(0.5 if not port_is_open(port) else 1.0)
    return False


def stop_process(process: subprocess.Popen | None, name: str) -> None:
    if process is None or process.poll() is not None:
        return
    pid = process.pid
    print(f"正在停止{name} (PID {pid})...", flush=True)
    if os.name == "nt":
        # npm.cmd 会再派生 node 进程，/T 确保只清理本次启动的进程树。
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
            check=False,
        )
    else:
        try:
            process.terminate()
        except OSError:
            pass
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass


def start_process(command: list[str], name: str) -> subprocess.Popen:
    print(f"正在启动{name}...", flush=True)
    return subprocess.Popen(
        command,
        cwd=str(ROOT),
        env={**os.environ, "NEXT_TELEMETRY_DISABLED": "1"},
        creationflags=CREATE_NEW_PROCESS_GROUP,
    )


def start_or_reuse(url: str, port: int, command: list[str], name: str) -> subprocess.Popen | None:
    if http_ready(url):
        print(f"{name}已经在运行，复用现有服务。", flush=True)
        return None
    process = start_process(command, name)
    if not wait_for_service(url, port, process):
        raise RuntimeError(f"{name}启动失败。")
    print(f"{name}已就绪。", flush=True)
    return process


def main() -> int:
    backend = None
    frontend = None
    pet = None
    web_mode = "--web" in sys.argv[1:]
    try:
        python = find_python()
        backend = start_or_reuse(
            BACKEND_URL,
            8000,
            [python, "-u", str(ROOT / "backend" / "main.py")],
            "后端",
        )

        if web_mode:
            npm = find_npm()
            frontend = start_or_reuse(
                FRONTEND_URL,
                3000,
                [npm, "run", "dev"],
                "前端",
            )
            print("正在打开 http://127.0.0.1:3000", flush=True)
            webbrowser.open(FRONTEND_URL)
            print("网页管理模式已启动。", flush=True)
        else:
            pet = start_process([python, "-u", str(ROOT / "desktop_pet.py")], "桌面小宠物")
            time.sleep(1)
            if pet.poll() is not None:
                raise RuntimeError("桌面小宠物启动失败。")
            print("桌面小宠物已启动。关闭小宠物窗口可停止监听。", flush=True)

        while True:
            if backend is not None and backend.poll() is not None:
                raise RuntimeError("后端进程已退出，请查看 backend/logs/ 目录中的日志。")
            if frontend is not None and frontend.poll() is not None:
                raise RuntimeError("前端进程已退出，请检查 Node.js 或前端输出。")
            if pet is not None and pet.poll() is not None:
                return 0
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n收到停止指令。", flush=True)
        return 0
    except Exception as error:
        print(f"启动失败：{error}", file=sys.stderr, flush=True)
        return 1
    finally:
        stop_process(pet, "桌面小宠物")
        stop_process(frontend, "前端")
        stop_process(backend, "后端")


if __name__ == "__main__":
    raise SystemExit(main())
