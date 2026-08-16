import Foundation

// MARK: - .env 三级处理引擎
class EnvSyncEngine {
    static let shared = EnvSyncEngine()

    // MARK: - 解析 .env 文件
    func parseEnvFile(_ content: String) -> [(key: String, value: String)] {
        var pairs: [(String, String)] = []
        for line in content.split(separator: "\n") {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if trimmed.isEmpty || trimmed.hasPrefix("#") { continue }
            if let eqIndex = trimmed.firstIndex(of: "=") {
                let key = String(trimmed[..<eqIndex]).trimmingCharacters(in: .whitespaces)
                var value = String(trimmed[trimmed.index(after: eqIndex)...]).trimmingCharacters(in: .whitespaces)
                if value.hasPrefix("\"") && value.hasSuffix("\"") {
                    value = String(value.dropFirst().dropLast())
                }
                pairs.append((key, value))
            }
        }
        return pairs
    }

    func parseEnvFile(at path: String) -> [(key: String, value: String)] {
        guard let content = try? String(contentsOfFile: path, encoding: .utf8) else { return [] }
        return parseEnvFile(content)
    }

    // MARK: - 读取远程 .env
    func fetchRemoteEnv(remotePath: String,
                        completion: @escaping ([(key: String, value: String)]?) -> Void) {
        DispatchQueue.global().async {
            let cfg = SyncConfig.shared
            let remoteCmd = "cat \(remotePath)/.env 2>/dev/null"
            let fullCmd = "ssh -i \(cfg.remoteKey) -o StrictHostKeyChecking=no \(cfg.remoteUser)@\(cfg.remoteHost) '\(remoteCmd)'"
            let args = ["-i", cfg.jumpKey, "-o", "StrictHostKeyChecking=no",
                        "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", cfg.jumpHost, fullCmd]
            let (output, code) = runSSH(args)
            if code != 0 { completion(nil); return }
            completion(self.parseEnvFile(output))
        }
    }

    // MARK: - 刷新 env_keys 表（从本地和远程 .env 解析）
    func refreshEnvKeys(localPath: String,
                        remotePath: String,
                        completion: @escaping (Bool) -> Void) {
        let localEnv = parseEnvFile(at: localPath + "/.env")
        fetchRemoteEnv(remotePath: remotePath) { remoteEnv in
            guard let remoteEnv = remoteEnv else {
                DispatchQueue.main.async { completion(false) }
                return
            }
            let cfg = SyncConfig.shared
            var allKeys = Set<String>()
            for (k, _) in localEnv { allKeys.insert(k) }
            for (k, _) in remoteEnv { allKeys.insert(k) }

            for key in allKeys {
                let lv = localEnv.first { $0.key == key }?.value ?? ""
                let rv = remoteEnv.first { $0.key == key }?.value ?? ""
                let shouldSync = !cfg.envExcludeKeys.contains(key)
                Database.shared.upsertEnvKey(key: key, localValue: lv, remoteValue: rv, shouldSync: shouldSync)
            }
            DispatchQueue.main.async { completion(true) }
        }
    }

    // MARK: - 合并 .env（级别 2：选择性 key 同步）
    func mergeEnv(local: [(key: String, value: String)],
                  remote: [(key: String, value: String)],
                  syncKeys: Set<String>,
                  excludeKeys: Set<String>) -> String {
        var localMap: [String: String] = [:]
        var remoteMap: [String: String] = [:]
        var keyOrder: [String] = []
        for (k, v) in local { if localMap[k] == nil { keyOrder.append(k) }; localMap[k] = v }
        for (k, v) in remote { if localMap[k] == nil && remoteMap[k] == nil { keyOrder.append(k) }; remoteMap[k] = v }

        var lines: [String] = []
        lines.append("# SyncMaster 合并 - \(DateFormatter.fullStamp.string(from: Date()))")
        lines.append("")

        for key in keyOrder {
            let lv = localMap[key] ?? ""
            let rv = remoteMap[key] ?? ""

            if excludeKeys.contains(key) {
                // 保留各自值（写入远程时保留远程值）
                lines.append("\(key)=\(rv.isEmpty ? lv : rv)")
            } else if syncKeys.contains(key) || syncKeys.isEmpty {
                // 同步本地值到远程
                lines.append("\(key)=\(lv)")
            } else {
                lines.append("\(key)=\(rv.isEmpty ? lv : rv)")
            }
        }
        return lines.joined(separator: "\n") + "\n"
    }

    // MARK: - 同步 .env
    func syncEnv(localPath: String,
                 remotePath: String,
                 completion: @escaping (Bool, String) -> Void) {
        let localEnv = parseEnvFile(at: localPath + "/.env")
        fetchRemoteEnv(remotePath: remotePath) { remoteEnv in
            guard let remoteEnv = remoteEnv else {
                DispatchQueue.main.async { completion(false, "无法读取远程 .env") }
                return
            }

            let envKeys = Database.shared.getEnvKeys()
            let syncKeys = Set(envKeys.filter { $0.shouldSync }.map { $0.key })
            let excludeKeys = Set(envKeys.filter { !$0.shouldSync }.map { $0.key })

            let mergedContent = self.mergeEnv(local: localEnv, remote: remoteEnv,
                                               syncKeys: syncKeys, excludeKeys: excludeKeys)

            // 写入临时文件并上传
            let tempFile = "/tmp/syncmaster-env-\(UUID().uuidString.prefix(8))"
            try? mergedContent.write(toFile: tempFile, atomically: true, encoding: .utf8)

            let cfg = SyncConfig.shared
            let cmd = "scp -i \(cfg.jumpKey) -o StrictHostKeyChecking=no \(tempFile) \(cfg.jumpHost):\(remotePath)/.env"
            let p = Process(); p.executableURL = URL(fileURLWithPath: "/bin/zsh")
            p.arguments = ["-c", cmd]
            let pipe = Pipe(); p.standardOutput = pipe; p.standardError = pipe
            do {
                try p.run(); p.waitUntilExit()
                try? FileManager.default.removeItem(atPath: tempFile)
                if p.terminationStatus == 0 {
                    Database.shared.addAudit(".env 同步", "合并 \(envKeys.count) 个 key")
                    DispatchQueue.main.async { completion(true, ".env 同步完成 ✅") }
                } else {
                    let err = String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
                    DispatchQueue.main.async { completion(false, ".env 同步失败: \(err)") }
                }
            } catch {
                DispatchQueue.main.async { completion(false, ".env 同步错误: \(error.localizedDescription)") }
            }
        }
    }

    // MARK: - 级别 3：多环境身份文件检查
    func checkMultiEnvFiles(localPath: String) -> [String] {
        let fm = FileManager.default
        var found: [String] = []
        for name in [".env.local", ".env.server"] {
            if fm.fileExists(atPath: localPath + "/" + name) {
                found.append(name)
            }
        }
        return found
    }

    // MARK: - 生成 .env.example
    func generateEnvExample(localPath: String) -> String {
        let env = parseEnvFile(at: localPath + "/.env")
        var lines: [String] = ["# .env.example - 公共配置模板（可安全同步）", ""]
        for (key, _) in env {
            lines.append("\(key)=")
        }
        return lines.joined(separator: "\n") + "\n"
    }
}
