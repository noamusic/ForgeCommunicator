import SwiftUI

struct NativeCommunicatorHomeView: View {
    let source: Source
    let onProviderConfigUpdate: ((Data?) -> Void)?

    @StateObject private var store: NativeCommunicatorStore
    @State private var collapsedGroups: Set<CommunicatorConversationGroupKind> = []
    @State private var searchText = ""

    init(source: Source, onProviderConfigUpdate: ((Data?) -> Void)? = nil) {
        self.source = source
        self.onProviderConfigUpdate = onProviderConfigUpdate
        _store = StateObject(wrappedValue: NativeCommunicatorStore(source: source, onProviderConfigUpdate: onProviderConfigUpdate))
    }

    var body: some View {
        ZStack {
            ForgeBackgroundLayer()

            VStack(spacing: 12) {
                heroBar

                if store.token == nil {
                    loginPane
                } else {
                    workspacePane
                }
            }
            .padding(12)
        }
        .onAppear {
            store.onAppear()
        }
        .alert("Communicator Error", isPresented: Binding(
            get: { store.errorMessage != nil },
            set: { if !$0 { store.errorMessage = nil } }
        )) {
            Button("OK", role: .cancel) { store.errorMessage = nil }
        } message: {
            Text(store.errorMessage ?? "")
        }
    }

