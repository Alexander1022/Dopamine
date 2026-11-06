import AppKit
import UniformTypeIdentifiers

// Projects and Open Browser. The engine owns the .dopamine files (engine/project.py); these are the native
// dialogs around its /api/project calls, plus launching a Chromium browser with a throwaway profile on the proxy.

struct ProjectState: Equatable {
    var name: String
    var path: String
}

extension DopamineApp {

    static let projectType = UTType(filenameExtension: "dopamine") ?? .data

    // MARK: Project files

    @objc func newProject() { switchProject(["action": "new"]) }

    @objc func openProject() {
        let panel = NSOpenPanel()
        panel.title = "Open Project"
        panel.allowedContentTypes = [Self.projectType]
        panel.allowsMultipleSelection = false
        panel.directoryURL = projectsFolder
        presentPanel(panel) { url in self.switchProject(["action": "open", "path": url.path]) }
    }

    @objc func openRecentProject(_ sender: NSMenuItem) {
        guard let path = sender.representedObject as? String else { return }
        switchProject(["action": "open", "path": path])
    }

    @objc func moveProject() {
        guard let project else { return }
        let panel = NSSavePanel()
        panel.title = "Rename or Move Project"
        panel.prompt = "Save"
        panel.nameFieldStringValue = project.name
        panel.allowedContentTypes = [Self.projectType]
        panel.directoryURL = URL(fileURLWithPath: project.path).deletingLastPathComponent()
        presentPanel(panel) { url in self.switchProject(["action": "move", "path": url.path]) }
    }

