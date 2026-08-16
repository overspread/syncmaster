import Cocoa

// MARK: - 分类管理面板 (还原设计图 V3.0 风格)
class CategoryPanel: NSView {

    private let engine = SyncEngine()
    private var categories: [SyncCategory] = []
    private var onCategorySelected: ((SyncCategory) -> Void)?
    private var onSyncCategory: ((SyncCategory) -> Void)?
    private var onCategoryUpdated: (() -> Void)?

    private let scrollView = NSScrollView()
    private let listView = NSView()
    private let addBtn = NSButton(title: "+ 创建分类", target: nil, action: nil)

    func configure(selected: @escaping (SyncCategory) -> Void,
                   sync: @escaping (SyncCategory) -> Void,
                   updated: @escaping () -> Void) {
        self.onCategorySelected = selected
        self.onSyncCategory = sync
        self.onCategoryUpdated = updated
    }

    override init(frame: NSRect) {
        super.init(frame: frame)
        setupUI()
        loadCategories()
    }

    required init?(coder: NSCoder) { fatalError() }

    private func setupUI() {
        let title = makeLabel("同步分类管理", size: 20, weight: .bold, color: .white)
        title.frame = CGRect(x: 24, y: 650, width: 200, height: 30)
        addSubview(title)
        let sub = makeLabel("多分类独立管理，独立路径、模式与规则配置", size: 11, weight: .regular, color: NSColor(white: 0.5, alpha: 1))
        sub.frame = CGRect(x: 24, y: 628, width: 400, height: 18)
        addSubview(sub)

        addBtn.title = "+ 创建同步分类"; addBtn.bezelStyle = .rounded
        addBtn.font = .systemFont(ofSize: 11, weight: .semibold)
        addBtn.frame = CGRect(x: 610, y: 628, width: 126, height: 28)
        addBtn.target = self; addBtn.action = #selector(showCreateCategory)
        addBtn.contentTintColor = NSColor(red: 0, green: 0.7, blue: 1, alpha: 1)
        addSubview(addBtn)

        scrollView.frame = CGRect(x: 24, y: 40, width: 712, height: 570)
        scrollView.wantsLayer = true
        scrollView.layer?.backgroundColor = NSColor(red: 0.07, green: 0.09, blue: 0.14, alpha: 1).cgColor
        scrollView.layer?.cornerRadius = 10; scrollView.layer?.borderWidth = 1
        scrollView.layer?.borderColor = NSColor(red: 0.16, green: 0.22, blue: 0.3, alpha: 1).cgColor
        scrollView.hasVerticalScroller = true; scrollView.autohidesScrollers = true
        scrollView.documentView = listView
        addSubview(scrollView)
    }

    func loadCategories() {
        categories = Database.shared.getCategories()
        renderCategories()
    }

    func getCategories() -> [SyncCategory] { categories }

    func refreshCategories() {
        categories = Database.shared.getCategories()
        renderCategories()
    }

    private func renderCategories() {
        listView.subviews.forEach { $0.removeFromSuperview() }
        var y: CGFloat = 8
        for (i, cat) in categories.enumerated() {
            let item = createCategoryItem(cat, index: i, y: y)
            listView.addSubview(item)
            y += 84
        }
        listView.frame = CGRect(x: 0, y: 0, width: 694, height: max(y + 8, 570))
        scrollView.contentView.scroll(to: .zero)
    }

