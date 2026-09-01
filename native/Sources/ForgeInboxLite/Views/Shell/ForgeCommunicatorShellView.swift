import SwiftUI
import WebKit

struct ForgeCommunicatorShellView: View {
    @StateObject private var store = AccountStore()
    @State private var webSessionManager = WebSessionManager()
    @State private var showingAddSheet = false

    var body: some View {
        ZStack {
            ForgeBackgroundLayer()

            HStack(spacing: 0) {
                ZStack {
                    SidebarStarfieldBackground()

                    VStack(spacing: 0) {
                        sidebarHeader

                        ScrollView {
                            VStack(spacing: 4) {
                                ForEach(store.sources) { source in
                                    sourceRow(source)
                                        .onTapGesture {
                                            store.selectAccount(id: source.id)
                                        }
                                        .contextMenu {
                                            Button("Rename") {}
                                            if source.type == .telegram {
                                                Button("Reset Telegram Session", role: .destructive) {
                                                    resetWebSession(for: source)
                                                }
                                            }
                                            if source.type == .whatsapp {
                                                Button("Reset WhatsApp Session", role: .destructive) {
                                                    resetWebSession(for: source)
                                                }
                                            }
                                            Button("Remove", role: .destructive) {
                                                store.removeAccount(id: source.id)
                                            }
                                        }
                                }
                            }
                            .padding(.horizontal, 10)
                            .padding(.vertical, 6)
                        }

                        Spacer(minLength: 0)
                    }
                }
                .frame(minWidth: 240, idealWidth: 280, maxWidth: 320)

                Rectangle()
                    .fill(Color.white.opacity(0.10))
                    .frame(width: 1)

                Group {
                    if let source = store.selectedSource ?? store.sources.first {
                        providerView(for: source)
                            .id("source-\(source.id.uuidString)")
                            .onAppear {
                                if store.selectedSourceID != source.id {
                                    store.selectAccount(id: source.id)
                                }
                            }
                    } else {
                        emptyState
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(ForgeTheme.dark950)
            }
            .tint(ForgeTheme.primary)
        }
        .preferredColorScheme(.dark)
        .sheet(isPresented: $showingAddSheet) {
            AddAccountSheet(mode: .add) { type, name in
                store.addSource(type: type, displayName: name)
            }
        }
        .onAppear {
            store.load()
        }
        .onReceive(NotificationCenter.default.publisher(for: .forgeSelectSource)) { note in
            guard let id = note.userInfo?["sourceID"] as? UUID else { return }
            store.selectAccount(id: id)
        }
    }

    // MARK: - Provider views

    @ViewBuilder
    private func providerView(for source: Source) -> some View {
        switch source.type {
        case .communicator:
            CommunicatorProvider().makeMainView(
                for: source,
                onProviderConfigUpdate: { encoded in
                    store.updateSourceProviderConfig(id: source.id, providerConfig: encoded)
                }
            )
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        case .whatsapp:
            WhatsAppProvider(sessionManager: webSessionManager)
                .makeMainView(for: source, onProviderConfigUpdate: nil)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        case .signal:
            SignalProvider(sessionManager: webSessionManager)
                .makeMainView(for: source, onProviderConfigUpdate: nil)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        case .telegram:
            TelegramProviderView(source: source, sessionManager: webSessionManager)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        case .irc:
            IRCProvider(sessionManager: webSessionManager)
                .makeMainView(for: source, onProviderConfigUpdate: nil)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }

    private var emptyState: some View {
        VStack(spacing: 16) {
            ForgeLogoIcon(size: 46)
            Text("No sources configured")
                .font(.headline)
                .foregroundStyle(.white)
            Text("Add WhatsApp, Telegram, Signal, or a Forge server with the + button.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 320)
            Button("Add Source") { showingAddSheet = true }
                .buttonStyle(.borderedProminent)
        }
        .padding(32)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: - Sidebar

    private var sidebarHeader: some View {
        HStack(spacing: 10) {
            ForgeLogoIcon(size: 30)

            Text("Sources")
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(ForgeTheme.white)

            Spacer()

            Button {
                showingAddSheet = true
            } label: {
                Image(systemName: "plus")
                    .font(.system(size: 12, weight: .bold))
                    .foregroundStyle(.white)
                    .frame(width: 26, height: 26)
                    .background(Circle().fill(Color.white.opacity(0.10)))
                    .overlay(Circle().stroke(Color.white.opacity(0.18), lineWidth: 1))
            }
            .buttonStyle(.plain)
            .help("Add source")
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .background(ForgeTheme.dark950)
        .overlay(Rectangle().fill(ForgeTheme.glassBorder).frame(height: 1), alignment: .bottom)
    }

    @ViewBuilder
    private func sourceRow(_ source: Source) -> some View {
        let selected = store.selectedSourceID == source.id
        let color = iconColor(for: source.type)

        HStack(spacing: 12) {
            ZStack {
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .fill(color.opacity(0.16))
                    .frame(width: 34, height: 34)
                RoundedRectangle(cornerRadius: 8, style: .continuous)
                    .stroke(color.opacity(0.28), lineWidth: 1)
                    .frame(width: 34, height: 34)
                Image(systemName: iconName(for: source.type))
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(color)
            }

            VStack(alignment: .leading, spacing: 2) {
                Text(source.displayName)
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(ForgeTheme.white)
                    .lineLimit(1)
                Text(source.type.displayLabel)
                    .font(.system(size: 11))
                    .foregroundStyle(ForgeTheme.silver.opacity(0.5))
                    .lineLimit(1)
            }

            Spacer(minLength: 0)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .background(
            RoundedRectangle(cornerRadius: 11, style: .continuous)
                .fill(selected ? ForgeTheme.primary.opacity(0.14) : ForgeTheme.dark800.opacity(0.60))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 11, style: .continuous)
                .stroke(selected ? ForgeTheme.glassBorderActive : ForgeTheme.glassBorder, lineWidth: 1)
        )
        .shadow(color: selected ? ForgeTheme.primary.opacity(0.18) : .clear, radius: 6, x: 0, y: 2)
    }

    private func iconName(for type: SourceType) -> String {
        switch type {
        case .communicator: return "waveform"
        case .whatsapp:     return "bubble.left"
        case .signal:       return "dot.radiowaves.left.and.right"
        case .telegram:     return "paperplane"
        case .irc:          return "number"
        }
    }

    private func iconColor(for type: SourceType) -> Color {
        switch type {
        case .communicator: return ForgeTheme.primary
        case .whatsapp:     return ForgeTheme.green
        case .signal:       return ForgeTheme.amber
        case .telegram:     return Color(hex: "#29B6F6")
        case .irc:          return ForgeTheme.violet
        }
    }

    private func resetWebSession(for source: Source) {
        webSessionManager.removeWebsiteData(for: source) {
            if store.selectedSourceID == source.id {
                store.selectAccount(id: source.id)
            }
        }
    }
}

// MARK: - Telegram provider view

private struct TelegramProviderView: View {
    let source: Source
    let sessionManager: WebSessionManager

    private let telegramDesktopURL = URL(string: "https://web.telegram.org/k/")!
    private let telegramDesktopUserAgent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

    var body: some View {
        let webView = sessionManager.webView(for: source)
        AccountWebContainerView(webView: webView)
            .onAppear {
                if webView.customUserAgent != telegramDesktopUserAgent {
                    webView.customUserAgent = telegramDesktopUserAgent
                }
                if shouldForceTelegramDesktopLoad(webView.url) {
                    webView.load(URLRequest(url: telegramDesktopURL))
                }
            }
    }

    private func shouldForceTelegramDesktopLoad(_ currentURL: URL?) -> Bool {
        guard let currentURL, let host = currentURL.host?.lowercased(), host.contains("telegram.org") else {
            return true
        }
        return !currentURL.path.hasPrefix("/k/")
    }
}

// MARK: - Sidebar starfield

private struct SidebarStarfieldBackground: View {
    private let stars: [CGPoint] = [
        CGPoint(x: 0.05, y: 0.08), CGPoint(x: 0.13, y: 0.22), CGPoint(x: 0.18, y: 0.47),
        CGPoint(x: 0.24, y: 0.14), CGPoint(x: 0.33, y: 0.36), CGPoint(x: 0.42, y: 0.19),
        CGPoint(x: 0.51, y: 0.28), CGPoint(x: 0.63, y: 0.11), CGPoint(x: 0.72, y: 0.40),
        CGPoint(x: 0.81, y: 0.24), CGPoint(x: 0.91, y: 0.15), CGPoint(x: 0.12, y: 0.67),
        CGPoint(x: 0.27, y: 0.74), CGPoint(x: 0.39, y: 0.83), CGPoint(x: 0.58, y: 0.72),
        CGPoint(x: 0.74, y: 0.80), CGPoint(x: 0.88, y: 0.64)
    ]

    var body: some View {
        ZStack {
            ForgeTheme.dark950
            GeometryReader { proxy in
                ZStack {
                    ForEach(stars.indices, id: \.self) { index in
                        Circle()
                            .fill(Color.white.opacity(index.isMultiple(of: 3) ? 0.44 : 0.26))
                            .frame(width: index.isMultiple(of: 4) ? 2.0 : 1.4)
                            .position(
                                x: stars[index].x * proxy.size.width,
                                y: stars[index].y * proxy.size.height
                            )
                    }
                }
            }
            .allowsHitTesting(false)
        }
        .ignoresSafeArea()
    }
}
