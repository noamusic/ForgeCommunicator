import SwiftUI
import WebKit

struct ContentView: View {
    struct RenameSheetState: Identifiable {
        let accountID: UUID
        let type: AccountType
        let currentName: String
        var id: UUID { accountID }
    }

    @StateObject private var store = AccountStore()
    @State private var webSessionManager = WebSessionManager()

    @State private var showingAddSheet = false
    @State private var renameTarget: RenameSheetState?
    @State private var signalStatusMessage: String?
    @State private var whatsappSessionResetNonce = 0

    var body: some View {
        NavigationSplitView {
            List(selection: Binding(
                get: { store.selectedAccountID },
                set: { newValue in
                    guard let id = newValue else { return }
                    store.selectAccount(id: id)
                    if let account = store.selectedAccount {
                        NotificationService.post(
                            title: "ForgeInbox Lite",
                            body: "Opened \(account.displayName)"
                        )
                    }
                }
            )) {
                ForEach(store.accounts) { account in
                    HStack(spacing: 10) {
                        ZStack {
                            RoundedRectangle(cornerRadius: 8)
                                .fill(iconBackground(for: account.type))
                                .frame(width: 32, height: 32)
                            Image(systemName: symbolName(for: account.type))
                                .font(.system(size: 15, weight: .medium))
                                .foregroundStyle(symbolColor(for: account.type))
                        }
                        VStack(alignment: .leading, spacing: 2) {
                            Text(account.displayName)
                                .font(.system(size: 13, weight: .medium, design: .default))
                            Text(account.type.displayLabel)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                    .tag(account.id)
                    .contextMenu {
                        Button("Rename") {
                            renameTarget = RenameSheetState(
                                accountID: account.id,
                                type: account.type,
                                currentName: account.displayName
                            )
                        }
                        if account.type == .whatsapp {
                            Button("Reset WhatsApp Session", role: .destructive) {
                                resetWhatsAppSession(for: account)
                            }
                        }
                        Button("Remove", role: .destructive) {
                            removeAccount(account)
                        }
                    }
                }
                .onMove(perform: store.moveAccounts)
            }
            .navigationTitle("ForgeInbox Lite")
            .toolbar {
                ToolbarItemGroup {
                    Button {
                        showingAddSheet = true
                    } label: {
                        Label("Add Source", systemImage: "plus")
                    }
                }
            }
        } detail: {
            Group {
                if let selected = store.selectedAccount {
                    switch selected.type {
                    case .communicator:
                        CommunicatorWorkspaceView(account: selected)
                    case .whatsapp:
                        WhatsAppWorkspaceView(
                            account: selected,
                            manager: webSessionManager,
                            onResetSession: { resetWhatsAppSession(for: selected) }
                        )
                        .id("\(selected.id.uuidString)-\(whatsappSessionResetNonce)")
                    case .signal:
                        SignalWorkspaceView(account: selected, statusMessage: $signalStatusMessage)
                    case .telegram:
                        TelegramWorkspaceView(account: selected, manager: webSessionManager)
                    case .irc:
                        IRCProvider(sessionManager: webSessionManager).makeMainView(for: selected)
                    }
                } else {
                    VStack(spacing: 12) {
                        Image(systemName: "bubble.left.and.bubble.right")
                            .font(.system(size: 40))
                            .foregroundStyle(ForgeTheme.primary.opacity(0.5))
                        Text("Select a source")
                            .font(.title3)
                            .foregroundStyle(ForgeTheme.silver)
                        Text("Choose a messaging source from the sidebar")
                            .font(.subheadline)
                            .foregroundStyle(ForgeTheme.silver.opacity(0.5))
                    }
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .sheet(isPresented: $showingAddSheet) {
            AddAccountSheet(mode: .add) { type, name in
                store.addAccount(type: type, displayName: name)
            }
        }
        .sheet(item: $renameTarget) { account in
            AddAccountSheet(mode: .rename(account.accountID), initialType: account.type, initialName: account.currentName) { _, name in
                store.renameAccount(id: account.accountID, newName: name)
            }
        }
        .alert("Error", isPresented: Binding(
            get: { store.lastError != nil },
            set: { if !$0 { store.lastError = nil } }
        )) {
            Button("OK", role: .cancel) { store.lastError = nil }
        } message: {
            Text(store.lastError ?? "")
        }
        .onAppear {
            store.load()
        }
    }

    private func symbolName(for type: AccountType) -> String {
        switch type {
        case .communicator:
            return "waveform"
        case .whatsapp:
            return "bubble.left"
        case .signal:
            return "dot.radiowaves.left.and.right"
        case .telegram:
            return "paperplane"
        case .irc:
            return "number"
        }
    }

    private func iconBackground(for type: AccountType) -> Color {
        switch type {
        case .communicator:
            return ForgeTheme.primary.opacity(0.15)
        case .whatsapp:
            return Color.green.opacity(0.15)
        case .signal:
            return ForgeTheme.amber.opacity(0.15)
        case .telegram:
            return Color.cyan.opacity(0.15)
        case .irc:
            return ForgeTheme.violet.opacity(0.15)
        }
    }

    private func symbolColor(for type: AccountType) -> Color {
        switch type {
        case .communicator:
            return ForgeTheme.primary
        case .whatsapp:
            return .green
        case .signal:
            return ForgeTheme.amber
        case .telegram:
            return .cyan
        case .irc:
            return ForgeTheme.violet
        }
    }

    private func resetWhatsAppSession(for account: Account) {
        guard account.type == .whatsapp else { return }

        webSessionManager.removeWebsiteData(for: account) {
            if store.selectedAccountID == account.id {
                whatsappSessionResetNonce += 1
            }
        }
    }

    private func removeAccount(_ account: Account) {
        guard account.type == .whatsapp else {
            store.removeAccount(id: account.id)
            return
        }

        webSessionManager.removeWebsiteData(for: account) {
            if store.selectedAccountID == account.id {
                whatsappSessionResetNonce += 1
            }
            store.removeAccount(id: account.id)
        }
    }
}

private struct CommunicatorWorkspaceView: View {
    let account: Account

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(account.displayName)
                .font(.title3)

            Text("Communicator source scaffold is ready. In the next step this pane will host the native Communicator chat stack (API + WebSocket) inside the unified source shell.")
                .foregroundStyle(.secondary)

            HStack(spacing: 8) {
                Image(systemName: "checkmark.seal.fill")
                    .foregroundStyle(.green)
                Text("Source model migration complete")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Spacer()
        }
        .padding(20)
    }
}

private struct WhatsAppWorkspaceView: View {
    let account: Account
    let manager: WebSessionManager
    let onResetSession: () -> Void

    var body: some View {
        let webView = manager.webView(for: account)

        VStack(spacing: 0) {
            HStack {
                Text(account.displayName)
                    .font(.headline)
                Spacer()
                Button("Reset Session", role: .destructive, action: onResetSession)
                    .font(.caption)
                Text("Isolated Web Session")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)

            Divider()

            AccountWebContainerView(webView: webView)
                .onAppear {
                    if webView.url == nil {
                        webView.load(URLRequest(url: URL(string: "https://web.whatsapp.com")!))
                    }
                }
        }
    }
}

private struct SignalWorkspaceView: View {
    let account: Account
    @Binding var statusMessage: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(account.displayName)
                .font(.title3)

            Text("Signal accounts are launched as isolated local Signal processes using account-specific profile directories when Signal.app is installed.")
                .foregroundStyle(.secondary)

            HStack(spacing: 12) {
                Button("Launch Signal Session") {
                    switch SignalLauncher.launch(account: account) {
                    case .launched:
                        statusMessage = "Signal launched with profile: \(account.profilePath)"
                    case .signalNotInstalled:
                        statusMessage = "Signal.app was not found in /Applications."
                    case .failed(let error):
                        statusMessage = "Failed to launch Signal: \(error.localizedDescription)"
                    }
                }

                if let statusMessage {
                    Text(statusMessage)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }

            Spacer()
        }
        .padding(20)
    }
}

private struct TelegramWorkspaceView: View {
    let account: Account
    let manager: WebSessionManager

    private let telegramDesktopURL = URL(string: "https://web.telegram.org/k/")!

    var body: some View {
        let webView = manager.webView(for: account)

        VStack(spacing: 0) {
            HStack {
                Text(account.displayName)
                    .font(.headline)
                Spacer()
                Text("Telegram Web")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)

            Divider()

            AccountWebContainerView(webView: webView)
                .onAppear {
                    if webView.url == nil {
                        webView.load(URLRequest(url: telegramDesktopURL))
                    }
                }
        }
    }
}