    private var heroBar: some View {
        HStack(spacing: 12) {
            ForgeLogoIcon(size: 34)

            VStack(alignment: .leading, spacing: 2) {
                Text("FORGE")
                    .font(ForgeTheme.brandFont(size: 16, weight: .bold))
                    .tracking(2.4)
                    .foregroundStyle(ForgeTheme.silver)
                Text("COMMUNICATOR")
                    .font(ForgeTheme.brandFont(size: 8, weight: .bold))
                    .tracking(3.4)
                    .foregroundStyle(ForgeTheme.amber)
                Text(store.currentUserDisplayName ?? "Native workspace")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Spacer(minLength: 0)

            if store.token != nil {
                Button("Refresh") {
                    Task { try? await store.refreshAll() }
                }
                .buttonStyle(.bordered)

                Button("Sign Out") {
                    store.signOut()
                }
                .buttonStyle(.bordered)
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .forgeGlassSurface()
    }

    private var loginPane: some View {
        VStack(alignment: .leading, spacing: 16) {
            VStack(alignment: .leading, spacing: 6) {
                Text("Sign in to your Forge server")
                    .font(.title3.weight(.semibold))
                    .foregroundStyle(.white)
                Text("Clear channels, direct messages, unread state, and replies from the native mission desk.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }

            VStack(spacing: 12) {
                TextField("https://comms.buildly.io", text: $store.serverURL)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit {
                        store.updateServerURL(store.serverURL)
                    }

                TextField("Email", text: $store.email)
                    .textFieldStyle(.roundedBorder)

                SecureField("Password", text: $store.password)
                    .textFieldStyle(.roundedBorder)
            }

            HStack(spacing: 10) {
                Button("Save Server") {
                    store.updateServerURL(store.serverURL)
                }
                .buttonStyle(.bordered)

                Button {
                    Task { await store.signIn() }
                } label: {
                    if store.isLoading {
                        ProgressView()
                            .progressViewStyle(.circular)
                    } else {
                        Text("Sign In")
                    }
                }
                .buttonStyle(.borderedProminent)
            }

            Divider()
                .overlay(Color.white.opacity(0.15))

            Button {
                Task { await store.signInWithGoogle() }
            } label: {
                HStack(spacing: 8) {
                    Image(systemName: "globe")
                        .font(.system(size: 14, weight: .semibold))
                    Text("Sign in with Google")
                        .font(.system(size: 13, weight: .semibold))
                }
                .frame(maxWidth: .infinity)
            }
            .buttonStyle(.bordered)
            .disabled(store.isLoading)

            Spacer(minLength: 0)
        }
        .padding(20)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .forgeGlassSurface()
    }

    private var workspacePane: some View {
        HStack(spacing: 12) {
            sidebarPane
                .frame(minWidth: 300, idealWidth: 340, maxWidth: 400)

            detailPane
        }
    }

    private var sidebarPane: some View {
        VStack(spacing: 12) {
            headerCard

            HStack(spacing: 10) {
                Image(systemName: "magnifyingglass")
                    .foregroundStyle(.secondary)
                TextField("Search conversations", text: $searchText)
                    .textFieldStyle(.plain)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 10)
            .forgeGlassSurface()

            ScrollView {
                LazyVStack(spacing: 12) {
                    ForEach(store.groupedConversationKinds, id: \.self) { kind in
                        let items = conversations(for: kind)
                        if !items.isEmpty {
                            ConversationSectionView(
                                kind: kind,
                                isCollapsed: collapsedGroups.contains(kind),
                                totalUnread: items.reduce(0) { $0 + $1.unreadCount },
                                onToggle: {
                                    if collapsedGroups.contains(kind) {
                                        collapsedGroups.remove(kind)
                                    } else {
                                        collapsedGroups.insert(kind)
                                    }
                                }
                            ) {
                                if !collapsedGroups.contains(kind) {
                                    VStack(spacing: 6) {
                                        ForEach(items) { conversation in
                                            ConversationRowView(
                                                conversation: conversation,
                                                isSelected: store.selectedConversationID == conversation.channelID
                                            ) {
                                                store.selectConversation(conversation)
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }

                    if store.groupedConversationKinds.isEmpty {
                        emptySidebarState
                    }
                }
                .padding(2)
            }
        }
        .padding(14)
        .forgeGlassSurface()
    }

    private var headerCard: some View {
        HStack(spacing: 12) {
            ConversationAvatarClusterView(conversation: store.selectedConversation ?? store.conversations.first)
                .frame(width: 42, height: 42)

            VStack(alignment: .leading, spacing: 3) {
                Text(store.currentUserDisplayName ?? "Signed In")
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(.white)
                Text("Folders: DMs, channels, Slack, Discord")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Spacer(minLength: 0)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .forgeGlassSurface()
    }

    private var emptySidebarState: some View {
        VStack(spacing: 10) {
            Image(systemName: "tray")
                .font(.system(size: 26, weight: .medium))
                .foregroundStyle(.secondary)
            Text("No conversations loaded")
                .foregroundStyle(.secondary)
            Button("Refresh") {
                Task { try? await store.refreshAll() }
            }
            .buttonStyle(.bordered)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 24)
    }

    private var detailPane: some View {
        Group {
            if let conversation = store.selectedConversation {
                VStack(spacing: 12) {
                    detailHeader(for: conversation)
                    messagesPane(for: conversation)
                    composerBar
                }
                .padding(14)
                .forgeGlassSurface()
            } else {
                VStack(spacing: 14) {
                    ForgeLogoIcon(size: 48)
                    Text("Select a conversation")
                        .font(.title3.weight(.semibold))
                        .foregroundStyle(.white)
                    Text("The native Communicator pane now mirrors the web app more closely, with grouped folders and richer conversation previews.")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                }
                .padding(24)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .forgeGlassSurface()
            }
        }
    }

    private func detailHeader(for conversation: CommunicatorConversation) -> some View {
        HStack(spacing: 12) {
            ConversationAvatarClusterView(conversation: conversation)
                .frame(width: 44, height: 44)

            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 8) {
                    Text(conversation.name)
                        .font(.headline)
                        .foregroundStyle(.white)
                    if conversation.isDM {
                        PillLabel(text: "DM", systemImage: "person.2.fill")
                    } else {
                        PillLabel(text: "Channel", systemImage: "number")
                    }
                    if let bridged = conversation.bridgedPlatform, !bridged.isEmpty {
                        PillLabel(text: bridged.capitalized, systemImage: bridged == "slack" ? "bubble.left" : "dot.radiowaves.left.and.right")
                    }
                }

                Text(conversation.workspaceName)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Spacer(minLength: 0)

            Button("Mark Read") {
                Task {
                    try? await store.refreshAll()
                }
            }
            .buttonStyle(.bordered)
        }
        .padding(.horizontal, 6)
    }

    private func messagesPane(for conversation: CommunicatorConversation) -> some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(spacing: 10) {
                    ForEach(store.messages) { message in
                        MessageBubbleView(message: message)
                    }
                }
                .padding(.vertical, 8)
                Color.clear.frame(height: 1).id("messagesBottom")
            }
            .onAppear {
                proxy.scrollTo("messagesBottom", anchor: .bottom)
            }
            .onChange(of: store.messages.count) { _ in
                withAnimation(.easeOut(duration: 0.2)) {
                    proxy.scrollTo("messagesBottom", anchor: .bottom)
                }
            }
        }
    }

    private var composerBar: some View {
        HStack(spacing: 10) {
            TextField("Type a message", text: $store.draft, axis: .vertical)
                .textFieldStyle(.roundedBorder)
                .lineLimit(1...4)

            Button("Send") {
                Task { await store.sendDraftMessage() }
            }
            .buttonStyle(.borderedProminent)
        }
        .padding(.top, 4)
    }

    private func conversations(for kind: CommunicatorConversationGroupKind) -> [CommunicatorConversation] {
        let filtered = store.conversations.filter { $0.groupKind == kind }
        if searchText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            return filtered
        }

        let query = searchText.lowercased()
        return filtered.filter {
            $0.name.lowercased().contains(query) ||
            $0.workspaceName.lowercased().contains(query) ||
            ($0.lastMessage?.body.lowercased().contains(query) ?? false)
        }
    }
}

private struct ConversationSectionView<Content: View>: View {
    let kind: CommunicatorConversationGroupKind
    let isCollapsed: Bool
    let totalUnread: Int
    let onToggle: () -> Void
    @ViewBuilder let content: () -> Content

    var body: some View {
        VStack(spacing: 8) {
            Button(action: onToggle) {
                HStack(spacing: 10) {
                    Image(systemName: isCollapsed ? "chevron.right" : "chevron.down")
                        .font(.system(size: 11, weight: .bold))
                        .foregroundStyle(.secondary)
                        .frame(width: 14)

                    Image(systemName: kind.systemImage)
                        .foregroundStyle(ForgeTheme.primary)
                        .font(.system(size: 13, weight: .semibold))

                    Text(kind.title)
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(.white)

                    if totalUnread > 0 {
                        Text("\(totalUnread)")
                            .font(.system(size: 10, weight: .bold))
                            .foregroundStyle(.white)
                            .padding(.horizontal, 5)
                            .padding(.vertical, 2)
                            .background(Capsule().fill(ForgeTheme.amber))
                    }

                    Spacer(minLength: 0)
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 9)
                .background(
                    RoundedRectangle(cornerRadius: 12, style: .continuous)
                        .fill(ForgeTheme.overlayFill.opacity(0.76))
                )
                .overlay(
                    RoundedRectangle(cornerRadius: 12, style: .continuous)
                        .stroke(ForgeTheme.glassBorder, lineWidth: 1)
                )
            }
            .buttonStyle(.plain)

            content()
        }
    }
}

private struct ConversationRowView: View {
    let conversation: CommunicatorConversation
    let isSelected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 10) {
                ConversationAvatarClusterView(conversation: conversation)
                    .frame(width: 34, height: 34)

                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 6) {
                        Text(conversation.name)
                            .font(.system(size: 13, weight: .semibold))
                            .foregroundStyle(.white)
                            .lineLimit(1)
                        if conversation.unreadCount > 0 {
                            Text("\(conversation.unreadCount)")
                                .font(.system(size: 10, weight: .bold))
                                .foregroundStyle(.white)
                                .padding(.horizontal, 6)
                                .padding(.vertical, 2)
                                .background(Capsule().fill(ForgeTheme.amber))
                        }
                    }

                    HStack(spacing: 6) {
                        Text(conversation.workspaceName)
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                        if let bridged = conversation.bridgedPlatform {
                            Text(bridged.capitalized)
                                .font(.caption2.weight(.semibold))
                                .foregroundStyle(ForgeTheme.primary)
                        }
                    }

                    Text(conversation.lastMessage?.body ?? "No recent messages")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }

                Spacer(minLength: 0)
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 9)
            .background(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .fill(isSelected ? ForgeTheme.primary.opacity(0.14) : ForgeTheme.overlayFill.opacity(0.74))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .stroke(isSelected ? ForgeTheme.glassBorderActive : ForgeTheme.glassBorder, lineWidth: 1)
            )
        }
        .buttonStyle(.plain)
    }
}

private struct ConversationAvatarClusterView: View {
    let conversation: CommunicatorConversation?

    var body: some View {
        if let conversation {
            if conversation.isDM {
                dmAvatarCluster(for: conversation)
            } else {
                channelAvatar(for: conversation)
            }
        } else {
            fallbackAvatar
        }
    }

    private func dmAvatarCluster(for conversation: CommunicatorConversation) -> some View {
        let members = Array(conversation.members.prefix(3))
        return ZStack {
            if members.count > 1 {
                ForEach(Array(members.enumerated()), id: \.offset) { index, member in
                    MemberAvatarView(member: member, size: 26)
                        .offset(x: CGFloat(index) * 10, y: CGFloat(index) * 0)
                }
            } else if let member = members.first {
                MemberAvatarView(member: member, size: 32)
            } else {
                fallbackAvatar
            }
        }
    }

    private func channelAvatar(for conversation: CommunicatorConversation) -> some View {
        return ZStack {
            Circle()
                .fill(
                    LinearGradient(
                        colors: [ForgeTheme.primary, ForgeTheme.amber],
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    )
                )
            Image(systemName: conversation.bridgedPlatform == nil ? "number" : "waveform")
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(.white)
        }
    }

    private var fallbackAvatar: some View {
        Circle()
            .fill(LinearGradient(colors: [ForgeTheme.primary, ForgeTheme.amber], startPoint: .topLeading, endPoint: .bottomTrailing))
            .overlay(Image(systemName: "person.fill").font(.system(size: 12, weight: .semibold)).foregroundStyle(.white))
    }
}

private struct MemberAvatarView: View {
    let member: CommunicatorUserProfile
    var size: CGFloat = 30

    var body: some View {
        Group {
            if let urlString = member.avatarURL, let url = URL(string: urlString) {
                AsyncImage(url: url) { phase in
                    switch phase {
                    case .success(let image):
                        image.resizable().scaledToFill()
                    default:
                        initialsAvatar
                    }
                }
            } else {
                initialsAvatar
            }
        }
        .frame(width: size, height: size)
        .clipShape(Circle())
        .overlay(Circle().stroke(Color.white.opacity(0.2), lineWidth: 1))
        .shadow(color: Color.black.opacity(0.18), radius: 4, x: 0, y: 1)
    }

    private var initialsAvatar: some View {
        ZStack {
            Circle().fill(LinearGradient(colors: [ForgeTheme.primary, ForgeTheme.amber], startPoint: .topLeading, endPoint: .bottomTrailing))
            Text(initials(for: member.displayName))
                .font(.system(size: max(size * 0.32, 10), weight: .bold))
                .foregroundStyle(.white)
        }
    }

    private func initials(for value: String) -> String {
        let parts = value.split(separator: " ")
        let letters = parts.prefix(2).compactMap { $0.first }
        return letters.isEmpty ? "?" : String(letters).uppercased()
    }
}

private struct MessageBubbleView: View {
    let message: CommunicatorMessage

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            MemberAvatarView(
                member: CommunicatorUserProfile(
                    id: message.author?.id ?? 0,
                    displayName: message.author?.displayName ?? "Unknown",
                    avatarURL: message.author?.avatarURL
                ),
                size: 30
            )

            VStack(alignment: .leading, spacing: 6) {
                HStack {
                    Text(message.author?.displayName ?? "Unknown")
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(.white)
                    Spacer(minLength: 0)
                    Text(message.createdAt, style: .time)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }

                MessageBodyText(text: message.body)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .fill(ForgeTheme.overlayFill.opacity(0.76))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 16, style: .continuous)
                    .stroke(ForgeTheme.glassBorder, lineWidth: 1)
            )
        }
    }
}