    @objc func showProjectInFinder() {
        guard let project else { return }
        NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: project.path)])
    }

    @objc func showProjectsFolder() {
        guard let projectsFolder else { return }
        try? FileManager.default.createDirectory(at: projectsFolder, withIntermediateDirectories: true)
        NSWorkspace.shared.open(projectsFolder)
    }

    /// Opens .dopamine files handed over by Finder once the engine is ready.
    func openProjectFiles(_ urls: [URL]) {
        let projects = urls.filter { $0.pathExtension == "dopamine" }
        guard let last = projects.last else { return }
        if engine == nil { pendingOpen = [last]; return }
        switchProject(["action": "open", "path": last.path])
    }

    private func presentPanel(_ panel: NSSavePanel, then: @escaping (URL) -> Void) {
        showWindow()
        panel.beginSheetModal(for: window) { response in
            guard response == .OK, let url = panel.url else { return }
            then(url)
        }
    }

    /// Calls /api/project. Held Intercept messages must be forwarded or dropped first, so the engine answers 409 with a count.
    func switchProject(_ body: [String: Any]) {
        request("/api/project", body: body, timeout: 30) { result in
            if let held = result?["held"] as? Int {
                self.askAboutHeld(held) { action in
                    var retry = body
                    retry["held"] = action
                    self.switchProject(retry)
                }
            } else if let error = result?["error"] as? String {
                self.showAlert("Couldn’t change the project", error)
            } else if let result {
                self.applyProject(result)
                self.poll()
            } else {
                self.showAlert("Couldn’t change the project", "The local engine did not answer. Try again.")
            }
        }
    }

    private func askAboutHeld(_ count: Int, then: @escaping (String) -> Void) {
        let alert = NSAlert()
        alert.messageText = count == 1 ? "1 request is held in Intercept" : "\(count) requests are held in Intercept"
        alert.informativeText = "Forward or drop them before switching projects. The proxy keeps running, and new traffic goes to the project you open."
        alert.addButton(withTitle: "Forward All")
        alert.addButton(withTitle: "Drop All")
        alert.addButton(withTitle: "Cancel")
        showWindow()
        alert.beginSheetModal(for: window) { response in
            switch response {
            case .alertFirstButtonReturn: then("forward")
            case .alertSecondButtonReturn: then("drop")
            default: break
            }
        }
    }

    /// Takes /api/project's answer: name and path for the title, the recent list and the projects folder for the menus.
    func applyProject(_ info: [String: Any]) {
        guard let name = info["name"] as? String, let path = info["path"] as? String else { return }
        project = ProjectState(name: name, path: path)
        window.title = "\(name) — Dopamine"
        window.representedURL = URL(fileURLWithPath: path)
        recentProjects = (info["recent"] as? [[String: Any]] ?? []).compactMap { entry in
            guard let name = entry["name"] as? String, let path = entry["path"] as? String, path != project?.path else { return nil }
            return ProjectState(name: name, path: path)
        }
        if let folder = info["projects_dir"] as? String { projectsFolder = URL(fileURLWithPath: folder) }
        NSDocumentController.shared.noteNewRecentDocumentURL(URL(fileURLWithPath: path))
    }

    /// Fetches the project details when the poll sees a project it has not loaded yet (at launch, or after a switch).
    func refreshProject() {
        request("/api/project") { info in if let info { self.applyProject(info) } }
    }

    func fillRecentMenu(_ menu: NSMenu) {
        menu.removeAllItems()
        for entry in recentProjects {
            let item = NSMenuItem(title: entry.name, action: #selector(openRecentProject(_:)), keyEquivalent: "")
            item.target = self
            item.representedObject = entry.path
            item.toolTip = entry.path
            menu.addItem(item)
        }
        if recentProjects.isEmpty {
            menu.addItem(withTitle: "No Recent Projects", action: nil, keyEquivalent: "").isEnabled = false
        }
        menu.addItem(.separator())
        let folder = NSMenuItem(title: "Show Projects Folder", action: #selector(showProjectsFolder), keyEquivalent: "")
        folder.target = self
        menu.addItem(folder)
    }

    func showAlert(_ title: String, _ text: String) {
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = text
        showWindow()
        alert.beginSheetModal(for: window)
    }

    // MARK: Open Browser

    private static let chromium = ["com.google.Chrome", "com.brave.Browser", "com.microsoft.edgemac", "org.chromium.Chromium"]

    /// Opens an installed Chromium browser with a fresh profile that uses the proxy. Offers to trust the CA first
    /// when HTTPS interception is on, and starts the listener if it is stopped.
    @objc func openBrowser() {
        guard let browser = Self.chromium.lazy.compactMap({ NSWorkspace.shared.urlForApplication(withBundleIdentifier: $0) }).first else {
            showAlert("No Chromium browser found", "Open Browser uses Google Chrome, Brave, Microsoft Edge or Chromium. Install one of them, or point another browser’s proxy at 127.0.0.1:\(engine?.port ?? 18080).")
            return
        }
        request("/api/tls") { tls in
            let intercept = tls?["intercept"] as? Bool ?? false
            let trusted = (tls?["ca"] as? [String: Any])?["trusted"] as? Bool
            guard intercept, trusted == false else { self.startProxyThenLaunch(browser); return }
            let alert = NSAlert()
            alert.messageText = "Trust the Dopamine certificate first?"
            alert.informativeText = "Without it, the browser shows a certificate warning on every HTTPS site. macOS asks for your password to add it to your login keychain."
            alert.addButton(withTitle: "Trust and Open")
            alert.addButton(withTitle: "Open Without Trusting")
            alert.addButton(withTitle: "Cancel")
            self.showWindow()
            alert.beginSheetModal(for: self.window) { response in
                switch response {
                case .alertFirstButtonReturn:
                    self.request("/api/tls/trust", body: [:], timeout: 190) { result in
                        if let error = result?["error"] as? String { self.showAlert("The certificate was not trusted", error); return }
                        self.startProxyThenLaunch(browser)
                    }
                case .alertSecondButtonReturn: self.startProxyThenLaunch(browser)
                default: break
                }
            }
        }
    }

    private func startProxyThenLaunch(_ browser: URL) {
        guard let engine else { return }
        if engine.running { launch(browser, port: engine.port); return }
        request("/api/proxy", body: ["running": true, "port": engine.port]) { status in
            if let error = status?["error"] as? String { self.showAlert("The proxy did not start", error); return }
            self.poll()
            self.launch(browser, port: status?["proxy_port"] as? Int ?? engine.port)
        }
    }

    private func launch(_ browser: URL, port: Int) {
        let profile = FileManager.default.temporaryDirectory.appendingPathComponent("Dopamine Browser/\(UUID().uuidString)")
        guard let executable = Bundle(url: browser)?.executableURL else {
            showAlert("Couldn’t open the browser", "\(browser.lastPathComponent) has no executable.")
            return
        }
        let process = Process()
        process.executableURL = executable
        process.arguments = [
            "--user-data-dir=\(profile.path)",
            "--proxy-server=http://127.0.0.1:\(port)",
            "--proxy-bypass-list=<-loopback>",  // Send localhost through the proxy too.
            "--no-first-run", "--no-default-browser-check", "--disable-default-apps", "--disable-sync",
            // Keep the browser's own background traffic out of history and Intercept.
            "--disable-background-networking", "--disable-component-update", "--disable-domain-reliability",
            "--disable-client-side-phishing-detection", "--metrics-recording-only", "--no-pings",
            "--disable-features=OptimizationHints,OptimizationGuideModelDownloading,MediaRouter,Translate,AutofillServerCommunication,CertificateTransparencyComponentUpdater",
            "http://dopamine/",
        ]
        do { try process.run() }
        catch { showAlert("Couldn’t open \(browser.deletingPathExtension().lastPathComponent)", error.localizedDescription) }
    }
}
