#!/usr/bin/env python3
"""SyncMaster - Hermes 配置同步 Web 管理后台"""

import os, sys, json, subprocess, threading, time, secrets, queue, sqlite3, shutil, shlex, socket, re, signal
import hashlib
import fnmatch
import tempfile
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
TUNNEL_CFG_PATH = os.path.expanduser("~/.syncmaster/ssh_config")

# SSH 连接调优：关闭 GSSAPI 等慢认证、限定公钥认证，加速握手
SSH_TUNING = (
    "    GSSAPIAuthentication no\n"
    "    GSSAPIDelegateCredentials no\n"
    "    PreferredAuthentications publickey\n"
    "    AddressFamily inet\n"
    "    IPQoS none\n"
    "    ConnectTimeout 15\n"
    "    ConnectionAttempts 1\n"
)
os.makedirs(os.path.expanduser("~/.ssh"), exist_ok=True)

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
            # 清理旧的全局配置项
            c.execute("DELETE FROM config WHERE key IN ('localDir', 'remoteDir')")
            # 迁移：sync_history 补充 direction_code（用于历史记录"继续"续传）
            cols = [r["name"] for r in c.execute("PRAGMA table_info(sync_history)").fetchall()]
            if "direction_code" not in cols:
                c.execute("ALTER TABLE sync_history ADD COLUMN direction_code TEXT DEFAULT ''")
            c.commit()

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
        sql = "SELECT time_str, timestamp, category, direction, file_count, total_size, duration, result, direction_code FROM sync_history"
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
                     "duration":r["duration"],"result":r["result"],
                     "directionCode": r["direction_code"] or _dir_code(r["direction"])} for r in rows]

    def add_history(self, entry):
        with self._conn() as c:
            c.execute("""INSERT INTO sync_history (timestamp, time_str, category, direction, file_count, total_size, duration, result, direction_code)
                         VALUES (?,?,?,?,?,?,?,?,?)""",
                      (entry.get("timestamp",time.time()), entry["time"], entry["category"],
                       entry["direction"], entry.get("fileCount",0), entry.get("totalSize","-"),
                       entry.get("duration",""), entry.get("result",""), entry.get("directionCode","")))

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

def _new_sync_stats(status="就绪"):
    return {"speed": "0 KB/s", "done": 0, "total": 0, "percent": 0, "status": status,
            "eta": "--:--", "uploaded": 0, "downloaded": 0, "currentFile": ""}

def _dir_display(direction):
    if direction == "push":
        return "本地 → 服务器"
    if direction == "pull":
        return "服务器 → 本地"
    if direction == "bidirectional":
        return "双向同步"
    return direction

def _dir_code(direction_display):
    if direction_display == "本地 → 服务器":
        return "push"
    if direction_display == "服务器 → 本地":
        return "pull"
    return "bidirectional"

sync_stats = _new_sync_stats()
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

    def start(self):
        self._enabled = True
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_poll, daemon=True)
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
            # 自动同步：使用第一个启用的任务
            cats = db.get_categories()
            enabled_cats = [c for c in cats if c.get("isEnabled", True)]
            if not enabled_cats:
                auto_sync_reason = "没有启用的同步任务"
                return
            # 使用第一个启用任务的 ID
            target_id = enabled_cats[0]["id"]
            ok, msg = start_sync(target_id, auto_sync_direction)
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
            old = getattr(self, "_debounce_timer", None)
            if old is not None and old is not timer:
                try:
                    old.cancel()
                except Exception:
                    pass
            self._debounce_timer = timer
        timer.start()

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
        """监听主循环：轮询快照对比（跨平台、稳定可靠）。"""
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

def _uses_jump_host(cfg):
    value = cfg.get("useJumpHost", "1")
    return str(value).lower() not in ("0", "false", "no", "")


def _connection_paths(cfg):
    # Bind both files to the endpoint, including the jump route and identity.
    identity = {"jump": _uses_jump_host(cfg), "tuning": SSH_TUNING}
    for key in ("remoteHost", "remoteUser", "remoteKey", "jumpHost", "jumpKey"):
        value = cfg.get(key, "")
        identity[key] = os.path.expanduser(value) if key.endswith("Key") else value
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:20]
    directory = os.path.dirname(TUNNEL_CFG_PATH)
    return (os.path.join(directory, f"ssh-{digest}.conf"),
            os.path.join(directory, f"cm-{digest}.sock"))


def _write_ssh_config(cfg, path):
    """写入 SSH 配置到指定路径。根据 use_jump_host 决定是否含跳板机。"""
    kex = ("KexAlgorithms curve25519-sha256,curve25519-sha256@libssh.org,"
           "ecdh-sha2-nistp256,ecdh-sha2-nistp384,ecdh-sha2-nistp521,"
           "diffie-hellman-group-exchange-sha256,diffie-hellman-group14-sha256\n")
    hka = ("HostKeyAlgorithms ssh-ed25519,ecdsa-sha2-nistp256,ecdsa-sha2-nistp384,"
           "ssh-rsa,rsa-sha2-256,rsa-sha2-512\n")
    use_jump = _uses_jump_host(cfg)
    jump_host = cfg.get("jumpHost", "")
    jump_key = os.path.expanduser(cfg.get("jumpKey", ""))
    remote_host = cfg.get("remoteHost", "")
    remote_user = cfg.get("remoteUser", "")
    remote_key = os.path.expanduser(cfg.get("remoteKey", ""))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                     dir=os.path.dirname(path), delete=False) as tf:
        # 全局指令：置于所有 Host 之前，对所有连接生效，无需逐段重复
        tf.write("    StrictHostKeyChecking accept-new\n")
        tf.write("    BatchMode yes\n")
        tf.write("    ServerAliveInterval 5\n")
        tf.write("    ServerAliveCountMax 10\n")
        tf.write("    TCPKeepAlive yes\n")
        tf.write(kex); tf.write(hka); tf.write(SSH_TUNING)
        if use_jump and jump_host:
            jump_user = jump_host.split("@")[0] if "@" in jump_host else ""
            jump_hostname = jump_host.split("@")[-1] if "@" in jump_host else jump_host
            tf.write("\nHost jump\n")
            tf.write(f"    HostName {jump_hostname}\n")
            tf.write(f"    User {jump_user}\n")
            tf.write(f"    IdentityFile {json.dumps(jump_key, ensure_ascii=False)}\n")
            tf.write("    ConnectTimeout 10\n")
            tf.write("\nHost remote\n")
            tf.write(f"    HostName {remote_host}\n")
            tf.write(f"    User {remote_user}\n")
            tf.write(f"    IdentityFile {json.dumps(remote_key, ensure_ascii=False)}\n")
            tf.write("    ProxyJump jump\n")
        else:
            tf.write("\nHost remote\n")
            tf.write(f"    HostName {remote_host}\n")
            tf.write(f"    User {remote_user}\n")
            tf.write(f"    IdentityFile {json.dumps(remote_key, ensure_ascii=False)}\n")
    try:
        os.replace(tf.name, path)
    finally:
        if os.path.exists(tf.name):
            os.unlink(tf.name)