private struct MessageBodyText: View {
    let text: String

    var body: some View {
        Text(attributedBody)
            .font(.system(size: 13))
            .fixedSize(horizontal: false, vertical: true)
            .tint(ForgeTheme.amber)
    }

    private var attributedBody: AttributedString {
        var result = AttributedString(text)
        result.foregroundColor = .white

        guard let detector = try? NSDataDetector(
            types: NSTextCheckingResult.CheckingType.link.rawValue
        ) else { return result }

        let nsLen = (text as NSString).length
        let matches = detector.matches(in: text, range: NSRange(location: 0, length: nsLen))
        for match in matches {
            guard let url = match.url,
                  let swiftRange = Range(match.range, in: text),
                  let attrRange = Range(swiftRange, in: result) else { continue }
            result[attrRange].link = url
            result[attrRange].foregroundColor = ForgeTheme.amber
        }
        return result
    }
}

private struct PillLabel: View {
    let text: String
    let systemImage: String

    var body: some View {
        HStack(spacing: 4) {
            Image(systemName: systemImage)
            Text(text)
        }
        .font(.caption2.weight(.semibold))
        .foregroundStyle(.secondary)
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
        .background(Capsule().fill(ForgeTheme.dark800.opacity(0.72)))
        .overlay(Capsule().stroke(ForgeTheme.glassBorder, lineWidth: 1))
    }
}
