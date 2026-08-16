import Cocoa

// MARK: - 应用内文件浏览器
class FileBrowserPanel: NSView, NSTableViewDataSource, NSTableViewDelegate, NSSearchFieldDelegate {

    struct FileItem {
        let name: String
        let path: String
        let isDirectory: Bool
        let size: Int64
        let modTime: Date
    }

    private(set) var currentPath: String
    private var items: [FileItem] = []
    private var filteredItems: [FileItem] = []
    private var selectedPaths: Set<String> = []
    private var searchQuery: String = ""

    var onSelectionChange: (([String]) -> Void)?

    private let pathBar: NSTextField
    private let upBtn: NSButton
    private let homeBtn: NSButton
    private let searchField: NSSearchField
    private let tableView: NSTableView
    private let scrollView: NSScrollView
    private let selectedLabel: NSTextField

    init(frame: NSRect, initialPath: String) {
        currentPath = initialPath
        pathBar = NSTextField(frame: .zero)
        upBtn = NSButton(title: "↑", target: nil, action: nil)
        homeBtn = NSButton(title: "🏠", target: nil, action: nil)
        searchField = NSSearchField(frame: .zero)
        tableView = NSTableView()
        scrollView = NSScrollView()
        selectedLabel = NSTextField(labelWithString: "已选择 0 项")
        super.init(frame: frame)
        wantsLayer = true
        layer?.backgroundColor = NSColor(red: 0.07, green: 0.1, blue: 0.15, alpha: 1).cgColor
        layer?.cornerRadius = 8
        setupUI()
        navigate(to: currentPath)
    }

    required init?(coder: NSCoder) { fatalError() }

    private func setupUI() {
        let topY: CGFloat = frame.height - 28

        homeBtn.bezelStyle = .rounded; homeBtn.font = .systemFont(ofSize: 12)
        homeBtn.frame = CGRect(x: 8, y: topY, width: 36, height: 22)
        homeBtn.target = self; homeBtn.action = #selector(goHome)
        addSubview(homeBtn)

        upBtn.bezelStyle = .rounded; upBtn.font = .systemFont(ofSize: 12)
        upBtn.frame = CGRect(x: 48, y: topY, width: 36, height: 22)
        upBtn.target = self; upBtn.action = #selector(goUp)
        addSubview(upBtn)

        pathBar.font = NSFont(name: "SF Mono", size: 10) ?? .systemFont(ofSize: 10)
        pathBar.textColor = NSColor(white: 0.7, alpha: 1)
        pathBar.backgroundColor = NSColor(red: 0.1, green: 0.14, blue: 0.2, alpha: 1)
        pathBar.isBezeled = true; pathBar.bezelStyle = .roundedBezel
        pathBar.frame = CGRect(x: 90, y: topY, width: frame.width - 220, height: 22)
        pathBar.target = self; pathBar.action = #selector(pathEntered)
        addSubview(pathBar)

        searchField.placeholderString = "搜索文件名..."
        searchField.frame = CGRect(x: frame.width - 120, y: topY, width: 112, height: 22)
        searchField.target = self; searchField.action = #selector(searchChanged)
        searchField.delegate = self
        searchField.font = .systemFont(ofSize: 11)
        addSubview(searchField)

        tableView.dataSource = self; tableView.delegate = self
        tableView.rowHeight = 24
        tableView.backgroundColor = NSColor(red: 0.07, green: 0.1, blue: 0.15, alpha: 1)
        tableView.selectionHighlightStyle = .none
        tableView.allowsMultipleSelection = true

        let nameCol = NSTableColumn(identifier: NSUserInterfaceItemIdentifier("name"))
        nameCol.title = "名称"; nameCol.width = frame.width - 180
        tableView.addTableColumn(nameCol)
        let sizeCol = NSTableColumn(identifier: NSUserInterfaceItemIdentifier("size"))
        sizeCol.title = "大小"; sizeCol.width = 80
        tableView.addTableColumn(sizeCol)
        let dateCol = NSTableColumn(identifier: NSUserInterfaceItemIdentifier("date"))
        dateCol.title = "修改时间"; dateCol.width = 100
        tableView.addTableColumn(dateCol)

        scrollView.frame = CGRect(x: 4, y: 30, width: frame.width - 8, height: frame.height - 64)
        scrollView.documentView = tableView
        scrollView.hasVerticalScroller = true; scrollView.autohidesScrollers = true
        scrollView.drawsBackground = false
        addSubview(scrollView)

        selectedLabel.font = .systemFont(ofSize: 10, weight: .medium)
        selectedLabel.textColor = NSColor(red: 0, green: 0.7, blue: 1, alpha: 1)
        selectedLabel.frame = CGRect(x: 8, y: 8, width: frame.width - 16, height: 16)
        selectedLabel.isEditable = false; selectedLabel.isBordered = false; selectedLabel.backgroundColor = .clear
        addSubview(selectedLabel)
    }

    // MARK: - 导航
    func navigate(to path: String) {
        var p = path
        p = p.replacingOccurrences(of: "~", with: NSHomeDirectory())
        var isDir: ObjCBool = false
        if FileManager.default.fileExists(atPath: p, isDirectory: &isDir), !isDir.boolValue {
            p = (p as NSString).deletingLastPathComponent
        }
        currentPath = p
        pathBar.stringValue = p
        loadDirectory()
    }

