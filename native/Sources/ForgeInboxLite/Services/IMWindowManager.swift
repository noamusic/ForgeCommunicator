import SwiftUI
import AppKit

@MainActor
final class IMWindowManager: ObservableObject {

    static let shared = IMWindowManager()

    private var windows: [Int: NSPanel] = [:]

    private init() {}

    func openConversation(_ conversation: CommunicatorConversation, store: NativeCommunicatorStore) {
        let channelID = conversation.channelID

        if let existing = windows[channelID] {
            existing.orderFront(nil)
            return
        }

        let panel = NSPanel(
            contentRect: NSRect(x: 0, y: 0, width: 380, height: 560),
            styleMask: [.titled, .closable, .resizable, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )

        panel.isFloatingPanel = true
        panel.hidesOnDeactivate = false
        panel.becomesKeyOnlyIfNeeded = true
        panel.title = conversation.name
        panel.minSize = NSSize(width: 340, height: 480)

        if let screenFrame = NSScreen.main?.frame {
            let windowWidth: CGFloat = 380
            let windowHeight: CGFloat = 560
            let cascadeOffset = CGFloat(windows.count) * 30
            let originX = screenFrame.midX - windowWidth / 2 + cascadeOffset
            let originY = screenFrame.midY - windowHeight / 2 - cascadeOffset
            panel.setFrameOrigin(NSPoint(x: originX, y: originY))
        }

        let contentView = FloatingChatWindowView(conversation: conversation, store: store)
        panel.contentView = NSHostingView(rootView: contentView)

        let delegate = WindowCloseDelegate(manager: self, channelID: channelID)
        panel.delegate = delegate

        // Retain the delegate on the panel via associated object so ARC doesn't release it
        objc_setAssociatedObject(panel, &AssociatedKeys.delegateKey, delegate, .OBJC_ASSOCIATION_RETAIN_NONATOMIC)

        panel.orderFront(nil)
        windows[channelID] = panel
    }

    func closeConversation(channelID: Int) {
        windows[channelID]?.close()
        windows.removeValue(forKey: channelID)
    }

    // Called by WindowCloseDelegate when the window is about to close.
    fileprivate func windowDidClose(channelID: Int) {
        windows.removeValue(forKey: channelID)
    }
}

// MARK: - Associated Object Keys

private enum AssociatedKeys {
    static var delegateKey: UInt8 = 0
}

// MARK: - Window Close Delegate

private final class WindowCloseDelegate: NSObject, NSWindowDelegate {

    private weak var manager: IMWindowManager?
    private let channelID: Int

    init(manager: IMWindowManager, channelID: Int) {
        self.manager = manager
        self.channelID = channelID
    }

    func windowWillClose(_ notification: Notification) {
        Task { @MainActor in
            self.manager?.windowDidClose(channelID: self.channelID)
        }
    }
}
