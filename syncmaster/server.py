#!/usr/bin/env python3
"""SyncMaster - Hermes 配置同步 Web 管理后台"""

import os, sys, json, subprocess, threading, time, secrets, queue, sqlite3, shutil, shlex, socket, re
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

# 本地 API 鉴权 token（进程启动时生成，仅同机可访问）
API_TOKEN = secrets.token_hex(16)

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
        h = os.path.expanduser
        return [
            {"id":"core","name":"核心配置","localPath":h("~/.hermes/config.yaml"),"remotePath":"/home/opc/.hermes","mode":"bidirectional","isEnabled":True,"lastSync":"-","files":[],"deletePolicy":"noDelete"},
            {"id":"skills","name":"技能库","localPath":h("~/.hermes/skills"),"remotePath":"/home/opc/.hermes/skills","mode":"toServer","isEnabled":True,"lastSync":"-","files":[],"deletePolicy":"noDelete"},
            {"id":"workspace","name":"工作区","localPath":h("~/.hermes"),"remotePath":"/home/opc/.hermes","mode":"bidirectional","isEnabled":True,"lastSync":"-","files":[],"deletePolicy":"noDelete"},
            {"id":"cron","name":"定时任务","localPath":h("~/.hermes/cron"),"remotePath":"/home/opc/.hermes/cron","mode":"bidirectional","isEnabled":True,"lastSync":"-","files":[],"deletePolicy":"noDelete"},
            {"id":"plugins","name":"插件","localPath":h("~/.hermes/plugins"),"remotePath":"/home/opc/.hermes/plugins","mode":"bidirectional","isEnabled":False,"lastSync":"-","files":[],"deletePolicy":"noDelete"},
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
sync_cancelled = False
sync_lock = threading.Lock()
sync_stats = {"speed": "0 KB/s", "done": 0, "total": 0, "percent": 0, "status": "就绪", "eta": "--:--", "uploaded": 0, "downloaded": 0, "currentFile": ""}
auto_sync_enabled = False
auto_sync_reason = ""
auto_sync_direction = "bidirectional"   # "push" | "pull" | "bidirectional"

def set_auto_sync(enabled: bool, direction: str = None):
    """开启/关闭自动持续同步。返回 (ok, msg)。"""
    global auto_sync_enabled, auto_sync_direction
    auto_sync_enabled = bool(enabled)
    if direction and direction in ("push", "pull", "bidirectional"):
        auto_sync_direction = direction
    if auto_sync_enabled:
        auto_engine.start()
    else:
        auto_engine.stop()
    db.save_config({"autoSync": "1" if auto_sync_enabled else "0",
                    "autoSyncDirection": auto_sync_direction})
    db.add_audit("自动同步", "开启" if auto_sync_enabled else "关闭")
    broadcast({"type": "autosync", "enabled": auto_sync_enabled, "direction": auto_sync_direction})
    return True, "自动同步已" + ("开启" if auto_sync_enabled else "关闭")


def set_auto_sync_direction(direction: str):
    """切换自动同步方向。返回 (ok, msg)。"""
    global auto_sync_direction
    if direction not in ("push", "pull", "bidirectional"):
        return False, "无效的同步方向"
    auto_sync_direction = direction
    db.save_config({"autoSyncDirection": direction})
    db.add_audit("自动同步方向", {"push": "本地 → 远程", "pull": "远程 → 本地",
                                  "bidirectional": "双向同步"}[direction])
    broadcast({"type": "autosync", "enabled": auto_sync_enabled, "direction": auto_sync_direction})
    return True, "自动同步方向已更新"


# ── 自动持续同步引擎 ──
# 理念借鉴 Syncthing：文件变化自动同步，无需手动操作。
# 用 macOS FSEvents 实时监听本地目录；变化经 debounce 后自动触发双向同步。
class AutoSyncEngine:
    def __init__(self):
        self._enabled = False
        self._thread = None
        self._stop_event = threading.Event()
        self._debounce_timer = None
        self._debounce_lock = threading.Lock()
        self._last_paths = set()
        self._retry_after = 15.0          # 失败后静默重试间隔（秒）
        self._min_interval = 10.0         # 两次自动同步最小间隔，防止风暴
        self._last_sync_at = 0.0
        self._mode = "poll"               # "fsevents" | "poll"

    def start(self):
        self._enabled = True
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        db.add_audit("自动同步", "监听已启动")

    def stop(self):
        self._enabled = False
        self._stop_event.set()
        if self._debounce_timer:
            with self._debounce_lock:
                if self._debounce_timer:
                    self._debounce_timer.cancel()
                    self._debounce_timer = None

    def notify(self, paths):
        """文件系统变化回调：debounce 后触发同步。"""
        if not self._enabled:
            return
        # 同步进行中：变化是 rsync/备份产生的，丢弃（同步完成后 rsync 已处理）
        if sync_running:
            return
        # 过滤掉同步过程自身产生的变化（备份目录等）
        keep = set()
        for p in paths:
            if isinstance(p, str) and (BACKUP_DIR in Path(p).parents or "backups" in p):
                continue
            keep.add(p)
        if not keep:
            return
        with self._debounce_lock:
            self._last_paths |= keep
            if self._debounce_timer:
                self._debounce_timer.cancel()
            # 5 秒 debounce：等待文件写入稳定
            self._debounce_timer = threading.Timer(5.0, self._fire_sync)
            self._debounce_timer.daemon = True
            self._debounce_timer.start()

    def _fire_sync(self):
        with self._debounce_lock:
            self._debounce_timer = None
        if not self._enabled:
            return
        now = time.time()
        if now - self._last_sync_at < self._min_interval:
            return
        self._last_sync_at = now
        # 后台线程执行，不阻塞监听
        threading.Thread(target=self._do_auto_sync, daemon=True).start()

    def _do_auto_sync(self):
        global auto_sync_reason, auto_sync_direction
        if not self._enabled:
            return
        try:
            ok, msg = start_sync("", auto_sync_direction)
            if not ok and "运行中" in msg:
                return  # 手动同步正在跑，跳过
            if not ok:
                # 失败：静默重试（下一次文件变化或定时器都会再试）
                auto_sync_reason = msg
                self._schedule_retry()
        except Exception as e:
            auto_sync_reason = str(e)
            self._schedule_retry()

    def _schedule_retry(self):
        if not self._enabled:
            return
        timer = threading.Timer(self._retry_after, self._fire_sync)
        timer.daemon = True
        with self._debounce_lock:
            self._debounce_timer = timer
        timer.start()

    def _run(self):
        """监听主循环：使用轮询快照对比（跨平台、稳定可靠）。"""
        self._mode = "poll"
        self._run_poll()

    # ── FSEvents 实现（macOS） ──
    # ── 轮询实现（跨平台回退） ──
    def _snapshot(self):
        """返回 {path: (mtime_ns, size)} 快照，用于检测变化。"""
        snap = {}
        try:
            for root, dirs, files in os.walk(HERMES_DIR):
                if "backups" in root or ".rsync-partial" in root:
                    continue
                for f in files:
                    fp = os.path.join(root, f)
                    try:
                        st = os.stat(fp)
                        snap[fp] = (st.st_mtime_ns, st.st_size)
                    except Exception:
                        pass
        except Exception:
            pass
        return snap

    def _run_poll(self):
        last = self._snapshot()
        while not self._stop_event.is_set():
            time.sleep(2.0)
            if not self._enabled:
                continue
            cur = self._snapshot()
            changed = [p for p in cur if p not in last or cur[p] != last.get(p)]
            # 也检测被删除的文件
            deleted = set(last) - set(cur)
            if changed or deleted:
                self.notify(list(changed) + list(deleted))
            last = cur

auto_engine = AutoSyncEngine()

def _resolve_sync_cfg():
    c = db.get_config()
    return {
        "name": "OCI 187",
        "jump_host": c.get("jumpHost", "ubuntu@54.160.252.171"),
        "jump_key": os.path.expanduser(c.get("jumpKey", "~/Documents/2api.pem")),
        "remote_user": c.get("remoteUser", "opc"),
        "remote_host": c.get("remoteHost", "155.248.172.187"),
        "remote_key": os.path.expanduser(c.get("remoteKey", "~/Documents/oci_opc_key.pem")),
        "remote_dir": c.get("remoteDir", "/home/opc/.hermes"),
        "local_dir": os.path.expanduser(c.get("localDir", "~/.hermes")),
    }

def _create_backup(local_dir):
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"sync_{ts}"
        backup_path = BACKUP_DIR / backup_name
        shutil.copytree(local_dir, backup_path, dirs_exist_ok=True)
        return backup_path
    except Exception:
        return None

def _parse_rsync_progress(line):
    count = 0
    speed = ""
    pct = 0
    m = re.match(r'^(\d+)%', line)
    if m:
        pct = int(m.group(1))
    m = re.search(r'(\d+)% of .* at\s+([\d.]+\s*\w+/s)', line)
    if m:
        pct = int(m.group(1))
        speed = m.group(2)
    m = re.search(r'xfer#(\d+)', line)
    if m:
        count = int(m.group(1))
    return count, speed, pct

def run_sync_impl(cfg: dict, direction: str):
    global sync_running, sync_process, sync_stats, sync_cancelled
    broadcast({"type": "started", "target": cfg["name"], "direction": direction})
    sync_stats = {"speed": "0 KB/s", "done": 0, "total": 0, "percent": 0, "status": "同步中...", "eta": "--:--", "uploaded": 0, "downloaded": 0, "currentFile": ""}
    try:
        if not os.path.isfile(cfg["jump_key"]):
            raise FileNotFoundError(f"跳板机本地密钥不存在: {cfg['jump_key']}")
        if not os.path.isfile(cfg["remote_key"]):
            raise FileNotFoundError(f"服务器本地密钥不存在: {cfg['remote_key']}")

        local_dir = cfg.get("local_dir", HERMES_DIR)

        # M15: 同步前创建本地备份
        backup = _create_backup(local_dir)
        if backup:
            broadcast({"type": "log", "text": f"已创建备份快照: {backup.name}"})

        import tempfile
        jump_host = cfg["jump_host"]
        jump_user = jump_host.split("@")[0] if "@" in jump_host else ""
        jump_hostname = jump_host.split("@")[-1] if "@" in jump_host else jump_host
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ssh_config", delete=False) as tf:
            tf.write("Host jump\n")
            tf.write(f"    HostName {jump_hostname}\n")
            tf.write(f"    User {jump_user}\n")
            tf.write(f"    IdentityFile {cfg['jump_key']}\n")
            tf.write("    StrictHostKeyChecking accept-new\n")
            tf.write("    BatchMode yes\n")
            tf.write("\nHost remote\n")
            tf.write(f"    HostName {cfg['remote_host']}\n")
            tf.write(f"    User {cfg['remote_user']}\n")
            tf.write(f"    IdentityFile {cfg['remote_key']}\n")
            tf.write("    ProxyJump jump\n")
            tf.write("    StrictHostKeyChecking accept-new\n")
            tf.write("    BatchMode yes\n")
            tf.write("    ServerAliveInterval 10\n")
            ssh_config_path = tf.name
        os.chmod(ssh_config_path, 0o600)

        try:
            remote_ssh = f"ssh -F {ssh_config_path}"
            excl = ["--exclude-from=" + str(EXCLUDE_FILE)] if EXCLUDE_FILE.exists() else []
            remote = f"remote:{cfg['remote_dir'].rstrip('/')}/"
            base_cmd = ["rsync", "-avz", "--partial", "--partial-dir=.rsync-partial",
                        "--timeout=300", "--delete", "--progress"] + excl + ["-e", remote_ssh]

            # M7: bidirectional = push then pull
            stages = []
            if direction == "bidirectional":
                stages = [("push", base_cmd + [f"{local_dir}/", remote]),
                          ("pull", base_cmd + [remote, f"{local_dir}/"])]
            elif direction == "push":
                stages = [("push", base_cmd + [f"{local_dir}/", remote])]
            else:
                stages = [("pull", base_cmd + [remote, f"{local_dir}/"])]

            def _run_rsync(cmd, label):
                global sync_process
                broadcast({"type": "log", "text": f"[{label}] 开始同步..."})
                sync_process = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                )
                for line in iter(sync_process.stdout.readline, ""):
                    line = line.strip()
                    if line:
                        broadcast({"type": "log", "text": f"[{label}] {line}"})
                        sync_stats["currentFile"] = line
                        # M9: 解析 rsync 进度
                        count, speed, pct = _parse_rsync_progress(line)
                        if count: sync_stats["done"] = count
                        if speed: sync_stats["speed"] = speed
                        if pct: sync_stats["percent"] = pct
                return sync_process.wait()

            for i, (stage_label, stage_cmd) in enumerate(stages):
                label = f"{i+1}/{len(stages)}"
                rc = _run_rsync(stage_cmd, label)
                if rc != 0 and not sync_cancelled:
                    broadcast({"type": "log", "text": f"[{label}] 首次失败，10 秒后自动重试（支持断点续传）..."})
                    time.sleep(10)
                    if not sync_cancelled:
                        rc = _run_rsync(stage_cmd, f"{label}重试")
                        if rc != 0:
                            raise Exception(f"rsync 同步失败（[{label}] exit={rc}）")
        finally:
            try:
                os.unlink(ssh_config_path)
            except Exception:
                pass

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
        if not sync_cancelled:
            global auto_sync_reason
            if auto_sync_enabled:
                auto_sync_reason = str(e)
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
        # 已取消时 cancel_sync 已处理状态/广播/审计，这里不再重复
    finally:
        with sync_lock:
            sync_running = False
            sync_process = None
            sync_cancelled = False