    private func createCategoryItem(_ cat: SyncCategory, index: Int, y: CGFloat) -> NSView {
        let item = makeCardFrame(CGRect(x: 8, y: y, width: 694, height: 76))

        // 分类 Emoji 图标（参考设计图 V3.0）
        let icons = ["💼", "🧩", "📁", "⭐", "⚙️", "📄"]
        let icon = icons[index % icons.count]
        let iconLbl = makeLabel(icon, size: 24, weight: .regular, color: .white)
        iconLbl.frame = CGRect(x: 14, y: 38, width: 32, height: 30)
        item.addSubview(iconLbl)

        let toggle = NSButton(checkboxWithTitle: "", target: self, action: #selector(toggleCategory(_:)))
        toggle.frame = CGRect(x: 14, y: 12, width: 18, height: 18)
        toggle.state = cat.isEnabled ? .on : .off
        toggle.identifier = NSUserInterfaceItemIdentifier(cat.id)
        item.addSubview(toggle)

        let nameLbl = makeLabel(cat.name, size: 14, weight: .bold, color: .white)
        nameLbl.frame = CGRect(x: 52, y: 46, width: 220, height: 22)
        item.addSubview(nameLbl)

        let pathLbl = makeLabel("\(cat.localPath)  ⇄  \(cat.remotePath)", size: 9, weight: .regular, color: NSColor(white: 0.5, alpha: 1))
        pathLbl.frame = CGRect(x: 52, y: 28, width: 400, height: 16)
        item.addSubview(pathLbl)

        let stats = ManifestEngine.shared.getCategoryStats(cat.id)
        let statsLbl = makeLabel("已同步 \(stats.synced) · 待同步 \(stats.pending) · 冲突 \(stats.conflicts)", size: 9, weight: .regular, color: NSColor(white: 0.4, alpha: 1))
        statsLbl.frame = CGRect(x: 52, y: 10, width: 300, height: 16)
        item.addSubview(statsLbl)

        // 同步模式胶囊 (对应设计图 V3.0)
        let modeIcon = cat.mode == "toServer" ? "↑ 本地→服务器" : cat.mode == "toLocal" ? "↓ 服务器→本地" : "↔ 智能双向"
        let modeBadge = makeTagBadge(modeIcon, bgR: 0, 0.6, 0.9, alpha: 0.15, textR: 0, 0.7, 1.0)
        modeBadge.frame = CGRect(x: 450, y: 44, width: 96, height: 22)
        item.addSubview(modeBadge)

        let syncLbl = makeLabel("上次: \(cat.lastSync)", size: 8, weight: .regular, color: NSColor(white: 0.45, alpha: 1))
        syncLbl.frame = CGRect(x: 450, y: 16, width: 100, height: 16)
        item.addSubview(syncLbl)

        let useBtn = NSButton(title: "选择", target: self, action: #selector(selectCategory(_:)))
        useBtn.bezelStyle = .rounded; useBtn.font = .systemFont(ofSize: 10, weight: .medium)
        useBtn.frame = CGRect(x: 554, y: 26, width: 44, height: 24)
        useBtn.identifier = NSUserInterfaceItemIdentifier(cat.id)
        useBtn.contentTintColor = NSColor(red: 0, green: 0.7, blue: 1, alpha: 1)
        item.addSubview(useBtn)

        let syncBtn = NSButton(title: "同步", target: self, action: #selector(syncCategory(_:)))
        syncBtn.bezelStyle = .rounded; syncBtn.font = .systemFont(ofSize: 10, weight: .medium)
        syncBtn.frame = CGRect(x: 600, y: 26, width: 44, height: 24)
        syncBtn.identifier = NSUserInterfaceItemIdentifier(cat.id)
        syncBtn.contentTintColor = NSColor(red: 0.3, green: 0.8, blue: 0.4, alpha: 1)
        item.addSubview(syncBtn)

        let editBtn = NSButton(title: "编辑", target: self, action: #selector(editCategory(_:)))
        editBtn.bezelStyle = .rounded; editBtn.font = .systemFont(ofSize: 10)
        editBtn.frame = CGRect(x: 646, y: 42, width: 40, height: 22)
        editBtn.identifier = NSUserInterfaceItemIdentifier(cat.id)
        editBtn.contentTintColor = NSColor(white: 0.7, alpha: 1)
        item.addSubview(editBtn)

        let delBtn = NSButton(title: "✕", target: self, action: #selector(deleteCategory(_:)))
        delBtn.bezelStyle = .rounded; delBtn.font = .systemFont(ofSize: 10)
        delBtn.frame = CGRect(x: 646, y: 12, width: 40, height: 22)
        delBtn.identifier = NSUserInterfaceItemIdentifier(cat.id)
        delBtn.contentTintColor = NSColor(red: 0.8, green: 0.3, blue: 0.3, alpha: 1)
        item.addSubview(delBtn)

        return item
    }

    // MARK: - 操作
    @objc private func toggleCategory(_ sender: NSButton) {
        if let idx = categories.firstIndex(where: { $0.id == sender.identifier?.rawValue }) {
            categories[idx].isEnabled = sender.state == .on
            Database.shared.saveCategory(categories[idx])
        }
    }

    @objc private func selectCategory(_ sender: NSButton) {
        guard let cat = categories.first(where: { $0.id == sender.identifier?.rawValue }) else { return }
        onCategorySelected?(cat)
        Database.shared.addAudit("选择分类", cat.name)
    }

    @objc private func syncCategory(_ sender: NSButton) {
        guard let cat = categories.first(where: { $0.id == sender.identifier?.rawValue }), cat.isEnabled else { return }
        onSyncCategory?(cat)
    }

    @objc private func deleteCategory(_ sender: NSButton) {
        guard let id = sender.identifier?.rawValue else { return }
        let alert = NSAlert()
        alert.messageText = "删除分类"
        alert.informativeText = "确认删除此同步分类？相关 Manifest 数据也会被清除。"
        alert.addButton(withTitle: "删除"); alert.addButton(withTitle: "取消")
        if alert.runModal() == .alertFirstButtonReturn {
            Database.shared.deleteCategory(id: id)
            Database.shared.addAudit("删除分类", id)
            refreshCategories()
            onCategoryUpdated?()
        }
    }

    @objc private func editCategory(_ sender: NSButton) {
        guard let cat = categories.first(where: { $0.id == sender.identifier?.rawValue }) else { return }
        showEditWindow(for: cat)
    }

    @objc private func showCreateCategory() {
        showEditWindow(for: nil)
    }

    // MARK: - 编辑窗口
    private func showEditWindow(for category: SyncCategory?) {
        let isEdit = category != nil
        let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 560, height: 620),
                              styleMask: [.titled, .closable],
                              backing: .buffered, defer: false)
        window.title = isEdit ? "编辑分类" : "创建同步分类"
        window.center()
        window.isReleasedWhenClosed = false

        let content = NSView(frame: NSRect(x: 0, y: 0, width: 560, height: 620))
        content.wantsLayer = true
        content.layer?.backgroundColor = NSColor(red: 0.05, green: 0.07, blue: 0.1, alpha: 1).cgColor
        window.contentView = content

        let nameLbl = makeLabel("分类名称", size: 11, weight: .regular, color: NSColor(white: 0.5, alpha: 1))
        nameLbl.frame = CGRect(x: 20, y: 580, width: 100, height: 16)
        content.addSubview(nameLbl)
        let nameField = makeTextField(category?.name ?? "")
        nameField.placeholderString = "如：Python 配置"
        nameField.frame = CGRect(x: 20, y: 554, width: 520, height: 24)
        content.addSubview(nameField)

        let browserTitle = makeLabel("文件浏览器（单击选择/取消，双击进入目录）", size: 11, weight: .regular, color: NSColor(white: 0.5, alpha: 1))
        browserTitle.frame = CGRect(x: 20, y: 524, width: 300, height: 16)
        content.addSubview(browserTitle)

        let browser = FileBrowserPanel(frame: NSRect(x: 20, y: 230, width: 520, height: 288),
                                        initialPath: category?.localPath ?? NSHomeDirectory())
        content.addSubview(browser)

        if isEdit, let cat = category, !cat.files.isEmpty {
            browser.setSelectedPaths(cat.files)
        }

        let selectedTitle = makeLabel("已选择文件", size: 10, weight: .medium, color: NSColor(red: 0, green: 0.7, blue: 1, alpha: 1))
        selectedTitle.frame = CGRect(x: 20, y: 204, width: 200, height: 16)
        content.addSubview(selectedTitle)

        let selectedScroll = NSScrollView(frame: CGRect(x: 20, y: 110, width: 520, height: 88))
        selectedScroll.wantsLayer = true
        selectedScroll.layer?.backgroundColor = NSColor(red: 0.07, green: 0.1, blue: 0.15, alpha: 1).cgColor
        selectedScroll.layer?.cornerRadius = 6
        selectedScroll.hasVerticalScroller = true; selectedScroll.autohidesScrollers = true
        let selectedList = NSTextView()
        selectedList.isEditable = false
        selectedList.backgroundColor = NSColor(red: 0.07, green: 0.1, blue: 0.15, alpha: 1)
        selectedList.textColor = NSColor(white: 0.7, alpha: 1)
        selectedList.font = NSFont(name: "SF Mono", size: 9) ?? .systemFont(ofSize: 9)
        selectedScroll.documentView = selectedList
        content.addSubview(selectedScroll)

        let clearBtn = NSButton(title: "清除选择", target: nil, action: nil)
        clearBtn.bezelStyle = .rounded; clearBtn.font = .systemFont(ofSize: 9)
        clearBtn.frame = CGRect(x: 440, y: 204, width: 100, height: 18)
        clearBtn.contentTintColor = NSColor(red: 0.8, green: 0.3, blue: 0.3, alpha: 1)
        content.addSubview(clearBtn)

        browser.onSelectionChange = { paths in
            selectedList.string = paths.enumerated().map { (i, p) in
                let name = (p as NSString).lastPathComponent
                let suffix = p.hasSuffix("/") ? " (文件夹)" : ""
                return "\(i + 1). \(name)\(suffix)\n  \(p)"
            }.joined(separator: "\n")
        }

        clearBtn.target = browser
        clearBtn.action = #selector(FileBrowserPanel.clearSelection)

        let remoteLbl = makeLabel("服务器路径", size: 11, weight: .regular, color: NSColor(white: 0.5, alpha: 1))
        remoteLbl.frame = CGRect(x: 20, y: 82, width: 100, height: 16)
        content.addSubview(remoteLbl)
        let remoteField = makeTextField(category?.remotePath ?? SyncConfig.shared.remoteDir)
        remoteField.frame = CGRect(x: 20, y: 58, width: 250, height: 24)
        content.addSubview(remoteField)

        let modeLbl = makeLabel("同步模式", size: 10, weight: .regular, color: NSColor(white: 0.5, alpha: 1))
        modeLbl.frame = CGRect(x: 280, y: 82, width: 80, height: 16)
        content.addSubview(modeLbl)
        let modePopup = NSPopUpButton(frame: CGRect(x: 280, y: 58, width: 120, height: 24))
        modePopup.addItems(withTitles: ["↑ 本地→服务器", "↓ 服务器→本地", "↔ 智能双向"])
        if let cat = category {
            switch cat.mode {
            case "toServer": modePopup.selectItem(at: 0)
            case "toLocal": modePopup.selectItem(at: 1)
            default: modePopup.selectItem(at: 2)
            }
        } else { modePopup.selectItem(at: 2) }
        content.addSubview(modePopup)

        let dpLbl = makeLabel("删除策略", size: 10, weight: .regular, color: NSColor(white: 0.5, alpha: 1))
        dpLbl.frame = CGRect(x: 410, y: 82, width: 80, height: 16)
        content.addSubview(dpLbl)
        let dpPopup = NSPopUpButton(frame: CGRect(x: 410, y: 58, width: 130, height: 24))
        for dp in DeletePolicy.allCases { dpPopup.addItem(withTitle: dp.label) }
        let currentDP = DeletePolicy(rawValue: category?.deletePolicy ?? SyncConfig.shared.defaultDeletePolicy) ?? .noDelete
        dpPopup.selectItem(withTitle: currentDP.label)
        content.addSubview(dpPopup)

        let cancelBtn = NSButton(title: "取消", target: nil, action: nil)
        cancelBtn.bezelStyle = .rounded; cancelBtn.font = .systemFont(ofSize: 12)
        cancelBtn.frame = CGRect(x: 340, y: 16, width: 90, height: 30)
        cancelBtn.target = window; cancelBtn.action = #selector(NSWindow.close)
        content.addSubview(cancelBtn)

        let saveBtn = NSButton(title: isEdit ? "保存" : "创建", target: nil, action: nil)
        saveBtn.bezelStyle = .rounded; saveBtn.font = .systemFont(ofSize: 12, weight: .semibold)
        saveBtn.frame = CGRect(x: 440, y: 16, width: 100, height: 30)
        saveBtn.contentTintColor = NSColor(red: 0, green: 0.7, blue: 1, alpha: 1)
        content.addSubview(saveBtn)

        saveBtn.target = self
        saveBtn.action = #selector(saveCategoryFromWindow)

        objc_setAssociatedObject(saveBtn, "nameField", nameField, .OBJC_ASSOCIATION_RETAIN)
        objc_setAssociatedObject(saveBtn, "remoteField", remoteField, .OBJC_ASSOCIATION_RETAIN)
        objc_setAssociatedObject(saveBtn, "modePopup", modePopup, .OBJC_ASSOCIATION_RETAIN)
        objc_setAssociatedObject(saveBtn, "dpPopup", dpPopup, .OBJC_ASSOCIATION_RETAIN)
        objc_setAssociatedObject(saveBtn, "browser", browser, .OBJC_ASSOCIATION_RETAIN)
        objc_setAssociatedObject(saveBtn, "window", window, .OBJC_ASSOCIATION_RETAIN)
        objc_setAssociatedObject(saveBtn, "category", category, .OBJC_ASSOCIATION_RETAIN)

        window.makeKeyAndOrderFront(nil)
        NSApp.runModal(for: window)
    }

    @objc private func saveCategoryFromWindow(_ sender: NSButton) {
        let nameField = objc_getAssociatedObject(sender, "nameField") as? NSTextField
        let remoteField = objc_getAssociatedObject(sender, "remoteField") as? NSTextField
        let modePopup = objc_getAssociatedObject(sender, "modePopup") as? NSPopUpButton
        let dpPopup = objc_getAssociatedObject(sender, "dpPopup") as? NSPopUpButton
        let browser = objc_getAssociatedObject(sender, "browser") as? FileBrowserPanel
        let window = objc_getAssociatedObject(sender, "window") as? NSWindow
        let existing = objc_getAssociatedObject(sender, "category") as? SyncCategory

        let name = nameField?.stringValue.trimmingCharacters(in: .whitespaces) ?? ""
        guard !name.isEmpty else { return }

        let files = browser?.getSelectedPaths() ?? []
        let localPath = files.isEmpty ? (existing?.localPath ?? browser?.currentPath ?? "") : files.first ?? (existing?.localPath ?? "")
        let remote = remoteField?.stringValue.trimmingCharacters(in: .whitespaces) ?? ""
        let modeIdx = modePopup?.indexOfSelectedItem ?? 2
        let mode = ["toServer", "toLocal", "bidirectional"][modeIdx]
        let dpLabel = dpPopup?.titleOfSelectedItem ?? DeletePolicy.noDelete.label
        let dp = DeletePolicy.allCases.first { $0.label == dpLabel }?.rawValue ?? "noDelete"

        let id = existing?.id ?? String(UUID().uuidString.prefix(8).lowercased())
        let cat = SyncCategory(id: id, name: name, localPath: localPath, remotePath: remote,
                               mode: mode, isEnabled: existing?.isEnabled ?? true,
                               lastSync: existing?.lastSync ?? "-", files: files, deletePolicy: dp)
        Database.shared.saveCategory(cat)
        Database.shared.addAudit(existing != nil ? "编辑分类" : "创建分类", name)
        refreshCategories()
        onCategoryUpdated?()

        window?.close()
        NSApp.stopModal()
    }
}