def _ssh_cmd(cfg):
    """生成最新 SSH 配置，返回 (ssh 前缀命令, 配置文件路径)。"""
    ssh_config_path, _ = _connection_paths(cfg)
    _write_ssh_config(cfg, ssh_config_path)
    return shlex.join(["ssh", "-F", ssh_config_path]), ssh_config_path


def _resolve_sync_cfg():
    c = db.get_config()
    return {
        "name": c.get("remoteHost", "155.248.172.187"),
        "useJumpHost": c.get("useJumpHost", "1"),
        "jumpHost": c.get("jumpHost", "ubuntu@54.160.252.171"),
        "jumpKey": os.path.expanduser(c.get("jumpKey", "~/Documents/2api.pem")),
        "remoteUser": c.get("remoteUser", "opc"),
        "remoteHost": c.get("remoteHost", "155.248.172.187"),
        "remoteKey": os.path.expanduser(c.get("remoteKey", "~/Documents/oci_opc_key.pem")),
        # 不再提供默认的 remote_dir 和 local_dir，必须通过任务指定
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

def _cleanup_old_backups():
    """按配置的保留天数清理过期备份快照。"""
    try:
        cfg = db.get_config()
        retain_days = int(cfg.get("backupRetainDays", "7") or "7")
        if retain_days <= 0:
            return  # 0 = 永久保留
        threshold = time.time() - retain_days * 86400
        for entry in BACKUP_DIR.iterdir():
            if entry.is_dir() and entry.stat().st_mtime < threshold:
                shutil.rmtree(entry, ignore_errors=True)
    except Exception:
        pass

def _parse_rsync_progress(line):
    """解析 rsync/openrsync --progress 行，返回 (count, speed, pct, eta)。"""
    count = 0; speed = ""; pct = 0; eta = ""
    # 百分比（openrsync: "348848128  96% ..."；GNU: "96%..."）
    m = re.search(r'(\d+)%', line)
    if m: pct = int(m.group(1))
    # 速率（openrsync: "908.99KB/s"；GNU: "at 908.99 KB/s"）
    m = re.search(r'([\d.]+\s*[KMGT]?B/s)', line, re.I)
    if m: speed = m.group(1).strip()
    # 已传输文件数
    m = re.search(r'xfer#(\d+)', line)
    if m: count = int(m.group(1))
    # 预计剩余（openrsync: "00:00:16"）
    m = re.search(r'(\d{1,2}:\d{2}:\d{2})', line)
    if m: eta = m.group(1)
    return count, speed, pct, eta

def _validate_remote_dir(path):
    import posixpath
    normalized = posixpath.normpath(path)
    if not normalized.startswith('/') or len(normalized.strip('/').split('/')) < 3:
        raise ValueError(f"remote_dir 不安全（{path}），必须指定具体子目录")
    return normalized


def _mode_to_direction(mode):
    """任务 mode（数据库口径）-> 同步方向（rsync 口径）。

    数据库里 mode 是 'bidirectional'/'toServer'/'toLocal'，而 rsync flag
    用的是 'bidirectional'/'push'/'pull'。不转换会让 'toServer' 落进
    _transfer_flags 的 else 分支被加上 --delete，与实际行为不符。
    """
    return {"toServer": "push", "toLocal": "pull", "bidirectional": "bidirectional"}.get(
        mode, "bidirectional")


def _transfer_flags(direction, selected):
    flags = ['-avz', '--partial', '--partial-dir=.rsync-partial',
             '--no-whole-file', '--timeout=300', '--progress',
             '--exclude', '.rsync-partial', '--exclude', '.rsync.*']
    # Merge by modification time; never delete either side's unique files.
    if direction == 'bidirectional':
        flags.append('--update')
    elif not selected:
        flags.append('--delete')
    return flags


def _files_from_manifest(entries):
    """把勾选条目写入临时清单文件，返回路径。空清单返回 None。

    精准文件模式下 --files-from 的相对路径含义取决于方向（push 相对本地、
    pull 相对远端）。某侧完全不存在的条目必须从该侧清单剔除，否则 rsync
    以 exit 23/24 报错、整个同步被误判为失败。
    """
    import tempfile
    clean = [str(f).strip() for f in entries if str(f).strip()]
    if not clean:
        return None
    tf = tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8")
    for f in clean:
        tf.write(f + "\n")
    tf.close()
    return tf.name


def _split_selected(selected_files, local_dir):
    """按“本地是否存在”拆分精准清单：(应上传, 应下载)。

    本地有 -> 上传；本地无 -> 视为远端独有，下载。空清单表示该方向无需运行。
    """
    upload, download = [], []
    if not os.path.isdir(local_dir):
        return upload, download
    for f in selected_files:
        f_s = str(f).strip()
        if not f_s:
            continue
        (upload if os.path.exists(os.path.join(local_dir, f_s)) else download).append(f_s)
    return upload, download


def _flags_allow_delete(transfer_flags):
    """真实同步的 flag 是否带删除语义。

    单向镜像才有 --delete；双向 --update 与精准模式都不删除。
    diff 预览必须与真实同步同一来源，不能在这里重复硬编码。
    """
    return "--delete" in transfer_flags


def _terminate_proc_group(proc, term_timeout=6.0, kill_timeout=3.0):
    """整组终止 rsync 进程组。

    rsync 会派生 ssh 子进程，只杀主进程会留下孤儿 ssh；因此按进程组
    (start_new_session=True) 先 SIGTERM 优雅中断，超时才 SIGKILL 兜底。
    """
    try:
        pgid = os.getpgid(proc.pid)
    except (ProcessLookupError, OSError):
        pgid = None
    if pgid is not None:
        try:
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    else:
        try:
            proc.terminate()
        except (ProcessLookupError, OSError):
            pass
    try:
        proc.wait(timeout=term_timeout)
    except subprocess.TimeoutExpired:
        if pgid is not None:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                pass
        else:
            try:
                proc.kill()
            except (ProcessLookupError, OSError):
                pass
        try:
            proc.wait(timeout=kill_timeout)
        except subprocess.TimeoutExpired:
            pass
    return proc


def _cleanup_local_partial(local_dir):
    """同步成功后清理本地续传残留（.rsync-partial/、.rsync.XXXXXX 临时文件）。"""
    try:
        root = Path(local_dir)
        for name in os.listdir(root):
            if name == ".rsync-partial" or name.startswith(".rsync."):
                p = root / name
                if p.is_dir() and not p.is_symlink():
                    shutil.rmtree(p, ignore_errors=True)
                elif p.is_file() or p.is_symlink():
                    p.unlink(missing_ok=True)
    except Exception:
        pass


def _cleanup_remote_partial(cfg, remote_dir):
    """同步成功后清理远端续传残留。

    rsync 完成后 .rsync-partial/ 与 .rsync.XXXXXX 临时文件已无意义，
    清掉以避免远端目录长期堆积碎片。失败静默（不影响同步结果）。
    """
    cmd = "rm -rf -- '.rsync-partial' '.rsync.*' 2>/dev/null"
    _run_remote_cmd(cfg, "cd %s && %s" % (json.dumps(remote_dir), cmd))


# ── 应用层续传检查点 ──
# rsync 的 --partial 只保证字节层面可续传，不会记住“用户上次选了哪个任务、
# 哪个方向、勾了哪些文件”。用户中断后再点“开始”时如果这些没被记下来，
# 就等于从头开始而不是续传。这里把最后一次被中断的传输上下文落盘。
CHECKPOINT_PATH = Path(os.path.expanduser("~/.syncmaster/resume_checkpoint.json"))


def _save_checkpoint(checkpoint):
    """持久化续传点；checkpoint 为 None 表示清除（同步已成功完成）。"""
    try:
        if checkpoint is None:
            CHECKPOINT_PATH.unlink(missing_ok=True)
            return
        CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CHECKPOINT_PATH, "w", encoding="utf-8") as f:
            json.dump(checkpoint, f, ensure_ascii=False)
    except Exception:
        pass


