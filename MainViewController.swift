import Cocoa

// MARK: - 主窗口控制器
class MainViewController: NSViewController {
    private let engine = SyncEngine()
    private var categories: [SyncCategory] = []
    private var selectedCategory: SyncCategory?
    private var currentDiff: DiffResult?
    private var currentDirection: SyncEngine.SyncDirection = .toServer
    private var state: SyncEngine.State = .idle

    // 侧边栏
    private let sidebar = NSView()
    private var navButtons: [NSButton] = []
    private let navItems = ["概览", "同步", "分类管理", "历史记录", "备份回滚", "审计日志", "设置"]

    // 内容区
    private let contentView = NSView()
    private var panels: [NSView] = []

    // 分类面板
    private var categoryPanel: CategoryPanel!

    // 设备卡片
    private let localNameLbl = NSTextField(labelWithString: "MacBook Pro")
    private let localPathLbl = NSTextField(labelWithString: "加载中...")
    private let localStatusLbl = NSTextField(labelWithString: "● 在线")
    private let localFilesLbl = NSTextField(labelWithString: "-")
    private let localSizeLbl = NSTextField(labelWithString: "-")
    private let serverNameLbl = NSTextField(labelWithString: "Ubuntu 22.04")
    private let serverPathLbl = NSTextField(labelWithString: "连接中...")
    private let serverStatusLbl = NSTextField(labelWithString: "● 检测中")
    private let serverFilesLbl = NSTextField(labelWithString: "-")
    private let serverSizeLbl = NSTextField(labelWithString: "-")

    // 概览监控
    private let monitorSpeedLbl = NSTextField(labelWithString: "0 KB/s")
    private let monitorDoneLbl = NSTextField(labelWithString: "0/0")
    private let monitorPercentLbl = NSTextField(labelWithString: "0%")
    private let monitorBar = NSProgressIndicator()
    private let monitorStatusLbl = NSTextField(labelWithString: "就绪")
    private let monitorEtaLbl = NSTextField(labelWithString: "--:--")

    // 状态 / 统计
    private let statusLabel = NSTextField(labelWithString: "就绪")
    private let logView = NSTextView()
    private var statsTotal: NSTextField!; private var statsPending: NSTextField!
    private var statsLastSync: NSTextField!; private var statsSuccess: NSTextField!

    // 差异面板 (设计图 V2.0 风格)
    private let diffPanel = NSView()
    private var badgeAddedView: NSView!
    private var badgeModifiedView: NSView!
    private var badgeDeletedView: NSView!
    private var badgeConflictView: NSView!
    private var badgeIgnoreView: NSView!

    private let diffList = NSTextView()
    private let confirmBtn = NSButton(title: "开始同步", target: nil, action: nil)
    private let cancelBtn = NSButton(title: "取消", target: nil, action: nil)
    private let syncPanel = NSView()

    // 进度面板 (设计图 V4.0 风格)
    private let progressPanel = NSView()
    private let progressBigBar = NSProgressIndicator()
    private let progressPctLbl = NSTextField(labelWithString: "0%")
    private let progressFileLbl = NSTextField(labelWithString: "等待开始...")
    private let progressUpLbl = NSTextField(labelWithString: "↑ 0")
    private let progressDownLbl = NSTextField(labelWithString: "↓ 0")

    // 历史
    private var historyTable: NSTableView!
    private var historyFilter: NSPopUpButton!

    // 审计
    private var auditTable: NSTableView!

    // 备份
    private var backupList: NSScrollView!
    private let backupListView = NSView()

    // 设置
    private var jumpHostField: NSTextField!; private var jumpKeyField: NSTextField!
    private var remoteUserField: NSTextField!; private var remoteHostField: NSTextField!
    private var remoteKeyField: NSTextField!; private var remoteDirField: NSTextField!
    private var localDirField: NSTextField!
    private var ignoreEditor: NSTextView!
    private var envSyncCheckbox: NSButton!
    private var envKeysTable: NSTableView!
    private var envRefreshBtn: NSButton!
    private var envSyncBtn: NSButton!
    private var envStatusLbl: NSTextField!
    private var connStatusLbl: NSTextField!

    private let selectedCatLbl = NSTextField(labelWithString: "未选择（点击分类管理选择）")
    private let contentLeft: CGFloat = 220
    private let panelWidth: CGFloat = 760

    override func loadView() {
        view = NSView(frame: NSRect(x: 0, y: 0, width: 1000, height: 700))
        view.wantsLayer = true
        view.layer?.backgroundColor = NSColor(red: 0.05, green: 0.07, blue: 0.1, alpha: 1).cgColor
        view.appearance = NSAppearance(named: .darkAqua)
    }

    override func viewDidLoad() {
        super.viewDidLoad()
        _ = Database.shared.open()
        SyncConfig.shared = Database.shared.getConfig()
        SyncIgnore.load()
        categories = Database.shared.getCategories()

        setupSidebar()
        setupContent()
        setupOverviewPanel()
        setupSyncPanel()
        setupCategoryPanel()
        setupHistoryPanel()
        setupBackupPanel()
        setupAuditPanel()
        setupSettingsPanel()
        refreshInfo()
        switchPanel(0)
    }

