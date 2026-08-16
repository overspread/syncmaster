import Cocoa
import CryptoKit

// MARK: - UI 辅助函数与视觉增强组件

func makeLabel(_ text: String, size: CGFloat, weight: NSFont.Weight, color: NSColor) -> NSTextField {
    let l = NSTextField(labelWithString: text)
    l.font = .systemFont(ofSize: size, weight: weight)
    l.textColor = color
    l.isEditable = false; l.isBordered = false; l.backgroundColor = .clear
    return l
}

// 胶囊 Tag 标签组件
func makeTagBadge(_ text: String, bgR: CGFloat, _ g: CGFloat, _ b: CGFloat, alpha: CGFloat = 0.2, textR: CGFloat, _ tg: CGFloat, _ tb: CGFloat) -> NSView {
    let container = NSView()
    container.wantsLayer = true
    container.layer?.backgroundColor = NSColor(red: bgR, green: g, blue: b, alpha: alpha).cgColor
    container.layer?.cornerRadius = 10
    container.layer?.borderWidth = 1
    container.layer?.borderColor = NSColor(red: textR, green: tg, blue: tb, alpha: 0.3).cgColor

    let label = makeLabel(text, size: 10, weight: .semibold, color: NSColor(red: textR, green: tg, blue: tb, alpha: 1))
    label.alignment = .center
    label.frame = CGRect(x: 8, y: 3, width: 80, height: 14)
    container.addSubview(label)
    return container
}

// 大号三色两行按钮 (设计图 V1.0 风格)
func makeBigSyncBtn(title: String, subtitle: String, r: CGFloat, _ g: CGFloat, _ b: CGFloat) -> NSButton {
    let btn = NSButton()
    btn.wantsLayer = true
    btn.layer?.backgroundColor = NSColor(red: r * 0.15, green: g * 0.15, blue: b * 0.15, alpha: 1).cgColor
    btn.layer?.cornerRadius = 10
    btn.layer?.borderWidth = 1.5
    btn.layer?.borderColor = NSColor(red: r, green: g, blue: b, alpha: 0.6).cgColor
    btn.isBordered = false
    btn.focusRingType = .none

    let mainLbl = makeLabel(title, size: 14, weight: .bold, color: .white)
    mainLbl.alignment = .center
    mainLbl.tag = 101
    btn.addSubview(mainLbl)

    let subLbl = makeLabel(subtitle, size: 10, weight: .regular, color: NSColor(red: r, green: g, blue: b, alpha: 0.9))
    subLbl.alignment = .center
    subLbl.tag = 102
    btn.addSubview(subLbl)

    return btn
}

// 设置按钮 frame 后调用此方法居中子标签
func layoutBigSyncBtn(_ btn: NSButton) {
    let w = btn.frame.width
    for sv in btn.subviews {
        if sv.tag == 101 { sv.frame = CGRect(x: 10, y: 24, width: w - 20, height: 20) }
        if sv.tag == 102 { sv.frame = CGRect(x: 10, y: 6, width: w - 20, height: 14) }
    }
}

func makeCardFrame(_ frame: NSRect) -> NSView {
    let card = NSView(frame: frame)
    card.wantsLayer = true
    card.layer?.backgroundColor = NSColor(red: 0.09, green: 0.13, blue: 0.19, alpha: 1).cgColor
    card.layer?.cornerRadius = 12
    card.layer?.borderWidth = 1
    card.layer?.borderColor = NSColor(red: 0.16, green: 0.22, blue: 0.3, alpha: 1).cgColor
    return card
}

func makeStatField() -> NSTextField {
    let f = NSTextField(labelWithString: "-")
    f.font = .systemFont(ofSize: 20, weight: .bold)
    f.textColor = .white
    f.isEditable = false; f.isBordered = false; f.backgroundColor = .clear
    return f
}

func createStatCard(frame: NSRect, label: String, field: NSTextField, accentColor: NSColor) -> NSView {
    let card = makeCardFrame(frame)

    // 左侧彩色色条
    let bar = NSView(frame: CGRect(x: 0, y: 10, width: 4, height: frame.height - 20))
    bar.wantsLayer = true
    bar.layer?.backgroundColor = accentColor.cgColor
    bar.layer?.cornerRadius = 2
    card.addSubview(bar)

    field.font = .systemFont(ofSize: 20, weight: .bold)
    field.textColor = .white
    field.frame = CGRect(x: 16, y: 35, width: 140, height: 26)
    field.isEditable = false; field.isBordered = false; field.backgroundColor = .clear
    card.addSubview(field)

    let l = makeLabel(label, size: 10, weight: .medium, color: NSColor(white: 0.55, alpha: 1))
    l.frame = CGRect(x: 16, y: 12, width: 140, height: 16)
    card.addSubview(l)

    return card
}

