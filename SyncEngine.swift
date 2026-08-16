import Foundation

// MARK: - 同步引擎
class SyncEngine {
    enum SyncDirection: String {
        case toServer, toLocal, bidirectional
        var label: String {
            switch self {
            case .toServer: return "↑ 同步到服务器"
            case .toLocal: return "↓ 同步到本地"
            case .bidirectional: return "↔ 智能双向同步"
            }
        }
    }
    enum State { case idle, scanning, preview, syncing, done }

    // MARK: - 设备信息
    func getLocalInfo() -> DeviceInfo {
        let fm = FileManager.default
        let name = Host.current().localizedName ?? "MacBook Pro"
        var fileCount = 0; var totalSize: UInt64 = 0
        let dir = SyncConfig.shared.localDir
        if let enumerator = fm.enumerator(atPath: dir) {
            for case let path as String in enumerator {
                let fullPath = (dir as NSString).appendingPathComponent(path)
                if let attrs = try? fm.attributesOfItem(atPath: fullPath),
                   attrs[.type] as? String == FileAttributeType.typeRegular.rawValue {
                    fileCount += 1; totalSize += (attrs[.size] as? UInt64) ?? 0
                }
            }
        }
        return DeviceInfo(name: name, path: dir, isOnline: true, totalFiles: fileCount,
                          totalSize: ByteCountFormatter.string(fromByteCount: Int64(totalSize), countStyle: .file))
    }

    func checkServerOnline() -> Bool {
        let cfg = SyncConfig.shared
        let args = ["-i", cfg.jumpKey, "-o", "StrictHostKeyChecking=no",
                    "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", cfg.jumpHost, "echo ok"]
        let (_, code) = runSSH(args)
        return code == 0
    }

    func getRemoteInfo(completion: @escaping (DeviceInfo?) -> Void) {
        DispatchQueue.global().async {
            let cfg = SyncConfig.shared
            let remoteCmd = "find \(cfg.remoteDir) -type f 2>/dev/null | wc -l && du -sh \(cfg.remoteDir) 2>/dev/null | cut -f1"
            let fullCmd = "ssh -i \(cfg.remoteKey) -o StrictHostKeyChecking=no \(cfg.remoteUser)@\(cfg.remoteHost) '\(remoteCmd)'"
            let args = ["-i", cfg.jumpKey, "-o", "StrictHostKeyChecking=no",
                        "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", cfg.jumpHost, fullCmd]
            let (output, code) = runSSH(args)
            if code != 0 { completion(nil); return }
            let lines = output.split(separator: "\n").map(String.init)
            let fc = Int(lines.first?.trimmingCharacters(in: .whitespaces) ?? "0") ?? 0
            let sz = lines.count > 1 ? lines[1].trimmingCharacters(in: .whitespaces) : "0B"
            completion(DeviceInfo(name: "Ubuntu 24.04", path: cfg.remoteDir, isOnline: true,
                                  totalFiles: fc, totalSize: sz))
        }
    }

    // MARK: - 差异扫描（使用 Manifest + Hash）
    func scanDiff(for category: SyncCategory,
                  progress: @escaping (String) -> Void,
                  completion: @escaping (DiffResult?) -> Void) {
        DispatchQueue.global().async {
            progress("正在扫描本地文件...")
            let localHashes = ManifestEngine.shared.scanLocalHashes(for: category)

            ManifestEngine.shared.scanRemoteHashes(for: category, progress: progress) { remoteHashes in
                guard let remoteHashes = remoteHashes else {
                    DispatchQueue.main.async { completion(nil) }
                    return
                }
                progress("正在计算差异...")
                let diff = ManifestEngine.shared.computeDiff(category: category,
                                                              localHashes: localHashes,
                                                              remoteHashes: remoteHashes)
                DispatchQueue.main.async { completion(diff) }
            }
        }
    }

