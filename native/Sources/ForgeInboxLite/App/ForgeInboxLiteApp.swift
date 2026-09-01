import SwiftUI
import AppKit
import UserNotifications

extension Notification.Name {
    static let forgeOpenWorkspace = Notification.Name("forge.openWorkspace")
    /// userInfo key "sourceID": UUID — open workspace and select this source
    static let forgeOpenWorkspaceSource = Notification.Name("forge.openWorkspaceSource")
    /// userInfo key "sourceID": UUID — select this source inside the workspace shell
    static let forgeSelectSource = Notification.Name("forge.selectSource")
}

// MARK: - RailHostView
// A self-contained SwiftUI view that owns AccountStore and a per-source
// NativeCommunicatorStore, suitable for embedding in the floating rail panel.

private struct RailHostView: View {
    @StateObject private var accountStore = AccountStore()
    @State private var communicatorStore: NativeCommunicatorStore?

    var body: some View {
        Group {
            if let store = communicatorStore {
                RailStoreView(
                    store: store,
                    accountStore: accountStore,
                    onOpenSettings: {
                        NotificationCenter.default.post(name: .forgeOpenWorkspace, object: nil)
                    }
                )
            } else {
                // No communicator source at all — prompt to open settings to add one
                VStack(spacing: 14) {
                    ForgeLogoIcon(size: 36)
                    Text("No Forge server")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(ForgeTheme.white)
                    Text("Add a Forge Communicator source in Settings.")
                        .font(.system(size: 11))
                        .foregroundStyle(ForgeTheme.silver.opacity(0.55))
                        .multilineTextAlignment(.center)
                    Button("Open Settings") {
                        NotificationCenter.default.post(name: .forgeOpenWorkspace, object: nil)
                    }
                    .buttonStyle(.plain)
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(ForgeTheme.primary)
                }
                .padding(20)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .onAppear { accountStore.load() }
        .onChange(of: accountStore.accounts) { accounts in
            guard communicatorStore == nil else { return }
            if let source = accounts.first(where: { $0.type == .communicator }) {
                let s = NativeCommunicatorStore(source: source)
                communicatorStore = s
                s.onAppear()
            }
        }
        .onChange(of: accountStore.selectedAccountID) { _ in
            guard let source = accountStore.accounts.first(where: { $0.type == .communicator }),
                  communicatorStore == nil else { return }
            let s = NativeCommunicatorStore(source: source)
            communicatorStore = s
            s.onAppear()
        }
    }
}

/// Holds a live NativeCommunicatorStore as @ObservedObject so SwiftUI re-renders on changes.
private struct RailStoreView: View {
    @ObservedObject var store: NativeCommunicatorStore
    @ObservedObject var accountStore: AccountStore
    var onOpenSettings: () -> Void

    private var selectedSourceBinding: Binding<UUID?> {
        Binding(
            get: { accountStore.selectedAccountID },
            set: { id in
                if let id { accountStore.selectAccount(id: id) }
            }
        )
    }

    var body: some View {
        if store.token != nil {
            ConversationRailView(
                store: store,
                accountStore: accountStore,
                selectedSourceID: selectedSourceBinding,
                onOpenConversation: { conversation in
                    IMWindowManager.shared.openConversation(conversation, store: store)
                },
                onOpenSettings: onOpenSettings,
                onCompose: onOpenSettings
            )
        } else {
            RailLoginView(store: store, onOpenSettings: onOpenSettings)
        }
    }
}

// MARK: - RailWindowController

final class RailWindowController: NSObject {
    private var panel: NSPanel?

    func createAndShow() {
        guard let screen = NSScreen.main else { return }

        let screenFrame = screen.frame
        let visibleFrame = screen.visibleFrame
        let panelWidth: CGFloat = 260
        let defaultHeight: CGFloat = min(640, visibleFrame.height)
        // Restore previously saved size/position, or default to right-edge placement.
        let savedFrame = savedWindowFrame(screen: screen, defaultWidth: panelWidth, defaultHeight: defaultHeight)

        let p = NSPanel(
            contentRect: savedFrame,
            styleMask: [.resizable, .nonactivatingPanel, .titled, .fullSizeContentView],
            backing: .buffered,
            defer: false
        )
        p.isMovableByWindowBackground = true
        p.titleVisibility = .hidden
        p.titlebarAppearsTransparent = true
        p.isFloatingPanel = true
        p.hidesOnDeactivate = false
        p.level = .floating
        p.collectionBehavior = [.canJoinAllSpaces, .stationary]
        p.isOpaque = false
        p.backgroundColor = NSColor(red: 0.07, green: 0.09, blue: 0.13, alpha: 0.97)
        p.minSize = NSSize(width: 220, height: 300)
        p.maxSize = NSSize(width: 400, height: screenFrame.height)

        // Persist frame whenever the user resizes or moves.
        p.setFrameAutosaveName("ForgeRailPanel")

        let hosting = NSHostingView(rootView: RailHostView())
        hosting.autoresizingMask = [.width, .height]
        p.contentView = hosting

        self.panel = p
        p.orderFrontRegardless()
    }

    private func savedWindowFrame(screen: NSScreen, defaultWidth: CGFloat, defaultHeight: CGFloat) -> NSRect {
        let visibleFrame = screen.visibleFrame
        let screenFrame = screen.frame
        // Default: right edge, vertically centered in visible area
        let defaultX = screenFrame.maxX - defaultWidth
        let defaultY = visibleFrame.minY + (visibleFrame.height - defaultHeight) / 2
        return NSRect(x: defaultX, y: defaultY, width: defaultWidth, height: defaultHeight)
    }

    func show() {
        panel?.orderFrontRegardless()
    }

    func hide() {
        panel?.orderOut(nil)
    }

    func toggle() {
        guard let p = panel else { return }
        if p.isVisible { hide() } else { show() }
    }
}

// MARK: - AppDelegate

private final class AppDelegate: NSObject, NSApplicationDelegate {

    let railController = RailWindowController()

    func applicationDidFinishLaunching(_ notification: Notification) {
        // Start as accessory so no main SwiftUI window appears automatically.
        NSApp.setActivationPolicy(.accessory)

        // Hide any workspace windows SwiftUI auto-opened (we show them on demand).
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) {
            for window in NSApp.windows where !(window is NSPanel) {
                window.orderOut(nil)
            }
        }

        NotificationCenter.default.addObserver(
            forName: NSWindow.didBecomeMainNotification,
            object: nil,
            queue: .main
        ) { _ in
            self.applyWorkspaceWindowStyle()
        }

        NotificationCenter.default.addObserver(
            forName: .forgeOpenWorkspace,
            object: nil,
            queue: .main
        ) { _ in
            self.openWorkspace(selectingSourceID: nil)
        }

        NotificationCenter.default.addObserver(
            forName: .forgeOpenWorkspaceSource,
            object: nil,
            queue: .main
        ) { note in
            let sourceID = note.userInfo?["sourceID"] as? UUID
            self.openWorkspace(selectingSourceID: sourceID)
        }

        // Show the rail panel on launch.
        railController.createAndShow()
    }

    func applicationDidBecomeActive(_ notification: Notification) {
        // Badge count is managed by NativeCommunicatorStore.refreshAll and markRead(for:).
        // Do not zero it here — that would clear unread counts that haven't been read yet.
    }

    /// Call this when the user explicitly opens the full workspace window.
    /// Pass `selectingSourceID` to switch the workspace to a specific source on open.
    func openWorkspace(selectingSourceID: UUID? = nil) {
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
        DispatchQueue.main.async {
            guard let w = NSApp.windows.first(where: { !($0 is NSPanel) }) else { return }
            if let railFrame = NSApp.windows.compactMap({ $0 as? NSPanel }).first?.frame,
               let screen = NSScreen.main {
                let workspaceWidth: CGFloat = max(w.frame.width, 980)
                let workspaceHeight: CGFloat = max(w.frame.height, 620)
                let x = max(screen.visibleFrame.minX, railFrame.minX - workspaceWidth - 8)
                let y = max(screen.visibleFrame.minY, railFrame.midY - workspaceHeight / 2)
                w.setFrame(NSRect(x: x, y: y, width: workspaceWidth, height: workspaceHeight), display: true)
            }
            w.makeKeyAndOrderFront(nil)
            self.applyWorkspaceWindowStyle()
            // Tell the workspace shell to select a specific source if requested.
            if let id = selectingSourceID {
                NotificationCenter.default.post(
                    name: .forgeSelectSource,
                    object: nil,
                    userInfo: ["sourceID": id]
                )
            }
        }
    }

    private func applyWorkspaceWindowStyle() {
        for window in NSApp.windows where !(window is NSPanel) {
            window.titleVisibility = .hidden
            window.titlebarAppearsTransparent = true
            window.styleMask.insert(.fullSizeContentView)
            window.isOpaque = false
            window.backgroundColor = .black
            window.toolbar?.showsBaselineSeparator = false
            window.toolbar = nil
            window.titlebarSeparatorStyle = .none
            window.isMovableByWindowBackground = true
        }
    }
}

// MARK: - App

@main
struct ForgeInboxLiteApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate

    init() {
        NotificationService.configure()
        NotificationService.requestAuthorization()

        if Bundle.main.bundleIdentifier == nil {
            print("[ForgeInboxLite] Running outside an app bundle; macOS notifications may be limited.")
        }
    }

    var body: some Scene {
        // Full workspace window — not shown by default, opened on demand via menu or settings.
        WindowGroup {
            ForgeCommunicatorShellView()
                .frame(minWidth: 980, minHeight: 620)
        }
        .windowStyle(.hiddenTitleBar)
        .commands {
            CommandMenu("Communicator") {
                Button("Show Rail") {
                    appDelegate.railController.toggle()
                }
                .keyboardShortcut("f", modifiers: [.command, .shift])

                Divider()

                Button("Open Workspace") {
                    appDelegate.openWorkspace()
                }
                .keyboardShortcut("0", modifiers: [.command, .shift])
            }
        }
    }
}