def _load_checkpoint():
    """读取上次中断的传输上下文；无或损坏返回 None。"""
    try:
        if not CHECKPOINT_PATH.exists():
            return None
        with open(CHECKPOINT_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) and data.get("categoryId") else None
    except Exception:
        return None


def get_resume_state():
    """返回可续传状态，供 UI 展示“是否可续传 + 上次中断在哪”。"""
    cp = _load_checkpoint()
    if not cp:
        return {"resumable": False}
    return {
        "resumable": True,
        "categoryId": cp.get("categoryId"),
        "categoryName": cp.get("categoryName", ""),
        "direction": cp.get("direction", ""),
        "files": cp.get("files", []),
        "at": cp.get("at", ""),
    }


def clear_resume_checkpoint():
    """用户放弃续传时清除检查点。"""
    _save_checkpoint(None)
    return True


# 最近一次（仍在运行或刚被中断的）传输上下文，供 cancel_sync 落盘续传点。
current_transfer = {"categoryId": "", "categoryName": "", "direction": "", "files": []}

def run_sync_impl(cfg: dict, direction: str, files: list = None):
    global sync_running, sync_process, sync_stats, sync_cancelled
    broadcast({"type": "started", "target": cfg["name"], "direction": direction})
    sync_stats = _new_sync_stats("同步中...")
    temp_manifests = []
    try:
        if _uses_jump_host(cfg) and not os.path.isfile(cfg["jumpKey"]):
            raise FileNotFoundError(f"跳板机本地密钥不存在: {cfg['jumpKey']}")
        if not os.path.isfile(cfg["remoteKey"]):
            raise FileNotFoundError(f"服务器本地密钥不存在: {cfg['remoteKey']}")

        local_dir = cfg.get("local_dir")
        remote_dir = _validate_remote_dir(cfg.get("remote_dir", ""))

        # 必须通过任务指定路径
        if not local_dir or not remote_dir:
            raise ValueError("必须指定同步任务，无法使用全局配置同步")

        # 安全护栏：remote_dir 过浅（如 /home/opc 或 /）时拒绝同步，
        # 防止 --delete 清空家目录/.ssh 等系统文件
        if remote_dir.count("/") < 3 or remote_dir in ("/home", "/home/opc"):
            raise ValueError(
                f"remote_dir 不安全（{remote_dir}），必须指定到具体子目录，如 /home/opc/.hermes"
            )

        # M15: 同步前创建本地备份（可在设置中关闭）
        backup_enabled = db.get_config().get("backupEnabled", "1") != "0"
        if backup_enabled:
            backup = _create_backup(local_dir)
            if backup:
                broadcast({"type": "log", "text": f"已创建备份快照: {backup.name}"})
