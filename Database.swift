import Foundation
import SQLite3

// MARK: - SQLite 数据库封装
final class Database {
    static let shared = Database()
    private var db: OpaquePointer?
    private let queue = DispatchQueue(label: "syncmaster.db")
    private let dbPath: String

    private init() {
        let dir = NSHomeDirectory() + "/.syncmaster"
        try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
        dbPath = dir + "/db.sqlite"
    }

    func open() -> Bool {
        if db != nil { return true }
        let result = sqlite3_open_v2(dbPath, &db,
            SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX, nil)
        if result != SQLITE_OK {
            print("SQLite open failed: \(String(cString: sqlite3_errmsg(db)))")
            return false
        }
        migrate()
        migrateFromJSON()
        return true
    }

    // MARK: - 从旧 JSON 文件迁移
    private func migrateFromJSON() {
        let home = NSHomeDirectory()
        let oldConfig = home + "/.syncmaster-settings.json"
        let oldCats = home + "/.syncmaster-categories.json"
        let oldIgnore = home + "/.syncmaster-ignore"
        let oldHistory = home + "/.syncmaster-history.json"

        // 迁移配置
        if FileManager.default.fileExists(atPath: oldConfig) {
            if let data = try? Data(contentsOf: URL(fileURLWithPath: oldConfig)),
               let cfg = try? JSONDecoder().decode(SyncConfig.self, from: data) {
                // 检查 config 表是否已有数据
                let existing = getConfig()
                let isEmpty = existing.jumpHost == "user@your-jump-host"
                if isEmpty {
                    saveConfig(cfg)
                    print("SM: migrated config from JSON")
                }
            }
            try? FileManager.default.removeItem(atPath: oldConfig)
        }

        // 迁移分类
        if FileManager.default.fileExists(atPath: oldCats) {
            if let data = try? Data(contentsOf: URL(fileURLWithPath: oldCats)),
               let cats = try? JSONDecoder().decode([SyncCategory].self, from: data) {
                let dbCats = getCategories()
                if dbCats.isEmpty || dbCats.allSatisfy({ $0.localPath.isEmpty || $0.localPath.contains("your-") }) {
                    for c in cats { saveCategory(c) }
                    print("SM: migrated \(cats.count) categories from JSON")
                }
            }
            try? FileManager.default.removeItem(atPath: oldCats)
        }

        // 迁移 .syncignore
        if FileManager.default.fileExists(atPath: oldIgnore) {
            // SyncIgnore.load() 已经处理了，不需要额外操作
            // 但确保 .syncignore 内容写入
        }

        // 迁移历史
        if FileManager.default.fileExists(atPath: oldHistory) {
            if let data = try? Data(contentsOf: URL(fileURLWithPath: oldHistory)),
               let entries = try? JSONDecoder().decode([HistoryEntry].self, from: data) {
                for e in entries { addHistory(e) }
                print("SM: migrated \(entries.count) history entries from JSON")
            }
            try? FileManager.default.removeItem(atPath: oldHistory)
        }
    }