    private func loadDirectory() {
        items = []
        let fm = FileManager.default
        guard let contents = try? fm.contentsOfDirectory(atPath: currentPath) else {
            filteredItems = []; tableView.reloadData(); return
        }
        for name in contents {
            let full = (currentPath as NSString).appendingPathComponent(name)
            if SyncIgnore.shouldExclude(full) { continue }
            let attrs = try? fm.attributesOfItem(atPath: full)
            let isDir = (attrs?[.type] as? String) == FileAttributeType.typeDirectory.rawValue
            let size = (attrs?[.size] as? Int64) ?? 0
            let modTime = (attrs?[.modificationDate] as? Date) ?? Date()
            items.append(FileItem(name: name, path: full, isDirectory: isDir, size: size, modTime: modTime))
        }
        items.sort { ($0.isDirectory == $1.isDirectory) ? ($0.name.lowercased() < $1.name.lowercased()) : $0.isDirectory }
        applyFilter()
    }

    private func applyFilter() {
        if searchQuery.isEmpty {
            filteredItems = items
        } else {
            filteredItems = items.filter { $0.name.lowercased().contains(searchQuery.lowercased()) }
        }
        tableView.reloadData()
    }

    @objc private func goHome() {
        navigate(to: NSHomeDirectory())
    }

    @objc private func goUp() {
        let parent = (currentPath as NSString).deletingLastPathComponent
        if !parent.isEmpty { navigate(to: parent) }
    }

    @objc private func pathEntered() {
        navigate(to: pathBar.stringValue)
    }

    @objc private func searchChanged() {
        searchQuery = searchField.stringValue
        applyFilter()
    }

    func control(_ control: NSControl, textView: NSTextView, doCommandBy commandSelector: Selector) -> Bool {
        if commandSelector == #selector(NSResponder.insertNewline(_:)) {
            if control === searchField { searchChanged() }
            else if control === pathBar { pathEntered() }
            return true
        }
        return false
    }

    // MARK: - 选择管理
    func toggleSelection(_ path: String) {
        if selectedPaths.contains(path) { selectedPaths.remove(path) }
        else { selectedPaths.insert(path) }
        updateSelectedLabel()
        onSelectionChange?(getSelectedPaths())
        tableView.reloadData()
    }

    func getSelectedPaths() -> [String] {
        return Array(selectedPaths).sorted()
    }

    func setSelectedPaths(_ paths: [String]) {
        selectedPaths = Set(paths)
        updateSelectedLabel()
        tableView.reloadData()
    }

    @objc func clearSelection() {
        selectedPaths.removeAll()
        updateSelectedLabel()
        tableView.reloadData()
    }

    private func updateSelectedLabel() {
        selectedLabel.stringValue = "已选择 \(selectedPaths.count) 项"
    }

    // MARK: - NSTableViewDataSource
    func numberOfRows(in tableView: NSTableView) -> Int {
        return filteredItems.count
    }

    func tableView(_ tableView: NSTableView, viewFor tableColumn: NSTableColumn?, row: Int) -> NSView? {
        guard row < filteredItems.count else { return nil }
        let item = filteredItems[row]
        let cell = NSTableCellView()
        let label = NSTextField(labelWithString: "")
        label.font = .systemFont(ofSize: 11)
        label.isEditable = false; label.isBordered = false; label.backgroundColor = .clear
        label.frame = CGRect(x: 6, y: 3, width: (tableColumn?.width ?? 100) - 12, height: 18)

        let id = tableColumn?.identifier.rawValue ?? ""
        switch id {
        case "name":
            let prefix = item.isDirectory ? "📁 " : "📄 "
            label.stringValue = prefix + item.name
            if selectedPaths.contains(item.path) {
                label.textColor = NSColor(red: 0, green: 0.7, blue: 1, alpha: 1)
                cell.layer?.backgroundColor = NSColor(red: 0.1, green: 0.16, blue: 0.24, alpha: 1).cgColor
            } else {
                label.textColor = NSColor(white: 0.8, alpha: 1)
            }
        case "size":
            label.stringValue = item.isDirectory ? "--" : ByteCountFormatter.string(fromByteCount: item.size, countStyle: .file)
            label.font = NSFont(name: "SF Mono", size: 10) ?? .systemFont(ofSize: 10)
            label.textColor = NSColor(white: 0.5, alpha: 1)
        case "date":
            let df = DateFormatter(); df.dateFormat = "MM-dd HH:mm"
            label.stringValue = df.string(from: item.modTime)
            label.font = NSFont(name: "SF Mono", size: 10) ?? .systemFont(ofSize: 10)
            label.textColor = NSColor(white: 0.5, alpha: 1)
        default: break
        }

        cell.wantsLayer = true
        cell.addSubview(label)
        return cell
    }

    func tableView(_ tableView: NSTableView, shouldSelectRow row: Int) -> Bool { true }

    func tableView(_ tableView: NSTableView, doubleAction: Selector?) {
        // handled in mouseDown
    }

    override func mouseDown(with event: NSEvent) {
        let point = tableView.convert(event.locationInWindow, from: nil)
        let row = tableView.row(at: point)
        if row >= 0 && row < filteredItems.count {
            let item = filteredItems[row]
            if item.isDirectory {
                // 双击进入目录，单击切换选择
                if event.clickCount >= 2 { navigate(to: item.path) }
                else { toggleSelection(item.path) }
            } else {
                toggleSelection(item.path)
            }
        }
    }
}