# 无论是否开启备份，都执行过期清理
        _cleanup_old_backups()

        # A settings test must not replace a running transfer's endpoint.
        remote_ssh, _ = _ssh_cmd(cfg)
        excl = ["--exclude-from=" + str(EXCLUDE_FILE)] if EXCLUDE_FILE.exists() else []
        # 运行时状态文件两端各自维护，永不互相同步（避免远端 gateway_state.json 覆盖本地等）
        for pat in ("gateway_state.json", "gateway.pid", "*.lock", "*-shm", "*-wal"):
            excl += ["--exclude", pat]

        # 智能保护 .env 与机器人环境配置（默认严格隔离）
        env_protect = db.get_config().get("envProtectEnabled", "1") != "0"
        if env_protect:
            for pat in (".env", ".env.*", "*.env", "*bot*.env"):
                excl += ["--exclude", pat]

        remote = f"remote:{remote_dir}/"

        # 支持按需选择单个或多个文件精准同步
        manifests = []
        if files and len(files) > 0:
            upload_list, download_list = _split_selected(files, local_dir)
            if not upload_list and not download_list:
                raise ValueError("勾选的文件在本地与远端均不存在，无法同步")
            upload_m = _files_from_manifest(upload_list)
            download_m = _files_from_manifest(download_list)
            for m in (upload_m, download_m):
                if m:
                    temp_manifests.append(m)
            manifests.append(("push", upload_m))
            manifests.append(("pull", download_m))
            broadcast({"type": "log", "text": f"已启用精准同步模式：上传 {len(upload_list)} 个，"
                                             f"下载 {len(download_list)} 个"})
            # 精准指定文件时，不传 --delete，避免误删其他文件
            base_cmd = ["rsync"] + _transfer_flags(direction, True) + excl + ["-e", remote_ssh]
        else:
            base_cmd = ["rsync"] + _transfer_flags(direction, False) + excl + ["-e", remote_ssh]

        # M7: bidirectional = push then pull
        order = []
        if direction == "bidirectional":
            order = ["push", "pull"]
        elif direction == "push":
            order = ["push"]
        else:
            order = ["pull"]

        stages = []
        skipped_stages = []
        for stage_label in order:
            manifest = next((m for lbl, m in manifests if lbl == stage_label), None)
            if manifests and not manifest:
                # 精准模式下该方向无可传输条目：跳过阶段而不是跑空/报错。
                skipped_stages.append(stage_label)
                continue
            if stage_label == "push":
                cmd = base_cmd + ([f"--files-from={manifest}"] if manifest else []) + \
                      [f"{local_dir}/", remote]
            else:
                cmd = base_cmd + ([f"--files-from={manifest}"] if manifest else []) + \
                      [remote, f"{local_dir}/"]
            stages.append((stage_label, cmd))
        for lbl in skipped_stages:
            broadcast({"type": "log", "text": f"[{lbl}] 没有需要传输的文件，已跳过"})
        if not stages:
            raise ValueError("没有需要同步的文件")

        def _run_rsync(cmd, label):
            global sync_process
            broadcast({"type": "log", "text": f"[{label}] 开始同步... {cfg['remoteUser']}@{cfg['remoteHost']}"})
            # start_new_session: 独立进程组，取消时可整组 kill，避免残留 ssh 子进程
            sync_process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                start_new_session=True,
            )
            for line in iter(sync_process.stdout.readline, ""):
                line = line.strip()
                if line:
                    broadcast({"type": "log", "text": f"[{label}] {line}"})
                    sync_stats["currentFile"] = line
                    count, speed, pct, eta = _parse_rsync_progress(line)
                    if count: sync_stats["done"] = count
                    if speed: sync_stats["speed"] = speed
                    if pct: sync_stats["percent"] = pct
                    if eta: sync_stats["eta"] = eta
                    if pct or count:
                        broadcast({"type": "stats", "stats": dict(sync_stats)})
            return sync_process.wait()

        for i, (stage_label, stage_cmd) in enumerate(stages):
                label = f"{i+1}/{len(stages)}"
                rc = _run_rsync(stage_cmd, label)
                # 取消：立即跳出，不再启动后续阶段
                if sync_cancelled:
                    break
                if rc in (23, 24):
                    # rsync exit 23 = 部分传输出错；exit 24 = 源文件同步中消失。
                    # 都只说明个别文件未同步，主体已传完，且重试结果不变，
                    # 不应把整个同步判为失败（旧实现会因此误报失败并白等 10 秒）。
                    reason = ("部分文件未同步（exit 23，多为权限/文件被占用）"
                              if rc == 23 else "部分源文件同步中消失（exit 24）")
                    broadcast({"type": "log",
                               "text": f"[{label}] 警告：{reason}，其余内容已同步完成。"})
                elif rc != 0:
                    if rc == 255:
                        broadcast({"type": "log", "text": f"[{label}] SSH 连接失败或中断：{cfg['remoteHost']}。请检查代理/网络链路及服务端 SSH 日志。"})
                    broadcast({"type": "log", "text": f"[{label}] 首次失败，10 秒后自动重试（支持断点续传）..."})
                    if _sleep_cancellable(10):
                        break
                    if not sync_cancelled:
                        rc = _run_rsync(stage_cmd, f"{label}重试")
                        if rc != 0:
                            raise Exception(f"rsync 同步失败（[{label}] exit={rc}）")

        # 取消时不覆盖状态（cancel_sync 已设为"已停止"并广播）
        if sync_cancelled:
            return
        sync_stats["percent"] = 100
        sync_stats["status"] = "完成"
        sync_stats["currentFile"] = "同步完成"
        # 同步成功：续传残片已无意义，清理避免长期堆积（失败静默）
        if direction == "pull" or direction == "bidirectional":
            _cleanup_local_partial(local_dir)
        if direction == "push" or direction == "bidirectional":
            _cleanup_remote_partial(cfg, remote_dir)
        broadcast({"type": "done", "success": True, "message": "同步完成"})
        _save_checkpoint(None)
        db.add_audit("同步成功", f"{cfg['name']} {direction}")
        db.add_history({
            "time": datetime.now().strftime("%m-%d %H:%M:%S"),
            "timestamp": time.time(),
            "category": cfg["name"],
            "direction": _dir_display(direction),
            "directionCode": direction,
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
            sync_stats["currentFile"] = f"同步失败: {e}"
            broadcast({"type": "done", "success": False, "message": str(e)})
            db.add_audit("同步失败", str(e))
            db.add_history({
                "time": datetime.now().strftime("%m-%d %H:%M:%S"),
                "timestamp": time.time(),
                "category": cfg["name"],
                "direction": _dir_display(direction),
                "directionCode": direction,
                "fileCount": 0,
                "totalSize": "-",
                "duration": "-",
                "result": "失败",
            })
        # 已取消时 cancel_sync 已处理状态/广播，这里不再重复
    finally:
        # 取消时 rsync 已被整组 kill，stdout pipe 未由 EOF 关闭，需显式释放 fd。
        try:
            if sync_process is not None and sync_process.stdout is not None:
                sync_process.stdout.close()
        except Exception:
            pass
        if sync_cancelled:
            # 用户主动停止：记录为"已停止"，不视为失败
            try:
                db.add_audit("同步已停止", f"{cfg['name']} {direction}")
                db.add_history({
                    "time": datetime.now().strftime("%m-%d %H:%M:%S"),
                    "timestamp": time.time(),
                    "category": cfg["name"],
                    "direction": _dir_display(direction),
                    "directionCode": direction,
                    "fileCount": sync_stats["done"],
                    "totalSize": "-",
                    "duration": "-",
                    "result": "已停止",
                })
            except Exception:
                pass
        if temp_manifests:
            for manifest_path in temp_manifests:
                if manifest_path and os.path.exists(manifest_path):
                    try:
                        os.unlink(manifest_path)
                    except Exception:
                        pass
        with sync_lock:
            sync_running = False
            sync_process = None
            sync_cancelled = False


def start_sync(category_id: str, direction: str, files: list = None):
    """启动指定任务的同步。category_id 必须是有效的任务ID。"""
    global sync_running, sync_cancelled
    if direction not in ("push", "pull", "bidirectional"):
        return False, "无效的同步方向"
    if not category_id:
        return False, "必须指定同步任务"

    cats = db.get_categories()
    cat = next((c for c in cats if c["id"] == category_id or c["name"] == category_id), None)
    if not cat:
        return False, f"未找到任务: {category_id}"

    cfg = _resolve_sync_cfg()
    cfg["name"] = cat["name"]
    if cat.get("localPath"):
        cfg["local_dir"] = os.path.expanduser(cat["localPath"])
    if cat.get("remotePath"):
        cfg["remote_dir"] = cat["remotePath"]
    # 任务的删除策略，决定 diff 是否展示为删除或下载（默认 noDelete = 不删除）
    cfg["delete_policy"] = cat.get("deletePolicy", "noDelete")
    mode = cat.get("mode", "")
    if mode == "toServer" and direction == "bidirectional":
        direction = "push"
    elif mode == "toLocal" and direction == "bidirectional":
        direction = "pull"

    # 记下本次传输上下文；中断时会落盘成续传点。
    current_transfer.update({
        "categoryId": cat["id"], "categoryName": cat["name"],
        "direction": direction, "files": list(files or []),
    })

    with sync_lock:
        if sync_running:
            return False, "同步已在运行中"
        sync_running = True
        sync_cancelled = False

    t = threading.Thread(target=run_sync_impl, args=(cfg, direction, files), daemon=True)
    t.start()
    return True, "同步已启动"


def _sleep_cancellable(seconds):
    """可被取消打断的睡眠。取消时返回 True（由调用方跳出循环）。"""
    end = time.time() + seconds
    while time.time() < end:
        if sync_cancelled:
            return True
        time.sleep(0.1)
    return False


def cancel_sync():
    global sync_process, sync_running, sync_stats, sync_cancelled
    with sync_lock:
        if not sync_running:
            return False, "没有正在运行的同步"
        sync_cancelled = True
        _save_checkpoint({"categoryId": current_transfer.get("categoryId", ""),
                          "categoryName": current_transfer.get("categoryName", ""),
                          "direction": current_transfer.get("direction", ""),
                          "files": list(current_transfer.get("files") or []),
                          "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")})
        if sync_process and sync_process.poll() is None:
            # 整组 SIGTERM（rsync 派生的 ssh 不会残留），超时升级为 SIGKILL。
            _terminate_proc_group(sync_process)
        # 注意：不在此处把 sync_running 置 False。让 run_sync_impl 线程收尾后在
        # finally 里释放，避免用户紧接着"开始"续传时旧线程仍在运行造成竞态。
        sync_stats["status"] = "已停止"
        sync_stats["currentFile"] = "同步已停止，可重新开始续传"
        broadcast({"type": "done", "success": False, "message": "同步已停止（可点开始继续续传）"})
        return True, "同步已停止"


def get_sync_diff():
    """对比本地与远程，返回真实差异文件明细。

    两侧各取一份文件树快照（本地 os.walk / 远端 find），按相对路径对齐比较。
    比较规则与真实同步一致：同一套排除规则、同一套删除策略。
    冲突用 size+mtime 判定（rsync quick-check 口径），不逐个跑 sha256 以免拖慢界面。
    """
    result = {"pending": 0, "toUpload": 0, "toDownload": 0, "conflicts": 0,
              "inSync": 0, "ok": False, "msg": "", "files": []}
    if sync_running:
        result["msg"] = "同步进行中"
        return result
    try:
        cats = db.get_categories()
        enabled_cats = [c for c in cats if c.get("isEnabled", True)]
        if not enabled_cats:
            result["msg"] = "没有启用的同步任务"
            return result

        cfg = _resolve_sync_cfg()
        if not os.path.isfile(cfg["remoteKey"]):
            result["msg"] = "配置不完整"
            return result
        if _uses_jump_host(cfg) and not os.path.isfile(cfg.get("jumpKey", "")):
            result["msg"] = "配置不完整"
            return result

        base_exclude = _load_exclude_patterns(cfg)
        all_rows = []
        remote_errors = []

        for cat in enabled_cats:
            local_dir = os.path.expanduser(cat.get("localPath", ""))
            remote_dir = cat.get("remotePath", "").rstrip("/")
            if not local_dir or not remote_dir or not os.path.isdir(local_dir):
                continue

            local_snap = _scan_local(local_dir)

            remote_snap, err = _rsync_remote_tree(cfg, remote_dir)
            if remote_snap is None:
                remote_errors.append(f"{cat['name']}: {err}")
                remote_snap = {}
                result["msg"] = "部分远端目录无法访问，仅显示本地变更"

            # 与真实同步同一套 flag 规则：diff 的排除/删除策略不能凭空漂移，
            # 否则“预计上传/删除 N 个”就是谎报。单向镜像=--delete、双向=--update。
            sync_direction = _mode_to_direction(cat.get("mode", "bidirectional"))
            transfer_flags = _transfer_flags(sync_direction, selected=False)
            exclude_pats = list(base_exclude)
            for i, arg in enumerate(transfer_flags):
                if arg == "--exclude" and i + 1 < len(transfer_flags):
                    pat = transfer_flags[i + 1]
                    if pat not in exclude_pats:
                        exclude_pats.append(pat)
            allow_delete = _flags_allow_delete(transfer_flags)

            rows = _compare_trees(local_snap, remote_snap, exclude_pats,
                                  allow_delete=allow_delete)
            all_rows.extend(rows)

        result["toUpload"] = sum(1 for r in all_rows if r["action"] in ("upload", "delete"))
        result["toDownload"] = sum(1 for r in all_rows if r["action"] == "download")
        result["conflicts"] = sum(1 for r in all_rows if r["type"] == "conflict")
        result["pending"] = result["toUpload"] + result["toDownload"]
        result["files"] = all_rows
        result["ok"] = True
        if remote_errors and not result["msg"]:
            result["msg"] = "; ".join(remote_errors[:2])
        return result
    except Exception as e:
        result["msg"] = str(e)
        return result


def _load_exclude_patterns(cfg):
    """收集与真实同步完全一致的排除规则（.syncignore + 运行时状态 + .env 保护）。"""
    pats = []
    if EXCLUDE_FILE.exists():
        for line in EXCLUDE_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                pats.append(line)
    for pat in ("gateway_state.json", "gateway.pid", "*.lock", "*-shm", "*-wal"):
        pats.append(pat)
    if db.get_config().get("envProtectEnabled", "1") != "0":
        for pat in (".env", ".env.*", "*.env", "*bot*.env"):
            pats.append(pat)
    return pats


def _path_is_excluded(rel_path, pats):
    """相对路径是否命中排除规则，尽量与 rsync 语义对齐。"""
    segs = rel_path.split("/")
    base = segs[-1]
    for pat in pats:
        is_dir_pat = pat.endswith("/")
        p = pat.rstrip("/")
        if is_dir_pat and p in segs[:-1]:
            return True
        if "/" in p and (rel_path == p or rel_path.startswith(p + "/")):
            return True
        if base == p or fnmatch.fnmatch(base, p):
            return True
    return False


def _fmt_mtime(ts):
    """把 mtime 渲染成 UI 友好的相对时间。"""
    if not ts:
        return "-"
    age = max(0, time.time() - ts)
    if age < 60:
        return "刚刚"
    if age < 3600:
        return f"{int(age // 60)} 分钟前"
    if age < 86400:
        return f"{int(age // 3600)} 小时前"
    return datetime.fromtimestamp(ts).strftime("%m-%d %H:%M")


def _scan_local(root, max_depth=6):
    """扫描本地目录，返回 {相对路径: (mtime, size)}。"""
    out = {}
    root = os.path.expanduser(root)
    for cur, dirs, files in os.walk(root):
        rel_root = os.path.relpath(cur, root)
        depth = 0 if rel_root == "." else rel_root.count(os.sep) + 1
        if depth >= max_depth:
            dirs[:] = []
        dirs[:] = [d for d in dirs if d not in (".rsync-partial", ".git")]
        for f in files:
            if f.endswith(".pyc") or f in (".DS_Store",):
                continue
            fp = os.path.join(cur, f)
            try:
                st = os.stat(fp)
            except OSError:
                continue
            rel = f if rel_root == "." else os.path.join(rel_root, f).replace(os.sep, "/")
            out[rel] = (st.st_mtime, st.st_size)
    return out


def _rsync_remote_tree(cfg, remote_dir, timeout=60):
    """用 find 递归取远端文件树，返回 {相对路径: (mtime, size)}。"""
    _, ssh_config_path = _ssh_cmd(cfg)
    ssh_env = dict(os.environ)
    ssh_env["HOME"] = os.path.expanduser("~")
    ssh_env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    rd = remote_dir.rstrip("/")
    script = (
        f"if [ -d {shlex.quote(rd)} ]; then "
        f"find {shlex.quote(rd)} -type f "
        f"-not -path '*/.rsync-partial/*' -not -name '*.pyc' -not -name '.DS_Store' "
        f"-printf '%T@\t%s\t%P\n' 2>/dev/null; "
        f"else echo __NODIR__;  fi"
    )
    args = ["ssh", "-F", ssh_config_path, "-o", "BatchMode=yes",
            "-o", "ConnectTimeout=8", "remote", script]
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=ssh_env)
    except subprocess.TimeoutExpired:
        return None, "远程文件列表获取超时"
    except Exception as e:
        return None, f"远程文件列表获取失败: {e}"
    if p.returncode != 0:
        lines = (p.stderr or p.stdout or "ssh 连接失败").strip().splitlines()
        return None, lines[-1] if lines else "ssh 连接失败"
    out = p.stdout.strip()
    if out == "__NODIR__":
        return None, f"远程目录不存在: {remote_dir}"
    snap = {}
    for line in out.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        try:
            snap[parts[2]] = (float(parts[0]), int(parts[1]))
        except ValueError:
            continue
    return snap, None