def start_sync(target_name: str, direction: str):
    global sync_running
    with sync_lock:
        if sync_running:
            return False, "同步已在运行中"
        sync_running = True
        sync_cancelled = False
    cfg = _resolve_sync_cfg()
    # M8: 如果指定了分类，用分类的路径覆盖
    if target_name:
        cats = db.get_categories()
        cat = next((c for c in cats if c["id"] == target_name or c["name"] == target_name), None)
        if cat:
            cfg["name"] = cat["name"]
            if cat.get("localPath"):
                cfg["local_dir"] = os.path.expanduser(cat["localPath"])
            if cat.get("remotePath"):
                cfg["remote_dir"] = cat["remotePath"]
            mode = cat.get("mode", "")
            if mode == "toServer" and direction == "bidirectional":
                direction = "push"
            elif mode == "toLocal" and direction == "bidirectional":
                direction = "pull"
    t = threading.Thread(target=run_sync_impl, args=(cfg, direction), daemon=True)
    t.start()
    return True, "同步已启动"


def cancel_sync():
    global sync_process, sync_running, sync_stats, sync_cancelled
    with sync_lock:
        if not sync_running:
            return False, "没有正在运行的同步"
        sync_cancelled = True
        if sync_process and sync_process.poll() is None:
            sync_process.terminate()
        sync_running = False
        sync_stats["status"] = "已取消"
        broadcast({"type": "done", "success": False, "message": "同步已取消"})
        return True, "同步已取消"


