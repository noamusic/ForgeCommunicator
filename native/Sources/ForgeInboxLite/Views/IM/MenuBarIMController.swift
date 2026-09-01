import SwiftUI
import AppKit

// MARK: - MenuBarIMController

final class MenuBarIMController: NSObject, NSPopoverDelegate, ObservableObject {

    var statusItem: NSStatusItem?
    var popover: NSPopover?

    @Published var totalUnread: Int = 0

    @MainActor
    func setup(
        store: NativeCommunicatorStore,
        accountStore: AccountStore,
        windowManager: IMWindowManager
    ) {
        let item = NSStatusBar.system.statusItem(withLength: 28)
        if let button = item.button {
            button.image = NSImage(
                systemSymbolName: "bubble.left.and.bubble.right.fill",
                accessibilityDescription: "Forge"
            )
            button.imageScaling = .scaleProportionallyDown
            button.action = #selector(togglePopover(_:))
            button.target = self
        }
        self.statusItem = item

        let pop = NSPopover()
        pop.contentSize = NSSize(width: 300, height: 440)
        pop.behavior = .transient
        pop.animates = true
        pop.delegate = self
        pop.contentViewController = NSHostingController(
            rootView: MenuBarPopoverView(
                store: store,
                accountStore: accountStore,
                windowManager: windowManager,
                controller: self
            )
        )
        self.popover = pop
    }

    @objc func togglePopover(_ sender: Any?) {
        guard let pop = popover, let button = statusItem?.button else { return }
        if pop.isShown {
            pop.performClose(sender)
        } else {
            pop.show(relativeTo: button.bounds, of: button, preferredEdge: .maxY)
        }
    }

    @MainActor
    func updateBadge(unread: Int) {
        totalUnread = unread
        guard let button = statusItem?.button else { return }
        if unread > 0 {
            button.image = NSImage(
                systemSymbolName: "bubble.left.and.bubble.right.fill",
                accessibilityDescription: "Forge"
            )
            button.title = " \(unread)"
            let attr = NSMutableAttributedString(string: button.title)
            attr.addAttributes(
                [
                    .font: NSFont.boldSystemFont(ofSize: 9),
                    .foregroundColor: NSColor.white
                ],
                range: NSRange(location: 0, length: attr.length)
            )
            button.attributedTitle = attr
        } else {
            button.image = NSImage(
                systemSymbolName: "bubble.left.and.bubble.right.fill",
                accessibilityDescription: "Forge"
            )
            button.title = ""
        }
    }

    // MARK: NSPopoverDelegate

    func popoverShouldDetach(_ popover: NSPopover) -> Bool { false }
}

// MARK: - MenuBarPopoverView

struct MenuBarPopoverView: View {
    @ObservedObject var store: NativeCommunicatorStore
    @ObservedObject var accountStore: AccountStore
    var windowManager: IMWindowManager
    var controller: MenuBarIMController

    var body: some View {
        VStack(spacing: 0) {
            headerBar
            Divider().background(ForgeTheme.glassBorder)

            if store.token == nil {
                loggedOutState
            } else {
                conversationList
            }
        }
        .frame(width: 300, height: 440)
        .background(ForgeTheme.dark900)
        .colorScheme(.dark)
    }

    // MARK: Sub-views

    private var headerBar: some View {
        HStack(spacing: 6) {
            Text("Forge Communicator")
                .font(.system(size: 13, weight: .semibold))
                .foregroundColor(ForgeTheme.white)

            Circle()
                .fill(store.token != nil ? ForgeTheme.green : ForgeTheme.dark600)
                .frame(width: 6, height: 6)

            Spacer()

            Button {
                openMainWindow()
            } label: {
                Image(systemName: "gearshape")
                    .font(.system(size: 13))
                    .foregroundColor(ForgeTheme.silver.opacity(0.7))
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal, 12)
        .frame(height: 40)
    }

    private var loggedOutState: some View {
        VStack(spacing: 8) {
            Image(systemName: "bubble.left.and.bubble.right")
                .font(.system(size: 28))
                .foregroundColor(ForgeTheme.dark600)
            Text("Open Forge to sign in")
                .font(.system(size: 13))
                .foregroundColor(ForgeTheme.silver.opacity(0.6))
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var conversationList: some View {
        ScrollView {
            LazyVStack(spacing: 0) {
                ForEach(store.conversations) { conv in
                    conversationRow(conv)
                        .onTapGesture {
                            windowManager.openConversation(conv, store: store)
                            controller.popover?.performClose(nil)
                        }
                }
            }
        }
    }

    private func conversationRow(_ conv: CommunicatorConversation) -> some View {
        HStack(spacing: 8) {
            // Avatar
            ZStack {
                Circle()
                    .fill(ForgeTheme.dark700)
                    .frame(width: 24, height: 24)
                Text(conv.name.prefix(1).uppercased())
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundColor(ForgeTheme.primary)
            }

            // Name + preview
            VStack(alignment: .leading, spacing: 2) {
                Text(conv.name)
                    .font(.system(size: 12, weight: .medium))
                    .foregroundColor(ForgeTheme.white)
                    .lineLimit(1)

                if let msg = conv.lastMessage {
                    Text(msg.body)
                        .font(.system(size: 11))
                        .foregroundColor(ForgeTheme.silver.opacity(0.55))
                        .lineLimit(1)
                }
            }

            Spacer(minLength: 4)

            // Unread badge
            if conv.unreadCount > 0 {
                Text("\(conv.unreadCount)")
                    .font(.system(size: 10, weight: .bold))
                    .foregroundColor(.black)
                    .padding(.horizontal, 5)
                    .padding(.vertical, 2)
                    .background(ForgeTheme.primary)
                    .clipShape(Capsule())
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 7)
        .background(Color.clear)
        .contentShape(Rectangle())
        .overlay(alignment: .bottom) {
            Rectangle()
                .fill(ForgeTheme.glassBorder)
                .frame(height: 0.5)
        }
    }

    // MARK: Actions

    private func openMainWindow() {
        controller.popover?.performClose(nil)
        NSApp.activate(ignoringOtherApps: true)
        for window in NSApp.windows where window.title.contains("Forge") || window.identifier?.rawValue == "main" {
            window.makeKeyAndOrderFront(nil)
            return
        }
        NSApp.windows.first?.makeKeyAndOrderFront(nil)
    }
}