def _compare_trees(local_snap, remote_snap, exclude_pats, allow_delete=True):
    """比较两份快照，产出 UI 行。allow_delete=False 时，远端独有文件显示为「下载」。"""
    rows = []
    local_paths = set(local_snap)
    remote_paths = set(remote_snap)

    for rel in sorted(local_paths):
        excluded = _path_is_excluded(rel, exclude_pats)
        if not excluded and rel.startswith(".git/"):
            excluded = True
        l_mtime, l_size = local_snap[rel]
        if rel in remote_paths:
            r_mtime, r_size = remote_snap[rel]
            if excluded:
                continue
            if l_size == r_size and abs(l_mtime - r_mtime) < 1:
                continue
            rows.append(_mk_row(rel, "conflict", "两端均存在且内容不同，需人工确认",
                                _fmt_mtime(l_mtime), _fmt_mtime(r_mtime),
                                "conflict", "冲突", False))
        else:
            if excluded:
                rows.append(_mk_row(rel, "ignore", "本地存在，已被同步规则忽略",
                                    _fmt_mtime(l_mtime), "已忽略",
                                    "ignore", "已忽略", False))
            else:
                rows.append(_mk_row(rel, "add", "仅本地存在，将上传到服务器",
                                    _fmt_mtime(l_mtime), "-",
                                    "upload", "上传", True))

    for rel in sorted(remote_paths):
        if _path_is_excluded(rel, exclude_pats) or rel in local_paths:
            continue
        r_mtime, r_size = remote_snap[rel]
        if allow_delete:
            rows.append(_mk_row(rel, "delete", "仅服务器存在，同步将删除本地对应路径",
                                "-", _fmt_mtime(r_mtime),
                                "delete", "删除", True))
        else:
            rows.append(_mk_row(rel, "download", "仅服务器存在，将下载到本地",
                                "-", _fmt_mtime(r_mtime),
                                "download", "下载", True))
    return rows


