import AppKit
import AVFoundation
import Security
import WebKit

final class DopamineApp: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKScriptMessageHandler {
    var window: NSWindow!
    var web: WKWebView!
    var backend: Process?
    // DOPAMINE_PORT and DOPAMINE_DATA_DIR let a second copy run beside the real one for testing.
    let port = Int(ProcessInfo.processInfo.environment["DOPAMINE_PORT"] ?? "") ?? 18765
    /// A fresh secret per launch, handed to the engine on stdin. The engine requires it on /api, so only this app's web view and menus can drive it.
    let apiToken: String = {
        var bytes = [UInt8](repeating: 0, count: 32)
        guard SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes) == errSecSuccess else { fatalError("No secure random source") }
        return Data(bytes).base64EncodedString().replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "")
    }()
    var attempts = 0
    var splashShown = Date()
    var startupSound: AVAudioPlayer?
    var closing = false
    // Native integration state (NativeMenus.swift): the menu bar item, the engine's last polled state, agent access.
    var statusItem: NSStatusItem!
    var engine: EngineState?
    var agentAccess: (enabled: Bool, access: String)?
    var webReady = false
    var polling = false
    var pollTimer: Timer?
    var lastHeld = 0
    var dataFolder: URL?
    // Projects.swift: the open project, recent projects for File → Open Recent, and files Finder asked to open before the engine was ready.
    var project: ProjectState?
    var recentProjects: [ProjectState] = []
    var projectsFolder: URL?
    var pendingOpen: [URL] = []

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApplication.shared.mainMenu = buildMainMenu()
        prepareStartupSound()
        setUpStatusItem()
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .default()
        config.userContentController.add(self, name: "clipboard")
        config.userContentController.add(self, name: "theme")
        config.userContentController.add(self, name: "openBrowser")
        // The page sends this as X-Dopamine-Token on its API calls (index.html api()).
        config.userContentController.addUserScript(WKUserScript(source: "window.dopamineToken = '\(apiToken)';", injectionTime: .atDocumentStart, forMainFrameOnly: true))
        web = WKWebView(frame: .zero, configuration: config)
        web.navigationDelegate = self
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1280, height: 820), styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "Dopamine"
        window.titlebarAppearsTransparent = true
        window.titleVisibility = .hidden
        applyTheme(UserDefaults.standard.string(forKey: "theme") ?? "dark")
        window.minSize = NSSize(width: 800, height: 560)
        window.isReleasedWhenClosed = false
        window.contentView = web
        window.center(); window.makeKeyAndOrderFront(nil)
        NSApplication.shared.activate(ignoringOtherApps: true)
        let dark = window.appearance?.name == .darkAqua
        web.setValue(false, forKey: "drawsBackground")
        showSplash(dark: dark)
        startBackend()
    }

    /// The animated startup splash (splash.html), shown until the engine is ready.
    func showSplash(dark: Bool) {
        splashShown = Date()
        guard let url = Bundle.main.url(forResource: "splash", withExtension: "html"), let html = try? String(contentsOf: url, encoding: .utf8) else { return }
        web.loadHTMLString(html.replacingOccurrences(of: "data-theme=\"dark\"", with: "data-theme=\"\(dark ? "dark" : "light")\""), baseURL: nil)
    }

    /// Decodes the startup chime (assets/make-startup-sound.py) and primes the audio output before the window opens,
    /// so playStartupSound starts it without a load. Off with Dopamine → Startup Sound.
    func prepareStartupSound() {
        guard UserDefaults.standard.object(forKey: "startupSound") as? Bool ?? true,
              let url = Bundle.main.url(forResource: "startup", withExtension: "m4a"), let sound = try? AVAudioPlayer(contentsOf: url) else { return }
        sound.volume = 0.6
        sound.prepareToPlay()
        startupSound = sound
    }

    /// Starts the chime as the splash appears so the two stay in time; it plays once per launch.
    func playStartupSound() {
        guard let sound = startupSound else { return }
        startupSound = nil
        sound.play()
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) { _ = sound }  // keeps the player alive until it finishes
    }

    /// Lets the splash finish its draw-in and hit, fades it out, then loads the UI.
    func leaveSplash() {
        // Holds until the logo has finished drawing and the chime's bell (0.45 s) has rung.
        let remaining = max(0, 1.3 - Date().timeIntervalSince(splashShown))
        DispatchQueue.main.asyncAfter(deadline: .now() + remaining) {
            self.web.evaluateJavaScript("document.documentElement.classList.add('leaving')")
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.28) {
                guard !self.closing else { return }
                self.web.load(URLRequest(url: URL(string: "http://127.0.0.1:\(self.port)/")!))
            }
        }
    }

    func startBackend() {
        guard let resources = Bundle.main.resourceURL else { fail("Application resources are missing."); return }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        process.arguments = [resources.appendingPathComponent("server.py").path, "--token-stdin"]
        process.currentDirectoryURL = resources
        var env = ProcessInfo.processInfo.environment
        env["DOPAMINE_API_PORT"] = String(port)
        let support = ProcessInfo.processInfo.environment["DOPAMINE_DATA_DIR"].map { URL(fileURLWithPath: $0) } ?? defaultDataFolder()
        dataFolder = support
        do { try FileManager.default.createDirectory(at: support, withIntermediateDirectories: true) }
        catch { fail("Could not create the application data folder: \(error.localizedDescription)"); return }
        env["DOPAMINE_DATA_DIR"] = support.path
        if let projects = ProcessInfo.processInfo.environment["DOPAMINE_PROJECTS_DIR"] { env["DOPAMINE_PROJECTS_DIR"] = projects }
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"  // Keep the signed bundle unmodified.
        process.environment = env
        let logURL = support.appendingPathComponent("server.log")
        FileManager.default.createFile(atPath: logURL.path, contents: nil)
        if let log = try? FileHandle(forWritingTo: logURL) { process.standardOutput = log; process.standardError = log }
        let input = Pipe()
        process.standardInput = input
        backend = process
        do {
            try process.run()
            input.fileHandleForWriting.write(Data((apiToken + "\n").utf8))
            try? input.fileHandleForWriting.close()
            waitForBackend()
        }
        catch { fail("Could not launch /usr/bin/python3. \(error.localizedDescription)") }
    }

    func waitForBackend() {
        guard !closing else { return }
        guard backend?.isRunning == true else {
            let log = dataFolder?.appendingPathComponent("server.log").path ?? "server.log"
            fail("The local engine stopped. Dopamine needs Python 3 from the Xcode Command Line Tools; install them with “xcode-select --install” if they are missing, then reopen Dopamine.\n\nDetails: \(log)")
            return
        }
        attempts += 1
        if attempts > 60 { fail("The local engine did not become ready. Quit and reopen the app to retry."); return }
        let url = URL(string: "http://127.0.0.1:\(port)/api/status")!
        var request = URLRequest(url: url); request.timeoutInterval = 1
        request.setValue(apiToken, forHTTPHeaderField: "X-Dopamine-Token")
        URLSession.shared.dataTask(with: request) { data, _, _ in
            let object = data.flatMap { try? JSONSerialization.jsonObject(with: $0) } as? [String: Any]
            DispatchQueue.main.async {
                guard !self.closing else { return }
                if object?["app"] as? String == "dopamine" {
                    self.leaveSplash()
                    self.startPolling()
                    if !self.pendingOpen.isEmpty { self.openProjectFiles(self.pendingOpen); self.pendingOpen = [] }
                } else {
                    DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) { self.waitForBackend() }
                }
            }
        }.resume()
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else { decisionHandler(.cancel); return }
        if url.scheme == "about" || (url.host == "127.0.0.1" && url.port == port) { decisionHandler(.allow) }
        else { decisionHandler(.cancel) }
    }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        if webView.url?.absoluteString == "about:blank" { playStartupSound() }
        webReady = webView.url?.host == "127.0.0.1" && webView.url?.port == port
        #if DIAGNOSTICS
        if webReady { runDiagnostics() }  // test builds only; not compiled into Dopamine.app
        #endif
    }

    /// The data folder.
    func defaultDataFolder() -> URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first!
        return base.appendingPathComponent("Dopamine")
    }

    @objc func about() {
        let credits = NSMutableAttributedString(string: "HTTP and HTTPS testing proxy for macOS.\nProxy · Intercept · Repeater · Site map · Decoder · Agents\n\nUse only on systems you are authorized to test.",
            attributes: [.font: NSFont.systemFont(ofSize: 11), .foregroundColor: NSColor.secondaryLabelColor])
        let paragraph = NSMutableParagraphStyle(); paragraph.alignment = .center
        credits.addAttribute(.paragraphStyle, value: paragraph, range: NSRange(location: 0, length: credits.length))
        NSApp.orderFrontStandardAboutPanel(options: [.credits: credits])
        NSApp.activate(ignoringOtherApps: true)
    }
    func applyTheme(_ theme: String) {
        let dark = theme != "light"
        window.appearance = NSAppearance(named: dark ? .darkAqua : .aqua)
        window.backgroundColor = dark ? NSColor(srgbRed: 0x16/255, green: 0x16/255, blue: 0x18/255, alpha: 1) : NSColor(srgbRed: 0xf2/255, green: 0xf3/255, blue: 0xf0/255, alpha: 1)
        UserDefaults.standard.set(dark ? "dark" : "light", forKey: "theme")
    }
    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        if message.name == "theme", message.frameInfo.isMainFrame,
           message.frameInfo.securityOrigin.host == "127.0.0.1",
           message.frameInfo.securityOrigin.port == port,
           let theme = message.body as? String { applyTheme(theme); return }
        if message.name == "openBrowser", message.frameInfo.isMainFrame,
           message.frameInfo.securityOrigin.host == "127.0.0.1",
           message.frameInfo.securityOrigin.port == port { openBrowser(); return }
        guard message.name == "clipboard", message.frameInfo.isMainFrame,
              message.frameInfo.securityOrigin.host == "127.0.0.1",
              message.frameInfo.securityOrigin.port == port,
              let text = message.body as? String else { return }
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
    }
    func fail(_ message: String) {
        let alert = NSAlert(); alert.messageText = "Unable to start Dopamine"; alert.informativeText = message; alert.runModal()
    }
    /// .dopamine files double-clicked in Finder or dropped on the Dock icon.
    func application(_ application: NSApplication, open urls: [URL]) { openProjectFiles(urls) }
    // Closing the window keeps the engine and proxy running in the menu bar; Quit ends them.
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if !flag { showWindow() }
        return true
    }
    func applicationWillTerminate(_ notification: Notification) {
        closing = true
        pollTimer?.invalidate()
        if let process = backend, process.isRunning { process.terminate() }
    }
}
@main
enum DopamineMain {
    static func main() {
        let app = NSApplication.shared
        let delegate = DopamineApp()
        app.delegate = delegate
        app.setActivationPolicy(.regular)
        app.run()
    }
}
