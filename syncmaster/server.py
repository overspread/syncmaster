#!/usr/bin/env python3
"""SyncMaster - Hermes 配置同步 Web 管理后台"""

import os, sys, json, subprocess, threading, time, asyncio, queue, sqlite3, shutil
from pathlib import Path
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

# Jinja2 is required but may be missing from the system Python (e.g. inside a
# packaged Tauri .app). Self-install on first run so the app just works.
try:
    from jinja2 import Environment, FileSystemLoader, select_autoescape
except ImportError:
    # Self-install into the *user* site so it works even without write access
    # to the system Python (e.g. /usr/bin/python3 launched from a packaged
    # .app, where we can't write to /Library/.../site-packages).
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "--user", "--quiet", "jinja2"]
    )
    from jinja2 import Environment, FileSystemLoader, select_autoescape

# ── 配置 ──
HERMES_DIR = os.path.expanduser("~/.hermes")
SYNCMASTER_DIR = Path(__file__).parent
TEMPLATE_DIR = SYNCMASTER_DIR / "templates"
STATIC_DIR = SYNCMASTER_DIR / "static"
EXCLUDE_FILE = Path(HERMES_DIR) / "bin" / "sync-exclude.txt"
BACKUP_DIR = Path(os.path.expanduser("~/.syncmaster/backups"))
DB_PATH = os.path.expanduser("~/.syncmaster/db.sqlite")
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