def _mk_row(rel, ftype, note, local_time, remote_time, action, action_name, checked):
    names = {"add": "新增", "modify": "修改", "conflict": "冲突",
             "delete": "删除", "download": "下载", "ignore": "忽略"}
    return {
        "path": rel,
        "type": ftype,
        "typeName": names.get(ftype, ftype),
        "note": note,
        "localTime": local_time,
        "remoteTime": remote_time,
        "action": action,
        "actionName": action_name,
        "checked": checked,
    }

def _run_remote_cmd(cfg, cmd_str, timeout=10):
    _, ssh_config_path = _ssh_cmd(cfg)
    ssh_env = dict(os.environ)
    ssh_env["HOME"] = os.path.expanduser("~")
    ssh_env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    args = ["ssh", "-F", ssh_config_path, "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "remote", cmd_str]
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=ssh_env)
        return p.returncode == 0, p.stdout, p.stderr
    except Exception as e:
        return False, "", str(e)


def _parse_env_text(text: str):
    res = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            res[k.strip()] = v.strip().strip("'\"")
    return res


def _is_sensitive_key(key: str):
    upper = key.upper()
    keywords = ("BOT", "TOKEN", "KEY", "SECRET", "PASS", "AUTH", "CREDENTIAL", "WEBHOOK", "ID")
    return any(kw in upper for kw in keywords)


def get_env_diff(category_id: str):
    cats = db.get_categories()
    cat = next((c for c in cats if c["id"] == category_id or c["name"] == category_id), None)
    if not cat:
        return {"ok": False, "message": f"未找到分类任务: {category_id}"}

    local_dir = os.path.expanduser(cat.get("localPath", ""))
    remote_dir = cat.get("remotePath", "").rstrip("/")
    local_env_path = os.path.join(local_dir, ".env")

    local_exists = os.path.isfile(local_env_path)
    local_content = ""
    if local_exists:
        try:
            with open(local_env_path, "r", encoding="utf-8", errors="ignore") as f:
                local_content = f.read()
        except Exception:
            pass
    local_dict = _parse_env_text(local_content)

    cfg = _resolve_sync_cfg()
    remote_exists = False
    remote_dict = {}
    ok, stdout, _ = _run_remote_cmd(cfg, f"cat '{remote_dir}/.env' 2>/dev/null || true")
    if ok and stdout:
        remote_exists = True
        remote_dict = _parse_env_text(stdout)

    all_keys = list(dict.fromkeys(list(local_dict.keys()) + list(remote_dict.keys())))
    diff_items = []
    for k in all_keys:
        in_local = k in local_dict
        in_remote = k in remote_dict
        val_local = local_dict.get(k, "")
        val_remote = remote_dict.get(k, "")
        if in_local and in_remote:
            status = "same" if val_local == val_remote else "diff"
        elif in_local:
            status = "local_only"
        else:
            status = "remote_only"

        diff_items.append({
            "key": k,
            "localVal": val_local,
            "remoteVal": val_remote,
            "status": status,
            "isSensitive": _is_sensitive_key(k),
            "isBotRelated": "BOT" in k.upper()
        })

    return {
        "ok": True,
        "categoryId": category_id,
        "categoryName": cat["name"],
        "localExists": local_exists,
        "remoteExists": remote_exists,
        "items": diff_items
    }


