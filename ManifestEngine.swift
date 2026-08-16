import Foundation
import CryptoKit

// MARK: - Manifest 引擎（Hash 扫描 + Diff 计算）
class ManifestEngine {
    static let shared = ManifestEngine()

    // MARK: - 本地文件 Hash 扫描
    func scanLocalHashes(for category: SyncCategory) -> [String: FileHash] {
        var results: [String: FileHash] = [:]
        let basePath = category.localPath

        if !category.files.isEmpty {
            for file in category.files {
                if file.hasSuffix("/") {
                    scanDirectory(file, basePath: basePath, results: &results)
                } else {
                    if let fh = quickFileHash(file) {
                        let rel = relativePath(file, from: basePath)
                        results[rel] = fh
                    }
                }
            }
        } else {
            scanDirectory(basePath, basePath: basePath, results: &results)
        }
        return results
    }

    private func scanDirectory(_ dir: String, basePath: String, results: inout [String: FileHash]) {
        let fm = FileManager.default
        guard let enumerator = fm.enumerator(atPath: dir) else { return }
        for case let path as String in enumerator {
            let fullPath = (dir as NSString).appendingPathComponent(path)
            if SyncIgnore.shouldExclude(fullPath) { continue }
            if let attrs = try? fm.attributesOfItem(atPath: fullPath),
               attrs[.type] as? String == FileAttributeType.typeRegular.rawValue {
                if let fh = quickFileHash(fullPath) {
                    let rel = relativePath(fullPath, from: basePath)
                    results[rel] = fh
                }
            }
        }
    }

    private func relativePath(_ fullPath: String, from basePath: String) -> String {
        let normalized = (fullPath as NSString).standardizingPath
        let base = (basePath as NSString).standardizingPath
        if normalized.hasPrefix(base) {
            let rel = String(normalized.dropFirst(base.count))
            return rel.hasPrefix("/") ? String(rel.dropFirst()) : rel
        }
        return (fullPath as NSString).lastPathComponent
    }

    // MARK: - 远程文件 Hash 扫描
    func scanRemoteHashes(for category: SyncCategory,
                          progress: @escaping (String) -> Void,
                          completion: @escaping ([String: FileHash]?) -> Void) {
        DispatchQueue.global().async {
            let cfg = SyncConfig.shared
            let remoteDir = category.remotePath.isEmpty ? cfg.remoteDir : category.remotePath

            let remoteCmd = "cd '\(remoteDir)' && find . -type f ! -path '*/.git/*' -exec sha256sum {} + 2>/dev/null"
            let fullCmd = "ssh -i \(cfg.remoteKey) -o StrictHostKeyChecking=no -o BatchMode=yes \(cfg.remoteUser)@\(cfg.remoteHost) '\(remoteCmd)'"
            let sshArgs = ["-i", cfg.jumpKey, "-o", "StrictHostKeyChecking=no",
                           "-o", "ConnectTimeout=15", "-o", "BatchMode=yes", cfg.jumpHost, fullCmd]

            progress("正在扫描远程文件...")
            let (output, exitCode) = runSSH(sshArgs)
            if exitCode != 0 && output.isEmpty {
                completion(nil); return
            }

            var results: [String: FileHash] = [:]
            for line in output.split(separator: "\n") {
                let parts = line.split(separator: " ", maxSplits: 1, omittingEmptySubsequences: true)
                guard parts.count >= 2 else { continue }
                let hash = String(parts[0])
                var pathStr = String(parts[1]).trimmingCharacters(in: .whitespaces)
                if pathStr.hasPrefix("./") { pathStr = String(pathStr.dropFirst(2)) }
                if SyncIgnore.shouldExclude(pathStr) { continue }
                results[pathStr] = FileHash(hash: hash, size: 0, modTime: 0)
            }
            progress("远程扫描完成：\(results.count) 个文件")
            completion(results)
        }
    }

