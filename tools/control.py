"""SIGNAL LAB — 简易控制窗口（启停网页前后端；后端固定 9000 并强制清端口）。"""
from __future__ import annotations

import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from collections import deque
from pathlib import Path
from tkinter import messagebox
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
HOST = "127.0.0.1"
API_PORT_START = 9000
WEB_PORT_START = 5173

# Hide child console windows on Windows (npm.cmd / python.exe / cloudflared)
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def _python_cli() -> str:
    """Prefer python.exe even under pythonw, so -m uvicorn works; window is still hidden."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        sibling = exe.with_name("python.exe")
        if sibling.is_file():
            return str(sibling)
    return str(exe)


def _popen_silent(cmd: list[str], **kwargs) -> subprocess.Popen[str]:
    kwargs = dict(kwargs)
    if sys.platform == "win32":
        flags = int(kwargs.pop("creationflags", 0)) | _CREATE_NO_WINDOW
        kwargs["creationflags"] = flags
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0  # SW_HIDE
        kwargs["startupinfo"] = si
    return subprocess.Popen(cmd, **kwargs)


def _host_from_url(url: str) -> str | None:
    m = re.match(r"^https?://([^/:]+)", (url or "").strip())
    return m.group(1) if m else None


def _dns_resolves(host: str, timeout: float = 3.0) -> bool:
    """True if system DNS can resolve host (uses OS resolver, same as browsers)."""
    try:
        socket.setdefaulttimeout(timeout)
        socket.getaddrinfo(host, 443)
        return True
    except OSError:
        return False
    finally:
        socket.setdefaulttimeout(None)


def _dns_resolves_via(host: str, nameserver: str, timeout: float = 3.0) -> bool:
    """Best-effort check via public DNS (nslookup). Used only for diagnostics."""
    try:
        out = subprocess.check_output(
            ["nslookup", host, nameserver],
            text=True,
            encoding="mbcs",
            errors="replace",
            timeout=timeout + 2,
            creationflags=_CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        low = out.lower()
        if "non-existent" in low or "找不到" in out or "can't find" in low:
            return False
        return "address" in low or "addresses" in low or "名称:" in out
    except Exception:  # noqa: BLE001
        return False


def _which_cloudflared() -> str | None:
    found = shutil.which("cloudflared")
    if found:
        return found
    for p in (
        Path(r"C:\Program Files (x86)\cloudflared\cloudflared.exe"),
        Path(r"C:\Program Files\cloudflared\cloudflared.exe"),
    ):
        if p.is_file():
            return str(p)
    return None


def _port_accepts(host: str, port: int, timeout: float = 0.25) -> bool:
    """True if something already accepts TCP connections on host:port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        try:
            sock.close()
        except Exception:  # noqa: BLE001
            pass


def _netstat_listen_map() -> dict[int, set[int]]:
    """Parse netstat once → {port: {pid, ...}} for LISTENING sockets."""
    mapping: dict[int, set[int]] = {}
    if sys.platform != "win32":
        return mapping
    try:
        out = subprocess.check_output(
            ["netstat", "-ano", "-p", "tcp"],
            text=True,
            encoding="mbcs",
            errors="replace",
            creationflags=_CREATE_NO_WINDOW,
        )
    except Exception:  # noqa: BLE001
        return mapping
    for line in out.splitlines():
        if "LISTENING" not in line.upper():
            continue
        parts = line.split()
        if len(parts) < 4:
            continue
        local = parts[1]
        # 127.0.0.1:9000 or [::1]:9000
        if ":" not in local:
            continue
        try:
            port_s = local.rsplit(":", 1)[-1]
            if port_s.startswith("]"):
                port_s = port_s[1:]
            port = int(port_s)
            pid = int(parts[-1])
        except ValueError:
            continue
        if pid > 0:
            mapping.setdefault(port, set()).add(pid)
    return mapping


def _pick_port(host: str, start: int, span: int = 40) -> int:
    """Pick a free TCP port. Do NOT use SO_REUSEADDR."""
    for port in range(start, start + span):
        if _port_accepts(host, port):
            continue
        if _can_bind(host, port):
            return port
    raise RuntimeError(f"在 {start}-{start + span - 1} 找不到可用端口")


