import Foundation

// MARK: - 日期格式化
extension DateFormatter {
    static let fileStamp: DateFormatter = {
        let f = DateFormatter(); f.dateFormat = "yyyyMMdd-HHmmss"; return f
    }()
    static let logStamp: DateFormatter = {
        let f = DateFormatter(); f.dateFormat = "MM-dd HH:mm:ss"; return f
    }()
    static let fullStamp: DateFormatter = {
        let f = DateFormatter(); f.dateFormat = "MM-dd HH:mm:ss"; return f
    }()
}

// MARK: - 同步配置
struct SyncConfig: Codable {
    static var shared = SyncConfig()
    var jumpHost = "user@your-jump-host"
    var jumpKey = "/path/to/jump-key.pem"
    var remoteUser = "user"
    var remoteHost = "your-server-host"
    var remoteKey = "/home/user/server-key.pem"
    var remoteDir = "/home/user/remote-dir"
    var localDir = "~/your-local-dir"
    var defaultDeletePolicy = "noDelete"
    var envSyncEnabled = false
    var envSyncKeys: [String] = []
    var envExcludeKeys: [String] = ["BOT_ID", "API_KEY", "TELEGRAM_TOKEN", "DATABASE_PASSWORD"]
}

// MARK: - 设备信息
struct DeviceInfo {
    let name: String
    let path: String
    let isOnline: Bool
    let totalFiles: Int
    let totalSize: String
}

// MARK: - Diff 结果
struct DiffResult {
    let added: [String]
    let modified: [String]
    let deleted: [String]
    let conflicts: [String]
    var uploadPaths: [String]
    var downloadPaths: [String]

    var total: Int { added.count + modified.count + deleted.count + conflicts.count }

    static let empty = DiffResult(added: [], modified: [], deleted: [], conflicts: [],
                                  uploadPaths: [], downloadPaths: [])
}

// MARK: - 文件 Hash 信息
struct FileHash {
    let hash: String
    let size: Int64
    let modTime: TimeInterval
}

// MARK: - Manifest 条目
struct ManifestEntry: Codable {
    var path: String
    var localHash: String
    var remoteHash: String
    var syncHash: String
    var syncTime: String
    var status: String
    var fileSize: Int64

    static let empty = ManifestEntry(path: "", localHash: "", remoteHash: "",
                                     syncHash: "", syncTime: "", status: "synced", fileSize: 0)
}

// MARK: - 冲突解决
struct ConflictResolution {
    let path: String
    enum Action { case useLocal, useRemote, skip }
    let action: Action
}

// MARK: - 同步分类
struct SyncCategory: Codable {
    var id: String
    var name: String
    var localPath: String
    var remotePath: String
    var mode: String
    var isEnabled: Bool
    var lastSync: String
    var files: [String]
    var deletePolicy: String

    init(id: String, name: String, localPath: String, remotePath: String,
         mode: String, isEnabled: Bool, lastSync: String, files: [String] = [],
         deletePolicy: String = "noDelete") {
        self.id = id; self.name = name; self.localPath = localPath
        self.remotePath = remotePath; self.mode = mode; self.isEnabled = isEnabled
        self.lastSync = lastSync; self.files = files; self.deletePolicy = deletePolicy
    }

    static func defaults() -> [SyncCategory] {
        let cfg = SyncConfig.shared
        return [
            SyncCategory(id: "core", name: "核心程序", localPath: cfg.localDir + "/config.yaml",
                         remotePath: cfg.remoteDir, mode: "bidirectional", isEnabled: true, lastSync: "-"),
            SyncCategory(id: "skills", name: "Skills", localPath: cfg.localDir + "/skills",
                         remotePath: cfg.remoteDir + "/skills", mode: "toServer", isEnabled: true, lastSync: "-"),
            SyncCategory(id: "workspace", name: "Workspace", localPath: cfg.localDir,
                         remotePath: cfg.remoteDir, mode: "bidirectional", isEnabled: true, lastSync: "-"),
         ]
     }
}

// MARK: - 历史记录
struct HistoryEntry: Codable {
    var time: String
    var timestamp: Double
    var category: String
    var direction: String
    var fileCount: Int
    var totalSize: String
    var duration: String
    var result: String
}

// MARK: - 审计日志
struct AuditEntry: Codable {
    var time: String
    var timestamp: Double
    var action: String
    var detail: String
}

// MARK: - 同步进度
struct SyncProgress {
    var currentFile: String = ""
    var percent: Double = 0
    var speed: String = ""
    var uploaded: Int = 0
    var downloaded: Int = 0
    var totalFiles: Int = 0
    var doneFiles: Int { uploaded + downloaded }
}

// MARK: - 删除策略
enum DeletePolicy: String, CaseIterable {
    case noDelete
    case deleteToServer
    case deleteToLocal
    case bidirectional
    case ask

    var label: String {
        switch self {
        case .noDelete:       return "不自动删除（推荐）"
        case .deleteToServer: return "本地删除 → 同步删除服务器"
        case .deleteToLocal:  return "服务器删除 → 同步删除本地"
        case .bidirectional:  return "双向删除同步"
        case .ask:            return "发现删除时询问"
        }
    }

    var rsyncDeleteArg: String {
        switch self {
        case .noDelete:   return ""
        case .deleteToServer, .bidirectional, .ask: return "--delete"
        case .deleteToLocal: return "--delete"
        }
    }

    func shouldDeleteForDirection(_ direction: String) -> Bool {
        switch self {
        case .noDelete: return false
        case .deleteToServer: return direction != "toLocal"
        case .deleteToLocal: return direction != "toServer"
        case .bidirectional, .ask: return true
        }
    }
}

// MARK: - .syncignore
struct SyncIgnore {
    static var rules: [String] = [
        ".env", ".env.local", ".env.production", ".env.server",
        "bot_id", "bot_id.json",
        "secrets/", "*.key", "*.pem",
        "__pycache__/", "*.pyc",
        "node_modules/", ".git/", "*.log",
        "tmp/", "cache/"
    ]

    static func load() {
        let path = NSHomeDirectory() + "/.syncmaster-ignore"
        if let content = try? String(contentsOfFile: path, encoding: .utf8) {
            let lines = content.split(separator: "\n").map(String.init).filter { !$0.trimmingCharacters(in: .whitespaces).isEmpty }
            if !lines.isEmpty { rules = lines }
        }
    }

    static func save() {
        let path = NSHomeDirectory() + "/.syncmaster-ignore"
        try? rules.joined(separator: "\n").write(toFile: path, atomically: true, encoding: .utf8)
    }

    static var rsyncArgs: String { rules.map { "--exclude='\($0)'" }.joined(separator: " ") }

    static func shouldExclude(_ path: String) -> Bool {
        let name = (path as NSString).lastPathComponent
        for rule in rules {
            if rule.hasSuffix("/") {
                let dir = String(rule.dropLast())
                if path.contains("/\(dir)/") || name == dir { return true }
            } else if rule.hasPrefix("*") {
                let ext = String(rule.dropFirst())
                if name.hasSuffix(ext) { return true }
            } else if name == rule || path.contains("/\(rule)") {
                return true
            }
        }
        return false
    }
}