    // MARK: - Diff 计算（核心）
    func computeDiff(category: SyncCategory,
                     localHashes: [String: FileHash],
                     remoteHashes: [String: FileHash]) -> DiffResult {
        let manifest = Database.shared.getManifest(categoryId: category.id)
        var manifestMap: [String: ManifestEntry] = [:]
        for m in manifest { manifestMap[m.path] = m }

        let allPaths = Set(localHashes.keys).union(Set(remoteHashes.keys))

        var added: [String] = []
        var modified: [String] = []
        var deleted: [String] = []
        var conflicts: [String] = []
        var uploadPaths: [String] = []
        var downloadPaths: [String] = []

        for path in allPaths.sorted() {
            let local = localHashes[path]
            let remote = remoteHashes[path]
            let syncEntry = manifestMap[path]
            let syncHash = syncEntry?.syncHash ?? ""

            if local == nil && remote != nil {
                deleted.append(path)
                Database.shared.updateManifestStatus(categoryId: category.id, path: path, status: "deleted")
            }
            else if local != nil && remote == nil {
                added.append(path)
                uploadPaths.append(path)
                Database.shared.updateManifestStatus(categoryId: category.id, path: path, status: "added")
            }
            else if let lh = local?.hash, let rh = remote?.hash {
                if syncHash.isEmpty {
                    // 首次扫描：以当前状态为基准
                    Database.shared.upsertManifest(ManifestEntry(
                        path: path, localHash: lh, remoteHash: rh, syncHash: rh,
                        syncTime: DateFormatter.logStamp.string(from: Date()),
                        status: "synced", fileSize: local?.size ?? 0
                    ), categoryId: category.id)
                }
                else if lh != syncHash && rh == syncHash {
                    // 本地变了，服务器没变 → 上传
                    modified.append(path)
                    uploadPaths.append(path)
                    Database.shared.updateManifestStatus(categoryId: category.id, path: path, status: "localModified")
                }
                else if lh == syncHash && rh != syncHash {
                    // 服务器变了，本地没变 → 下载
                    modified.append(path)
                    downloadPaths.append(path)
                    Database.shared.updateManifestStatus(categoryId: category.id, path: path, status: "remoteModified")
                }
                else if lh != syncHash && rh != syncHash && lh != rh {
                    // 两边都变了且不同 → 真冲突
                    conflicts.append(path)
                    Database.shared.updateManifestStatus(categoryId: category.id, path: path, status: "conflict")
                }
                // else: 两边都没变 → synced（不处理）
            }
        }

        return DiffResult(added: added, modified: modified, deleted: deleted, conflicts: conflicts,
                          uploadPaths: uploadPaths, downloadPaths: downloadPaths)
    }

    // MARK: - 同步完成后更新 Manifest
    func updateManifestAfterSync(category: SyncCategory,
                                  localHashes: [String: FileHash],
                                  remoteHashes: [String: FileHash]) {
        let now = DateFormatter.logStamp.string(from: Date())
        for (path, lh) in localHashes {
            let rh = remoteHashes[path]?.hash ?? ""
            let syncHash = rh.isEmpty ? lh.hash : rh
            Database.shared.upsertManifest(ManifestEntry(
                path: path, localHash: lh.hash, remoteHash: rh, syncHash: syncHash,
                syncTime: now, status: "synced", fileSize: lh.size
            ), categoryId: category.id)
        }
        // 标记远程有但本地无的文件
        for (path, _) in remoteHashes where localHashes[path] == nil {
            let rh = remoteHashes[path]!.hash
            Database.shared.upsertManifest(ManifestEntry(
                path: path, localHash: "", remoteHash: rh, syncHash: rh,
                syncTime: now, status: "synced", fileSize: 0
            ), categoryId: category.id)
        }
    }

    // MARK: - 获取分类统计
    func getCategoryStats(_ categoryId: String) -> (total: Int, synced: Int, pending: Int, conflicts: Int) {
        let manifest = Database.shared.getManifest(categoryId: categoryId)
        let synced = manifest.filter { $0.status == "synced" }.count
        let pending = manifest.filter { $0.status == "localModified" || $0.status == "remoteModified" || $0.status == "added" }.count
        let conflicts = manifest.filter { $0.status == "conflict" }.count
        return (manifest.count, synced, pending, conflicts)
    }
}