def _pids_listening_on_port(port: int, listen_map: dict[int, set[int]] | None = None) -> set[int]:
    """PIDs with a TCP LISTENING socket on the given local port."""
    if listen_map is not None:
        return set(listen_map.get(port) or ())
    if sys.platform != "win32":
        pids: set[int] = set()
        try:
            out = subprocess.check_output(
                ["lsof", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                text=True,
                errors="replace",
            )
            for line in out.splitlines():
                line = line.strip()
                if line.isdigit():
                    pids.add(int(line))
        except Exception:  # noqa: BLE001
            pass
        return pids
    return set(_netstat_listen_map().get(port) or ())


def _can_bind(host: str, port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        sock.bind((host, port))
        sock.close()
        return True
    except OSError:
        try:
            sock.close()
        except Exception:  # noqa: BLE001
            pass
        return False


def _force_claim_port(
    host: str,
    port: int,
    *,
    rounds: int = 4,
    log=None,
) -> bool:
    """Kill whatever holds `port` and verify we can bind it. Returns True on success."""
    # One-shot: kill project uvicorn/vite + orphan --reload workers
    proj = _iter_project_pids()
    if proj:
        if log:
            log(f"[端口] 结束本项目残留进程：{', '.join(map(str, sorted(proj)))}")
        _kill_pids(proj)
        time.sleep(0.3)

    for i in range(rounds):
        listen_map = _netstat_listen_map()
        pids = set(listen_map.get(port) or ())
        # Windows: netstat may still show a dead parent PID while orphan
        # multiprocessing children keep the socket alive.
        orphans = _orphan_mp_pids(parent_hints=pids or None)
        targets = set(pids) | orphans
        if targets:
            if log:
                log(f"[端口] 强制释放 {port}，结束 PID：{', '.join(map(str, sorted(targets)))}")
            _kill_pids(targets)
            time.sleep(0.25 + i * 0.1)
        # Success criterion: we can bind. Ghost acceptors that don't own a bindable
        # socket are rare; requiring "not accepts" caused multi-minute hangs.
        if _can_bind(host, port):
            return True
    return _can_bind(host, port)


def _kill_pids(pids: set[int]) -> list[int]:
    """Force-kill PIDs; return list of PIDs we successfully ended (or gone)."""
    ended: list[int] = []
    for pid in sorted(pids):
        if pid <= 0:
            continue
        if sys.platform == "win32":
            r = subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                text=True,
                encoding="mbcs",
                errors="replace",
                check=False,
                creationflags=_CREATE_NO_WINDOW,
            )
            # Also try without /T in case tree kill fails
            if r.returncode != 0:
                subprocess.run(
                    ["taskkill", "/F", "/PID", str(pid)],
                    capture_output=True,
                    text=True,
                    encoding="mbcs",
                    errors="replace",
                    check=False,
                    creationflags=_CREATE_NO_WINDOW,
                )
            # Treat "not found" as cleaned
            err = (r.stderr or "") + (r.stdout or "")
            if r.returncode == 0 or "not found" in err.lower() or "没有" in err or "不存在" in err:
                ended.append(pid)
        else:
            try:
                os.kill(pid, 9)
                ended.append(pid)
            except ProcessLookupError:
                ended.append(pid)
            except Exception:  # noqa: BLE001
                pass
    return ended


def _kill_port(port: int) -> list[int]:
    """Best-effort kill listeners on port. Returns PIDs targeted."""
    listen = _pids_listening_on_port(port)
    # Also reap orphan reload workers that keep the socket after parent dies.
    return _kill_pids(listen | _orphan_mp_pids(parent_hints=listen or None))


def _win_proc_rows(names: list[str]) -> list[dict]:
    """Fast filtered Win32_Process rows: ProcessId, ParentProcessId, CommandLine."""
    if sys.platform != "win32" or not names:
        return []
    filt = " OR ".join(f"Name='{n}'" for n in names)
    try:
        out = subprocess.check_output(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_Process -Filter "
                f"\"{filt}\" |"
                " Select-Object ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress",
            ],
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_CREATE_NO_WINDOW,
            timeout=12,
        )
    except Exception:  # noqa: BLE001
        return []
    try:
        import json

        rows = json.loads(out or "[]")
    except Exception:  # noqa: BLE001
        return []
    if isinstance(rows, dict):
        rows = [rows]
    return [r for r in (rows or []) if isinstance(r, dict)]


def _orphan_mp_pids(*, parent_hints: set[int] | None = None) -> set[int]:
    """Find leftover uvicorn --reload / multiprocessing workers.

    On Windows, netstat often still attributes LISTENING to a dead reloader
    parent while the real holder is an orphan `spawn_main` child.
    """
    found: set[int] = set()
    if sys.platform != "win32":
        return found
    rows = _win_proc_rows(["python.exe", "pythonw.exe"])
    alive = {int(r["ProcessId"]) for r in rows if r.get("ProcessId") is not None}
    for row in rows:
        cmd = str(row.get("CommandLine") or "").lower()
        if "spawn_main" not in cmd and "multiprocessing-fork" not in cmd:
            continue
        try:
            pid_i = int(row.get("ProcessId"))
            parent_i = int(row.get("ParentProcessId") or 0)
        except (TypeError, ValueError):
            continue
        parent_dead = parent_i > 0 and parent_i not in alive
        hinted = bool(parent_hints) and parent_i in parent_hints
        if hinted or parent_dead:
            found.add(pid_i)
    return found


