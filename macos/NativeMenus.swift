import AppKit
import WebKit

// macOS integration: the main menu, the menu bar item, the Dock badge and the engine poll that keeps them current.
// Listener and Intercept toggles run through the page's own controls (window.dopamineNative) so Intercept drafts
// and busy states are respected; agent access is set through the engine's local API.

struct EngineState {
    var running: Bool
    var port: Int
    var intercept: Bool
    var held: Int
}

private enum Tag: Int {
    case listenerLine = 1, interceptLine, toggleListener, toggleIntercept, review, copyAddress
    case agentRead, agentWorkspace, agentActive, agentEverything, agentOff, appearance, startupSound, projectItem, openBrowser
}

extension DopamineApp: NSMenuDelegate, NSMenuItemValidation {

    // MARK: Menus

    private func item(_ title: String, _ action: Selector?, _ key: String = "", _ modifiers: NSEvent.ModifierFlags = .command,
                      tag: Tag? = nil, view: String? = nil, own: Bool = true) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: key)
        item.keyEquivalentModifierMask = modifiers
        if own { item.target = self }
        if let tag { item.tag = tag.rawValue }
        item.representedObject = view
        return item
    }

    private func submenu(_ title: String, _ items: [NSMenuItem], into bar: NSMenu) -> NSMenu {
        let menu = NSMenu(title: title)
        items.forEach(menu.addItem)
        let holder = NSMenuItem(title: title, action: nil, keyEquivalent: "")
        holder.submenu = menu
        bar.addItem(holder)
        return menu
    }

    private func agentAccessMenu() -> NSMenuItem {
        let holder = NSMenuItem(title: "Agent Access", action: nil, keyEquivalent: "")
        let menu = NSMenu(title: "Agent Access")
        menu.addItem(item("Read", #selector(setAgentAccess(_:)), tag: .agentRead))
        menu.addItem(item("Workspace", #selector(setAgentAccess(_:)), tag: .agentWorkspace))
        menu.addItem(item("Active", #selector(setAgentAccess(_:)), tag: .agentActive))
        menu.addItem(item("Everything", #selector(setAgentAccess(_:)), tag: .agentEverything))
        menu.addItem(item("Disabled", #selector(setAgentAccess(_:)), tag: .agentOff))
        menu.addItem(.separator())
        menu.addItem(item("Agent Setup…", #selector(goView(_:)), view: "agents"))
        menu.delegate = self
        holder.submenu = menu
        return holder
    }

    func buildMainMenu() -> NSMenu {
        let bar = NSMenu()
        let services = NSMenu(title: "Services")
        let servicesItem = NSMenuItem(title: "Services", action: nil, keyEquivalent: "")
        servicesItem.submenu = services
        NSApp.servicesMenu = services
        _ = submenu("Dopamine", [
            item("About Dopamine", #selector(about)),
            .separator(),
            item("Settings…", #selector(goView(_:)), ",", view: "settings"),
            item("Startup Sound", #selector(toggleStartupSound), tag: .startupSound),
            .separator(),
            servicesItem,
            .separator(),
            item("Hide Dopamine", #selector(NSApplication.hide(_:)), "h", own: false),
            item("Hide Others", #selector(NSApplication.hideOtherApplications(_:)), "h", [.command, .option], own: false),
            item("Show All", #selector(NSApplication.unhideAllApplications(_:)), own: false),
            .separator(),
            item("Quit Dopamine", #selector(NSApplication.terminate(_:)), "q", own: false),
        ], into: bar)
        let recent = NSMenuItem(title: "Open Recent", action: nil, keyEquivalent: "")
        recent.submenu = NSMenu(title: "Open Recent")
        recent.submenu?.delegate = self
        _ = submenu("File", [
            item("New Project", #selector(newProject), "n", tag: .projectItem),
            item("Open Project…", #selector(openProject), "o", tag: .projectItem),
            recent,
            item("Rename or Move Project…", #selector(moveProject), tag: .projectItem),
            item("Show Project in Finder", #selector(showProjectInFinder), tag: .projectItem),
            .separator(),
            item("New Repeater Tab", #selector(newRepeaterTab), "t"),
            item("Import cURL…", #selector(importCurl), "i", [.command, .shift]),
            .separator(),
            item("Close Window", #selector(NSWindow.performClose(_:)), "w", own: false),
        ], into: bar)
        _ = submenu("Edit", [
            item("Undo", Selector(("undo:")), "z", own: false),
            item("Redo", Selector(("redo:")), "z", [.command, .shift], own: false),
            .separator(),
            item("Cut", #selector(NSText.cut(_:)), "x", own: false),
            item("Copy", #selector(NSText.copy(_:)), "c", own: false),
            item("Paste", #selector(NSText.paste(_:)), "v", own: false),
            item("Select All", #selector(NSText.selectAll(_:)), "a", own: false),
            .separator(),
            item("Find…", #selector(findInView), "f"),
        ], into: bar)
        _ = submenu("View", [
            item("Site Map", #selector(goView(_:)), "1", view: "target"),
            item("HTTP History", #selector(goView(_:)), "2", view: "history"),
            item("Intercept", #selector(goView(_:)), "3", view: "intercept"),
            item("Repeater", #selector(goView(_:)), "4", view: "repeater"),
            item("Decoder", #selector(goView(_:)), "5", view: "decoder"),
            item("Agents", #selector(goView(_:)), "6", view: "agents"),
            .separator(),
            item("Use Light Appearance", #selector(toggleAppearance), "l", [.command, .shift], tag: .appearance),
            .separator(),
            item("Enter Full Screen", #selector(NSWindow.toggleFullScreen(_:)), "f", [.command, .control], own: false),
        ], into: bar).delegate = self
        _ = submenu("Proxy", [
            item("Start Listener", #selector(toggleListener), "p", [.command, .shift], tag: .toggleListener),
            item("Intercept Requests", #selector(toggleIntercept), "i", tag: .toggleIntercept),
            item("Open Intercept", #selector(goView(_:)), tag: .review, view: "intercept"),
            item("Open Browser", #selector(openBrowser), "b", [.command, .shift], tag: .openBrowser),
            .separator(),
            item("Scope", #selector(goView(_:)), view: "scope"),
            item("Match and Replace", #selector(goView(_:)), view: "rules"),
            .separator(),
            item("Copy Proxy Address", #selector(copyProxyAddress), tag: .copyAddress),
            item("HTTPS Certificate…", #selector(goView(_:)), view: "settings"),
            .separator(),
            agentAccessMenu(),
        ], into: bar).delegate = self
        let window = submenu("Window", [
            item("Minimize", #selector(NSWindow.performMiniaturize(_:)), "m", own: false),
            item("Zoom", #selector(NSWindow.performZoom(_:)), own: false),
            .separator(),
            item("Main Window", #selector(showWindowAction), "0"),
            .separator(),
            item("Bring All to Front", #selector(NSApplication.arrangeInFront(_:)), own: false),
        ], into: bar)
        NSApp.windowsMenu = window
        NSApp.helpMenu = submenu("Help", [
            item("Dopamine Guide", #selector(goView(_:)), "?", view: "guide"),
        ], into: bar)
        return bar
    }

    func setUpStatusItem() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.image = Self.sprout()
        statusItem.button?.imagePosition = .imageLeft
        statusItem.button?.toolTip = "Dopamine"
        let menu = NSMenu(title: "Dopamine")
        menu.addItem(withTitle: "Starting Dopamine…", action: nil, keyEquivalent: "").tag = Tag.listenerLine.rawValue
        menu.addItem(withTitle: "", action: nil, keyEquivalent: "").tag = Tag.interceptLine.rawValue
        menu.addItem(.separator())
        menu.addItem(item("Start Listener", #selector(toggleListener), tag: .toggleListener))
        menu.addItem(item("Intercept Requests", #selector(toggleIntercept), tag: .toggleIntercept))
        menu.addItem(item("Open Intercept", #selector(goView(_:)), tag: .review, view: "intercept"))
        menu.addItem(item("Open Browser", #selector(openBrowser), tag: .openBrowser))
        menu.addItem(.separator())
        menu.addItem(item("Copy Proxy Address", #selector(copyProxyAddress), tag: .copyAddress))
        menu.addItem(agentAccessMenu())
        menu.addItem(.separator())
        menu.addItem(item("Open Dopamine", #selector(showWindowAction)))
        menu.addItem(item("Quit Dopamine", #selector(NSApplication.terminate(_:)), own: false))
        menu.delegate = self
        statusItem.menu = menu
        updateIndicators()
    }

    func menuNeedsUpdate(_ menu: NSMenu) {
        if menu.title == "Open Recent" { fillRecentMenu(menu); return }
        let theme = UserDefaults.standard.string(forKey: "theme") ?? "dark"
        for item in menu.items {
            switch Tag(rawValue: item.tag) {
            case .listenerLine:
                item.title = engine.map { $0.running ? "Listening on 127.0.0.1:\($0.port)" : "Proxy stopped" } ?? "Starting Dopamine…"
            case .interceptLine:
                item.isHidden = engine == nil
                if let e = engine { item.title = "Intercept \(e.intercept ? "on" : "off")" + (e.held > 0 ? " · \(e.held) held" : "") }
            case .toggleListener:
                item.title = engine?.running == true ? "Stop Listener" : "Start Listener"
            case .toggleIntercept:
                item.state = engine?.intercept == true ? .on : .off
            case .review:
                let held = engine?.held ?? 0
                item.title = held > 0 ? "Review \(held) Held \(held == 1 ? "Message" : "Messages")…" : "Open Intercept"
            case .copyAddress:
                item.title = "Copy Proxy Address" + (engine.map { " (127.0.0.1:\($0.port))" } ?? "")
            case .agentRead:
                item.state = agentAccess.map { $0.enabled && $0.access == "read" } == true ? .on : .off
            case .agentWorkspace:
                item.state = agentAccess.map { $0.enabled && $0.access == "workspace" } == true ? .on : .off
            case .agentActive:
                item.state = agentAccess.map { $0.enabled && $0.access == "active" } == true ? .on : .off
            case .agentEverything:
                item.state = agentAccess.map { $0.enabled && $0.access == "everything" } == true ? .on : .off
            case .agentOff:
                item.state = agentAccess?.enabled == false ? .on : .off
            case .appearance:
                item.title = theme == "light" ? "Use Dark Appearance" : "Use Light Appearance"
            case .startupSound, .projectItem, .openBrowser, nil:
                break
            }
        }
    }

    func validateMenuItem(_ item: NSMenuItem) -> Bool {
        switch Tag(rawValue: item.tag) {
        case .copyAddress, .openBrowser: return engine != nil
        case .projectItem: return engine != nil && (item.action == #selector(newProject) || item.action == #selector(openProject) || project != nil)
        case .startupSound:
            item.state = UserDefaults.standard.object(forKey: "startupSound") as? Bool ?? true ? .on : .off
            return true
        case .agentRead, .agentWorkspace, .agentActive, .agentEverything, .agentOff: return agentAccess != nil
        default:
            if item.action == #selector(about) || item.action == #selector(showWindowAction) { return true }
            return webReady
        }
    }

    // MARK: Actions

    /// Calls the page's native bridge. Navigation shows the window; listener and Intercept toggles do not.
    private func page(_ call: String, show: Bool) {
        if show { showWindow() }
        guard webReady else { return }
        web.evaluateJavaScript("window.dopamineNative && window.dopamineNative.\(call)", completionHandler: nil)
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { self.poll() }
    }

    @objc func showWindowAction() { showWindow() }
    func showWindow() {
        if window.isMiniaturized { window.deminiaturize(nil) }
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    @objc func goView(_ sender: NSMenuItem) {
        guard let view = sender.representedObject as? String else { return }
        page("go('\(view)')", show: true)
    }
    @objc func newRepeaterTab() { page("newTab()", show: true) }
    @objc func importCurl() { page("importCurl()", show: true) }
    @objc func findInView() { page("find()", show: true) }
    @objc func toggleAppearance() { page("toggleTheme()", show: false) }
    @objc func toggleStartupSound() {
        let on = UserDefaults.standard.object(forKey: "startupSound") as? Bool ?? true
        UserDefaults.standard.set(!on, forKey: "startupSound")
    }
    @objc func toggleListener() { page("toggleProxy()", show: false) }
    @objc func toggleIntercept() { page("toggleIntercept()", show: false) }

    @objc func copyProxyAddress() {
        guard let engine else { return }
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString("127.0.0.1:\(engine.port)", forType: .string)
    }

    @objc func setAgentAccess(_ sender: NSMenuItem) {
        let body: [String: Any]
        switch Tag(rawValue: sender.tag) {
        case .agentRead: body = ["enabled": true, "access": "read"]
        case .agentWorkspace: body = ["enabled": true, "access": "workspace"]
        case .agentActive: body = ["enabled": true, "access": "active"]
        case .agentEverything: body = ["enabled": true, "access": "everything"]
        default: body = ["enabled": false]
        }
        request("/api/mcp/settings", body: body) { _ in self.poll() }
    }

    // MARK: Engine poll, menu bar item and Dock

    func startPolling() {
        pollTimer?.invalidate()
        pollTimer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in self?.poll() }
        poll()
    }

    func poll() {
        guard !polling, !closing else { return }
        polling = true
        request("/api/status") { status in
            self.request("/api/mcp/settings") { settings in
                self.polling = false
                if let s = status, let running = s["proxy_running"] as? Bool {
                    self.engine = EngineState(running: running, port: s["proxy_port"] as? Int ?? 18080,
                                              intercept: s["intercept"] as? Bool ?? false, held: s["pending_count"] as? Int ?? 0)
                    if let path = (s["project"] as? [String: Any])?["path"] as? String, path != self.project?.path { self.refreshProject() }
                } else {
                    self.engine = nil
                }
                if let m = settings, let enabled = m["enabled"] as? Bool {
                    self.agentAccess = (enabled, m["access"] as? String ?? "read")
                }
                self.updateIndicators()
            }
        }
    }

    /// Menu bar title and dimming, Dock badge, and one bounce when a message is held while Dopamine is in the background.
    func updateIndicators() {
        let held = engine?.held ?? 0
        if let button = statusItem?.button {
            button.title = held > 0 ? " \(held)" : ""
            button.appearsDisabled = engine?.running != true
            button.toolTip = engine.map { e in
                "Dopamine · " + (e.running ? "Listening on 127.0.0.1:\(e.port)" : "Proxy stopped") + (e.held > 0 ? " · \(e.held) held" : "")
            } ?? "Dopamine"
        }
        NSApp.dockTile.badgeLabel = held > 0 ? String(held) : nil
        if held > lastHeld && !NSApp.isActive { NSApp.requestUserAttention(.informationalRequest) }
        lastHeld = held
    }

    /// GET (no body) or POST JSON to the local engine; the completion runs on the main queue.
    func request(_ path: String, body: [String: Any]? = nil, timeout: TimeInterval = 2, completion: @escaping ([String: Any]?) -> Void) {
        var request = URLRequest(url: URL(string: "http://127.0.0.1:\(port)\(path)")!)
        request.timeoutInterval = timeout
        request.setValue(apiToken, forHTTPHeaderField: "X-Dopamine-Token")
        if let body {
            request.httpMethod = "POST"
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try? JSONSerialization.data(withJSONObject: body)
        }
        URLSession.shared.dataTask(with: request) { data, _, _ in
            let object = data.flatMap { try? JSONSerialization.jsonObject(with: $0) } as? [String: Any]
            DispatchQueue.main.async { completion(object) }
        }.resume()
    }

    /// The Dopamine sprout from the web mark (28-unit SVG box), drawn as a template image for the menu bar.
    static func sprout() -> NSImage {
        let image = NSImage(size: NSSize(width: 18, height: 18), flipped: true) { _ in
            let scale = NSAffineTransform()
            scale.scale(by: 18.0 / 28.0)
            scale.concat()
            NSColor.black.set()
            let outline = NSBezierPath()
            outline.move(to: NSPoint(x: 14, y: 25))
            outline.line(to: NSPoint(x: 14, y: 13))
            outline.move(to: NSPoint(x: 14, y: 16))
            outline.curve(to: NSPoint(x: 3, y: 6), controlPoint1: NSPoint(x: 6, y: 17), controlPoint2: NSPoint(x: 3, y: 11))
            outline.curve(to: NSPoint(x: 14, y: 16), controlPoint1: NSPoint(x: 11, y: 5), controlPoint2: NSPoint(x: 15, y: 9))
            outline.lineWidth = 2.2
            outline.lineJoinStyle = .round
            outline.lineCapStyle = .round
            outline.stroke()
            let leaf = NSBezierPath()
            leaf.move(to: NSPoint(x: 14, y: 12))
            leaf.curve(to: NSPoint(x: 25, y: 3), controlPoint1: NSPoint(x: 13, y: 5), controlPoint2: NSPoint(x: 18, y: 2))
            leaf.curve(to: NSPoint(x: 14, y: 12), controlPoint1: NSPoint(x: 25, y: 10), controlPoint2: NSPoint(x: 21, y: 14))
            leaf.close()
            leaf.fill()
            return true
        }
        image.isTemplate = true
        image.accessibilityDescription = "Dopamine"
        return image
    }
}