    // MARK: - 执行同步
    func runSync(for category: SyncCategory,
                 direction: SyncDirection,
                 resolutions: [ConflictResolution]? = nil,
                 progressCallback: @escaping (String) -> Void,
                 progressUpdate: @escaping (SyncProgress) -> Void,
                 completion: @escaping (Bool, String, Int, String) -> Void) {
        DispatchQueue.global().async {
            let cfg = SyncConfig.shared
            let backupDir = NSHomeDirectory() + "/.syncmaster-backups/\(DateFormatter.fileStamp.string(from: Date()))-\(category.name)"
            try? FileManager.default.createDirectory(atPath: backupDir, withIntermediateDirectories: true)

            let deletePolicy = DeletePolicy(rawValue: category.deletePolicy) ?? .noDelete
            let shouldDelete = deletePolicy.shouldDeleteForDirection(direction.rawValue)
            let deleteArg = shouldDelete ? deletePolicy.rsyncDeleteArg : ""

            let sshOpts = "ssh -i \(cfg.jumpKey) -o StrictHostKeyChecking=no -o ServerAliveInterval=10"
            let ignoreArgs = SyncIgnore.rsyncArgs
            var totalFiles: Int = 0
            var totalSizeStr = "0B"

            // 如果有自定义文件列表，逐文件同步
            if !category.files.isEmpty {
                let remoteBase = category.remotePath.isEmpty ? cfg.remoteDir : category.remotePath
                for file in category.files {
                    let isDir = file.hasSuffix("/")
                    let fileName = (file as NSString).lastPathComponent
                    let remoteDest = "\(cfg.jumpHost):\(remoteBase)/"
                    let cmd: String
                    if direction == .toLocal {
                        cmd = "rsync -avz \(deleteArg) \(ignoreArgs) --info=progress2 -e '\(sshOpts)' \(cfg.jumpHost):\(remoteBase)/\(fileName) \(file)"
                    } else {
                        let src = isDir ? file : file
                        cmd = "rsync -avz \(deleteArg) \(ignoreArgs) --info=progress2 -e '\(sshOpts)' \(src) \(remoteDest)"
                    }
                    totalFiles += 1
                    progressCallback("同步: \(fileName)")
                    let p = Process(); p.executableURL = URL(fileURLWithPath: "/bin/zsh")
                    p.arguments = ["-c", cmd]
                    let pipe = Pipe(); p.standardOutput = pipe; p.standardError = pipe
                    do {
                        try p.run(); p.waitUntilExit()
                    } catch { completion(false, "同步错误: \(error.localizedDescription)", 0, "0s"); return }
                }
            } else {
                // 整目录同步
                let localSrc = category.localPath.hasSuffix("/") ? category.localPath : category.localPath + "/"
                let remoteDest = "\(cfg.jumpHost):/tmp/hermes-sync-cat/"
                let cmd: String
                switch direction {
                case .toServer, .bidirectional:
                    cmd = "rsync -avz \(deleteArg) --info=progress2 \(ignoreArgs) -e '\(sshOpts)' \(localSrc) \(remoteDest)"
                case .toLocal:
                    cmd = "rsync -avz \(deleteArg) --info=progress2 \(ignoreArgs) -e '\(sshOpts)' \(remoteDest) \(localSrc)"
                }

                let p = Process(); p.executableURL = URL(fileURLWithPath: "/bin/zsh")
                p.arguments = ["-c", cmd]
                let pipe = Pipe(); p.standardOutput = pipe; p.standardError = pipe
                let startTime = Date()
                do {
                    try p.run()
                    var prog = SyncProgress()
                    let handle = pipe.fileHandleForReading
                    handle.readabilityHandler = { h in
                        let data = h.availableData
                        if let s = String(data: data, encoding: .utf8), !s.isEmpty {
                            for raw in s.split(separator: "\n") {
                                let str = String(raw).trimmingCharacters(in: .whitespaces)
                                if str.isEmpty { continue }
                                if str.contains("%") {
                                    if let pctMatch = str.range(of: #"(\d{1,3})\s*%"#, options: .regularExpression) {
                                        let pctStr = str[pctMatch].replacingOccurrences(of: "%", with: "").trimmingCharacters(in: .whitespaces)
                                        if let pct = Double(pctStr) { prog.percent = pct }
                                    }
                                    let parts = str.split(separator: " ").map(String.init)
                                    for (i, part) in parts.enumerated() {
                                        if part.contains("%"), i + 1 < parts.count, parts[i+1].contains("/s") {
                                            prog.speed = parts[i+1]
                                        }
                                    }
                                    if let filePart = str.split(separator: "%").first?.split(separator: " ").last {
                                        prog.currentFile = String(filePart)
                                    }
                                    progressUpdate(prog)
                                }
                                else if str.hasPrefix(">f") || str.hasPrefix("<f") {
                                    let isUpload = str.hasPrefix(">")
                                    let parts = str.split(separator: " ").map(String.init)
                                    if parts.count >= 2 {
                                        let file = parts[1].trimmingCharacters(in: .whitespaces)
                                        if !file.isEmpty && !file.contains("/") && file.count < 80 { prog.currentFile = file }
                                        if isUpload { prog.uploaded += 1 } else { prog.downloaded += 1 }
                                        progressUpdate(prog)
                                    }
                                }
                                // 解析 total size
                                if str.hasPrefix("Total file size:") {
                                    let parts = str.split(separator: " ").map(String.init)
                                    if parts.count >= 4 { totalSizeStr = parts[3] }
                                }
                                if !str.hasPrefix(".") && !str.hasPrefix("building") && !str.hasPrefix("sending") &&
                                   !str.hasPrefix("receiving") && !str.hasPrefix("sent ") && !str.hasPrefix("total ") &&
                                   !str.hasPrefix(">f") && !str.hasPrefix("<f") {
                                    progressCallback(str)
                                }
                            }
                        }
                    }
                    p.waitUntilExit(); handle.readabilityHandler = nil

                    // 备份
                    let backupCmd = "rsync -aq \(ignoreArgs) \(localSrc) \(backupDir)/ 2>/dev/null"
                    let bp = Process(); bp.executableURL = URL(fileURLWithPath: "/bin/zsh")
                    bp.arguments = ["-c", backupCmd]
                    try? bp.run(); bp.waitUntilExit()

                    let duration = String(format: "%.1fs", Date().timeIntervalSince(startTime))
                    if p.terminationStatus == 0 {
                        // 更新 Manifest
                        let localHashes = ManifestEngine.shared.scanLocalHashes(for: category)
                        ManifestEngine.shared.scanRemoteHashes(for: category, progress: { _ in }, completion: { remoteHashes in
                            if let rh = remoteHashes {
                                ManifestEngine.shared.updateManifestAfterSync(category: category, localHashes: localHashes, remoteHashes: rh)
                            }
                        })
                        completion(true, "同步完成 ✅", prog.doneFiles, duration)
                    }
                    else {
                        completion(false, "同步失败 (exit code: \(p.terminationStatus))", prog.doneFiles, duration)
                    }
                } catch { completion(false, "同步错误: \(error.localizedDescription)", 0, "0s") }
                return
            }

            // 自定义文件列表同步完成
            let duration = "0s"
            let localHashes = ManifestEngine.shared.scanLocalHashes(for: category)
            ManifestEngine.shared.scanRemoteHashes(for: category, progress: { _ in }, completion: { remoteHashes in
                if let rh = remoteHashes {
                    ManifestEngine.shared.updateManifestAfterSync(category: category, localHashes: localHashes, remoteHashes: rh)
                }
            })
            completion(true, "同步完成 ✅", totalFiles, duration)
        }
    }

    // MARK: - 备份
    func listBackups() -> [(String, String)] {
        let dir = NSHomeDirectory() + "/.syncmaster-backups"
        guard let items = try? FileManager.default.contentsOfDirectory(atPath: dir) else { return [] }
        return items.sorted().reversed().map { (dir + "/" + $0, $0) }
    }

    func rollback(from backupPath: String, to localPath: String, completion: @escaping (Bool, String) -> Void) {
        DispatchQueue.global().async {
            let dest = localPath.hasSuffix("/") ? localPath : localPath + "/"
            let cmd = "rsync -aq \(backupPath)/ \(dest) 2>/dev/null"
            let p = Process(); p.executableURL = URL(fileURLWithPath: "/bin/zsh")
            p.arguments = ["-c", cmd]
            do {
                try p.run(); p.waitUntilExit()
                completion(p.terminationStatus == 0, p.terminationStatus == 0 ? "回滚成功 ✅" : "回滚失败")
            } catch { completion(false, "回滚错误: \(error.localizedDescription)") }
        }
    }
}