def _iter_project_pids() -> set[int]:
    """Find uvicorn / vite / npm-dev processes belonging to this repo (fast path)."""
    pids: set[int] = set()
    frontend_s = str(FRONTEND).replace("\\", "/").lower()
    root_s = str(ROOT).replace("\\", "/").lower()

    if sys.platform == "win32":
        rows = _win_proc_rows(["python.exe", "pythonw.exe", "node.exe", "cmd.exe"])
        uvicorn_parents: set[int] = set()
        for row in rows:
            cmd = str(row.get("CommandLine") or "")
            if not cmd:
                continue
            cl = cmd.replace("\\", "/").lower()
            try:
                pid_i = int(row.get("ProcessId"))
            except (TypeError, ValueError):
                continue
            if "uvicorn" in cl and "main:app" in cl:
                pids.add(pid_i)
                uvicorn_parents.add(pid_i)
            elif "vite" in cl and (frontend_s in cl or root_s in cl):
                pids.add(pid_i)
            elif "npm" in cl and "run" in cl and "dev" in cl and frontend_s in cl:
                pids.add(pid_i)
            elif "cmd.exe" in cl and frontend_s in cl and ("vite" in cl or "npm" in cl):
                pids.add(pid_i)
        # Include orphan reload workers (parent dead or known uvicorn parent)
        pids |= _orphan_mp_pids(parent_hints=uvicorn_parents or None)
        return pids

    try:
        out = subprocess.check_output(["ps", "-ax", "-o", "pid=,command="], text=True, errors="replace")
    except Exception:  # noqa: BLE001
        return pids
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(None, 1)
        if len(parts) < 2:
            continue
        try:
            pid_i = int(parts[0])
        except ValueError:
            continue
        cl = parts[1].lower()
        if "uvicorn" in cl and "main:app" in cl:
            pids.add(pid_i)
        elif "vite" in cl and frontend_s in cl.replace("\\", "/"):
            pids.add(pid_i)
    return pids


def _kill_port_range(start: int, span: int = 40) -> dict[int, list[int]]:
    """Kill listeners across a port range using one netstat snapshot."""
    hit: dict[int, list[int]] = {}
    listen_map = _netstat_listen_map()
    all_pids: set[int] = set()
    for port in range(start, start + span):
        pids = set(listen_map.get(port) or ())
        if pids:
            hit[port] = sorted(pids)
            all_pids |= pids
    all_pids |= _orphan_mp_pids(parent_hints=all_pids or None)
    if all_pids:
        _kill_pids(all_pids)
    return hit