# ── 数据库层 ──
class DB:
    def __init__(self):
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _conn(self):
        c = sqlite3.connect(DB_PATH, timeout=5)
        c.row_factory = sqlite3.Row
        return c

    def _init_db(self):
        with self._conn() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS categories (
                    id            TEXT PRIMARY KEY,
                    name          TEXT NOT NULL,
                    local_path    TEXT NOT NULL,
                    remote_path   TEXT DEFAULT '',
                    mode          TEXT DEFAULT 'bidirectional',
                    is_enabled    INTEGER DEFAULT 1,
                    last_sync     TEXT DEFAULT '-',
                    files         TEXT DEFAULT '[]',
                    delete_policy TEXT DEFAULT 'noDelete'
                );
                CREATE TABLE IF NOT EXISTS sync_history (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp   REAL NOT NULL,
                    time_str    TEXT NOT NULL,
                    category    TEXT NOT NULL,
                    direction   TEXT NOT NULL,
                    file_count  INTEGER DEFAULT 0,
                    total_size  TEXT DEFAULT '-',
                    duration    TEXT DEFAULT '',
                    result      TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS audit_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp   REAL NOT NULL,
                    time_str    TEXT NOT NULL,
                    action      TEXT NOT NULL,
                    detail      TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS config (
                    key   TEXT PRIMARY KEY,
                    value TEXT
                );
                CREATE TABLE IF NOT EXISTS env_keys (
                    key          TEXT PRIMARY KEY,
                    local_value  TEXT DEFAULT '',
                    remote_value TEXT DEFAULT '',
                    should_sync  INTEGER DEFAULT 1
                );
            """)

    # Categories
    def get_categories(self):
        with self._conn() as c:
            rows = c.execute("SELECT * FROM categories ORDER BY rowid").fetchall()
            result = []
            for r in rows:
                result.append({
                    "id": r["id"], "name": r["name"], "localPath": r["local_path"],
                    "remotePath": r["remote_path"], "mode": r["mode"],
                    "isEnabled": bool(r["is_enabled"]), "lastSync": r["last_sync"],
                    "files": json.loads(r["files"] or "[]"), "deletePolicy": r["delete_policy"],
                })
            if not result:
                for cat in self._default_categories():
                    self.save_category(cat)
                    result.append(cat)
            return result

    def save_category(self, cat):
        with self._conn() as c:
            c.execute("""INSERT INTO categories (id, name, local_path, remote_path, mode, is_enabled, last_sync, files, delete_policy)
                         VALUES (?,?,?,?,?,?,?,?,?)
                         ON CONFLICT(id) DO UPDATE SET
                           name=excluded.name, local_path=excluded.local_path,
                           remote_path=excluded.remote_path, mode=excluded.mode,
                           is_enabled=excluded.is_enabled, last_sync=excluded.last_sync,
                           files=excluded.files, delete_policy=excluded.delete_policy""",
                      (cat["id"], cat["name"], cat["localPath"], cat.get("remotePath",""),
                       cat.get("mode","bidirectional"), 1 if cat.get("isEnabled",True) else 0,
                       cat.get("lastSync","-"), json.dumps(cat.get("files",[])),
                       cat.get("deletePolicy","noDelete")))

    def delete_category(self, cid):
        with self._conn() as c:
            c.execute("DELETE FROM categories WHERE id=?", (cid,))

    def toggle_category(self, cid, enabled):
        with self._conn() as c:
            c.execute("UPDATE categories SET is_enabled=? WHERE id=?", (1 if enabled else 0, cid))

    def _default_categories(self):
        return [
            {"id":"core","name":"核心配置","localPath":"~/.hermes/config.yaml","remotePath":"/home/opc/.hermes","mode":"bidirectional","isEnabled":True,"lastSync":"-","files":[],"deletePolicy":"noDelete"},
            {"id":"skills","name":"技能库","localPath":"~/.hermes/skills","remotePath":"/home/opc/.hermes/skills","mode":"toServer","isEnabled":True,"lastSync":"-","files":[],"deletePolicy":"noDelete"},
            {"id":"workspace","name":"工作区","localPath":"~/.hermes","remotePath":"/home/opc/.hermes","mode":"bidirectional","isEnabled":True,"lastSync":"-","files":[],"deletePolicy":"noDelete"},
            {"id":"cron","name":"定时任务","localPath":"~/.hermes/cron","remotePath":"/home/opc/.hermes/cron","mode":"bidirectional","isEnabled":True,"lastSync":"-","files":[],"deletePolicy":"noDelete"},
            {"id":"plugins","name":"插件","localPath":"~/.hermes/plugins","remotePath":"/home/opc/.hermes/plugins","mode":"bidirectional","isEnabled":False,"lastSync":"-","files":[],"deletePolicy":"noDelete"},
        ]

    # History
    def get_history(self, filter_idx=0):
        now = time.time()
        sql = "SELECT time_str, timestamp, category, direction, file_count, total_size, duration, result FROM sync_history"
        if filter_idx == 1:
            start_of_day = datetime.now().replace(hour=0,minute=0,second=0).timestamp()
            sql += f" WHERE timestamp > {start_of_day}"
        elif filter_idx == 2:
            sql += f" WHERE timestamp > {now - 7*86400}"
        elif filter_idx == 3:
            sql += f" WHERE timestamp > {now - 30*86400}"
        sql += " ORDER BY timestamp DESC LIMIT 500"
        with self._conn() as c:
            rows = c.execute(sql).fetchall()
            return [{"time":r["time_str"],"timestamp":r["timestamp"],"category":r["category"],
                     "direction":r["direction"],"fileCount":r["file_count"],"totalSize":r["total_size"],
                     "duration":r["duration"],"result":r["result"]} for r in rows]

    def add_history(self, entry):
        with self._conn() as c:
            c.execute("""INSERT INTO sync_history (timestamp, time_str, category, direction, file_count, total_size, duration, result)
                         VALUES (?,?,?,?,?,?,?,?)""",
                      (entry.get("timestamp",time.time()), entry["time"], entry["category"],
                       entry["direction"], entry.get("fileCount",0), entry.get("totalSize","-"),
                       entry.get("duration",""), entry.get("result","")))

    def clear_history(self):
        with self._conn() as c:
            c.execute("DELETE FROM sync_history")

    # Audit
    def get_audit(self):
        with self._conn() as c:
            rows = c.execute("SELECT time_str, timestamp, action, detail FROM audit_log ORDER BY timestamp DESC LIMIT 500").fetchall()
            return [{"time":r["time_str"],"action":r["action"],"detail":r["detail"]} for r in rows]

    def add_audit(self, action, detail=""):
        ts = time.time()
        tstr = datetime.now().strftime("%m-%d %H:%M:%S")
        with self._conn() as c:
            c.execute("INSERT INTO audit_log (timestamp, time_str, action, detail) VALUES (?,?,?,?)",
                      (ts, tstr, action, detail))

    # Config
    def get_config(self):
        with self._conn() as c:
            rows = c.execute("SELECT key, value FROM config").fetchall()
            cfg = {}
            for r in rows:
                cfg[r["key"]] = r["value"]
            return cfg

    def save_config(self, cfg: dict):
        with self._conn() as c:
            for k, v in cfg.items():
                c.execute("INSERT INTO config (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                          (k, str(v)))

    # Backups
    def list_backups(self):
        backups = []
        if BACKUP_DIR.exists():
            for entry in sorted(BACKUP_DIR.iterdir(), reverse=True):
                if entry.is_dir():
                    size = sum(f.stat().st_size for f in entry.rglob("*") if f.is_file())
                    backups.append({
                        "path": str(entry),
                        "name": entry.name,
                        "size": f"{size/1024/1024:.1f} MB" if size > 0 else "0 MB",
                        "time": datetime.fromtimestamp(entry.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
                    })
        return backups

db = DB()

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
sync_stats = {"speed": "0 KB/s", "done": 0, "total": 0, "percent": 0, "status": "就绪", "eta": "--:--", "uploaded": 0, "downloaded": 0, "currentFile": ""}

def run_sync_impl(cfg: dict, direction: str):
    global sync_running, sync_process, sync_stats
    broadcast({"type": "started", "target": cfg["name"], "direction": direction})
    sync_stats = {"speed": "0 KB/s", "done": 0, "total": 0, "percent": 0, "status": "同步中...", "eta": "--:--", "uploaded": 0, "downloaded": 0, "currentFile": ""}
    try:
        pid = os.getpid()
        step_dir = f"/tmp/hermes-sync-{pid}"
        excl = ["--exclude-from=" + str(EXCLUDE_FILE)] if EXCLUDE_FILE.exists() else []

        subprocess.run(
            ["scp", "-i", cfg["jump_key"], "-o", "StrictHostKeyChecking=no", "-q",
             str(EXCLUDE_FILE), f"{cfg['jump_host']}:{step_dir}-exclude.txt"],
            capture_output=True, timeout=30
        )

        ssh_opt = f"ssh -i {cfg['jump_key']} -o StrictHostKeyChecking=no -o ServerAliveInterval=10"

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
                sync_stats["currentFile"] = line
        sync_process.wait(timeout=300)
        if sync_process.returncode != 0:
            raise Exception("第一步 (本地↔跳板机) 失败")

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
                sync_stats["currentFile"] = line
        sync_process.wait(timeout=300)
        if sync_process.returncode != 0:
            raise Exception("第二步 (跳板机↔服务器) 失败")

        subprocess.run(
            ["ssh", "-i", cfg["jump_key"], "-o", "StrictHostKeyChecking=no",
             cfg["jump_host"], f"rm -rf {step_dir} {step_dir}-exclude.txt"],
            capture_output=True, timeout=30
        )

        sync_stats["percent"] = 100
        sync_stats["status"] = "完成"
        broadcast({"type": "done", "success": True, "message": "同步完成"})
        db.add_audit("同步成功", f"{cfg['name']} {direction}")
        db.add_history({
            "time": datetime.now().strftime("%m-%d %H:%M:%S"),
            "timestamp": time.time(),
            "category": cfg["name"],
            "direction": "本地 → 服务器" if direction == "push" else "服务器 → 本地",
            "fileCount": sync_stats["done"],
            "totalSize": "-",
            "duration": "-",
            "result": "成功",
        })
    except Exception as e:
        sync_stats["status"] = f"失败: {e}"
        broadcast({"type": "done", "success": False, "message": str(e)})
        db.add_audit("同步失败", str(e))
        db.add_history({
            "time": datetime.now().strftime("%m-%d %H:%M:%S"),
            "timestamp": time.time(),
            "category": cfg["name"],
            "direction": "本地 → 服务器" if direction == "push" else "服务器 → 本地",
            "fileCount": 0,
            "totalSize": "-",
            "duration": "-",
            "result": "失败",
        })
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
    global sync_process, sync_running, sync_stats
    with sync_lock:
        if sync_process and sync_process.poll() is None:
            sync_process.terminate()
            sync_running = False
            sync_stats["status"] = "已取消"
            broadcast({"type": "done", "success": False, "message": "同步已取消"})
            return True, "同步已取消"
        return False, "没有正在运行的同步"


def get_local_info():
    try:
        path = HERMES_DIR
        total_files = 0
        total_size = 0
        if os.path.exists(path):
            for root, dirs, files in os.walk(path):
                for f in files:
                    fp = os.path.join(root, f)
                    try:
                        total_files += 1
                        total_size += os.path.getsize(fp)
                    except:
                        pass
        size_str = f"{total_size/1024/1024:.1f} MB" if total_size > 0 else "0 MB"
        return {"name": "MacBook Pro", "path": path, "isOnline": True,
                "totalFiles": total_files, "totalSize": size_str}
    except:
        return {"name": "MacBook Pro", "path": HERMES_DIR, "isOnline": True,
                "totalFiles": 0, "totalSize": "0 MB"}


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
        pass

    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

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

    def _render(self, template_name, path):
        html = jinja_env.get_template(template_name).render(request_path=path)
        self._send_html(html)

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        params = parse_qs(parsed.query)

        # ── 页面路由 ──
        if path == "/":
            self._render("index.html", path)
        elif path == "/settings":
            self._render("settings.html", path)
        elif path == "/categories":
            self._render("categories.html", path)
        elif path == "/history":
            self._render("history.html", path)
        elif path == "/backup":
            self._render("backup.html", path)
        elif path == "/audit":
            self._render("audit.html", path)
        elif path == "/monitor":
            self._render("monitor.html", path)

        # ── 静态文件 ──
        elif path.startswith("/static/"):
            self._send_static(path[len("/static/"):])

        # ── API ──
        elif path == "/api/stats":
            stats = {"pending": 0, "running": sync_running}
            self._send_json(stats)

        elif path == "/api/categories":
            self._send_json(db.get_categories())

        elif path == "/api/history":
            filter_idx = int(params.get("filter", ["0"])[0])
            self._send_json(db.get_history(filter_idx))

        elif path == "/api/audit":
            self._send_json(db.get_audit())

        elif path == "/api/backups":
            self._send_json(db.list_backups())

        elif path == "/api/monitor":
            self._send_json(sync_stats)

        elif path == "/api/local-info":
            self._send_json(get_local_info())

        elif path == "/api/config":
            self._send_json(db.get_config())

        # ── SSE ──
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

        # ── 同步操作 ──
        if path == "/sync":
            target = params.get("target", ["OCI 187"])[0]
            direction = params.get("dir", ["push"])[0]
            ok, msg = start_sync(target, direction)
            self._send_json({"ok": ok, "message": msg})

        elif path == "/cancel":
            ok, msg = cancel_sync()
            self._send_json({"ok": ok, "message": msg})

        # ── 分类操作 ──
        elif path == "/api/category/toggle":
            cid = params.get("id", [""])[0]
            enabled = params.get("enabled", ["1"])[0] == "1"
            db.toggle_category(cid, enabled)
            db.add_audit("切换分类", f"{cid} -> {'启用' if enabled else '禁用'}")
            self._send_json({"ok": True})

        elif path == "/api/category/delete":
            cid = params.get("id", [""])[0]
            db.delete_category(cid)
            db.add_audit("删除分类", cid)
            self._send_json({"ok": True})

        elif path == "/api/category/save":
            body = self._read_body()
            cat = json.loads(body)
            if not cat.get("id"):
                import uuid
                cat["id"] = uuid.uuid4().hex[:8]
            db.save_category(cat)
            db.add_audit("编辑分类" if cat.get("_edit") else "创建分类", cat["name"])
            self._send_json({"ok": True, "id": cat["id"]})

        # ── 历史操作 ──
        elif path == "/api/history/clear":
            db.clear_history()
            db.add_audit("清空历史", "")
            self._send_json({"ok": True})

        # ── 备份操作 ──
        elif path == "/api/backup/rollback":
            bpath = params.get("path", [""])[0]
            target = params.get("target", [HERMES_DIR])[0]
            try:
                if os.path.isdir(bpath) and os.path.isdir(target):
                    shutil.copytree(bpath, target, dirs_exist_ok=True)
                    db.add_audit("回滚", os.path.basename(bpath))
                    self._send_json({"ok": True, "message": "回滚成功"})
                else:
                    self._send_json({"ok": False, "message": "路径不存在"})
            except Exception as e:
                self._send_json({"ok": False, "message": str(e)})

        elif path == "/api/backup/delete":
            bpath = params.get("path", [""])[0]
            try:
                if os.path.isdir(bpath):
                    shutil.rmtree(bpath)
                    db.add_audit("删除备份", os.path.basename(bpath))
                    self._send_json({"ok": True})
                else:
                    self._send_json({"ok": False, "message": "备份不存在"})
            except Exception as e:
                self._send_json({"ok": False, "message": str(e)})

        # ── 配置保存 ──
        elif path == "/api/config/save":
            body = self._read_body()
            cfg = json.loads(body)
            db.save_config(cfg)
            db.add_audit("配置修改", "更新服务器/路径配置")
            self._send_json({"ok": True})

        else:
            self._send_json({"error": "Not found"}, 404)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length:
            return self.rfile.read(length).decode()
        return "{}"


def main():
    # ThreadingHTTPServer avoids blocking the WebView's keep-alive connections
    # (and the SSE log stream) when other requests arrive concurrently.
    server = ThreadingHTTPServer(("127.0.0.1", PORT), SyncHandler)
    print(f"SyncMaster 已启动 -> http://127.0.0.1:{PORT}")
    print(f"按 Ctrl+C 停止")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止...")
        server.shutdown()


if __name__ == "__main__":
    main()
