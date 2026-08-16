#!/usr/bin/env python3
"""SyncMaster - Hermes 配置同步 Web 管理后台"""

import os, sys, json, subprocess, threading, time, asyncio, queue
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from jinja2 import Environment, FileSystemLoader, select_autoescape

# ── 配置 ──
HERMES_DIR = os.path.expanduser("~/.hermes")
SYNCMASTER_DIR = Path(__file__).parent
TEMPLATE_DIR = SYNCMASTER_DIR / "templates"
STATIC_DIR = SYNCMASTER_DIR / "static"
EXCLUDE_FILE = Path(HERMES_DIR) / "bin" / "sync-exclude.txt"
PORT = int(os.environ.get("SYNCMASTER_PORT", 9800))

CONFIGS = [
    {
        "name": "OCI 187",
        "jump_host": "ubuntu@54.160.252.171",
        "jump_key": os.path.expanduser("~/.hermes/../Documents/2api.pem"),
        "remote_user": "opc",
        "remote_host": "155.248.172.187",
        "remote_key": "/home/ubuntu/oci_opc_key.pem",
        "remote_dir": "/home/opc/.hermes",
    },
]

# ── SSE 事件队列 ──
sse_clients: list[queue.Queue] = []
sse_lock = threading.Lock()

def broadcast(event: dict):
    with sse_lock:
        dead = []
        for q in sse_clients:
            try:
                q.put_nowait(event)
            except queue.Full:
                dead.append(q)
        for q in dead:
            sse_clients.remove(q)

# ── 同步引擎 ──
sync_running = False
sync_process = None
sync_lock = threading.Lock()

def run_sync_impl(cfg: dict, direction: str):
    global sync_running, sync_process
    broadcast({"type": "started", "target": cfg["name"], "direction": direction})
    try:
        pid = os.getpid()
        step_dir = f"/tmp/hermes-sync-{pid}"
        excl = ["--exclude-from=" + str(EXCLUDE_FILE)] if EXCLUDE_FILE.exists() else []

        # 上传排除文件到跳板机
        subprocess.run(
            ["scp", "-i", cfg["jump_key"], "-o", "StrictHostKeyChecking=no", "-q",
             str(EXCLUDE_FILE), f"{cfg['jump_host']}:{step_dir}-exclude.txt"],
            capture_output=True, timeout=30
        )

        ssh_opt = f"ssh -i {cfg['jump_key']} -o StrictHostKeyChecking=no -o ServerAliveInterval=10"

        # Step 1: local ↔ jump
        rsync_cmd = ["rsync", "-avz", "--delete"] + excl + \
                    ["-e", ssh_opt]
        if direction == "push":
            rsync_cmd += [f"{HERMES_DIR}/", f"{cfg['jump_host']}:{step_dir}/"]
        else:
            rsync_cmd += [f"{cfg['jump_host']}:{step_dir}/", f"{HERMES_DIR}/"]

        sync_process = subprocess.Popen(rsync_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        for line in iter(sync_process.stdout.readline, ""):
            line = line.strip()
            if line:
                broadcast({"type": "log", "text": f"[1/2] {line}"})
        sync_process.wait(timeout=300)
        if sync_process.returncode != 0:
            raise Exception("第一步 (本地↔跳板机) 失败")

        # Step 2: jump ↔ remote
        remote_ssh = f"ssh -i {cfg['remote_key']} -o StrictHostKeyChecking=no -o ServerAliveInterval=10"
        if direction == "push":
            cmd = f"rsync -avz --delete --exclude-from='{step_dir}-exclude.txt' -e '{remote_ssh}' {step_dir}/ {cfg['remote_user']}@{cfg['remote_host']}:{cfg['remote_dir']}/"
        else:
            cmd = f"rsync -avz --delete --exclude-from='{step_dir}-exclude.txt' -e '{remote_ssh}' {cfg['remote_user']}@{cfg['remote_host']}:{cfg['remote_dir']}/ {step_dir}/"

        sync_process = subprocess.Popen(
            ["ssh", "-i", cfg["jump_key"], "-o", "StrictHostKeyChecking=no",
             "-o", "ServerAliveInterval=10", cfg["jump_host"], cmd],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
        )
        for line in iter(sync_process.stdout.readline, ""):
            line = line.strip()
            if line:
                broadcast({"type": "log", "text": f"[2/2] {line}"})
        sync_process.wait(timeout=300)
        if sync_process.returncode != 0:
            raise Exception("第二步 (跳板机↔服务器) 失败")

        # 清理跳板机上的临时文件
        subprocess.run(
            ["ssh", "-i", cfg["jump_key"], "-o", "StrictHostKeyChecking=no",
             cfg["jump_host"], f"rm -rf {step_dir} {step_dir}-exclude.txt"],
            capture_output=True, timeout=30
        )

        broadcast({"type": "done", "success": True, "message": "同步完成"})
    except Exception as e:
        broadcast({"type": "done", "success": False, "message": str(e)})
    finally:
        with sync_lock:
            sync_running = False
            sync_process = None


def start_sync(target_name: str, direction: str):
    global sync_running
    with sync_lock:
        if sync_running:
            return False, "同步已在运行中"
        sync_running = True
    cfg = next((c for c in CONFIGS if c["name"] == target_name), None)
    if not cfg:
        with sync_lock:
            sync_running = False
        return False, f"未找到目标: {target_name}"
    t = threading.Thread(target=run_sync_impl, args=(cfg, direction), daemon=True)
    t.start()
    return True, "同步已启动"


def cancel_sync():
    global sync_process, sync_running
    with sync_lock:
        if sync_process and sync_process.poll() is None:
            sync_process.terminate()
            sync_running = False
            broadcast({"type": "done", "success": False, "message": "同步已取消"})
            return True, "同步已取消"
        return False, "没有正在运行的同步"


# ── HTTP 服务器 ──
jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)