class Controller:
    def __init__(self, on_log) -> None:
        self.on_log = on_log
        self.backend: subprocess.Popen[str] | None = None
        self.frontend: subprocess.Popen[str] | None = None
        self.tunnel: subprocess.Popen[str] | None = None
        self.public_url: str | None = None
        self.api_port = API_PORT_START
        self.web_port = WEB_PORT_START
        self.logs: deque[str] = deque(maxlen=1500)

    @property
    def api_url(self) -> str:
        return f"http://{HOST}:{self.api_port}"

    @property
    def web_url(self) -> str:
        return f"http://{HOST}:{self.web_port}"

    def log(self, msg: str) -> None:
        line = msg.rstrip()
        if not line:
            return
        self.logs.append(line)
        self.on_log(line)

    def _pump(self, proc: subprocess.Popen[str], tag: str) -> None:
        assert proc.stdout is not None
        for raw in proc.stdout:
            text = raw.rstrip("\r\n")
            self.log(f"[{tag}] {text}")
            if tag == "公网" and "trycloudflare.com" in text:
                m = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", text)
                if m:
                    self.public_url = m.group(0)
                    self.log(f"[公网] {self.public_url}")

    def _alive(self, proc: subprocess.Popen[str] | None) -> bool:
        return bool(proc and proc.poll() is None)

    @property
    def backend_ok(self) -> bool:
        return self._alive(self.backend)

    @property
    def frontend_ok(self) -> bool:
        return self._alive(self.frontend)

    @property
    def tunnel_ok(self) -> bool:
        return self._alive(self.tunnel)

    def start_backend(self) -> None:
        if self.backend_ok:
            self.log("[后端] 已在运行")
            return
        # Always force API_PORT_START (9000): kill occupants, do not silently bump.
        self.log(f"[后端] 强制占用端口 {API_PORT_START}…")
        if not _force_claim_port(HOST, API_PORT_START, log=self.log):
            raise RuntimeError(
                f"无法释放端口 {API_PORT_START}（仍被占用或系统残留）。"
                f"请先点「全部停止」，或重启电脑后再试。"
            )
        self.api_port = API_PORT_START
        self.log(f"[后端] 使用端口 {self.api_port}")
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        # Avoid Windows Terminal / conhost attaching to child processes
        env["WT_SESSION"] = ""
        # No --reload: on Windows the reloader parent can die while orphan
        # multiprocessing children keep :9000, making restart look "stuck".
        cmd = [
            _python_cli(),
            "-m",
            "uvicorn",
            "main:app",
            "--host",
            HOST,
            "--port",
            str(self.api_port),
        ]
        self.log("[后端] 启动中…")
        self.backend = _popen_silent(
            cmd,
            cwd=str(BACKEND),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        threading.Thread(target=self._pump, args=(self.backend, "后端"), daemon=True).start()
        self._wait_url(f"{self.api_url}/api/health", "后端")
        # Persist proxy target for Vite loadEnv / manual restarts
        try:
            (FRONTEND / ".env.local").write_text(
                f"VITE_API_BASE=\nSIGNAL_API_URL={self.api_url}\n",
                encoding="utf-8",
            )
        except Exception:  # noqa: BLE001
            pass

    def start_frontend(self) -> None:
        if self.frontend_ok:
            self.log("[前端] 已在运行")
            return
        npm = shutil.which("npm")
        if not npm:
            raise RuntimeError("未找到 npm，请先安装 Node.js 18+")
        if not self.backend_ok:
            raise RuntimeError("请先启动后端")
        self.web_port = _pick_port(HOST, WEB_PORT_START)
        if self.web_port != WEB_PORT_START:
            self.log(f"[前端] 端口 {WEB_PORT_START} 不可用，改用 {self.web_port}")
        else:
            self.log(f"[前端] 使用端口 {self.web_port}")
        env = os.environ.copy()
        env["SIGNAL_API_URL"] = self.api_url
        # Empty VITE_API_BASE → browser uses same-origin /api via Vite proxy.
        # Avoid hardcoding 9000 (often occupied by a stale Windows listener).
        env["VITE_API_BASE"] = ""
        try:
            (FRONTEND / ".env.local").write_text(
                f"VITE_API_BASE=\nSIGNAL_API_URL={self.api_url}\n",
                encoding="utf-8",
            )
        except Exception:  # noqa: BLE001
            pass
        # npm on Windows is npm.cmd — run via cmd /c with hidden console
        if sys.platform == "win32":
            cmd = [
                "cmd.exe",
                "/d",
                "/c",
                npm,
                "run",
                "dev",
                "--",
                "--host",
                HOST,
                "--port",
                str(self.web_port),
                "--strictPort",
            ]
        else:
            cmd = [
                npm,
                "run",
                "dev",
                "--",
                "--host",
                HOST,
                "--port",
                str(self.web_port),
                "--strictPort",
            ]
        self.log("[前端] 启动中…")
        self.frontend = _popen_silent(
            cmd,
            cwd=str(FRONTEND),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        threading.Thread(target=self._pump, args=(self.frontend, "前端"), daemon=True).start()
        self._wait_url(self.web_url, "前端", timeout=90)

    def start_all(self) -> None:
        # Controller is the sole authority: clear leftovers, then start fresh.
        self.log("[控制] 启动前先清理本项目相关进程与端口…")
        self.stop_all(quiet=True)
        time.sleep(0.5)
        self.start_backend()
        self.start_frontend()
        self.log(f"[控制] 以控制器为准：网页 {self.web_url}  ·  接口 {self.api_url}")

    def stop_backend(self) -> None:
        port = self.api_port
        self._stop(self.backend, "后端")
        self.backend = None
        killed = _kill_port(port)
        if killed:
            self.log(f"[后端] 已清理端口 {port}（PID {', '.join(map(str, killed))}）")

    def stop_frontend(self) -> None:
        port = self.web_port
        self._stop(self.frontend, "前端")
        self.frontend = None
        killed = _kill_port(port)
        if killed:
            self.log(f"[前端] 已清理端口 {port}（PID {', '.join(map(str, killed))}）")

    def stop_tunnel(self) -> None:
        self._stop(self.tunnel, "公网")
        self.tunnel = None
        self.public_url = None

    def stop_all(self, *, quiet: bool = False) -> None:
        """Stop tracked children and wipe project-related ports/processes."""
        if not quiet:
            self.log("[控制] 正在全部停止并清理相关端口…")
        self.stop_tunnel()
        self._stop(self.frontend, "前端")
        self._stop(self.backend, "后端")
        self.frontend = None
        self.backend = None

        proj = _iter_project_pids()
        if proj:
            _kill_pids(proj)
            if not quiet:
                self.log(f"[控制] 已结束项目进程：{', '.join(map(str, sorted(proj)))}")

        api_hit = _kill_port_range(API_PORT_START, 40)
        web_hit = _kill_port_range(WEB_PORT_START, 40)
        # Second pass after taskkill /T settles
        time.sleep(0.35)
        api_hit2 = _kill_port_range(API_PORT_START, 40)
        web_hit2 = _kill_port_range(WEB_PORT_START, 40)

        ports = sorted(set(api_hit) | set(api_hit2) | set(web_hit) | set(web_hit2))
        if ports and not quiet:
            self.log(f"[控制] 已清理端口：{', '.join(map(str, ports))}")

        listen_map = _netstat_listen_map()
        check_ports = list(range(API_PORT_START, API_PORT_START + 10)) + list(
            range(WEB_PORT_START, WEB_PORT_START + 5)
        )
        stuck = [p for p in check_ports if listen_map.get(p)]
        if stuck and not quiet:
            self.log(
                f"[控制] 警告：以下端口仍有残留连接（进程可能已死但未释放）："
                f"{', '.join(map(str, stuck))}。"
                f"下次启动后端会强制清理 9000；若仍失败请重启电脑。"
            )
        elif not quiet and not ports:
            self.log("[控制] 相关端口已空闲")

        # Reset to defaults; next start_all will re-pick and rewrite .env.local
        self.api_port = API_PORT_START
        self.web_port = WEB_PORT_START
        try:
            (FRONTEND / ".env.local").write_text(
                "VITE_API_BASE=\nSIGNAL_API_URL=\n",
                encoding="utf-8",
            )
        except Exception:  # noqa: BLE001
            pass
        if not quiet:
            self.log("[控制] 全部已停止")

    def start_tunnel(self) -> None:
        exe = _which_cloudflared()
        if not exe:
            raise RuntimeError("未找到 cloudflared，请先安装 Cloudflare Tunnel")
        if not self.frontend_ok:
            raise RuntimeError("请先启动前端")
        if self.tunnel_ok:
            self.log("[公网] 已在运行")
            return
        self.public_url = None
        cmd = [exe, "tunnel", "--url", self.web_url]
        self.log("[公网] 正在创建临时隧道…")
        self.log("[公网] 提示：*.trycloudflare.com 在国内常被干扰，手机/访客端可能需翻墙才能打开")
        self.tunnel = _popen_silent(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        threading.Thread(target=self._pump, args=(self.tunnel, "公网"), daemon=True).start()
        deadline = time.time() + 35
        while time.time() < deadline:
            if not self.tunnel_ok:
                raise RuntimeError("公网隧道进程已退出，请查看上方日志")
            if self.public_url:
                self.log(f"[公网] 已就绪：{self.public_url}")
                self.log("[公网] 请用「复制公网」分享；首次打开若出现 Cloudflare 安全页，点 Continue 即可")
                self.diagnose_public_dns()
                return
            time.sleep(0.35)
        self.log("[公网] 仍未解析到 trycloudflare 地址，请看日志是否有报错；隧道进程仍在运行时可稍等")

    def diagnose_public_dns(self) -> str:
        """Check whether the public hostname resolves on this PC. Returns a short status."""
        url = self.public_url
        if not url:
            msg = "尚无公网链接，请先开启公网"
            self.log(f"[公网检测] {msg}")
            return msg
        host = _host_from_url(url)
        if not host:
            msg = f"无法解析链接：{url}"
            self.log(f"[公网检测] {msg}")
            return msg
        sys_ok = _dns_resolves(host)
        pub_ok = _dns_resolves_via(host, "1.1.1.1") or _dns_resolves_via(host, "8.8.8.8")
        if sys_ok:
            msg = f"本机 DNS 可解析 {host}，浏览器应能打开（若仍失败可试翻墙）"
            self.log(f"[公网检测] {msg}")
            return msg
        if pub_ok:
            msg = (
                f"本机 DNS 污染：系统解析不到 {host}（NXDOMAIN），"
                "但 1.1.1.1/8.8.8.8 能解析。"
                "请把网卡 DNS 改为 1.1.1.1 与 8.8.8.8，"
                "或在 Chrome 开启「使用安全 DNS」，或开 VPN 后再打开公网链接。"
            )
            self.log(f"[公网检测] {msg}")
            return msg
        msg = (
            f"公网域名 {host} 当前无法解析（隧道可能已失效）。"
            "请关闭公网后重新开启，并复制最新链接。"
        )
        self.log(f"[公网检测] {msg}")
        return msg

    def _stop(self, proc: subprocess.Popen[str] | None, tag: str) -> None:
        if not proc or proc.poll() is not None:
            return
        self.log(f"[{tag}] 正在停止…")
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
        except Exception as exc:  # noqa: BLE001
            self.log(f"[{tag}] 停止异常：{exc}")

    def _wait_url(self, url: str, tag: str, timeout: float = 45.0) -> None:
        deadline = time.time() + timeout
        last = ""
        while time.time() < deadline:
            proc = self.backend if tag == "后端" else self.frontend
            if proc is not None and proc.poll() is not None:
                raise RuntimeError(f"{tag} 进程已退出（code={proc.returncode}）")
            try:
                with urlopen(url, timeout=2) as resp:
                    if resp.status == 200:
                        self.log(f"[{tag}] 已就绪：{url}")
                        return
            except Exception as exc:  # noqa: BLE001
                last = str(exc)
            time.sleep(0.4)
        raise RuntimeError(f"{tag} 启动超时：{last or url}")


# —— UI palette (slate + teal, trading-desk feel) ——
_BG = "#0c1520"
_BG_PANEL = "#121e2c"
_BG_CARD = "#162536"
_BG_CARD_HI = "#1a2d42"
_BORDER = "#243547"
_TEXT = "#e7eef6"
_MUTED = "#8aa0b5"
_ACCENT = "#2dd4a8"
_ACCENT_DIM = "#1a9e7a"
_DANGER = "#e86a6a"
_WARN = "#e6b85c"
_OK = "#3ecf8e"
_IDLE = "#5a7188"
_LOG_BG = "#0a121b"
_LOG_FG = "#b8c9d8"
_FONT = ("Microsoft YaHei UI", 10)
_FONT_SM = ("Microsoft YaHei UI", 9)
_FONT_TITLE = ("Segoe UI Semibold", 16)
_FONT_MONO = ("Cascadia Mono", 9) if sys.platform == "win32" else ("Consolas", 9)


class App:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("SIGNAL LAB 控制台")
        self.root.geometry("820x600")
        self.root.minsize(680, 480)
        self.root.configure(bg=_BG)
        try:
            self.root.option_add("*Font", _FONT)
        except tk.TclError:
            pass

        self.q: queue.Queue[str] = queue.Queue()
        self.ctl = Controller(on_log=self.q.put)
        self.busy = False
        self._action_btns: list[tk.Button] = []

        self._ui()
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.after(100, self._drain)
        self.root.after(300, lambda: self._bg(self.ctl.start_all, open_browser=True))

    def _mk_btn(
        self,
        parent: tk.Misc,
        text: str,
        command,
        *,
        kind: str = "ghost",
        width: int | None = None,
    ) -> tk.Button:
        styles = {
            "primary": {"bg": _ACCENT_DIM, "fg": "#04140f", "activebackground": _ACCENT, "activeforeground": "#04140f"},
            "danger": {"bg": "#3a2228", "fg": "#ffb4b4", "activebackground": "#4a2a32", "activeforeground": "#ffe0e0"},
            "ghost": {"bg": _BG_CARD, "fg": _TEXT, "activebackground": _BG_CARD_HI, "activeforeground": _TEXT},
            "accent": {"bg": "#163528", "fg": _ACCENT, "activebackground": "#1c4332", "activeforeground": "#9ff0d4"},
        }
        s = styles.get(kind, styles["ghost"])
        btn = tk.Button(
            parent,
            text=text,
            command=command,
            relief="flat",
            bd=0,
            padx=14,
            pady=7,
            cursor="hand2",
            font=_FONT_SM,
            highlightthickness=1,
            highlightbackground=_BORDER,
            highlightcolor=_ACCENT,
            **s,
        )
        if width is not None:
            btn.configure(width=width)
        return btn

    def _mk_card(self, parent: tk.Misc, title: str) -> tuple[tk.Frame, tk.Label, tk.Label, tk.Label]:
        card = tk.Frame(parent, bg=_BG_CARD, highlightthickness=1, highlightbackground=_BORDER)
        top = tk.Frame(card, bg=_BG_CARD)
        top.pack(fill="x", padx=12, pady=(10, 2))
        dot = tk.Label(top, text="●", bg=_BG_CARD, fg=_IDLE, font=("Segoe UI", 9))
        dot.pack(side="left")
        tk.Label(top, text=title, bg=_BG_CARD, fg=_MUTED, font=_FONT_SM).pack(side="left", padx=(6, 0))
        state = tk.Label(card, text="已停止", bg=_BG_CARD, fg=_TEXT, font=("Microsoft YaHei UI", 11, "bold"))
        state.pack(anchor="w", padx=12, pady=(2, 0))
        detail = tk.Label(card, text="—", bg=_BG_CARD, fg=_MUTED, font=_FONT_SM)
        detail.pack(anchor="w", padx=12, pady=(2, 12))
        return card, dot, state, detail

    def _ui(self) -> None:
        # Header
        head = tk.Frame(self.root, bg=_BG)
        head.pack(fill="x", padx=18, pady=(16, 8))
        brand = tk.Frame(head, bg=_BG)
        brand.pack(side="left")
        tk.Label(brand, text="SIGNAL LAB", bg=_BG, fg=_TEXT, font=_FONT_TITLE).pack(anchor="w")
        tk.Label(brand, text="本地网页服务控制台", bg=_BG, fg=_MUTED, font=_FONT_SM).pack(anchor="w")
        self.busy_lbl = tk.Label(head, text="", bg=_BG, fg=_WARN, font=_FONT_SM)
        self.busy_lbl.pack(side="right", padx=(8, 0))

        # Status cards
        cards = tk.Frame(self.root, bg=_BG)
        cards.pack(fill="x", padx=18, pady=(4, 10))
        cards.columnconfigure(0, weight=1)
        cards.columnconfigure(1, weight=1)
        cards.columnconfigure(2, weight=1)

        self.card_be, self.dot_be, self.state_be, self.detail_be = self._mk_card(cards, "后端 API")
        self.card_fe, self.dot_fe, self.state_fe, self.detail_fe = self._mk_card(cards, "前端网页")
        self.card_tn, self.dot_tn, self.state_tn, self.detail_tn = self._mk_card(cards, "公网隧道")
        self.card_be.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        self.card_fe.grid(row=0, column=1, sticky="nsew", padx=3)
        self.card_tn.grid(row=0, column=2, sticky="nsew", padx=(6, 0))

        # Actions
        actions = tk.Frame(self.root, bg=_BG_PANEL, highlightthickness=1, highlightbackground=_BORDER)
        actions.pack(fill="x", padx=18, pady=(0, 10))
        inner = tk.Frame(actions, bg=_BG_PANEL)
        inner.pack(fill="x", padx=10, pady=10)

        self.btn_start = self._mk_btn(
            inner, "▶  全部启动", lambda: self._bg(self.ctl.start_all, open_browser=True), kind="primary"
        )
        self.btn_stop = self._mk_btn(inner, "■  全部停止", lambda: self._bg(self.ctl.stop_all), kind="danger")
        self.btn_open = self._mk_btn(inner, "打开网页", self._open, kind="accent")
        self.btn_copy = self._mk_btn(inner, "复制网页", self._copy_web)
        self.btn_copy_api = self._mk_btn(inner, "复制接口", self._copy_api)
        self.btn_tunnel = self._mk_btn(inner, "开启公网", self._toggle_tunnel)
        self.btn_copy_public = self._mk_btn(inner, "复制公网", self._copy_public, kind="accent")
        self.btn_check_public = self._mk_btn(inner, "检测公网", self._check_public)
        self._action_btns = [
            self.btn_start,
            self.btn_stop,
            self.btn_open,
            self.btn_copy,
            self.btn_copy_api,
            self.btn_tunnel,
            self.btn_copy_public,
            self.btn_check_public,
        ]
        for i, b in enumerate(self._action_btns):
            b.pack(side="left", padx=(0 if i == 0 else 6, 0))

        # URL + hint strip
        meta = tk.Frame(self.root, bg=_BG)
        meta.pack(fill="x", padx=18, pady=(0, 6))
        self.urls = tk.StringVar(value="等待启动…")
        self.hint = tk.StringVar(
            value="后端固定 9000（占用则强制杀掉）；前端从 5173 起，占用则换空闲端口"
        )
        tk.Label(meta, textvariable=self.urls, bg=_BG, fg=_ACCENT, font=_FONT_SM, anchor="w").pack(
            fill="x"
        )
        tk.Label(meta, textvariable=self.hint, bg=_BG, fg=_MUTED, font=_FONT_SM, anchor="w").pack(
            fill="x", pady=(2, 0)
        )

        # Log panel
        log_wrap = tk.Frame(self.root, bg=_BG_PANEL, highlightthickness=1, highlightbackground=_BORDER)
        log_wrap.pack(fill="both", expand=True, padx=18, pady=(0, 16))
        log_head = tk.Frame(log_wrap, bg=_BG_PANEL)
        log_head.pack(fill="x", padx=12, pady=(8, 4))
        tk.Label(log_head, text="运行日志", bg=_BG_PANEL, fg=_MUTED, font=_FONT_SM).pack(side="left")
        self.btn_clear = self._mk_btn(log_head, "清空", self._clear_log, kind="ghost")
        self.btn_clear.pack(side="right")
        self.btn_clear.configure(padx=8, pady=2)

        log_body = tk.Frame(log_wrap, bg=_LOG_BG)
        log_body.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.text = tk.Text(
            log_body,
            wrap="word",
            bg=_LOG_BG,
            fg=_LOG_FG,
            insertbackground=_ACCENT,
            relief="flat",
            bd=0,
            padx=10,
            pady=8,
            font=_FONT_MONO,
            selectbackground="#1e3a4f",
            selectforeground=_TEXT,
        )
        scroll = tk.Scrollbar(
            log_body,
            command=self.text.yview,
            bg=_BG_CARD,
            troughcolor=_LOG_BG,
            activebackground=_MUTED,
            width=10,
            relief="flat",
            bd=0,
        )
        self.text.configure(yscrollcommand=scroll.set)
        self.text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self.text.tag_configure("info", foreground=_LOG_FG)
        self.text.tag_configure("ok", foreground=_OK)
        self.text.tag_configure("err", foreground=_DANGER)
        self.text.tag_configure("warn", foreground=_WARN)
        self.text.tag_configure("copy", foreground=_ACCENT)
        self.text.tag_configure("sys", foreground=_MUTED)

        self._append_log("控制台已就绪。正在自动启动后端与前端…", "sys")
        self._append_log("端口以本控制器为准；全部停止会清理本项目相关端口与进程。", "sys")

    def _log_tag(self, line: str) -> str:
        low = line.lower()
        if "[错误]" in line or "error" in low or "traceback" in low:
            return "err"
        if "[已复制]" in line or "已就绪" in line:
            return "ok" if "已就绪" in line else "copy"
        if "超时" in line or "改用" in line or "警告" in line:
            return "warn"
        if line.startswith("[公网]") or "trycloudflare" in low:
            return "ok"
        return "info"

    def _append_log(self, line: str, tag: str | None = None) -> None:
        tag = tag or self._log_tag(line)
        self.text.configure(state="normal")
        self.text.insert("end", line + "\n", tag)
        self.text.see("end")
        self.text.configure(state="disabled")

    def _clear_log(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")
        self._append_log("日志已清空。", "sys")

    def _set_busy(self, v: bool) -> None:
        self.busy = v
        state = "disabled" if v else "normal"
        for b in self._action_btns:
            b.configure(state=state)
        self.busy_lbl.configure(text="处理中…" if v else "")

    def _set_card(
        self,
        dot: tk.Label,
        state: tk.Label,
        detail: tk.Label,
        *,
        online: bool,
        state_text: str,
        detail_text: str,
    ) -> None:
        color = _OK if online else _IDLE
        dot.configure(fg=color)
        state.configure(text=state_text, fg=_TEXT if online else _MUTED)
        detail.configure(text=detail_text)

    def _refresh(self) -> None:
        be_ok = self.ctl.backend_ok
        fe_ok = self.ctl.frontend_ok
        tn_ok = self.ctl.tunnel_ok

        self._set_card(
            self.dot_be,
            self.state_be,
            self.detail_be,
            online=be_ok,
            state_text="运行中" if be_ok else "已停止",
            detail_text=f"端口 {self.ctl.api_port}",
        )
        self._set_card(
            self.dot_fe,
            self.state_fe,
            self.detail_fe,
            online=fe_ok,
            state_text="运行中" if fe_ok else "已停止",
            detail_text=f"端口 {self.ctl.web_port}",
        )
        if tn_ok and self.ctl.public_url:
            tn_detail = self.ctl.public_url
            if len(tn_detail) > 44:
                tn_detail = tn_detail[:42] + "…"
        elif tn_ok:
            tn_detail = "等待公网地址…"
        else:
            tn_detail = "可选"
        self._set_card(
            self.dot_tn,
            self.state_tn,
            self.detail_tn,
            online=tn_ok,
            state_text="已开启" if tn_ok else "未开启",
            detail_text=tn_detail,
        )

        if self.ctl.public_url:
            self.urls.set(
                f"网页  {self.ctl.web_url}    ·    接口  {self.ctl.api_url}    ·    公网  {self.ctl.public_url}"
            )
            self.btn_tunnel.configure(text="关闭公网")
            self.hint.set(
                "公网已就绪：点「复制公网」分享。"
                "国内访问 trycloudflare.com 常需翻墙；首次可能有 Cloudflare 确认页"
            )
        else:
            self.urls.set(f"网页  {self.ctl.web_url}    ·    接口  {self.ctl.api_url}")
            self.btn_tunnel.configure(text="开启公网")
            bumped = []
            if self.ctl.web_port != WEB_PORT_START:
                bumped.append(f"前端已改用 {self.ctl.web_port}")
            if bumped:
                self.hint.set("；".join(bumped) + "（原端口被占用）")
            else:
                self.hint.set("后端固定使用 9000；占用时会强制释放该端口")

    def _drain(self) -> None:
        try:
            while True:
                line = self.q.get_nowait()
                self._append_log(line)
        except queue.Empty:
            pass
        self._refresh()
        self.root.after(120, self._drain)

    def _bg(self, fn, open_browser: bool = False) -> None:
        if self.busy:
            return

        def run() -> None:
            self.root.after(0, lambda: self._set_busy(True))
            err: Exception | None = None
            try:
                fn()
                if open_browser:
                    time.sleep(0.4)
                    webbrowser.open(self.ctl.web_url)
            except Exception as exc:  # noqa: BLE001
                err = exc
                self.ctl.log(f"[错误] {exc}")

            def done() -> None:
                self._set_busy(False)
                self._refresh()
                if err:
                    tip = str(err)
                    if "端口" in tip or "timeout" in tip.lower() or "超时" in tip:
                        tip += "\n\n可点「全部停止」后重试；后端会强制占用 9000，若仍失败请重启电脑。"
                    messagebox.showerror("SIGNAL LAB", tip)

            self.root.after(0, done)

        threading.Thread(target=run, daemon=True).start()

    def _open(self) -> None:
        if not self.ctl.frontend_ok:
            messagebox.showinfo("SIGNAL LAB", "前端尚未就绪，请先点「全部启动」。")
            return
        webbrowser.open(self.ctl.web_url)

    def _copy_web(self) -> None:
        url = self.ctl.web_url
        self.root.clipboard_clear()
        self.root.clipboard_append(url)
        self.ctl.log(f"[已复制] {url}")
        self.hint.set(f"已复制本地网页：{url}")

    def _copy_api(self) -> None:
        url = self.ctl.api_url
        self.root.clipboard_clear()
        self.root.clipboard_append(url)
        self.ctl.log(f"[已复制] {url}")
        self.hint.set(f"已复制接口地址：{url}")

    def _copy_public(self) -> None:
        url = self.ctl.public_url
        if not url:
            messagebox.showinfo(
                "SIGNAL LAB",
                "还没有公网链接。\n请先点「开启公网」，等日志出现 trycloudflare.com 地址后再复制。",
            )
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(url)
        self.ctl.log(f"[已复制公网] {url}")
        self.hint.set(f"已复制公网链接：{url}")

    def _check_public(self) -> None:
        def run() -> None:
            msg = self.ctl.diagnose_public_dns()

            def done() -> None:
                self.hint.set(msg[:160] + ("…" if len(msg) > 160 else ""))
                if "DNS 污染" in msg or "无法解析" in msg or "尚无公网" in msg:
                    messagebox.showwarning("公网检测", msg)
                else:
                    messagebox.showinfo("公网检测", msg)

            self.root.after(0, done)

        threading.Thread(target=run, daemon=True).start()

    def _toggle_tunnel(self) -> None:
        if self.ctl.tunnel_ok:
            self._bg(self.ctl.stop_tunnel)
        else:
            self._bg(self.ctl.start_tunnel)

    def _close(self) -> None:
        if self.ctl.backend_ok or self.ctl.frontend_ok or self.ctl.tunnel_ok:
            if not messagebox.askokcancel("SIGNAL LAB", "确定停止全部服务并退出？"):
                return
        try:
            self.ctl.stop_all()
        finally:
            self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    App().run()


if __name__ == "__main__":
    main()