def get_sync_diff():
    """对比本地与远程，返回待上传/待下载/一致的文件统计。同步中或未配置时返回空统计。"""
    result = {"pending": 0, "toUpload": 0, "toDownload": 0, "inSync": 0, "ok": False, "msg": ""}
    if sync_running:
        result["msg"] = "同步进行中"
        return result
    try:
        cfg = _resolve_sync_cfg()
        local_dir = cfg.get("local_dir", HERMES_DIR)
        if not os.path.isdir(local_dir) or not os.path.isfile(cfg["jump_key"]) or not os.path.isfile(cfg["remote_key"]):
            result["msg"] = "配置不完整"
            return result

        import tempfile
        jump_host = cfg["jump_host"]
        jump_user = jump_host.split("@")[0] if "@" in jump_host else ""
        jump_hostname = jump_host.split("@")[-1] if "@" in jump_host else jump_host
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ssh_config", delete=False) as tf:
            tf.write("Host jump\n")
            tf.write(f"    HostName {jump_hostname}\n")
            tf.write(f"    User {jump_user}\n")
            tf.write(f"    IdentityFile {cfg['jump_key']}\n")
            tf.write("    StrictHostKeyChecking accept-new\n")
            tf.write("    BatchMode yes\n")
            tf.write("    ConnectTimeout 10\n")
            tf.write("\nHost remote\n")
            tf.write(f"    HostName {cfg['remote_host']}\n")
            tf.write(f"    User {cfg['remote_user']}\n")
            tf.write(f"    IdentityFile {cfg['remote_key']}\n")
            tf.write("    ProxyJump jump\n")
            tf.write("    StrictHostKeyChecking accept-new\n")
            tf.write("    ConnectTimeout 10\n")
            ssh_config_path = tf.name
        try:
            os.chmod(ssh_config_path, 0o600)
            remote_ssh = f"ssh -F {ssh_config_path}"
            excl = ["--exclude-from=" + str(EXCLUDE_FILE)] if EXCLUDE_FILE.exists() else []
            remote = f"remote:{cfg['remote_dir'].rstrip('/')}/"
            base = ["rsync", "-avz", "--partial", "--dry-run", "--timeout=15",
                    "--delete", "--progress"] + excl + ["-e", remote_ssh]

            def _dry_run(cmd):
                try:
                    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                         text=True, bufsize=1)
                    out, _ = p.communicate(timeout=25)
                    count = 0
                    for line in out.splitlines():
                        line = line.strip()
                        # 跳过目录结尾/进度行/汇总行
                        if not line or line.endswith("/") or "/" in line:
                            continue
                        if line in ("sending incremental file list", "building file list",
                                    "sent", "total", "created directory",
                                    "delta-transmission", "receiving incremental file list") or line.endswith("bytes/sec") or "files to consider" in line:
                            continue
                        if any(line.startswith(p) for p in ("sent ", "total ", "Number of", "total size is")):
                            continue
                        if line[0].isdigit() and "%" in line:  # 进度行
                            continue
                        count += 1
                    return count, p.returncode
                except Exception:
                    return 0, -1

            up_count, up_rc = _dry_run(base + [f"{local_dir}/", remote])
            down_count, down_rc = _dry_run(base + [remote, f"{local_dir}/"])
            if up_rc != 0 and down_rc != 0:
                result["msg"] = "无法连接远程服务器"
                return result
            result["toUpload"] = up_count if up_rc == 0 else 0
            result["toDownload"] = down_count if down_rc == 0 else 0
            result["pending"] = result["toUpload"] + result["toDownload"]
            result["ok"] = True
            return result
        finally:
            try:
                os.unlink(ssh_config_path)
            except Exception:
                pass
    except Exception as e:
        result["msg"] = str(e)
        return result


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
        return {"name": socket.gethostname(), "path": path, "isOnline": True,
                "totalFiles": total_files, "totalSize": size_str}
    except:
        return {"name": socket.gethostname(), "path": HERMES_DIR, "isOnline": True,
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
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def _check_auth(self):
        token = None
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
        if not token:
            cookie = self.headers.get("Cookie", "")
            for part in cookie.split(";"):
                part = part.strip()
                if part.startswith("sm_token="):
                    token = part.split("=", 1)[1]
                    break
        return token == API_TOKEN

    def _send_html(self, html, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
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
        # 注入 API token，让前端 fetch 自动携带
        inject = f'<script>window.SM_TOKEN="{API_TOKEN}";' \
                  f'window.fetch2=function(u,o){{o=o||{{}};o.headers=o.headers||{{}};' \
                  f'o.headers["Authorization"]="Bearer "+window.SM_TOKEN;return fetch(u,o);}};' \
                  f'window.EventSource2=function(u){{return new EventSource(u+"?token="+window.SM_TOKEN);}};' \
                  f'</script>'
        html = html.replace("</head>", inject + "</head>", 1)
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
            stats = {"pending": 0, "running": sync_running, "autosync": auto_sync_enabled}
            self._send_json(stats)

        elif path == "/api/diff":
            self._send_json(get_sync_diff())

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

        elif path == "/api/autosync":
            self._send_json({
                "enabled": auto_sync_enabled,
                "mode": auto_engine._mode,
                "reason": auto_sync_reason,
                "lastSyncAt": auto_engine._last_sync_at,
                "direction": auto_sync_direction,
            })

        elif path == "/api/local-info":
            self._send_json(get_local_info())

        elif path == "/api/config":
            self._send_json(db.get_config())

        # ── SSE ──
        elif path == "/api/syncignore":
            rules = []
            if EXCLUDE_FILE.exists():
                rules = [l for l in EXCLUDE_FILE.read_text().splitlines() if l.strip()]
            self._send_json({"ok": True, "rules": rules})

        elif path == "/events":
            token = params.get("token", [""])[0]
            if token != API_TOKEN:
                self._send_json({"error": "Unauthorized"}, 403)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
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

        elif path == "/api/autosync/toggle":
            enabled = params.get("enabled", ["0"])[0] == "1"
            direction = params.get("direction", [None])[0]
            ok, msg = set_auto_sync(enabled, direction)
            self._send_json({"ok": ok, "message": msg, "direction": auto_sync_direction})

        elif path == "/api/autosync/direction":
            direction = params.get("direction", [""])[0]
            ok, msg = set_auto_sync_direction(direction)
            self._send_json({"ok": ok, "message": msg, "direction": auto_sync_direction})

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
            try:
                body = self._read_body()
                cat = json.loads(body)
            except Exception:
                self._send_json({"ok": False, "message": "请求体不是合法 JSON"}, 400)
                return
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
            backup_root = str(BACKUP_DIR.resolve())
            if not os.path.realpath(bpath).startswith(backup_root):
                self._send_json({"ok": False, "message": "非法路径：备份路径不在允许范围内"})
                return
            if not os.path.realpath(target).startswith(os.path.expanduser("~")):
                self._send_json({"ok": False, "message": "非法路径：目标路径不在用户目录下"})
                return
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
            backup_root = str(BACKUP_DIR.resolve())
            if not os.path.realpath(bpath).startswith(backup_root):
                self._send_json({"ok": False, "message": "非法路径：备份路径不在允许范围内"})
                return
            try:
                if os.path.isdir(bpath):
                    shutil.rmtree(bpath)
                    db.add_audit("删除备份", os.path.basename(bpath))
                    self._send_json({"ok": True})
                else:
                    self._send_json({"ok": False, "message": "备份不存在"})
            except Exception as e:
                self._send_json({"ok": False, "message": str(e)})

        # ── 本地路径选择：由 macOS 原生面板完成，不依赖 WebView/Tauri 前端桥接 ──
        elif path == "/api/pick_path":
            try:
                raw = self._read_body()
                request = json.loads(raw) if raw and raw.strip() else {}
            except Exception:
                self._send_json({"ok": False, "message": "请求体不是合法 JSON"}, 400)
                return

            kind = request.get("kind", "file")
            if kind not in ("file", "directory"):
                self._send_json({"ok": False, "message": "路径类型无效"}, 400)
                return
            if sys.platform != "darwin":
                self._send_json({"ok": False, "message": "当前系统暂不支持原生路径选择"}, 501)
                return

            # 使用系统默认标题，避免在 Finder 选择器中显示额外的业务提示；
            # 用户取消会返回 -128，属于正常取消而不是错误。
            script = (
                'POSIX path of (choose folder)'
                if kind == "directory"
                else 'POSIX path of (choose file)'
            )
            try:
                result = subprocess.run(
                    ["/usr/bin/osascript", "-e", script],
                    capture_output=True, text=True, timeout=300,
                )
            except subprocess.TimeoutExpired:
                self._send_json({"ok": False, "message": "选择窗口等待超时"})
                return
            except FileNotFoundError:
                self._send_json({"ok": False, "message": "未找到 macOS osascript"})
                return

            if result.returncode == 0:
                self._send_json({"ok": True, "path": result.stdout.strip()})
            elif "User canceled" in (result.stderr or "") or "-128" in (result.stderr or ""):
                self._send_json({"ok": True, "path": None, "cancelled": True})
            else:
                self._send_json({"ok": False, "message": (result.stderr or "打开系统选择窗口失败").strip()})
            return

        # ── 配置保存 ──
        elif path == "/api/test_connection":
            try:
                raw = self._read_body()
                cfg = json.loads(raw) if raw and raw.strip() else {}
            except Exception:
                self._send_json({"ok": False, "message": "请求体不是合法 JSON"}, 400)
                return

            def _ssh(args, timeout=30, env=None):
                try:
                    p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=env)
                    out = (p.stderr or p.stdout).strip()
                    msg = out.splitlines()[-1] if out else ""
                    return p.returncode == 0, msg
                except subprocess.TimeoutExpired:
                    return False, "连接超时"
                except FileNotFoundError:
                    return False, "未找到 ssh 命令"
                except Exception as e:
                    return False, str(e)

            jump_host = (cfg.get("jumpHost") or "").strip()
            jump_key = os.path.expanduser((cfg.get("jumpKey") or "").strip())
            remote_user = (cfg.get("remoteUser") or "").strip()
            remote_host = (cfg.get("remoteHost") or "").strip()
            remote_key = os.path.expanduser((cfg.get("remoteKey") or "").strip())

            ssh_env = dict(os.environ)
            ssh_env["HOME"] = os.path.expanduser("~")
            ssh_env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

            jump_args = ["ssh", "-o", "BatchMode=yes", "-o",
                         "StrictHostKeyChecking=no", "-o", "ConnectTimeout=10",
                         "-i", jump_key, jump_host]
            jump_ok, jump_msg = _ssh(jump_args, env=ssh_env)
            remote_ok = False
            remote_msg = "跳板机不通，未测试服务器"
            if jump_ok:
                remote_args = [
                    "ssh", "-J", jump_key and f"{jump_host}" or jump_host,
                    "-i", remote_key,
                    "-o", "BatchMode=yes",
                    "-o", "StrictHostKeyChecking=no",
                    "-o", "ConnectTimeout=15",
                    f"{remote_user}@{remote_host}", "echo ok",
                ]
                # -J needs the jump host identity; use ProxyJump with -i for both keys
                # Actually -J doesn't support per-hop identity keys, so use ProxyCommand
                # But write a temp SSH config file to avoid ProxyCommand string parsing issues
                import tempfile
                with tempfile.NamedTemporaryFile(mode="w", suffix=".ssh_config", delete=False) as tf:
                    tf.write(f"Host jump\n")
                    tf.write(f"    HostName {jump_host.split('@')[-1] if '@' in jump_host else jump_host}\n")
                    tf.write(f"    User {jump_host.split('@')[0] if '@' in jump_host else ''}\n")
                    tf.write(f"    IdentityFile {jump_key}\n")
                    tf.write(f"    StrictHostKeyChecking no\n")
                    tf.write(f"    BatchMode yes\n")
                    tf.write(f"\nHost remote\n")
                    tf.write(f"    HostName {remote_host}\n")
                    tf.write(f"    User {remote_user}\n")
                    tf.write(f"    IdentityFile {remote_key}\n")
                    tf.write(f"    ProxyJump jump\n")
                    tf.write(f"    StrictHostKeyChecking no\n")
                    tf.write(f"    BatchMode yes\n")
                    ssh_config_path = tf.name
                remote_args = [
                    "ssh", "-F", ssh_config_path, "remote", "echo ok",
                ]
                remote_ok, remote_msg = _ssh(remote_args, env=ssh_env, timeout=30)
                try:
                    os.unlink(ssh_config_path)
                except Exception:
                    pass

            self._send_json({
                "ok": jump_ok and remote_ok,
                "jumpOk": jump_ok,
                "jumpMsg": jump_msg,
                "remoteOk": remote_ok,
                "remoteMsg": remote_msg,
            })
            return

        elif path == "/api/config/save":
            try:
                body = self._read_body()
                cfg = json.loads(body)
            except Exception:
                self._send_json({"ok": False, "message": "请求体不是合法 JSON"}, 400)
                return
            db.save_config(cfg)
            db.add_audit("配置修改", "更新服务器/路径配置")
            self._send_json({"ok": True})

        elif path == "/api/syncignore/save":
            try:
                body = self._read_body()
                data = json.loads(body)
            except Exception:
                self._send_json({"ok": False, "message": "请求体不是合法 JSON"}, 400)
                return
            rules = data.get("rules", [])
            EXCLUDE_FILE.parent.mkdir(parents=True, exist_ok=True)
            EXCLUDE_FILE.write_text("\n".join(rules) + "\n")
            db.add_audit("规则修改", f"更新 .syncignore 规则（{len(rules)} 条）")
            self._send_json({"ok": True})

        else:
            self._send_json({"error": "Not found"}, 404)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length > 1048576:
            raise ValueError("请求体超过 1MB 限制")
        if length:
            return self.rfile.read(length).decode()
        return "{}"


def main():
    # 恢复自动同步开关状态与方向
    global auto_sync_enabled, auto_sync_direction
    saved = db.get_config()
    dir_saved = saved.get("autoSyncDirection")
    if dir_saved in ("push", "pull", "bidirectional"):
        auto_sync_direction = dir_saved
    if saved.get("autoSync") == "1":
        auto_sync_enabled = True
        auto_engine.start()

    # ThreadingHTTPServer avoids blocking the WebView's keep-alive connections
    # (and the SSE log stream) when other requests arrive concurrently.
    server = ThreadingHTTPServer(("127.0.0.1", PORT), SyncHandler)
    server.daemon_threads = True
    print(f"SyncMaster 已启动 -> http://127.0.0.1:{PORT}")
    print(f"按 Ctrl+C 停止")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在停止...")
        server.shutdown()


if __name__ == "__main__":
    main()