    // MARK: - 建表 / 迁移
    private func migrate() {
        exec("""
            CREATE TABLE IF NOT EXISTS manifest (
                category_id  TEXT NOT NULL,
                path         TEXT NOT NULL,
                local_hash   TEXT DEFAULT '',
                remote_hash  TEXT DEFAULT '',
                sync_hash    TEXT DEFAULT '',
                sync_time    TEXT DEFAULT '',
                status       TEXT DEFAULT 'synced',
                file_size    INTEGER DEFAULT 0,
                PRIMARY KEY (category_id, path)
            );
        """)
        exec("""
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
        """)
        exec("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   REAL NOT NULL,
                time_str    TEXT NOT NULL,
                action      TEXT NOT NULL,
                detail      TEXT DEFAULT ''
            );
        """)
        exec("""
            CREATE TABLE IF NOT EXISTS categories (
                id            TEXT PRIMARY KEY,
                name          TEXT NOT NULL,
                local_path    TEXT NOT NULL,
                remote_path   TEXT DEFAULT '',
                mode          TEXT DEFAULT 'bidirectional',
                is_enabled    INTEGER DEFAULT 1,
                last_sync     TEXT DEFAULT '-',
                files         TEXT DEFAULT '',
                delete_policy TEXT DEFAULT 'noDelete'
            );
        """)
        exec("""
            CREATE TABLE IF NOT EXISTS config (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
        """)
        exec("""
            CREATE TABLE IF NOT EXISTS env_keys (
                key          TEXT PRIMARY KEY,
                local_value  TEXT DEFAULT '',
                remote_value TEXT DEFAULT '',
                should_sync  INTEGER DEFAULT 1
            );
        """)
    }

    @discardableResult
    private func exec(_ sql: String) -> Bool {
        return queue.sync {
            var stmt: OpaquePointer?
            guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else {
                print("SQLite exec failed: \(String(cString: sqlite3_errmsg(db)))")
                return false
            }
            defer { sqlite3_finalize(stmt) }
            return sqlite3_step(stmt) == SQLITE_DONE
        }
    }

    // MARK: - Manifest CRUD
    func getManifest(categoryId: String) -> [ManifestEntry] {
        return queue.sync {
            var results: [ManifestEntry] = []
            var stmt: OpaquePointer?
            let sql = "SELECT path, local_hash, remote_hash, sync_hash, sync_time, status, file_size FROM manifest WHERE category_id = ?"
            guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return results }
            defer { sqlite3_finalize(stmt) }
            sqlite3_bind_text(stmt, 1, categoryId, -1, nil)
            while sqlite3_step(stmt) == SQLITE_ROW {
                let path = String(cString: sqlite3_column_text(stmt, 0))
                let lh = String(cString: sqlite3_column_text(stmt, 1))
                let rh = String(cString: sqlite3_column_text(stmt, 2))
                let sh = String(cString: sqlite3_column_text(stmt, 3))
                let st = String(cString: sqlite3_column_text(stmt, 4))
                let status = String(cString: sqlite3_column_text(stmt, 5))
                let size = sqlite3_column_int64(stmt, 6)
                results.append(ManifestEntry(path: path, localHash: lh, remoteHash: rh,
                                             syncHash: sh, syncTime: st, status: status, fileSize: size))
            }
            return results
        }
    }

    func upsertManifest(_ entry: ManifestEntry, categoryId: String) {
        let sql = """
            INSERT INTO manifest (category_id, path, local_hash, remote_hash, sync_hash, sync_time, status, file_size)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(category_id, path) DO UPDATE SET
                local_hash=excluded.local_hash, remote_hash=excluded.remote_hash,
                sync_hash=excluded.sync_hash, sync_time=excluded.sync_time,
                status=excluded.status, file_size=excluded.file_size
        """
        queue.sync {
            var stmt: OpaquePointer?
            guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return }
            defer { sqlite3_finalize(stmt) }
            sqlite3_bind_text(stmt, 1, categoryId, -1, nil)
            sqlite3_bind_text(stmt, 2, entry.path, -1, nil)
            sqlite3_bind_text(stmt, 3, entry.localHash, -1, nil)
            sqlite3_bind_text(stmt, 4, entry.remoteHash, -1, nil)
            sqlite3_bind_text(stmt, 5, entry.syncHash, -1, nil)
            sqlite3_bind_text(stmt, 6, entry.syncTime, -1, nil)
            sqlite3_bind_text(stmt, 7, entry.status, -1, nil)
            sqlite3_bind_int64(stmt, 8, entry.fileSize)
            sqlite3_step(stmt)
        }
    }

    func updateManifestStatus(categoryId: String, path: String, status: String) {
        let sql = "UPDATE manifest SET status = ? WHERE category_id = ? AND path = ?"
        queue.sync {
            var stmt: OpaquePointer?
            guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return }
            defer { sqlite3_finalize(stmt) }
            sqlite3_bind_text(stmt, 1, status, -1, nil)
            sqlite3_bind_text(stmt, 2, categoryId, -1, nil)
            sqlite3_bind_text(stmt, 3, path, -1, nil)
            sqlite3_step(stmt)
        }
    }

    func clearManifest(categoryId: String) {
        let sql = "DELETE FROM manifest WHERE category_id = ?"
        queue.sync {
            var stmt: OpaquePointer?
            guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return }
            defer { sqlite3_finalize(stmt) }
            sqlite3_bind_text(stmt, 1, categoryId, -1, nil)
            sqlite3_step(stmt)
        }
    }

    // MARK: - History CRUD
    func addHistory(_ entry: HistoryEntry) {
        let sql = "INSERT INTO sync_history (timestamp, time_str, category, direction, file_count, total_size, duration, result) VALUES (?,?,?,?,?,?,?,?)"
        queue.sync {
            var stmt: OpaquePointer?
            guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return }
            defer { sqlite3_finalize(stmt) }
            sqlite3_bind_double(stmt, 1, entry.timestamp)
            sqlite3_bind_text(stmt, 2, entry.time, -1, nil)
            sqlite3_bind_text(stmt, 3, entry.category, -1, nil)
            sqlite3_bind_text(stmt, 4, entry.direction, -1, nil)
            sqlite3_bind_int(stmt, 5, Int32(entry.fileCount))
            sqlite3_bind_text(stmt, 6, entry.totalSize, -1, nil)
            sqlite3_bind_text(stmt, 7, entry.duration, -1, nil)
            sqlite3_bind_text(stmt, 8, entry.result, -1, nil)
            sqlite3_step(stmt)
        }
    }

    func getHistory(filterIndex: Int = 0) -> [HistoryEntry] {
        return queue.sync {
            var results: [HistoryEntry] = []
            let now = Date().timeIntervalSince1970
            var sql = "SELECT time_str, timestamp, category, direction, file_count, total_size, duration, result FROM sync_history"
            switch filterIndex {
            case 1: sql += " WHERE timestamp > \(Calendar.current.startOfDay(for: Date()).timeIntervalSince1970)"
            case 2: sql += " WHERE timestamp > \(now - 7 * 86400)"
            case 3: sql += " WHERE timestamp > \(now - 30 * 86400)"
            default: break
            }
            sql += " ORDER BY timestamp DESC LIMIT 500"
            var stmt: OpaquePointer?
            guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return results }
            defer { sqlite3_finalize(stmt) }
            while sqlite3_step(stmt) == SQLITE_ROW {
                results.append(HistoryEntry(
                    time: String(cString: sqlite3_column_text(stmt, 0)),
                    timestamp: sqlite3_column_double(stmt, 1),
                    category: String(cString: sqlite3_column_text(stmt, 2)),
                    direction: String(cString: sqlite3_column_text(stmt, 3)),
                    fileCount: Int(sqlite3_column_int(stmt, 4)),
                    totalSize: String(cString: sqlite3_column_text(stmt, 5)),
                    duration: String(cString: sqlite3_column_text(stmt, 6)),
                    result: String(cString: sqlite3_column_text(stmt, 7))
                ))
            }
            return results
        }
    }

    func clearHistory() {
        exec("DELETE FROM sync_history")
    }

    // MARK: - Audit CRUD
    func addAudit(_ action: String, _ detail: String) {
        let sql = "INSERT INTO audit_log (timestamp, time_str, action, detail) VALUES (?,?,?,?)"
        queue.sync {
            var stmt: OpaquePointer?
            guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return }
            defer { sqlite3_finalize(stmt) }
            let now = Date()
            sqlite3_bind_double(stmt, 1, now.timeIntervalSince1970)
            sqlite3_bind_text(stmt, 2, DateFormatter.logStamp.string(from: now), -1, nil)
            sqlite3_bind_text(stmt, 3, action, -1, nil)
            sqlite3_bind_text(stmt, 4, detail, -1, nil)
            sqlite3_step(stmt)
        }
    }

    func getAudit() -> [AuditEntry] {
        return queue.sync {
            var results: [AuditEntry] = []
            let sql = "SELECT time_str, timestamp, action, detail FROM audit_log ORDER BY timestamp DESC LIMIT 500"
            var stmt: OpaquePointer?
            guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return results }
            defer { sqlite3_finalize(stmt) }
            while sqlite3_step(stmt) == SQLITE_ROW {
                results.append(AuditEntry(
                    time: String(cString: sqlite3_column_text(stmt, 0)),
                    timestamp: sqlite3_column_double(stmt, 1),
                    action: String(cString: sqlite3_column_text(stmt, 2)),
                    detail: String(cString: sqlite3_column_text(stmt, 3))
                ))
            }
            return results
        }
    }

    // MARK: - Categories CRUD
    func getCategories() -> [SyncCategory] {
        return queue.sync {
            var results: [SyncCategory] = []
            let sql = "SELECT id, name, local_path, remote_path, mode, is_enabled, last_sync, files, delete_policy FROM categories ORDER BY rowid"
            var stmt: OpaquePointer?
            guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return results }
            defer { sqlite3_finalize(stmt) }
            while sqlite3_step(stmt) == SQLITE_ROW {
                let id = String(cString: sqlite3_column_text(stmt, 0))
                let name = String(cString: sqlite3_column_text(stmt, 1))
                let local = String(cString: sqlite3_column_text(stmt, 2))
                let remote = String(cString: sqlite3_column_text(stmt, 3))
                let mode = String(cString: sqlite3_column_text(stmt, 4))
                let enabled = sqlite3_column_int(stmt, 5) == 1
                let lastSync = String(cString: sqlite3_column_text(stmt, 6))
                let filesStr = String(cString: sqlite3_column_text(stmt, 7))
                let deletePolicy = String(cString: sqlite3_column_text(stmt, 8))
                let files: [String] = filesStr.isEmpty ? [] :
                    (try? JSONDecoder().decode([String].self, from: Data(filesStr.utf8))) ?? []
                results.append(SyncCategory(id: id, name: name, localPath: local, remotePath: remote,
                    mode: mode, isEnabled: enabled, lastSync: lastSync, files: files, deletePolicy: deletePolicy))
            }
            if results.isEmpty {
                // 直接插入默认值（不调用 saveCategory 避免队列重入死锁）
                let defaults = SyncCategory.defaults()
                for c in defaults {
                    let filesData = (try? JSONEncoder().encode(c.files)).map { String(data: $0, encoding: .utf8) ?? "" } ?? "[]"
                    let insertSQL = "INSERT INTO categories (id, name, local_path, remote_path, mode, is_enabled, last_sync, files, delete_policy) VALUES (?,?,?,?,?,?,?,?,?)"
                    var insStmt: OpaquePointer?
                    if sqlite3_prepare_v2(db, insertSQL, -1, &insStmt, nil) == SQLITE_OK {
                        sqlite3_bind_text(insStmt, 1, c.id, -1, nil)
                        sqlite3_bind_text(insStmt, 2, c.name, -1, nil)
                        sqlite3_bind_text(insStmt, 3, c.localPath, -1, nil)
                        sqlite3_bind_text(insStmt, 4, c.remotePath, -1, nil)
                        sqlite3_bind_text(insStmt, 5, c.mode, -1, nil)
                        sqlite3_bind_int(insStmt, 6, c.isEnabled ? 1 : 0)
                        sqlite3_bind_text(insStmt, 7, c.lastSync, -1, nil)
                        sqlite3_bind_text(insStmt, 8, filesData, -1, nil)
                        sqlite3_bind_text(insStmt, 9, c.deletePolicy, -1, nil)
                        sqlite3_step(insStmt)
                        sqlite3_finalize(insStmt)
                    }
                }
                return defaults
            }
            return results
        }
    }

    func saveCategory(_ cat: SyncCategory) {
        let sql = """
            INSERT INTO categories (id, name, local_path, remote_path, mode, is_enabled, last_sync, files, delete_policy)
            VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name, local_path=excluded.local_path, remote_path=excluded.remote_path,
                mode=excluded.mode, is_enabled=excluded.is_enabled, last_sync=excluded.last_sync,
                files=excluded.files, delete_policy=excluded.delete_policy
        """
        queue.sync {
            var stmt: OpaquePointer?
            guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return }
            defer { sqlite3_finalize(stmt) }
            let filesData = (try? JSONEncoder().encode(cat.files)).map { String(data: $0, encoding: .utf8) ?? "" } ?? "[]"
            sqlite3_bind_text(stmt, 1, cat.id, -1, nil)
            sqlite3_bind_text(stmt, 2, cat.name, -1, nil)
            sqlite3_bind_text(stmt, 3, cat.localPath, -1, nil)
            sqlite3_bind_text(stmt, 4, cat.remotePath, -1, nil)
            sqlite3_bind_text(stmt, 5, cat.mode, -1, nil)
            sqlite3_bind_int(stmt, 6, cat.isEnabled ? 1 : 0)
            sqlite3_bind_text(stmt, 7, cat.lastSync, -1, nil)
            sqlite3_bind_text(stmt, 8, filesData, -1, nil)
            sqlite3_bind_text(stmt, 9, cat.deletePolicy, -1, nil)
            sqlite3_step(stmt)
        }
    }

    func deleteCategory(id: String) {
        let sql = "DELETE FROM categories WHERE id = ?"
        queue.sync {
            var stmt: OpaquePointer?
            guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return }
            defer { sqlite3_finalize(stmt) }
            sqlite3_bind_text(stmt, 1, id, -1, nil)
            sqlite3_step(stmt)
        }
        clearManifest(categoryId: id)
    }

    // MARK: - Config CRUD
    func getConfig() -> SyncConfig {
        var cfg = SyncConfig()
        return queue.sync {
            let sql = "SELECT key, value FROM config"
            var stmt: OpaquePointer?
            guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return cfg }
            defer { sqlite3_finalize(stmt) }
            while sqlite3_step(stmt) == SQLITE_ROW {
                let key = String(cString: sqlite3_column_text(stmt, 0))
                let val = String(cString: sqlite3_column_text(stmt, 1))
                switch key {
                case "jumpHost": cfg.jumpHost = val
                case "jumpKey": cfg.jumpKey = val
                case "remoteUser": cfg.remoteUser = val
                case "remoteHost": cfg.remoteHost = val
                case "remoteKey": cfg.remoteKey = val
                case "remoteDir": cfg.remoteDir = val
                case "localDir": cfg.localDir = val
                case "defaultDeletePolicy": cfg.defaultDeletePolicy = val
                case "envSyncEnabled": cfg.envSyncEnabled = (val == "1")
                case "envSyncKeys":
                    cfg.envSyncKeys = (try? JSONDecoder().decode([String].self, from: Data(val.utf8))) ?? []
                case "envExcludeKeys":
                    cfg.envExcludeKeys = (try? JSONDecoder().decode([String].self, from: Data(val.utf8))) ?? []
                default: break
                }
            }
            return cfg
        }
    }

    func saveConfig(_ cfg: SyncConfig) {
        queue.sync {
            let pairs: [(String, String)] = [
                ("jumpHost", cfg.jumpHost),
                ("jumpKey", cfg.jumpKey),
                ("remoteUser", cfg.remoteUser),
                ("remoteHost", cfg.remoteHost),
                ("remoteKey", cfg.remoteKey),
                ("remoteDir", cfg.remoteDir),
                ("localDir", cfg.localDir),
                ("defaultDeletePolicy", cfg.defaultDeletePolicy),
                ("envSyncEnabled", cfg.envSyncEnabled ? "1" : "0"),
                ("envSyncKeys", String(data: (try? JSONEncoder().encode(cfg.envSyncKeys)) ?? Data(), encoding: .utf8) ?? "[]"),
                ("envExcludeKeys", String(data: (try? JSONEncoder().encode(cfg.envExcludeKeys)) ?? Data(), encoding: .utf8) ?? "[]")
            ]
            for (k, v) in pairs {
                let sql = "INSERT INTO config (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value"
                var stmt: OpaquePointer?
                guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { continue }
                sqlite3_bind_text(stmt, 1, k, -1, nil)
                sqlite3_bind_text(stmt, 2, v, -1, nil)
                sqlite3_step(stmt)
                sqlite3_finalize(stmt)
            }
        }
    }

    // MARK: - Env Keys CRUD
    func getEnvKeys() -> [(key: String, localValue: String, remoteValue: String, shouldSync: Bool)] {
        return queue.sync {
            var results: [(String, String, String, Bool)] = []
            let sql = "SELECT key, local_value, remote_value, should_sync FROM env_keys ORDER BY key"
            var stmt: OpaquePointer?
            guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return results }
            defer { sqlite3_finalize(stmt) }
            while sqlite3_step(stmt) == SQLITE_ROW {
                let key = String(cString: sqlite3_column_text(stmt, 0))
                let lv = String(cString: sqlite3_column_text(stmt, 1))
                let rv = String(cString: sqlite3_column_text(stmt, 2))
                let sync = sqlite3_column_int(stmt, 3) == 1
                results.append((key, lv, rv, sync))
            }
            return results
        }
    }

    func upsertEnvKey(key: String, localValue: String, remoteValue: String, shouldSync: Bool) {
        let sql = """
            INSERT INTO env_keys (key, local_value, remote_value, should_sync)
            VALUES (?,?,?,?)
            ON CONFLICT(key) DO UPDATE SET
                local_value=excluded.local_value, remote_value=excluded.remote_value, should_sync=excluded.should_sync
        """
        queue.sync {
            var stmt: OpaquePointer?
            guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return }
            defer { sqlite3_finalize(stmt) }
            sqlite3_bind_text(stmt, 1, key, -1, nil)
            sqlite3_bind_text(stmt, 2, localValue, -1, nil)
            sqlite3_bind_text(stmt, 3, remoteValue, -1, nil)
            sqlite3_bind_int(stmt, 4, shouldSync ? 1 : 0)
            sqlite3_step(stmt)
        }
    }

    func deleteEnvKey(_ key: String) {
        let sql = "DELETE FROM env_keys WHERE key = ?"
        queue.sync {
            var stmt: OpaquePointer?
            guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { return }
            defer { sqlite3_finalize(stmt) }
            sqlite3_bind_text(stmt, 1, key, -1, nil)
            sqlite3_step(stmt)
        }
    }
}