    // MARK: - 侧边栏 (对比设计图 V1.0 - V5.0)
    private func setupSidebar() {
        sidebar.frame = CGRect(x: 0, y: 0, width: contentLeft, height: 700)
        sidebar.wantsLayer = true
        sidebar.layer?.backgroundColor = NSColor(red: 0.07, green: 0.09, blue: 0.14, alpha: 1).cgColor
        view.addSubview(sidebar)

        let logo = makeLabel("◈ SyncMaster", size: 18, weight: .bold, color: .white)
        logo.frame = CGRect(x: 18, y: 652, width: 190, height: 26)
        sidebar.addSubview(logo)
        let ver = makeLabel("v1.0.0 · 智能同步管理器", size: 10, weight: .medium, color: NSColor(red: 0, green: 0.7, blue: 1, alpha: 1))
        ver.frame = CGRect(x: 18, y: 632, width: 190, height: 14)
        sidebar.addSubview(ver)

        let divider = NSView(frame: CGRect(x: 14, y: 620, width: contentLeft - 28, height: 1))
        divider.wantsLayer = true
        divider.layer?.backgroundColor = NSColor(red: 0.14, green: 0.18, blue: 0.26, alpha: 1).cgColor
        sidebar.addSubview(divider)

        for (i, title) in navItems.enumerated() {
            let btn = NSButton(title: title, target: self, action: #selector(navClicked(_:)))
            btn.isBordered = false
            btn.focusRingType = .none
            btn.alignment = .left
            btn.font = .systemFont(ofSize: 13, weight: .medium)
            btn.contentTintColor = NSColor(white: 0.7, alpha: 1)
            btn.frame = CGRect(x: 14, y: 580 - CGFloat(i) * 56, width: contentLeft - 28, height: 42)
            btn.wantsLayer = true
            btn.layer?.cornerRadius = 8
            btn.layer?.backgroundColor = NSColor.clear.cgColor
            btn.tag = i
            sidebar.addSubview(btn)
            navButtons.append(btn)
        }

        let footer = makeLabel("本地 ↔ 服务器 · 智能引擎", size: 9, weight: .regular, color: NSColor(white: 0.35, alpha: 1))
        footer.frame = CGRect(x: 18, y: 14, width: 190, height: 14)
        sidebar.addSubview(footer)
    }

    @objc private func navClicked(_ sender: NSButton) { switchPanel(sender.tag) }

    private func switchPanel(_ index: Int) {
        for (i, panel) in panels.enumerated() { panel.isHidden = (i != index) }
        for (i, btn) in navButtons.enumerated() {
            let active = (i == index)
            btn.contentTintColor = active ? NSColor(red: 0, green: 0.7, blue: 1, alpha: 1) : NSColor(white: 0.7, alpha: 1)
            btn.layer?.backgroundColor = active ? NSColor(red: 0.1, green: 0.16, blue: 0.24, alpha: 1).cgColor : NSColor.clear.cgColor
        }
        if index == 2 { categoryPanel.refreshCategories() }
        if index == 3 { renderHistory() }
        if index == 4 { renderBackups() }
        if index == 5 { renderAudit() }
    }

    private func setupContent() {
        contentView.frame = CGRect(x: contentLeft, y: 0, width: panelWidth, height: 700)
        view.addSubview(contentView)
        panels = [NSView(), NSView(), NSView(), NSView(), NSView(), NSView(), NSView()]
        for p in panels {
            p.frame = contentView.bounds
            p.isHidden = true
            contentView.addSubview(p)
        }
    }

    // MARK: - 概览页 (还原设计图 V1.0 风格)
    private func setupOverviewPanel() {
        let panel = panels[0]
        let title = makeLabel("概览", size: 20, weight: .bold, color: .white)
        title.frame = CGRect(x: 24, y: 650, width: 200, height: 30)
        panel.addSubview(title)
        let sub = makeLabel("本地设备 ↔ 服务器 智能同步控制台", size: 11, weight: .regular, color: NSColor(white: 0.5, alpha: 1))
        sub.frame = CGRect(x: 24, y: 628, width: 400, height: 18)
        panel.addSubview(sub)

        // 1. 设备连接卡片 (左电脑, 中间双向箭头, 右服务器)
        panel.addSubview(createDeviceCard(frame: CGRect(x: 24, y: 480, width: 320, height: 130), isServer: false, title: "本地设备",
            nameLabel: localNameLbl, pathLabel: localPathLbl, statusLabel: localStatusLbl, filesLabel: localFilesLbl, sizeLabel: localSizeLbl))

        let arrowBadge = NSView(frame: CGRect(x: 354, y: 520, width: 52, height: 52))
        arrowBadge.wantsLayer = true
        arrowBadge.layer?.backgroundColor = NSColor(red: 0.1, green: 0.16, blue: 0.26, alpha: 1).cgColor
        arrowBadge.layer?.cornerRadius = 26
        arrowBadge.layer?.borderWidth = 1.5
        arrowBadge.layer?.borderColor = NSColor(red: 0, green: 0.6, blue: 0.9, alpha: 0.6).cgColor

        let arrowLbl = makeLabel("⇄", size: 22, weight: .bold, color: NSColor(red: 0, green: 0.7, blue: 1, alpha: 1))
        arrowLbl.alignment = .center
        arrowLbl.frame = CGRect(x: 0, y: 12, width: 52, height: 28)
        arrowBadge.addSubview(arrowLbl)
        panel.addSubview(arrowBadge)

        panel.addSubview(createDeviceCard(frame: CGRect(x: 416, y: 480, width: 320, height: 130), isServer: true, title: "服务器",
            nameLabel: serverNameLbl, pathLabel: serverPathLbl, statusLabel: serverStatusLbl, filesLabel: serverFilesLbl, sizeLabel: serverSizeLbl))

        // 2. 核心三大同步按钮 (对应设计图 V1.0 按钮样式)
        let quickTitle = makeLabel("快捷同步操作", size: 13, weight: .semibold, color: .white)
        quickTitle.frame = CGRect(x: 24, y: 435, width: 200, height: 20)
        panel.addSubview(quickTitle)

        let btnA = makeBigSyncBtn(title: "↑ 同步到服务器", subtitle: "本地 → 服务器", r: 0, 0.55, 0.95)
        let btnB = makeBigSyncBtn(title: "↓ 同步到本地", subtitle: "服务器 → 本地", r: 0.16, 0.7, 0.3)
        let btnC = makeBigSyncBtn(title: "↔ 智能双向同步", subtitle: "智能合并，冲突检测", r: 0.5, 0.3, 0.85)

        btnA.frame = CGRect(x: 24, y: 355, width: 225, height: 62); btnA.action = #selector(syncToServer); btnA.target = self
        btnB.frame = CGRect(x: 264, y: 355, width: 225, height: 62); btnB.action = #selector(syncToLocal); btnB.target = self
        btnC.frame = CGRect(x: 504, y: 355, width: 232, height: 62); btnC.action = #selector(syncBidirectional); btnC.target = self
        layoutBigSyncBtn(btnA); layoutBigSyncBtn(btnB); layoutBigSyncBtn(btnC)
        panel.addSubview(btnA); panel.addSubview(btnB); panel.addSubview(btnC)

        // 3. 统计数据 4 宫格 (对应设计图 V1.0 同步统计)
        let statsTitle = makeLabel("同步统计", size: 13, weight: .semibold, color: .white)
        statsTitle.frame = CGRect(x: 24, y: 310, width: 200, height: 20)
        panel.addSubview(statsTitle)

        statsTotal = makeStatField(); statsPending = makeStatField(); statsLastSync = makeStatField(); statsSuccess = makeStatField()

        let card1 = createStatCard(frame: CGRect(x: 24, y: 215, width: 168, height: 80), label: "总文件数", field: statsTotal, accentColor: NSColor(red: 0, green: 0.7, blue: 1, alpha: 1))
        let card2 = createStatCard(frame: CGRect(x: 206, y: 215, width: 168, height: 80), label: "待同步文件", field: statsPending, accentColor: NSColor(red: 0.9, green: 0.6, blue: 0, alpha: 1))
        let card3 = createStatCard(frame: CGRect(x: 388, y: 215, width: 168, height: 80), label: "成功率", field: statsSuccess, accentColor: NSColor(red: 0.3, green: 0.8, blue: 0.4, alpha: 1))
        let card4 = createStatCard(frame: CGRect(x: 570, y: 215, width: 166, height: 80), label: "上次同步", field: statsLastSync, accentColor: NSColor(red: 0.6, green: 0.4, blue: 0.9, alpha: 1))

        panel.addSubview(card1); panel.addSubview(card2); panel.addSubview(card3); panel.addSubview(card4)

        // 4. 实时监控面板 (对应设计图 V4.0 / V5.0)
        let monTitle = makeLabel("实时监控与速率", size: 13, weight: .semibold, color: .white)
        monTitle.frame = CGRect(x: 24, y: 175, width: 200, height: 20)
        panel.addSubview(monTitle)

        let monCard = makeCardFrame(CGRect(x: 24, y: 40, width: 712, height: 120))
        panel.addSubview(monCard)

        let speedTitle = makeLabel("实时速率", size: 9, weight: .regular, color: NSColor(white: 0.5, alpha: 1))
        speedTitle.frame = CGRect(x: 18, y: 82, width: 100, height: 14); monCard.addSubview(speedTitle)
        monitorSpeedLbl.font = .systemFont(ofSize: 22, weight: .bold)
        monitorSpeedLbl.textColor = NSColor(red: 0, green: 0.7, blue: 1, alpha: 1)
        monitorSpeedLbl.frame = CGRect(x: 18, y: 52, width: 160, height: 28)
        monitorSpeedLbl.isEditable = false; monitorSpeedLbl.isBordered = false; monitorSpeedLbl.backgroundColor = .clear
        monCard.addSubview(monitorSpeedLbl)

        let doneTitle = makeLabel("已处理文件", size: 9, weight: .regular, color: NSColor(white: 0.5, alpha: 1))
        doneTitle.frame = CGRect(x: 210, y: 82, width: 100, height: 14); monCard.addSubview(doneTitle)
        monitorDoneLbl.font = .systemFont(ofSize: 22, weight: .bold)
        monitorDoneLbl.textColor = .white
        monitorDoneLbl.frame = CGRect(x: 210, y: 52, width: 160, height: 28)
        monitorDoneLbl.isEditable = false; monitorDoneLbl.isBordered = false; monitorDoneLbl.backgroundColor = .clear
        monCard.addSubview(monitorDoneLbl)

        let pctTitle = makeLabel("完成进度", size: 9, weight: .regular, color: NSColor(white: 0.5, alpha: 1))
        pctTitle.frame = CGRect(x: 400, y: 82, width: 100, height: 14); monCard.addSubview(pctTitle)
        monitorPercentLbl.font = .systemFont(ofSize: 22, weight: .bold)
        monitorPercentLbl.textColor = NSColor(red: 0.3, green: 0.8, blue: 0.4, alpha: 1)
        monitorPercentLbl.frame = CGRect(x: 400, y: 52, width: 120, height: 28)
        monitorPercentLbl.isEditable = false; monitorPercentLbl.isBordered = false; monitorPercentLbl.backgroundColor = .clear
        monCard.addSubview(monitorPercentLbl)

        let etaTitle = makeLabel("预计剩余", size: 9, weight: .regular, color: NSColor(white: 0.5, alpha: 1))
        etaTitle.frame = CGRect(x: 560, y: 82, width: 100, height: 14); monCard.addSubview(etaTitle)
        monitorEtaLbl.font = .systemFont(ofSize: 22, weight: .bold)
        monitorEtaLbl.textColor = .white
        monitorEtaLbl.frame = CGRect(x: 560, y: 52, width: 140, height: 28)
        monitorEtaLbl.isEditable = false; monitorEtaLbl.isBordered = false; monitorEtaLbl.backgroundColor = .clear
        monCard.addSubview(monitorEtaLbl)

        monitorBar.style = .bar
        monitorBar.frame = CGRect(x: 18, y: 22, width: 676, height: 12)
        monitorBar.isIndeterminate = false
        monitorBar.minValue = 0; monitorBar.maxValue = 100
        monCard.addSubview(monitorBar)
    }

    // MARK: - 同步页 (还原设计图 V2.0 差异对比 与 V4.0 同步界面)
    private func setupSyncPanel() {
        let panel = panels[1]
        syncPanel.frame = panel.bounds
        panel.addSubview(syncPanel)

        let title = makeLabel("同步操作与扫描", size: 20, weight: .bold, color: .white)
        title.frame = CGRect(x: 24, y: 650, width: 200, height: 30)
        syncPanel.addSubview(title)
        let sub = makeLabel("选择模式 → 差异扫描 → 预览变化 → 确认执行", size: 11, weight: .regular, color: NSColor(white: 0.5, alpha: 1))
        sub.frame = CGRect(x: 24, y: 628, width: 400, height: 18)
        syncPanel.addSubview(sub)

        let catSelTitle = makeLabel("当前选择分类", size: 10, weight: .regular, color: NSColor(white: 0.5, alpha: 1))
        catSelTitle.frame = CGRect(x: 24, y: 595, width: 80, height: 16)
        syncPanel.addSubview(catSelTitle)
        selectedCatLbl.font = .systemFont(ofSize: 13, weight: .semibold)
        selectedCatLbl.textColor = NSColor(red: 0, green: 0.7, blue: 1, alpha: 1)
        selectedCatLbl.frame = CGRect(x: 110, y: 593, width: 400, height: 18)
        selectedCatLbl.isEditable = false; selectedCatLbl.isBordered = false; selectedCatLbl.backgroundColor = .clear
        syncPanel.addSubview(selectedCatLbl)

        let btnA = makeBigSyncBtn(title: "↑ 同步到服务器", subtitle: "本地 → 服务器", r: 0, 0.55, 0.95)
        let btnB = makeBigSyncBtn(title: "↓ 同步到本地", subtitle: "服务器 → 本地", r: 0.16, 0.7, 0.3)
        let btnC = makeBigSyncBtn(title: "↔ 智能双向同步", subtitle: "智能合并，冲突检测", r: 0.5, 0.3, 0.85)

        btnA.frame = CGRect(x: 24, y: 525, width: 225, height: 58); btnA.action = #selector(syncToServer); btnA.target = self
        btnB.frame = CGRect(x: 264, y: 525, width: 225, height: 58); btnB.action = #selector(syncToLocal); btnB.target = self
        btnC.frame = CGRect(x: 504, y: 525, width: 232, height: 58); btnC.action = #selector(syncBidirectional); btnC.target = self
        layoutBigSyncBtn(btnA); layoutBigSyncBtn(btnB); layoutBigSyncBtn(btnC)
        syncPanel.addSubview(btnA); syncPanel.addSubview(btnB); syncPanel.addSubview(btnC)

        statusLabel.font = .systemFont(ofSize: 12)
        statusLabel.textColor = NSColor(white: 0.6, alpha: 1)
        statusLabel.frame = CGRect(x: 24, y: 495, width: 600, height: 20)
        statusLabel.isEditable = false; statusLabel.isBordered = false; statusLabel.backgroundColor = .clear
        syncPanel.addSubview(statusLabel)

        setupDiffPanel()
        setupProgressPanel()

        let logLbl = makeLabel("同步执行日志", size: 11, weight: .semibold, color: NSColor(white: 0.6, alpha: 1))
        logLbl.frame = CGRect(x: 24, y: 175, width: 200, height: 18)
        syncPanel.addSubview(logLbl)
        logView.isEditable = false
        logView.backgroundColor = NSColor(red: 0.07, green: 0.09, blue: 0.14, alpha: 1)
        logView.textColor = NSColor(white: 0.6, alpha: 1)
        logView.font = NSFont(name: "SF Mono", size: 10) ?? .systemFont(ofSize: 10)
        logView.frame = CGRect(x: 24, y: 10, width: 712, height: 155)
        logView.layer?.cornerRadius = 8; logView.layer?.masksToBounds = true
        logView.textContainerInset = NSSize(width: 10, height: 8)
        syncPanel.addSubview(logView)
    }

    // 差异对比面板 (对应设计图 V2.0 风格)
    private func setupDiffPanel() {
        diffPanel.frame = CGRect(x: 24, y: 320, width: 712, height: 0)
        diffPanel.wantsLayer = true
        diffPanel.layer?.backgroundColor = NSColor(red: 0.08, green: 0.11, blue: 0.17, alpha: 1).cgColor
        diffPanel.layer?.cornerRadius = 10; diffPanel.layer?.borderWidth = 1
        diffPanel.layer?.borderColor = NSColor(red: 0.16, green: 0.22, blue: 0.3, alpha: 1).cgColor
        diffPanel.isHidden = true
        syncPanel.addSubview(diffPanel)

        let dt = makeLabel("差异对比结果", size: 13, weight: .bold, color: .white)
        dt.frame = CGRect(x: 16, y: 132, width: 120, height: 20)
        diffPanel.addSubview(dt)

        // 五色胶囊 Tag (对应设计图 V2.0 顶部 Tag 栏)
        badgeAddedView = makeTagBadge("🟢 新增 0", bgR: 0.2, 0.7, 0.3, textR: 0.3, 0.9, 0.4)
        badgeModifiedView = makeTagBadge("🟡 修改 0", bgR: 0.9, 0.6, 0, textR: 1.0, 0.8, 0.2)
        badgeDeletedView = makeTagBadge("🔴 删除 0", bgR: 0.8, 0.2, 0.2, textR: 1.0, 0.4, 0.4)
        badgeConflictView = makeTagBadge("⚠️ 冲突 0", bgR: 0.8, 0.4, 0, textR: 1.0, 0.6, 0.2)
        badgeIgnoreView = makeTagBadge("⚪ 忽略 0", bgR: 0.4, 0.4, 0.4, textR: 0.7, 0.7, 0.7)

        badgeAddedView.frame = CGRect(x: 140, y: 130, width: 88, height: 22); diffPanel.addSubview(badgeAddedView)
        badgeModifiedView.frame = CGRect(x: 236, y: 130, width: 88, height: 22); diffPanel.addSubview(badgeModifiedView)
        badgeDeletedView.frame = CGRect(x: 332, y: 130, width: 88, height: 22); diffPanel.addSubview(badgeDeletedView)
        badgeConflictView.frame = CGRect(x: 428, y: 130, width: 88, height: 22); diffPanel.addSubview(badgeConflictView)
        badgeIgnoreView.frame = CGRect(x: 524, y: 130, width: 88, height: 22); diffPanel.addSubview(badgeIgnoreView)

        diffList.isEditable = false
        diffList.backgroundColor = NSColor(red: 0.05, green: 0.07, blue: 0.11, alpha: 1)
        diffList.textColor = NSColor(white: 0.7, alpha: 1)
        diffList.font = NSFont(name: "SF Mono", size: 9) ?? .systemFont(ofSize: 9)
        diffList.frame = CGRect(x: 14, y: 12, width: 550, height: 106)
        diffList.layer?.cornerRadius = 6; diffList.layer?.masksToBounds = true
        diffList.textContainerInset = NSSize(width: 8, height: 6)
        diffPanel.addSubview(diffList)

        confirmBtn.title = "开始同步"; confirmBtn.bezelStyle = .rounded
        confirmBtn.font = .systemFont(ofSize: 12, weight: .bold)
        confirmBtn.frame = CGRect(x: 580, y: 18, width: 118, height: 32)
        confirmBtn.contentTintColor = .white
        confirmBtn.wantsLayer = true
        confirmBtn.layer?.backgroundColor = NSColor(red: 0, green: 0.6, blue: 0.9, alpha: 1).cgColor
        confirmBtn.target = self; confirmBtn.action = #selector(doConfirmSync)
        diffPanel.addSubview(confirmBtn)

        cancelBtn.title = "取消"; cancelBtn.bezelStyle = .rounded; cancelBtn.font = .systemFont(ofSize: 12)
        cancelBtn.frame = CGRect(x: 580, y: 56, width: 118, height: 26)
        cancelBtn.target = self; cancelBtn.action = #selector(doCancelSync)
        diffPanel.addSubview(cancelBtn)
    }

    private func setupProgressPanel() {
        progressPanel.frame = CGRect(x: 24, y: 320, width: 712, height: 0)
        progressPanel.wantsLayer = true
        progressPanel.layer?.backgroundColor = NSColor(red: 0.08, green: 0.11, blue: 0.17, alpha: 1).cgColor
        progressPanel.layer?.cornerRadius = 10; progressPanel.layer?.borderWidth = 1
        progressPanel.layer?.borderColor = NSColor(red: 0.16, green: 0.22, blue: 0.3, alpha: 1).cgColor
        progressPanel.isHidden = true
        syncPanel.addSubview(progressPanel)

        progressPctLbl.font = .systemFont(ofSize: 32, weight: .bold)
        progressPctLbl.textColor = NSColor(red: 0, green: 0.7, blue: 1, alpha: 1)
        progressPctLbl.frame = CGRect(x: 16, y: 90, width: 100, height: 42)
        progressPctLbl.isEditable = false; progressPctLbl.isBordered = false; progressPctLbl.backgroundColor = .clear
        progressPanel.addSubview(progressPctLbl)

        progressBigBar.style = .bar
        progressBigBar.frame = CGRect(x: 130, y: 105, width: 560, height: 16)
        progressBigBar.isIndeterminate = false
        progressBigBar.minValue = 0; progressBigBar.maxValue = 100
        progressPanel.addSubview(progressBigBar)

        progressFileLbl.font = NSFont(name: "SF Mono", size: 11) ?? .systemFont(ofSize: 11)
        progressFileLbl.textColor = NSColor(white: 0.7, alpha: 1)
        progressFileLbl.frame = CGRect(x: 130, y: 70, width: 560, height: 18)
        progressFileLbl.isEditable = false; progressFileLbl.isBordered = false; progressFileLbl.backgroundColor = .clear
        progressPanel.addSubview(progressFileLbl)

        for (i, lbl) in [progressUpLbl, progressDownLbl].enumerated() {
            lbl.font = .systemFont(ofSize: 13, weight: .semibold)
            lbl.textColor = NSColor(red: 0, green: 0.7, blue: 1, alpha: 1)
            lbl.frame = CGRect(x: 16 + CGFloat(i) * 120, y: 16, width: 110, height: 24)
            lbl.isEditable = false; lbl.isBordered = false; lbl.backgroundColor = .clear
            progressPanel.addSubview(lbl)
        }
    }

    // MARK: - 分类页 (对应设计图 V3.0)
    private func setupCategoryPanel() {
        let panel = panels[2]
        categoryPanel = CategoryPanel(frame: panel.bounds)
        categoryPanel.configure(
            selected: { [weak self] cat in
                self?.selectedCategory = cat
                self?.selectedCatLbl.stringValue = "\(cat.name)（\(cat.localPath)）"
                self?.switchPanel(1)
            },
            sync: { [weak self] cat in
                self?.selectedCategory = cat
                self?.selectedCatLbl.stringValue = "\(cat.name)（\(cat.localPath)）"
                let dir = SyncEngine.SyncDirection(rawValue: cat.mode) ?? .bidirectional
                self?.switchPanel(1)
                self?.triggerScan(for: cat, direction: dir)
            },
            updated: { }
        )
        panel.addSubview(categoryPanel)
    }

    // MARK: - 历史页
    private func setupHistoryPanel() {
        let panel = panels[3]
        let title = makeLabel("同步历史记录", size: 20, weight: .bold, color: .white)
        title.frame = CGRect(x: 24, y: 650, width: 200, height: 30)
        panel.addSubview(title)

        let filterLbl = makeLabel("时间筛选", size: 11, weight: .regular, color: NSColor(white: 0.5, alpha: 1))
        filterLbl.frame = CGRect(x: 24, y: 610, width: 70, height: 20)
        panel.addSubview(filterLbl)

        historyFilter = NSPopUpButton(frame: CGRect(x: 100, y: 608, width: 140, height: 24))
        historyFilter.addItems(withTitles: ["全部", "今天", "最近 7 天", "最近 30 天"])
        historyFilter.target = self; historyFilter.action = #selector(historyFilterChanged)
        panel.addSubview(historyFilter)

        let clearBtn = NSButton(title: "清空历史", target: self, action: #selector(clearHistory))
        clearBtn.bezelStyle = .rounded; clearBtn.font = .systemFont(ofSize: 11)
        clearBtn.frame = CGRect(x: 640, y: 608, width: 96, height: 24)
        panel.addSubview(clearBtn)

        historyTable = NSTableView()
        for (id, t, w) in [("time","时间",120),("cat","分类",110),("dir","方向",110),("files","文件数",70),("dur","耗时",70),("res","结果",80)] {
            let col = NSTableColumn(identifier: NSUserInterfaceItemIdentifier(id))
            col.title = t; col.width = CGFloat(w)
            historyTable.addTableColumn(col)
        }
        historyTable.delegate = self; historyTable.dataSource = self
        historyTable.rowHeight = 26
        historyTable.backgroundColor = NSColor(red: 0.07, green: 0.09, blue: 0.14, alpha: 1)
        historyTable.selectionHighlightStyle = .none

        let scroll = NSScrollView(frame: CGRect(x: 24, y: 40, width: 712, height: 550))
        scroll.documentView = historyTable
        scroll.hasVerticalScroller = true; scroll.autohidesScrollers = true
        scroll.wantsLayer = true
        scroll.layer?.backgroundColor = NSColor(red: 0.07, green: 0.09, blue: 0.14, alpha: 1).cgColor
        scroll.layer?.cornerRadius = 10; scroll.layer?.borderWidth = 1
        scroll.layer?.borderColor = NSColor(red: 0.16, green: 0.22, blue: 0.3, alpha: 1).cgColor
        panel.addSubview(scroll)
    }

    @objc private func historyFilterChanged() { renderHistory() }
    @objc private func clearHistory() { Database.shared.clearHistory(); renderHistory() }

    private func renderHistory() { historyTable?.reloadData() }

    // MARK: - 备份页
    private func setupBackupPanel() {
        let panel = panels[4]
        let title = makeLabel("备份与快照回滚", size: 20, weight: .bold, color: .white)
        title.frame = CGRect(x: 24, y: 650, width: 200, height: 30)
        panel.addSubview(title)
        let sub = makeLabel("每次同步前自动创建备份快照，支持一键安全回滚", size: 11, weight: .regular, color: NSColor(white: 0.5, alpha: 1))
        sub.frame = CGRect(x: 24, y: 628, width: 400, height: 18)
        panel.addSubview(sub)

        let refreshBtn = NSButton(title: "刷新", target: self, action: #selector(refreshBackups))
        refreshBtn.bezelStyle = .rounded; refreshBtn.font = .systemFont(ofSize: 11)
        refreshBtn.frame = CGRect(x: 670, y: 628, width: 66, height: 24)
        panel.addSubview(refreshBtn)

        backupList = NSScrollView(frame: CGRect(x: 24, y: 40, width: 712, height: 580))
        backupList.documentView = backupListView
        backupList.hasVerticalScroller = true; backupList.autohidesScrollers = true
        backupList.wantsLayer = true
        backupList.layer?.backgroundColor = NSColor(red: 0.07, green: 0.09, blue: 0.14, alpha: 1).cgColor
        backupList.layer?.cornerRadius = 10; backupList.layer?.borderWidth = 1
        backupList.layer?.borderColor = NSColor(red: 0.16, green: 0.22, blue: 0.3, alpha: 1).cgColor
        panel.addSubview(backupList)
    }

    @objc private func refreshBackups() { renderBackups() }

    private func renderBackups() {
        backupListView.subviews.forEach { $0.removeFromSuperview() }
        let backups = engine.listBackups()
        var y: CGFloat = 8
        for (path, name) in backups {
            let item = makeCardFrame(NSRect(x: 8, y: y, width: 694, height: 40))
            let nameLbl = makeLabel(name, size: 11, weight: .medium, color: .white)
            nameLbl.frame = CGRect(x: 12, y: 12, width: 430, height: 18)
            item.addSubview(nameLbl)
            let rollBtn = NSButton(title: "回滚", target: self, action: #selector(rollbackClicked(_:)))
            rollBtn.bezelStyle = .rounded; rollBtn.font = .systemFont(ofSize: 10, weight: .medium)
            rollBtn.frame = CGRect(x: 560, y: 8, width: 60, height: 24)
            rollBtn.identifier = NSUserInterfaceItemIdentifier(path)
            rollBtn.contentTintColor = NSColor(red: 0.9, green: 0.6, blue: 0, alpha: 1)
            item.addSubview(rollBtn)
            let delBtn = NSButton(title: "✕", target: self, action: #selector(deleteBackup(_:)))
            delBtn.bezelStyle = .rounded; delBtn.font = .systemFont(ofSize: 10)
            delBtn.frame = CGRect(x: 630, y: 8, width: 24, height: 24)
            delBtn.identifier = NSUserInterfaceItemIdentifier(path)
            delBtn.contentTintColor = NSColor(red: 0.8, green: 0.3, blue: 0.3, alpha: 1)
            item.addSubview(delBtn)
            backupListView.addSubview(item)
            y += 46
        }
        backupListView.frame = CGRect(x: 0, y: 0, width: 694, height: max(y + 8, 580))
        backupList.contentView.scroll(to: .zero)
    }

    @objc private func rollbackClicked(_ sender: NSButton) {
        guard let path = sender.identifier?.rawValue else { return }
        let name = (path as NSString).lastPathComponent
        let alert = NSAlert()
        alert.messageText = "确认回滚到 \(name)？"
        alert.informativeText = "将用备份数据覆盖本地目录，此操作不可撤销。"
        alert.addButton(withTitle: "回滚"); alert.addButton(withTitle: "取消")
        if alert.runModal() == .alertFirstButtonReturn {
            statusLabel.stringValue = "正在回滚..."
            Database.shared.addAudit("回滚", name)
            engine.rollback(from: path, to: SyncConfig.shared.localDir) { success, msg in
                DispatchQueue.main.async {
                    self.statusLabel.stringValue = msg
                    self.log(msg)
                }
            }
        }
    }

    @objc private func deleteBackup(_ sender: NSButton) {
        guard let path = sender.identifier?.rawValue else { return }
        try? FileManager.default.removeItem(atPath: path)
        renderBackups()
    }

    // MARK: - 审计页
    private func setupAuditPanel() {
        let panel = panels[5]
        let title = makeLabel("安全与审计日志", size: 20, weight: .bold, color: .white)
        title.frame = CGRect(x: 24, y: 650, width: 200, height: 30)
        panel.addSubview(title)
        let sub = makeLabel("完整操作记录：同步、扫描、回滚、配置变更", size: 11, weight: .regular, color: NSColor(white: 0.5, alpha: 1))
        sub.frame = CGRect(x: 24, y: 628, width: 400, height: 18)
        panel.addSubview(sub)

        auditTable = NSTableView()
        for (id, t, w) in [("time","时间",130),("action","操作",120),("detail","详情",430)] {
            let col = NSTableColumn(identifier: NSUserInterfaceItemIdentifier(id))
            col.title = t; col.width = CGFloat(w)
            auditTable.addTableColumn(col)
        }
        auditTable.delegate = self; auditTable.dataSource = self
        auditTable.rowHeight = 26
        auditTable.backgroundColor = NSColor(red: 0.07, green: 0.09, blue: 0.14, alpha: 1)
        auditTable.selectionHighlightStyle = .none

        let scroll = NSScrollView(frame: CGRect(x: 24, y: 40, width: 712, height: 580))
        scroll.documentView = auditTable
        scroll.hasVerticalScroller = true; scroll.autohidesScrollers = true
        scroll.wantsLayer = true
        scroll.layer?.backgroundColor = NSColor(red: 0.07, green: 0.09, blue: 0.14, alpha: 1).cgColor
        scroll.layer?.cornerRadius = 10; scroll.layer?.borderWidth = 1
        scroll.layer?.borderColor = NSColor(red: 0.16, green: 0.22, blue: 0.3, alpha: 1).cgColor
        panel.addSubview(scroll)
    }

    private func renderAudit() { auditTable?.reloadData() }

    // MARK: - 设置页
    private func setupSettingsPanel() {
        let panel = panels[6]
        let title = makeLabel("服务器与系统设置", size: 20, weight: .bold, color: .white)
        title.frame = CGRect(x: 24, y: 650, width: 200, height: 30)
        panel.addSubview(title)

        let cfg = SyncConfig.shared
        jumpHostField = makeTextField(cfg.jumpHost); jumpKeyField = makeTextField(cfg.jumpKey)
        remoteUserField = makeTextField(cfg.remoteUser); remoteHostField = makeTextField(cfg.remoteHost)
        remoteKeyField = makeTextField(cfg.remoteKey); remoteDirField = makeTextField(cfg.remoteDir)
        localDirField = makeTextField(cfg.localDir)

        let labels = ["跳板机 (user@host)", "跳板机密钥路径", "服务器用户", "服务器地址", "服务器密钥路径(远程)", "服务器目录", "本地目录"]
        let fields = [jumpHostField!, jumpKeyField!, remoteUserField!, remoteHostField!, remoteKeyField!, remoteDirField!, localDirField!]
        for (i, (l, f)) in zip(labels, fields).enumerated() {
            let lbl = makeLabel(l, size: 10, weight: .regular, color: NSColor(white: 0.55, alpha: 1))
            lbl.frame = CGRect(x: 24, y: 560 - CGFloat(i) * 42, width: 180, height: 16)
            panel.addSubview(lbl)
            f.frame = CGRect(x: 220, y: 560 - CGFloat(i) * 42 - 14, width: 470, height: 24)
            panel.addSubview(f)
        }

        let testBtn = NSButton(title: "测试连接", target: self, action: #selector(testConnection))
        testBtn.bezelStyle = .rounded; testBtn.font = .systemFont(ofSize: 11, weight: .medium)
        testBtn.frame = CGRect(x: 540, y: 462, width: 90, height: 26)
        testBtn.contentTintColor = NSColor(red: 0.3, green: 0.8, blue: 0.4, alpha: 1)
        panel.addSubview(testBtn)

        connStatusLbl = makeLabel("", size: 10, weight: .medium, color: NSColor(white: 0.5, alpha: 1))
        connStatusLbl.frame = CGRect(x: 24, y: 432, width: 500, height: 18)
        panel.addSubview(connStatusLbl)

        let saveBtn = NSButton(title: "保存设置", target: self, action: #selector(saveSettings))
        saveBtn.bezelStyle = .rounded; saveBtn.font = .systemFont(ofSize: 12, weight: .semibold)
        saveBtn.frame = CGRect(x: 640, y: 460, width: 100, height: 30)
        saveBtn.contentTintColor = NSColor(red: 0, green: 0.6, blue: 0.9, alpha: 1)
        panel.addSubview(saveBtn)

        // .env 同步策略区域
        let envTitle = makeLabel(".env 环境变量同步策略", size: 13, weight: .semibold, color: .white)
        envTitle.frame = CGRect(x: 24, y: 420, width: 200, height: 20)
        panel.addSubview(envTitle)

        envSyncCheckbox = NSButton(checkboxWithTitle: "启用 .env 智能同步", target: self, action: #selector(toggleEnvSync))
        envSyncCheckbox.state = cfg.envSyncEnabled ? .on : .off
        envSyncCheckbox.frame = CGRect(x: 24, y: 398, width: 200, height: 20)
        panel.addSubview(envSyncCheckbox)

        envStatusLbl = makeLabel("", size: 9, weight: .regular, color: NSColor(white: 0.5, alpha: 1))
        envStatusLbl.frame = CGRect(x: 230, y: 398, width: 300, height: 20)
        panel.addSubview(envStatusLbl)

        envRefreshBtn = NSButton(title: "刷新 Key 列表", target: self, action: #selector(refreshEnvKeys))
        envRefreshBtn.bezelStyle = .rounded; envRefreshBtn.font = .systemFont(ofSize: 10, weight: .medium)
        envRefreshBtn.frame = CGRect(x: 540, y: 398, width: 100, height: 20)
        envRefreshBtn.contentTintColor = NSColor(red: 0, green: 0.7, blue: 1, alpha: 1)
        panel.addSubview(envRefreshBtn)

        envSyncBtn = NSButton(title: "同步 .env", target: self, action: #selector(syncEnvNow))
        envSyncBtn.bezelStyle = .rounded; envSyncBtn.font = .systemFont(ofSize: 10, weight: .medium)
        envSyncBtn.frame = CGRect(x: 650, y: 398, width: 90, height: 20)
        envSyncBtn.contentTintColor = NSColor(red: 0.3, green: 0.8, blue: 0.4, alpha: 1)
        panel.addSubview(envSyncBtn)

        envKeysTable = NSTableView()
        for (id, t, w) in [("key","KEY",150),("local","本地值",200),("remote","远程值",200),("sync","同步?",60)] {
            let col = NSTableColumn(identifier: NSUserInterfaceItemIdentifier(id))
            col.title = t; col.width = CGFloat(w)
            envKeysTable.addTableColumn(col)
        }
        envKeysTable.delegate = self; envKeysTable.dataSource = self
        envKeysTable.rowHeight = 22
        envKeysTable.backgroundColor = NSColor(red: 0.07, green: 0.09, blue: 0.14, alpha: 1)
        envKeysTable.selectionHighlightStyle = .none

        let envScroll = NSScrollView(frame: CGRect(x: 24, y: 240, width: 712, height: 150))
        envScroll.documentView = envKeysTable
        envScroll.hasVerticalScroller = true; envScroll.autohidesScrollers = true
        envScroll.wantsLayer = true
        envScroll.layer?.backgroundColor = NSColor(red: 0.07, green: 0.09, blue: 0.14, alpha: 1).cgColor
        envScroll.layer?.cornerRadius = 8; envScroll.layer?.borderWidth = 1
        envScroll.layer?.borderColor = NSColor(red: 0.16, green: 0.22, blue: 0.3, alpha: 1).cgColor
        panel.addSubview(envScroll)

        // .syncignore 编辑器
        let igTitle = makeLabel(".syncignore 规则编辑器", size: 13, weight: .semibold, color: .white)
        igTitle.frame = CGRect(x: 24, y: 210, width: 200, height: 20)
        panel.addSubview(igTitle)
        let igHint = makeLabel("每行一条规则，支持通配符 *，目录加 / 结尾", size: 9, weight: .regular, color: NSColor(white: 0.5, alpha: 1))
        igHint.frame = CGRect(x: 24, y: 192, width: 400, height: 14)
        panel.addSubview(igHint)

        let igScroll = NSScrollView(frame: CGRect(x: 24, y: 40, width: 712, height: 140))
        ignoreEditor = NSTextView()
        ignoreEditor.isRichText = false
        ignoreEditor.font = NSFont(name: "SF Mono", size: 11) ?? .systemFont(ofSize: 11)
        ignoreEditor.textColor = NSColor(white: 0.8, alpha: 1)
        ignoreEditor.backgroundColor = NSColor(red: 0.07, green: 0.09, blue: 0.14, alpha: 1)
        ignoreEditor.string = SyncIgnore.rules.joined(separator: "\n")
        igScroll.documentView = ignoreEditor
        igScroll.hasVerticalScroller = true; igScroll.autohidesScrollers = true
        igScroll.wantsLayer = true
        igScroll.layer?.backgroundColor = NSColor(red: 0.07, green: 0.09, blue: 0.14, alpha: 1).cgColor
        igScroll.layer?.cornerRadius = 8; igScroll.layer?.borderWidth = 1
        igScroll.layer?.borderColor = NSColor(red: 0.16, green: 0.22, blue: 0.3, alpha: 1).cgColor
        panel.addSubview(igScroll)

        let igSaveBtn = NSButton(title: "保存规则", target: self, action: #selector(saveIgnoreRules))
        igSaveBtn.bezelStyle = .rounded; igSaveBtn.font = .systemFont(ofSize: 11)
        igSaveBtn.frame = CGRect(x: 640, y: 655, width: 90, height: 24)
        igSaveBtn.contentTintColor = NSColor(red: 0.16, green: 0.65, blue: 0.27, alpha: 1)
        panel.addSubview(igSaveBtn)
    }

    @objc private func testConnection() {
        connStatusLbl.stringValue = "⟳ 正在测试连接..."
        connStatusLbl.textColor = NSColor(red: 0, green: 0.7, blue: 1, alpha: 1)

        let jumpHost = jumpHostField.stringValue
        let jumpKey = jumpKeyField.stringValue
        let remoteUser = remoteUserField.stringValue
        let remoteHost = remoteHostField.stringValue
        let remoteKey = remoteKeyField.stringValue

        DispatchQueue.global().async {
            let jumpArgs = ["-i", jumpKey, "-o", "StrictHostKeyChecking=no",
                            "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", jumpHost, "echo ok"]
            let (_, jumpCode) = runSSH(jumpArgs)

            DispatchQueue.main.async {
                if jumpCode != 0 {
                    self.connStatusLbl.stringValue = "❌ 跳板机连接失败（exit=\(jumpCode)）— 检查密钥/地址"
                    self.connStatusLbl.textColor = NSColor(red: 0.8, green: 0.3, blue: 0.3, alpha: 1)
                    return
                }
                self.connStatusLbl.stringValue = "✅ 跳板机连接成功 → 正在测试服务器..."
                self.connStatusLbl.textColor = NSColor(red: 0.3, green: 0.8, blue: 0.4, alpha: 1)
            }

            let remoteCmd = "ssh -i \(remoteKey) -o StrictHostKeyChecking=no -o ConnectTimeout=10 -o BatchMode=yes \(remoteUser)@\(remoteHost) 'echo ok'"
            let serverArgs = ["-i", jumpKey, "-o", "StrictHostKeyChecking=no",
                              "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", jumpHost, remoteCmd]
            let (_, serverCode) = runSSH(serverArgs)

            DispatchQueue.main.async {
                if serverCode == 0 {
                    self.connStatusLbl.stringValue = "✅ 连接成功：\(jumpHost) → \(remoteUser)@\(remoteHost)"
                    self.connStatusLbl.textColor = NSColor(red: 0.3, green: 0.8, blue: 0.4, alpha: 1)
                    self.log("连接测试成功：\(jumpHost) → \(remoteUser)@\(remoteHost)")
                    Database.shared.addAudit("测试连接", "成功：\(jumpHost) → \(remoteUser)@\(remoteHost)")
                } else {
                    self.connStatusLbl.stringValue = "❌ 服务器连接失败（exit=\(serverCode)）— 检查远程密钥/地址"
                    self.connStatusLbl.textColor = NSColor(red: 0.8, green: 0.3, blue: 0.3, alpha: 1)
                    self.log("连接测试失败：跳板机OK，服务器失败")
                    Database.shared.addAudit("测试连接", "失败：服务器不可达")
                }
            }
        }
    }

    @objc private func saveSettings() {
        var cfg = SyncConfig.shared
        cfg.jumpHost = jumpHostField.stringValue; cfg.jumpKey = jumpKeyField.stringValue
        cfg.remoteUser = remoteUserField.stringValue; cfg.remoteHost = remoteHostField.stringValue
        cfg.remoteKey = remoteKeyField.stringValue; cfg.remoteDir = remoteDirField.stringValue
        cfg.localDir = localDirField.stringValue
        cfg.envSyncEnabled = envSyncCheckbox.state == .on
        Database.shared.saveConfig(cfg)
        SyncConfig.shared = cfg
        Database.shared.addAudit("配置修改", "更新服务器/路径配置")
        statusLabel.stringValue = "设置已保存 ✅"
        log("设置已保存")
        refreshInfo()
    }

    @objc private func saveIgnoreRules() {
        let rules = ignoreEditor.string.split(separator: "\n").map(String.init).filter { !$0.trimmingCharacters(in: .whitespaces).isEmpty }
        SyncIgnore.rules = rules; SyncIgnore.save()
        Database.shared.addAudit("规则修改", "更新 .syncignore 规则（\(rules.count) 条）")
        statusLabel.stringValue = "规则已保存 ✅"
        log("ignore 规则已保存 (\(rules.count) 条)")
    }

    @objc private func toggleEnvSync() {
        SyncConfig.shared.envSyncEnabled = envSyncCheckbox.state == .on
    }

    @objc private func refreshEnvKeys() {
        envStatusLbl.stringValue = "正在刷新..."
        let localPath = SyncConfig.shared.localDir
        let remotePath = SyncConfig.shared.remoteDir
        EnvSyncEngine.shared.refreshEnvKeys(localPath: localPath, remotePath: remotePath) { success in
            self.envStatusLbl.stringValue = success ? "Key 列表已刷新 ✅" : "刷新失败 ⚠️"
            self.envKeysTable.reloadData()
        }
    }

    @objc private func syncEnvNow() {
        if !SyncConfig.shared.envSyncEnabled {
            envStatusLbl.stringValue = "请先启用 .env 智能同步"
            return
        }
        envStatusLbl.stringValue = "正在同步..."
        EnvSyncEngine.shared.syncEnv(localPath: SyncConfig.shared.localDir, remotePath: SyncConfig.shared.remoteDir) { success, msg in
            self.envStatusLbl.stringValue = msg
            self.log(msg)
        }
    }

    // MARK: - 差异/进度
    private func showDiff(_ diff: DiffResult) {
        currentDiff = diff

        // 更新五色胶囊 (对应设计图 V2.0 风格)
        updateTagBadge(badgeAddedView, text: "🟢 新增 \(diff.added.count)")
        updateTagBadge(badgeModifiedView, text: "🟡 修改 \(diff.modified.count)")
        updateTagBadge(badgeDeletedView, text: "🔴 删除 \(diff.deleted.count)")
        updateTagBadge(badgeConflictView, text: "⚠️ 冲突 \(diff.conflicts.count)")
        updateTagBadge(badgeIgnoreView, text: "⚪ 忽略 0")

        var lines = ""
        for f in diff.added.prefix(12) { lines += "  🟢 [新增] \(f)\n" }
        for f in diff.modified.prefix(12) { lines += "  🟡 [修改] \(f)\n" }
        for f in diff.deleted.prefix(8) { lines += "  🔴 [删除] \(f)\n" }
        for f in diff.conflicts.prefix(8) { lines += "  ⚠️ [冲突] \(f)\n" }
        if diff.total > 40 { lines += "  ... 还有 \(diff.total - 40) 个文件\n" }
        diffList.string = lines
        diffPanel.isHidden = false
        diffPanel.frame = CGRect(x: 24, y: 320, width: 712, height: 160)

        if !diff.conflicts.isEmpty {
            statusLabel.stringValue = "⚠️ 检测到 \(diff.conflicts.count) 个冲突文件，点击「开始同步」进入冲突解决"
            statusLabel.textColor = NSColor(red: 0.9, green: 0.6, blue: 0, alpha: 1)
        } else {
            statusLabel.stringValue = "检测到 \(diff.total) 个变化，请确认同步"
            statusLabel.textColor = NSColor(red: 0.44, green: 0.26, blue: 0.76, alpha: 1)
        }
        state = .preview
    }

    private func updateTagBadge(_ view: NSView, text: String) {
        if let label = view.subviews.first as? NSTextField {
            label.stringValue = text
        }
    }

    private func hideDiff() {
        diffPanel.isHidden = true
        diffPanel.frame = CGRect(x: 24, y: 320, width: 712, height: 0)
        currentDiff = nil
    }

    @objc private func doConfirmSync() {
        hideDiff()
        guard let cat = selectedCategory else { return }
        let dir = SyncEngine.SyncDirection(rawValue: cat.mode) ?? .bidirectional

        if let diff = currentDiff, !diff.conflicts.isEmpty {
            let items = diff.conflicts.map { ConflictResolverWindow.ConflictItem(path: $0, localTime: "-", remoteTime: "-") }
            let resolver = ConflictResolverWindow(conflicts: items) { [weak self] resolutions in
                guard let self = self else { return }
                if resolutions.isEmpty {
                    self.statusLabel.stringValue = "已取消"
                    self.state = .idle
                } else {
                    self.startSync(for: cat, direction: dir, resolutions: resolutions)
                }
            }
            resolver.show()
        } else {
            startSync(for: cat, direction: dir)
        }
    }

    @objc private func doCancelSync() {
        hideDiff()
        statusLabel.stringValue = "已取消"
        statusLabel.textColor = NSColor(white: 0.6, alpha: 1)
        Database.shared.addAudit("取消同步", selectedCategory?.name ?? "?")
        state = .idle
    }

    private func showProgressPanel() {
        progressPanel.isHidden = false
        progressPanel.frame = CGRect(x: 24, y: 320, width: 712, height: 150)
        progressBigBar.doubleValue = 0
        progressPctLbl.stringValue = "0%"
        progressFileLbl.stringValue = "等待开始..."
        progressUpLbl.stringValue = "↑ 0"; progressDownLbl.stringValue = "↓ 0"
    }

    private func hideProgressPanel() {
        progressPanel.isHidden = true
        progressPanel.frame = CGRect(x: 24, y: 320, width: 712, height: 0)
    }

    // MARK: - 同步流程
    @objc private func syncToServer() {
        guard let cat = Database.shared.getCategories().first(where: { $0.isEnabled }) else { return }
        selectedCategory = cat; selectedCatLbl.stringValue = "\(cat.name)"
        triggerScan(for: cat, direction: .toServer)
    }

    @objc private func syncToLocal() {
        guard let cat = Database.shared.getCategories().first(where: { $0.isEnabled }) else { return }
        selectedCategory = cat; selectedCatLbl.stringValue = "\(cat.name)"
        triggerScan(for: cat, direction: .toLocal)
    }

    @objc private func syncBidirectional() {
        guard let cat = Database.shared.getCategories().first(where: { $0.isEnabled }) else { return }
        selectedCategory = cat; selectedCatLbl.stringValue = "\(cat.name)"
        triggerScan(for: cat, direction: .bidirectional)
    }

    private func triggerScan(for cat: SyncCategory, direction: SyncEngine.SyncDirection) {
        currentDirection = direction; state = .scanning
        statusLabel.stringValue = "正在扫描差异..."
        statusLabel.textColor = NSColor(red: 0, green: 0.6, blue: 0.9, alpha: 1)
        monitorStatusLbl.stringValue = "⟳ 扫描差异中..."
        monitorStatusLbl.textColor = NSColor(red: 0, green: 0.7, blue: 1, alpha: 1)
        log("开始扫描 \(cat.name): \(direction.label)")
        Database.shared.addAudit("扫描", "\(cat.name) \(direction.label)")

        engine.scanDiff(for: cat, progress: { msg in self.log(msg) }, completion: { diff in
            DispatchQueue.main.async {
                if let diff = diff, diff.total > 0 {
                    self.showDiff(diff)
                    self.monitorStatusLbl.stringValue = "检测到 \(diff.total) 个变化待确认"
                    self.monitorStatusLbl.textColor = NSColor(red: 0.44, green: 0.26, blue: 0.76, alpha: 1)
                    self.log("扫描完成：新增 \(diff.added.count)，修改 \(diff.modified.count)，删除 \(diff.deleted.count)，冲突 \(diff.conflicts.count)")
                } else if let diff = diff {
                    self.statusLabel.stringValue = "无变化 ✅"
                    self.statusLabel.textColor = NSColor(red: 0.3, green: 0.8, blue: 0.4, alpha: 1)
                    self.monitorStatusLbl.stringValue = "无变化 ✅"
                    self.monitorStatusLbl.textColor = NSColor(red: 0.3, green: 0.8, blue: 0.4, alpha: 1)
                    self.log("无变化"); self.state = .idle
                } else {
                    self.statusLabel.stringValue = "扫描失败，请检查连接"
                    self.statusLabel.textColor = NSColor(red: 0.8, green: 0.3, blue: 0.3, alpha: 1)
                    self.monitorStatusLbl.stringValue = "⚠️ 扫描失败"
                    self.monitorStatusLbl.textColor = NSColor(red: 0.8, green: 0.3, blue: 0.3, alpha: 1)
                    self.log("扫描失败"); self.state = .idle
                }
            }
        })
    }

    private func startSync(for cat: SyncCategory, direction: SyncEngine.SyncDirection, resolutions: [ConflictResolution]? = nil) {
        state = .syncing
        statusLabel.stringValue = "同步中..."
        statusLabel.textColor = NSColor(red: 0, green: 0.6, blue: 0.9, alpha: 1)
        monitorStatusLbl.stringValue = "⟳ 同步中..."
        monitorStatusLbl.textColor = NSColor(red: 0, green: 0.7, blue: 1, alpha: 1)
        showProgressPanel()
        log("开始 \(cat.name): \(direction.label)")
        Database.shared.addAudit("开始同步", "\(cat.name) \(direction.label)")

        engine.runSync(for: cat, direction: direction, resolutions: resolutions,
        progressCallback: { msg in
            self.log(msg)
        }, progressUpdate: { prog in
            DispatchQueue.main.async {
                self.progressBigBar.doubleValue = prog.percent
                self.progressPctLbl.stringValue = "\(Int(prog.percent))%"
                self.monitorBar.doubleValue = prog.percent
                self.monitorPercentLbl.stringValue = "\(Int(prog.percent))%"
                if !prog.speed.isEmpty { self.monitorSpeedLbl.stringValue = prog.speed }
                self.monitorDoneLbl.stringValue = "\(prog.doneFiles)/\(max(prog.totalFiles, prog.doneFiles))"
                if !prog.currentFile.isEmpty && !prog.currentFile.hasSuffix("%") {
                    self.progressFileLbl.stringValue = "正在同步: \(prog.currentFile)"
                }
                self.progressUpLbl.stringValue = "↑ \(prog.uploaded)"
                self.progressDownLbl.stringValue = "↓ \(prog.downloaded)"
            }
        }, completion: { success, msg, fileCount, duration in
            DispatchQueue.main.async {
                self.progressBigBar.doubleValue = success ? 100 : 0
                self.monitorBar.doubleValue = success ? 100 : 0
                self.hideProgressPanel()
                self.statusLabel.stringValue = msg
                self.statusLabel.textColor = success ? NSColor(red: 0.3, green: 0.8, blue: 0.4, alpha: 1) : NSColor(red: 0.8, green: 0.3, blue: 0.3, alpha: 1)
                self.monitorStatusLbl.stringValue = success ? "✅ \(msg)" : "⚠️ \(msg)"
                self.monitorStatusLbl.textColor = success ? NSColor(red: 0.3, green: 0.8, blue: 0.4, alpha: 1) : NSColor(red: 0.8, green: 0.3, blue: 0.3, alpha: 1)
                self.log(msg)

                let entry = HistoryEntry(time: DateFormatter.logStamp.string(from: Date()), timestamp: Date().timeIntervalSince1970,
                    category: cat.name, direction: direction.label, fileCount: fileCount, totalSize: "-",
                    duration: duration, result: success ? "成功" : "失败")
                Database.shared.addHistory(entry)
                Database.shared.addAudit(success ? "同步成功" : "同步失败", "\(cat.name) \(direction.label) 耗时 \(duration)")

                if success {
                    let now = DateFormatter.localizedString(from: Date(), dateStyle: .short, timeStyle: .short)
                    self.statsLastSync.stringValue = now
                    var updated = cat
                    updated.lastSync = now
                    Database.shared.saveCategory(updated)
                    if let cp = self.categoryPanel { cp.refreshCategories() }
                }
                self.state = .done
                self.refreshInfo()
            }
        })
    }

    // MARK: - 辅助
    private func refreshInfo() {
        let info = engine.getLocalInfo()
        localNameLbl.stringValue = info.name; localPathLbl.stringValue = info.path
        localStatusLbl.stringValue = "● 在线"; localFilesLbl.stringValue = "\(info.totalFiles)"
        localSizeLbl.stringValue = info.totalSize; statsTotal.stringValue = "\(info.totalFiles)"

        let cats = Database.shared.getCategories()
        statsPending.stringValue = "\(cats.filter { $0.isEnabled }.count)"

        let history = Database.shared.getHistory()
        let successCount = history.filter { $0.result == "成功" }.count
        statsSuccess.stringValue = history.isEmpty ? "98.5%" : "\(successCount * 100 / max(history.count, 1))%"

        DispatchQueue.global().async {
            let online = self.engine.checkServerOnline()
            DispatchQueue.main.async {
                self.serverStatusLbl.stringValue = online ? "● 在线" : "● 离线"
                self.serverStatusLbl.textColor = online ? NSColor(red: 0.3, green: 0.8, blue: 0.4, alpha: 1) : NSColor(red: 0.8, green: 0.3, blue: 0.3, alpha: 1)
            }
            if online {
                self.engine.getRemoteInfo { info in
                    DispatchQueue.main.async {
                        if let info = info {
                            self.serverNameLbl.stringValue = info.name
                            self.serverPathLbl.stringValue = info.path
                            self.serverFilesLbl.stringValue = "\(info.totalFiles)"
                            self.serverSizeLbl.stringValue = info.totalSize
                        }
                    }
                }
            }
        }
    }

    private func log(_ msg: String) {
        DispatchQueue.main.async {
            let ts = DateFormatter.logStamp.string(from: Date())
            self.logView.string = "[\(ts)] \(msg)\n" + self.logView.string
            if self.logView.string.count > 5000 { self.logView.string = String(self.logView.string.prefix(5000)) }
        }
    }
}

// MARK: - 表格数据源
extension MainViewController: NSTableViewDataSource, NSTableViewDelegate {
    func numberOfRows(in tableView: NSTableView) -> Int {
        if tableView === historyTable {
            return Database.shared.getHistory(filterIndex: historyFilter?.indexOfSelectedItem ?? 0).count
        }
        if tableView === auditTable { return Database.shared.getAudit().count }
        if tableView === envKeysTable { return Database.shared.getEnvKeys().count }
        return 0
    }

    func tableView(_ tableView: NSTableView, viewFor tableColumn: NSTableColumn?, row: Int) -> NSView? {
        let cell = NSTableCellView()
        let label = NSTextField(labelWithString: "")
        label.font = .systemFont(ofSize: 11)
        label.isEditable = false; label.isBordered = false; label.backgroundColor = .clear
        label.frame = CGRect(x: 6, y: 4, width: (tableColumn?.width ?? 100) - 12, height: 18)
        let id = tableColumn?.identifier.rawValue ?? ""

        if tableView === historyTable {
            let entries = Database.shared.getHistory(filterIndex: historyFilter?.indexOfSelectedItem ?? 0)
            guard row < entries.count else { return nil }
            let e = entries[row]
            switch id {
            case "time": label.stringValue = e.time
            case "cat": label.stringValue = e.category
            case "dir": label.stringValue = e.direction; label.textColor = NSColor(red: 0, green: 0.6, blue: 0.9, alpha: 1)
            case "files": label.stringValue = "\(e.fileCount)"
            case "dur": label.stringValue = e.duration
            case "res":
                label.stringValue = e.result
                label.textColor = e.result == "成功" ? NSColor(red: 0.3, green: 0.8, blue: 0.4, alpha: 1) : NSColor(red: 0.8, green: 0.3, blue: 0.3, alpha: 1)
            default: break
            }
        } else if tableView === auditTable {
            let entries = Database.shared.getAudit()
            guard row < entries.count else { return nil }
            let e = entries[row]
            switch id {
            case "time": label.stringValue = e.time
            case "action":
                label.stringValue = e.action
                label.textColor = e.action.contains("成功") ? NSColor(red: 0.3, green: 0.8, blue: 0.4, alpha: 1) :
                                (e.action.contains("失败") || e.action.contains("取消") ? NSColor(red: 0.8, green: 0.3, blue: 0.3, alpha: 1) : NSColor(white: 0.8, alpha: 1))
            case "detail": label.stringValue = e.detail
            default: break
            }
        } else if tableView === envKeysTable {
            let keys = Database.shared.getEnvKeys()
            guard row < keys.count else { return nil }
            let k = keys[row]
            switch id {
            case "key": label.stringValue = k.key; label.font = NSFont(name: "SF Mono", size: 10) ?? .systemFont(ofSize: 10)
            case "local":
                label.stringValue = k.localValue.prefix(30).description + (k.localValue.count > 30 ? "..." : "")
                label.font = NSFont(name: "SF Mono", size: 9) ?? .systemFont(ofSize: 9)
                label.textColor = NSColor(white: 0.7, alpha: 1)
            case "remote":
                label.stringValue = k.remoteValue.prefix(30).description + (k.remoteValue.count > 30 ? "..." : "")
                label.font = NSFont(name: "SF Mono", size: 9) ?? .systemFont(ofSize: 9)
                label.textColor = NSColor(white: 0.7, alpha: 1)
            case "sync":
                label.stringValue = k.shouldSync ? "☑ 同步" : "☐ 保留"
                label.textColor = k.shouldSync ? NSColor(red: 0.3, green: 0.8, blue: 0.4, alpha: 1) : NSColor(red: 0.8, green: 0.6, blue: 0.2, alpha: 1)
            default: break
            }
        }
        cell.addSubview(label)
        return cell
    }
}
