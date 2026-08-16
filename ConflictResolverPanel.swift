import Cocoa

// MARK: - 冲突解决窗口
class ConflictResolverWindow {

    struct ConflictItem {
        let path: String
        let localTime: String
        let remoteTime: String
    }

    private let window: NSWindow
    private let conflicts: [ConflictItem]
    private var resolutions: [String: ConflictResolution.Action] = [:]
    private var onComplete: (([ConflictResolution]) -> Void)?

    init(conflicts: [ConflictItem], onComplete: @escaping ([ConflictResolution]) -> Void) {
        self.conflicts = conflicts
        self.onComplete = onComplete

        let height = CGFloat(120 + conflicts.count * 80)
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 600, height: height),
                          styleMask: [.titled, .closable],
                          backing: .buffered, defer: false)
        window.title = "冲突解决"
        window.center()
        window.isReleasedWhenClosed = false

        let content = NSView(frame: NSRect(x: 0, y: 0, width: 600, height: height))
        content.wantsLayer = true
        content.layer?.backgroundColor = NSColor(red: 0.05, green: 0.07, blue: 0.1, alpha: 1).cgColor
        window.contentView = content

        let titleLbl = makeLabel("⚠️ 检测到 \(conflicts.count) 个冲突文件", size: 16, weight: .bold, color: NSColor(red: 0.9, green: 0.6, blue: 0, alpha: 1))
        titleLbl.frame = CGRect(x: 20, y: height - 36, width: 400, height: 24)
        content.addSubview(titleLbl)

        let hint = makeLabel("请为每个冲突文件选择处理方式", size: 11, weight: .regular, color: NSColor(white: 0.5, alpha: 1))
        hint.frame = CGRect(x: 20, y: height - 56, width: 400, height: 16)
        content.addSubview(hint)

        // 全部使用本地 / 全部使用服务器
        let allLocalBtn = NSButton(title: "全部使用本地", target: self, action: #selector(allUseLocal))
        allLocalBtn.bezelStyle = .rounded; allLocalBtn.font = .systemFont(ofSize: 10, weight: .medium)
        allLocalBtn.frame = CGRect(x: 340, y: height - 40, width: 110, height: 22)
        allLocalBtn.contentTintColor = NSColor(red: 0, green: 0.7, blue: 1, alpha: 1)
        content.addSubview(allLocalBtn)

        let allRemoteBtn = NSButton(title: "全部使用服务器", target: self, action: #selector(allUseRemote))
        allRemoteBtn.bezelStyle = .rounded; allRemoteBtn.font = .systemFont(ofSize: 10, weight: .medium)
        allRemoteBtn.frame = CGRect(x: 460, y: height - 40, width: 120, height: 22)
        allRemoteBtn.contentTintColor = NSColor(red: 0.3, green: 0.8, blue: 0.4, alpha: 1)
        content.addSubview(allRemoteBtn)

        // 冲突列表
        var y: CGFloat = height - 80
        for (i, item) in conflicts.enumerated() {
            y -= 80
            createConflictRow(item, index: i, y: y, in: content)
        }

        // 底部按钮
        let cancelBtn = NSButton(title: "取消", target: self, action: #selector(cancelClicked))
        cancelBtn.bezelStyle = .rounded; cancelBtn.font = .systemFont(ofSize: 12)
        cancelBtn.frame = CGRect(x: 340, y: 16, width: 90, height: 30)
        content.addSubview(cancelBtn)

        let confirmBtn = NSButton(title: "确认", target: self, action: #selector(confirmClicked))
        confirmBtn.bezelStyle = .rounded; confirmBtn.font = .systemFont(ofSize: 12, weight: .semibold)
        confirmBtn.frame = CGRect(x: 440, y: 16, width: 140, height: 30)
        confirmBtn.contentTintColor = NSColor(red: 0.3, green: 0.8, blue: 0.4, alpha: 1)
        content.addSubview(confirmBtn)

        objc_setAssociatedObject(confirmBtn, "window", window, .OBJC_ASSOCIATION_RETAIN)
    }

    private func createConflictRow(_ item: ConflictItem, index: Int, y: CGFloat, in content: NSView) {
        let row = makeCardFrame(NSRect(x: 20, y: y, width: 560, height: 70))

        let icon = makeLabel("📄", size: 14, weight: .regular, color: .white)
        icon.frame = CGRect(x: 12, y: 44, width: 24, height: 20)
        row.addSubview(icon)

        let pathLbl = makeLabel(item.path, size: 11, weight: .semibold, color: .white)
        pathLbl.frame = CGRect(x: 36, y: 46, width: 400, height: 18)
        row.addSubview(pathLbl)

        let timeLbl = makeLabel("本地: \(item.localTime)  |  服务器: \(item.remoteTime)", size: 9, weight: .regular, color: NSColor(white: 0.5, alpha: 1))
        timeLbl.frame = CGRect(x: 36, y: 28, width: 400, height: 16)
        row.addSubview(timeLbl)

        let localBtn = NSButton(title: "使用本地", target: self, action: #selector(useLocalClicked(_:)))
        localBtn.bezelStyle = .rounded; localBtn.font = .systemFont(ofSize: 9, weight: .medium)
        localBtn.frame = CGRect(x: 36, y: 6, width: 72, height: 20)
        localBtn.identifier = NSUserInterfaceItemIdentifier(item.path)
        localBtn.contentTintColor = NSColor(red: 0, green: 0.7, blue: 1, alpha: 1)
        row.addSubview(localBtn)

        let remoteBtn = NSButton(title: "使用服务器", target: self, action: #selector(useRemoteClicked(_:)))
        remoteBtn.bezelStyle = .rounded; remoteBtn.font = .systemFont(ofSize: 9, weight: .medium)
        remoteBtn.frame = CGRect(x: 114, y: 6, width: 84, height: 20)
        remoteBtn.identifier = NSUserInterfaceItemIdentifier(item.path)
        remoteBtn.contentTintColor = NSColor(red: 0.3, green: 0.8, blue: 0.4, alpha: 1)
        row.addSubview(remoteBtn)

        let diffBtn = NSButton(title: "查看差异", target: self, action: #selector(viewDiffClicked(_:)))
        diffBtn.bezelStyle = .rounded; diffBtn.font = .systemFont(ofSize: 9)
        diffBtn.frame = CGRect(x: 204, y: 6, width: 72, height: 20)
        diffBtn.identifier = NSUserInterfaceItemIdentifier(item.path)
        diffBtn.contentTintColor = NSColor(white: 0.6, alpha: 1)
        row.addSubview(diffBtn)

        let skipBtn = NSButton(title: "跳过", target: self, action: #selector(skipClicked(_:)))
        skipBtn.bezelStyle = .rounded; skipBtn.font = .systemFont(ofSize: 9)
        skipBtn.frame = CGRect(x: 284, y: 6, width: 52, height: 20)
        skipBtn.identifier = NSUserInterfaceItemIdentifier(item.path)
        skipBtn.contentTintColor = NSColor(white: 0.5, alpha: 1)
        row.addSubview(skipBtn)

        content.addSubview(row)
    }

    func show() {
        window.makeKeyAndOrderFront(nil)
        NSApp.runModal(for: window)
    }

    // MARK: - Actions
    @objc private func useLocalClicked(_ sender: NSButton) {
        if let path = sender.identifier?.rawValue {
            resolutions[path] = .useLocal
            sender.contentTintColor = NSColor(red: 0, green: 0.9, blue: 0.4, alpha: 1)
        }
    }

    @objc private func useRemoteClicked(_ sender: NSButton) {
        if let path = sender.identifier?.rawValue {
            resolutions[path] = .useRemote
            sender.contentTintColor = NSColor(red: 0.3, green: 1.0, blue: 0.5, alpha: 1)
        }
    }

    @objc private func skipClicked(_ sender: NSButton) {
        if let path = sender.identifier?.rawValue {
            resolutions[path] = .skip
            sender.contentTintColor = NSColor(white: 0.3, alpha: 1)
        }
    }

    @objc private func viewDiffClicked(_ sender: NSButton) {
        guard let path = sender.identifier?.rawValue else { return }
        let cfg = SyncConfig.shared
        let localFile = "\(cfg.localDir)/\(path)"
        let tempFile = "/tmp/syncmaster-diff-\(UUID().uuidString.prefix(8))"
        let remoteFile = "\(cfg.remoteDir)/\(path)"

        // 下载远程文件
        let remoteCmd = "ssh -i \(cfg.remoteKey) -o StrictHostKeyChecking=no \(cfg.remoteUser)@\(cfg.remoteHost) 'cat \(remoteFile)'"
        let args = ["-i", cfg.jumpKey, "-o", "StrictHostKeyChecking=no", "-o", "BatchMode=yes", cfg.jumpHost, remoteCmd]
        let (output, code) = runSSH(args)
        if code == 0 {
            try? output.write(toFile: tempFile, atomically: true, encoding: .utf8)
            let diffP = Process(); diffP.executableURL = URL(fileURLWithPath: "/usr/bin/diff")
            diffP.arguments = ["-u", localFile, tempFile]
            let pipe = Pipe(); diffP.standardOutput = pipe; diffP.standardError = pipe
            try? diffP.run(); diffP.waitUntilExit()
            let diffOutput = String(data: pipe.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? "无差异或无法比较"
            showDiffWindow(title: path, content: diffOutput)
            try? FileManager.default.removeItem(atPath: tempFile)
        }
    }

    private func showDiffWindow(title: String, content: String) {
        let w = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 700, height: 500),
                         styleMask: [.titled, .closable], backing: .buffered, defer: false)
        w.title = "差异: \(title)"
        w.center()
        let tv = NSTextView(frame: NSRect(x: 0, y: 0, width: 700, height: 500))
        tv.isEditable = false
        tv.backgroundColor = NSColor(red: 0.05, green: 0.07, blue: 0.1, alpha: 1)
        tv.textColor = NSColor(white: 0.8, alpha: 1)
        tv.font = NSFont(name: "SF Mono", size: 11) ?? .systemFont(ofSize: 11)
        tv.string = content
        let scroll = NSScrollView(frame: NSRect(x: 0, y: 0, width: 700, height: 500))
        scroll.documentView = tv
        w.contentView = scroll
        w.makeKeyAndOrderFront(nil)
    }

    @objc private func allUseLocal() {
        for item in conflicts { resolutions[item.path] = .useLocal }
        confirmClicked()
    }

    @objc private func allUseRemote() {
        for item in conflicts { resolutions[item.path] = .useRemote }
        confirmClicked()
    }

    @objc private func cancelClicked() {
        window.close()
        NSApp.stopModal()
        onComplete?([])
    }

    @objc private func confirmClicked() {
        var results: [ConflictResolution] = []
        for item in conflicts {
            let action = resolutions[item.path] ?? .skip
            results.append(ConflictResolution(path: item.path, action: action))
        }
        window.close()
        NSApp.stopModal()
        onComplete?(results)
    }
}