MIME_TYPES = {
    ".css": "text/css",
    ".js": "application/javascript",
    ".html": "text/html",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


class SyncHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # 静默日志

    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _send_html(self, html, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode())

    def _send_static(self, path):
        full_path = STATIC_DIR / path
        if not full_path.exists() or not full_path.is_file():
            self._send_json({"error": "Not found"}, 404)
            return
        ext = full_path.suffix
        ctype = MIME_TYPES.get(ext, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "max-age=3600")
        self.end_headers()
        self.wfile.write(full_path.read_bytes())

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        params = parse_qs(parsed.query)

        if path == "/":
            html = jinja_env.get_template("index.html").render()
            self._send_html(html)

        elif path.startswith("/static/"):
            self._send_static(path[len("/static/"):])

        elif path == "/api/stats":
            stats = {"pending": 0, "running": sync_running}
            self._send_json(stats)

        elif path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            q: queue.Queue = queue.Queue(maxsize=100)
            with sse_lock:
                sse_clients.append(q)
            try:
                while True:
                    try:
                        event = q.get(timeout=30)
                        data = json.dumps(event, ensure_ascii=False)
                        self.wfile.write(f"data: {data}\n\n".encode())
                        self.wfile.flush()
                    except queue.Empty:
                        self.wfile.write(": heartbeat\n\n".encode())
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                with sse_lock:
                    if q in sse_clients:
                        sse_clients.remove(q)

        else:
            self._send_json({"error": "Not found"}, 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        params = parse_qs(parsed.query)

        if path == "/sync":
            target = params.get("target", ["OCI 187"])[0]
            direction = params.get("dir", ["push"])[0]
            ok, msg = start_sync(target, direction)
            self._send_json({"ok": ok, "message": msg})

        elif path == "/cancel":
            ok, msg = cancel_sync()
            self._send_json({"ok": ok, "message": msg})

        else:
            self._send_json({"error": "Not found"}, 404)


def main():
    server = HTTPServer(("127.0.0.1", PORT), SyncHandler)
    print(f"SyncMaster 已启动 → http://127.0.0.1:{PORT}")
    print(f"按 Ctrl+C 停止")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止...")
        server.shutdown()


if __name__ == "__main__":
    main()