// 带电脑/服务器图标的设备卡片（对应设计图 V1.0）
func createDeviceCard(frame: NSRect, isServer: Bool, title: String, nameLabel: NSTextField, pathLabel: NSTextField,
                      statusLabel: NSTextField, filesLabel: NSTextField, sizeLabel: NSTextField) -> NSView {
    let card = makeCardFrame(frame)

    // 图标（电脑 💻 或 服务器 🖥️）
    let iconLbl = makeLabel(isServer ? "🖥️" : "💻", size: 28, weight: .regular, color: .white)
    iconLbl.frame = CGRect(x: 16, y: frame.height - 48, width: 36, height: 36)
    card.addSubview(iconLbl)

    let t = makeLabel(title, size: 10, weight: .semibold, color: NSColor(white: 0.5, alpha: 1))
    t.frame = CGRect(x: 58, y: frame.height - 26, width: 100, height: 16); card.addSubview(t)

    nameLabel.font = .systemFont(ofSize: 15, weight: .bold); nameLabel.textColor = .white
    nameLabel.frame = CGRect(x: 58, y: frame.height - 48, width: 270, height: 22)
    nameLabel.isEditable = false; nameLabel.isBordered = false; nameLabel.backgroundColor = .clear; card.addSubview(nameLabel)

    pathLabel.font = NSFont(name: "SF Mono", size: 9) ?? .systemFont(ofSize: 9)
    pathLabel.textColor = NSColor(white: 0.5, alpha: 1)
    pathLabel.frame = CGRect(x: 16, y: 36, width: 320, height: 14)
    pathLabel.isEditable = false; pathLabel.isBordered = false; pathLabel.backgroundColor = .clear; card.addSubview(pathLabel)

    // 在线状态胶囊
    statusLabel.font = .systemFont(ofSize: 10, weight: .bold)
    statusLabel.textColor = NSColor(red: 0.3, green: 0.8, blue: 0.4, alpha: 1)
    statusLabel.frame = CGRect(x: 16, y: 12, width: 100, height: 16)
    statusLabel.isEditable = false; statusLabel.isBordered = false; statusLabel.backgroundColor = .clear; card.addSubview(statusLabel)

    let ft = makeLabel("文件数", size: 9, weight: .regular, color: NSColor(white: 0.5, alpha: 1))
    ft.frame = CGRect(x: 210, y: 12, width: 40, height: 14); card.addSubview(ft)
    filesLabel.font = .systemFont(ofSize: 15, weight: .bold); filesLabel.textColor = .white
    filesLabel.frame = CGRect(x: 210, y: 26, width: 60, height: 20)
    filesLabel.isEditable = false; filesLabel.isBordered = false; filesLabel.backgroundColor = .clear; card.addSubview(filesLabel)

    let st = makeLabel("总大小", size: 9, weight: .regular, color: NSColor(white: 0.5, alpha: 1))
    st.frame = CGRect(x: 275, y: 12, width: 40, height: 14); card.addSubview(st)
    sizeLabel.font = .systemFont(ofSize: 13, weight: .bold); sizeLabel.textColor = NSColor(red: 0, green: 0.7, blue: 1, alpha: 1)
    sizeLabel.frame = CGRect(x: 275, y: 26, width: 70, height: 18)
    sizeLabel.isEditable = false; sizeLabel.isBordered = false; sizeLabel.backgroundColor = .clear; card.addSubview(sizeLabel)

    return card
}

func makeTextField(_ initial: String) -> NSTextField {
    let f = NSTextField(string: initial)
    f.font = NSFont(name: "SF Mono", size: 11) ?? .systemFont(ofSize: 11)
    f.textColor = .white
    f.backgroundColor = NSColor(red: 0.09, green: 0.13, blue: 0.19, alpha: 1)
    f.isBezeled = true; f.bezelStyle = .roundedBezel
    f.appearance = NSAppearance(named: .darkAqua)
    return f
}

// MARK: - 快速 Hash
func quickFileHash(_ path: String) -> FileHash? {
    let fm = FileManager.default
    guard let attrs = try? fm.attributesOfItem(atPath: path) else { return nil }
    let size = (attrs[.size] as? Int64) ?? 0
    let modTime = (attrs[.modificationDate] as? Date)?.timeIntervalSince1970 ?? 0
    guard let fh = FileHandle(forReadingAtPath: path) else { return nil }
    defer { fh.closeFile() }

    var hasher = SHA256()
    hasher.update(data: withUnsafeBytes(of: size.bigEndian) { Data($0) })
    let modTimeBits = modTime.bitPattern
    hasher.update(data: withUnsafeBytes(of: modTimeBits.bigEndian) { Data($0) })

    let chunkSize: Int = 65536
    if size <= Int64(chunkSize * 2) {
        let data = fh.readDataToEndOfFile()
        hasher.update(data: data)
    } else {
        let head = fh.readData(ofLength: chunkSize)
        hasher.update(data: head)
        fh.seekToEndOfFile()
        let endPos = fh.offsetInFile
        fh.seek(toFileOffset: endPos - UInt64(chunkSize))
        let tail = fh.readData(ofLength: chunkSize)
        hasher.update(data: tail)
    }
    let hash = hasher.finalize().compactMap { String(format: "%02x", $0) }.joined()
    return FileHash(hash: hash, size: size, modTime: modTime)
}

// MARK: - 远程 SSH 执行
func runSSH(_ args: [String]) -> (output: String, exitCode: Int32) {
    let p = Process(); p.executableURL = URL(fileURLWithPath: "/usr/bin/ssh")
    p.arguments = args
    let pipe = Pipe(); p.standardOutput = pipe; p.standardError = Pipe()
    do {
        try p.run(); p.waitUntilExit()
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        return (String(data: data, encoding: .utf8) ?? "", p.terminationStatus)
    } catch { return ("", -1) }
}