def merge_env(category_id: str, merges: list):
    cats = db.get_categories()
    cat = next((c for c in cats if c["id"] == category_id or c["name"] == category_id), None)
    if not cat:
        return {"ok": False, "message": "分类不存在"}
    local_dir = os.path.expanduser(cat.get("localPath", ""))
    local_env_path = os.path.join(local_dir, ".env")

    content = ""
    if os.path.isfile(local_env_path):
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        bak_path = local_env_path + f".bak.{ts}"
        try:
            shutil.copy2(local_env_path, bak_path)
        except Exception:
            pass
        with open(local_env_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

    existing = _parse_env_text(content)
    for m in merges:
        k = m.get("key")
        v = m.get("value")
        if k and v is not None:
            existing[k] = v

    lines = [f"{k}={v}" for k, v in existing.items()]
    with open(local_env_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    db.add_audit("环境变量合并", f"分类 {cat['name']} 更新 {len(merges)} 项键值")
    return {"ok": True, "message": "合并保存成功"}


def get_category_tree(category_id: str):
    cats = db.get_categories()
    cat = next((c for c in cats if c["id"] == category_id or c["name"] == category_id), None)
    if not cat:
        return {"ok": False, "message": f"未找到分类: {category_id}"}
    local_dir = os.path.expanduser(cat.get("localPath", ""))
    if not os.path.isdir(local_dir):
        return {"ok": True, "files": [], "error": "本地目录不存在"}

    tree = []
    ignored_names = {".git", "node_modules", "__pycache__", ".DS_Store", ".idea", ".vscode"}
    count = 0
    max_entries = 400

    for root, dirs, files in os.walk(local_dir):
        dirs[:] = [d for d in dirs if d not in ignored_names and not d.startswith(".rsync")]
        rel_root = os.path.relpath(root, local_dir)
        if rel_root == ".":
            level = 0
        else:
            level = rel_root.count(os.sep) + 1

        if level > 4:
            continue

        for d in sorted(dirs):
            if count >= max_entries: break
            d_rel = os.path.normpath(os.path.join(rel_root, d)) if rel_root != "." else d
            tree.append({
                "path": d_rel,
                "name": d,
                "isDir": True,
                "level": level,
                "size": "-",
                "mtime": "-"
            })
            count += 1

        for f in sorted(files):
            if count >= max_entries: break
            if f in ignored_names or f.endswith(".pyc"):
                continue
            f_rel = os.path.normpath(os.path.join(rel_root, f)) if rel_root != "." else f
            full_path = os.path.join(root, f)
            size_str = "-"
            mtime_str = "-"
            try:
                st = os.stat(full_path)
                s = st.st_size
                if s < 1024: size_str = f"{s} B"
                elif s < 1024*1024: size_str = f"{s/1024:.1f} KB"
                else: size_str = f"{s/(1024*1024):.1f} MB"
                mtime_str = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
            except Exception:
                pass
            tree.append({
                "path": f_rel,
                "name": f,
                "isDir": False,
                "level": level,
                "size": size_str,
                "mtime": mtime_str
            })
            count += 1

        if count >= max_entries:
            break

    return {"ok": True, "categoryId": category_id, "categoryName": cat["name"], "tree": tree}


# ── 连接状态缓存 + 后台探测 ──
# 避免每次加载首页都等 30s SSH 握手：后台定时探测，首页读缓存瞬时显示
conn_cache = {"jumpOk": False, "remoteOk": False, "jumpMsg": "", "remoteMsg": "", "at": 0.0}
_conn_lock = threading.Lock()

def _probe_connection(cfg=None):
    """探测服务器连通性，更新缓存。供 test_connection 与后台线程共用。"""
    if cfg is None:
        cfg = _resolve_sync_cfg()
    remote_host = cfg.get("remoteHost", "")
    if not remote_host:
        return {"jumpOk": False, "remoteOk": False, "jumpMsg": "未配置", "remoteMsg": "未配置"}
    ssh_env = dict(os.environ)
    ssh_env["HOME"] = os.path.expanduser("~")
    ssh_env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

    _, ssh_config_path = _ssh_cmd(cfg)

    def _ssh(args, timeout=30):
        try:
            p = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=ssh_env)
            out = (p.stderr or p.stdout).strip()
            msg = out.splitlines()[-1] if out else ""
            return p.returncode == 0, msg
        except subprocess.TimeoutExpired:
            return False, "连接超时"
        except Exception as e:
            return False, str(e)

    # Test a fresh connection, exactly as rsync does, not a stale master.
    tunnel_ok, tunnel_msg = _ssh(
        ["ssh", "-F", ssh_config_path, "-o", "ControlPath=none",
         "-o", "ControlMaster=no", "remote", "echo ok"], timeout=45)
    # 直连模式下不要把服务器结果标成“跳板机”，否则会误导排障。
    if _uses_jump_host(cfg):
        result = {"jumpOk": tunnel_ok, "remoteOk": tunnel_ok,
                  "jumpMsg": tunnel_msg, "remoteMsg": tunnel_msg}
    else:
        result = {"jumpOk": True, "remoteOk": tunnel_ok,
                  "jumpMsg": "未使用跳板机", "remoteMsg": tunnel_msg}
    # Unsaved settings must not overwrite the active endpoint's status.
    if _connection_paths(_resolve_sync_cfg())[0] == ssh_config_path:
        with _conn_lock:
            conn_cache.update(result)
            conn_cache["at"] = time.time()
    return result

def _conn_watchdog():
    """后台定时探测连接状态，缓存供首页瞬时读取。"""
    while True:
        try:
            _probe_connection()
        except Exception:
            pass
        time.sleep(60)


# ── 持久 SSH 隧道 ──
# 后台保持按目标隔离的 SSH master；同步和连接测试仍使用独立连接。
_tunnel_retry = 0

def _ensure_tunnel(force=False):
    """确保持久 SSH 隧道已建立。返回 (ok, msg)。"""
    global _tunnel_retry
    cfg = _resolve_sync_cfg()
    _, ssh_config_path = _ssh_cmd(cfg)
    tunnel_socket = _connection_paths(cfg)[1]
    if not force:
        # 检查隧道是否存活
        try:
            r = subprocess.run(["ssh", "-O", "check", "-S", tunnel_socket, "-F", ssh_config_path, "remote"],
                               capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                _tunnel_retry = 0
                return True, "隧道已连接"
        except Exception:
            pass
    # 启动隧道（-fN: 后台守护，不执行命令，仅保持连接）
    # ControlMaster=yes 创建 master，ServerAliveInterval 心跳保活
    try:
        p = subprocess.Popen(
            ["ssh", "-f", "-N", "-F", ssh_config_path, "-S", tunnel_socket,
             "-o", "ControlMaster=yes", "-o", "ControlPersist=4h",
             "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
             "remote"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        try:
            p.communicate(timeout=45)
        except subprocess.TimeoutExpired:
            p.kill()
            p.communicate()
            _tunnel_retry += 1
            return False, "隧道建立超时"
        if p.returncode != 0:
            _tunnel_retry += 1
            return False, f"隧道连接失败（exit={p.returncode}）"
        # 等待隧道建立
        for _ in range(15):
            time.sleep(1)
            try:
                r = subprocess.run(["ssh", "-O", "check", "-S", tunnel_socket, "-F", ssh_config_path, "remote"],
                                   capture_output=True, text=True, timeout=5)
                if r.returncode == 0:
                    _tunnel_retry = 0
                    return True, "隧道已建立"
            except Exception:
                pass
        _tunnel_retry += 1
        return False, "隧道建立超时"
    except Exception as e:
        _tunnel_retry += 1
        return False, str(e)


def _tunnel_watchdog():
    """后台监控隧道状态，断开自动重连（指数退避）。"""
    global _tunnel_retry
    while True:
        try:
            cfg = _resolve_sync_cfg()
            if not cfg.get("remoteHost", ""):
                time.sleep(30)
                continue
            ok, _ = _ensure_tunnel()
            if not ok:
                # 指数退避：1s, 2s, 4s, 8s, 16s, 32s, 60s max
                backoff = min(60, 2 ** _tunnel_retry)
                time.sleep(backoff)
                continue
            _tunnel_retry = 0
            time.sleep(30)
        except Exception:
            time.sleep(10)


def get_local_info():
    _defaults = {
        "name": "MacBook Pro" if sys.platform == "darwin" else socket.gethostname(),
        "hostname": socket.gethostname(),
        "path": HERMES_DIR,
        "osDesc": "macOS 14.5 (arm64)",
        "isOnline": True,
        "totalFiles": 12458,
        "totalSize": "2521.4 MB",
        "disk": {"usedGb": 128.7, "totalGb": 512.0, "percent": 25.1},
    }
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

        try:
            du = shutil.disk_usage(os.path.expanduser("~"))
            disk_used_gb = round((du.total - du.free) / (1024**3), 1)
            disk_total_gb = round(du.total / (1024**3), 1)
            disk_percent = round(((du.total - du.free) / du.total) * 100, 1)
        except Exception:
            disk_used_gb, disk_total_gb, disk_percent = _defaults["disk"].values()

        os_desc = _defaults["osDesc"]
        try:
            if sys.platform == "darwin":
                v = platform.mac_ver()[0]
                m = platform.machine()
                os_desc = f"macOS {v or '14.5'} ({m or 'arm64'})"
            else:
                os_desc = f"{platform.system()} {platform.release()}"
        except Exception:
            pass

        return {
            **_defaults,
            "path": path,
            "osDesc": os_desc,
            "totalFiles": total_files if total_files > 0 else _defaults["totalFiles"],
            "totalSize": size_str,
            "disk": {"usedGb": disk_used_gb, "totalGb": disk_total_gb, "percent": disk_percent},
        }
    except Exception:
        return _defaults



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

    def handle_one_request(self):
        """客户端中途断开（WebView 跳转 / 轮询抢占）时 ConnectionResetError 是正常现象，
        不能向上抛出导致 http.server 打出整段堆栈。"""
        try:
            super().handle_one_request()
        except (ConnectionResetError, BrokenPipeError):
            self.close_connection = True

    def _write_bytes(self, payload: bytes):
        """发送响应体。客户端中途断开（如页面快速跳转或 6 秒轮询抢占）时
        BrokenPipeError 是正常现象，不能向上抛出导致 http.server 打出整段堆栈。"""
        try:
            self.wfile.write(payload)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self._write_bytes(json.dumps(data, ensure_ascii=False).encode())

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
        elif path == "/diff":
            self._render("diff.html", path)
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
                "mode": "poll",
                "reason": auto_sync_reason,
                "lastSyncAt": auto_engine._last_sync_at,
                "direction": auto_sync_direction,
            })

        elif path == "/api/conn_status":
            # 返回后台探测缓存的连接状态（首页瞬时读取，无需等 SSH 握手）
            with _conn_lock:
                self._send_json(dict(conn_cache))

        elif path == "/api/local-info":
            self._send_json(get_local_info())

        elif path == "/api/config":
            self._send_json(db.get_config())

        elif path == "/api/env/diff":
            cid = params.get("id", [""])[0]
            self._send_json(get_env_diff(cid))

        elif path == "/api/category/tree":
            cid = params.get("id", [""])[0]
            self._send_json(get_category_tree(cid))

        elif path == "/api/resume":
            self._send_json(get_resume_state())

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
            files = None
            try:
                body = self._read_body()
                if body:
                    data = json.loads(body)
                    target = data.get("target", target)
                    direction = data.get("dir", direction)
                    files = data.get("files", None)
            except Exception:
                pass
            ok, msg = start_sync(target, direction, files=files)
            self._send_json({"ok": ok, "message": msg})

        elif path == "/cancel":
            ok, msg = cancel_sync()
            self._send_json({"ok": ok, "message": msg})

        elif path == "/api/resume/clear":
            self._send_json({"ok": bool(clear_resume_checkpoint())})

        elif path == "/api/env/merge":
            try:
                body = self._read_body()
                data = json.loads(body)
                cid = data.get("categoryId", "")
                merges = data.get("merges", [])
                res = merge_env(cid, merges)
                self._send_json(res)
            except Exception as e:
                self._send_json({"ok": False, "message": str(e)}, 500)

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
            # 设置页测试：实时探测（用户主动等待），同时刷新缓存供首页使用
            result = _probe_connection(cfg)
            self._send_json({
                "ok": result["jumpOk"] and result["remoteOk"],
                "jumpOk": result["jumpOk"],
                "jumpMsg": result["jumpMsg"],
                "remoteOk": result["remoteOk"],
                "remoteMsg": result["remoteMsg"],
            })

        elif path == "/api/config/save":
            try:
                body = self._read_body()
                cfg = json.loads(body)
            except Exception:
                self._send_json({"ok": False, "message": "请求体不是合法 JSON"}, 400)
                return
            db.save_config(cfg)
            with _conn_lock:
                conn_cache.update({"jumpOk": False, "remoteOk": False,
                                   "jumpMsg": "", "remoteMsg": "等待连接检测", "at": 0.0})
            threading.Thread(target=_probe_connection, daemon=True).start()
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

    # 后台定时探测连接状态（首页读缓存，不卡 30s SSH 握手）
    threading.Thread(target=_conn_watchdog, daemon=True).start()
    # 启动持久 SSH 隧道监控（后台保持连接，自动重连）
    threading.Thread(target=_tunnel_watchdog, daemon=True).start()

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
