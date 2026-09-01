import SwiftUI

// MARK: - ConversationRailView

struct ConversationRailView: View {
    @ObservedObject var store: NativeCommunicatorStore
    @ObservedObject var accountStore: AccountStore
    @Binding var selectedSourceID: UUID?
    var onOpenConversation: (CommunicatorConversation) -> Void
    var onOpenSettings: () -> Void
    var onCompose: (() -> Void)? = nil

    @State private var searchText: String = ""
    @State private var shimmerPhase = false
    @State private var collapsedGroups: Set<String> = []
    @State private var showNewDM = false

    // Web source (WA/Telegram) unread tracking
    @State private var webUnreadBySourceID: [UUID: Int] = [:]
    @State private var toastMessage: String? = nil
    @State private var toastTask: Task<Void, Never>? = nil

    // MARK: Filtered conversations

    private var filteredConversations: [CommunicatorConversation] {
        guard !searchText.isEmpty else { return store.conversations }
        let q = searchText.lowercased()
        return store.conversations.filter {
            $0.name.lowercased().contains(q) ||
            ($0.lastMessage?.body.lowercased().contains(q) == true)
        }
    }

    private var unreadConversations: [CommunicatorConversation] {
        filteredConversations.filter { $0.unreadCount > 0 }
    }

    private var channelConversations: [CommunicatorConversation] {
        filteredConversations.filter { !$0.isDM && $0.unreadCount == 0 && $0.bridgedPlatform == nil }
    }

    private var dmConversations: [CommunicatorConversation] {
        filteredConversations.filter { $0.isDM && $0.unreadCount == 0 && $0.bridgedPlatform == nil }
    }

    /// Bridged (Slack/Discord) conversations get their own folder, like the web sidebar.
    private var bridgedConversations: [CommunicatorConversation] {
        filteredConversations.filter { $0.unreadCount == 0 && $0.bridgedPlatform != nil }
    }

    private func groupedByPlatform(_ convos: [CommunicatorConversation]) -> [(String, [CommunicatorConversation])] {
        var result: [(String, [CommunicatorConversation])] = []
        var seen: [String: Int] = [:]
        for c in convos {
            let key = (c.bridgedPlatform ?? "bridged").capitalized
            if let idx = seen[key] {
                result[idx].1.append(c)
            } else {
                seen[key] = result.count
                result.append((key, [c]))
            }
        }
        return result
    }

    private func groupedByWorkspace(_ convos: [CommunicatorConversation]) -> [(String, [CommunicatorConversation])] {
        var result: [(String, [CommunicatorConversation])] = []
        var seen: [String: Int] = [:]
        for c in convos {
            let key = c.workspaceName.isEmpty ? "Unknown" : c.workspaceName
            if let idx = seen[key] {
                result[idx].1.append(c)
            } else {
                seen[key] = result.count
                result.append((key, [c]))
            }
        }
        return result
    }

    // MARK: Body

    var body: some View {
        ZStack(alignment: .bottom) {
            VStack(spacing: 0) {
                headerBar
                sourceSwitcher
                searchBar
                conversationList
                userFooter
            }

            // Toast banner for WA/Telegram new messages
            if let msg = toastMessage {
                HStack(spacing: 8) {
                    Image(systemName: "bell.fill")
                        .font(.system(size: 11))
                        .foregroundColor(ForgeTheme.amber)
                    Text(msg)
                        .font(.system(size: 11, weight: .medium))
                        .foregroundColor(ForgeTheme.white)
                        .lineLimit(2)
                }
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
                .background(ForgeTheme.dark800.opacity(0.97))
                .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                .overlay(
                    RoundedRectangle(cornerRadius: 10, style: .continuous)
                        .strokeBorder(ForgeTheme.amber.opacity(0.4), lineWidth: 1)
                )
                .padding(.horizontal, 8)
                .padding(.bottom, 8)
                .transition(.move(edge: .bottom).combined(with: .opacity))
            }
        }
        .frame(minWidth: 220, idealWidth: 260, maxWidth: 300)
        .background(ForgeTheme.dark900)
        .onReceive(NotificationCenter.default.publisher(for: .forgeWebSourceUnread)) { note in
            guard
                let sourceID = note.userInfo?["sourceID"] as? UUID,
                let count = note.userInfo?["unreadCount"] as? Int,
                let sourceName = note.userInfo?["sourceName"] as? String,
                let body = note.userInfo?["body"] as? String
            else { return }

            webUnreadBySourceID[sourceID] = count

            let preview = body.count > 60 ? String(body.prefix(60)) + "…" : body
            showToast("\(sourceName): \(preview)")
        }
    }

    private func showToast(_ message: String) {
        toastTask?.cancel()
        withAnimation(.spring(response: 0.3)) {
            toastMessage = message
        }
        toastTask = Task {
            try? await Task.sleep(nanoseconds: 4_000_000_000)
            guard !Task.isCancelled else { return }
            withAnimation(.easeOut(duration: 0.25)) {
                toastMessage = nil
            }
        }
    }

    // MARK: - Header

    private var headerBar: some View {
        VStack(spacing: 0) {
            HStack(spacing: 8) {
                ForgeLogoIcon(size: 22)

                Text("Forge")
                    .font(ForgeTheme.headingFont(size: 13, weight: .semibold))
                    .foregroundColor(ForgeTheme.white)

                Spacer()

                Circle()
                    .fill(ForgeTheme.statusOnline)
                    .frame(width: 8, height: 8)

                Button(action: { showNewDM = true }) {
                    Image(systemName: "square.and.pencil")
                        .font(.system(size: 13, weight: .regular))
                        .foregroundColor(ForgeTheme.silver.opacity(0.7))
                }
                .buttonStyle(.plain)
                .help("New message")
                .popover(isPresented: $showNewDM, arrowEdge: .bottom) {
                    NewDMSheet(store: store, onOpenConversation: onOpenConversation)
                }

                Button(action: onOpenSettings) {
                    Image(systemName: "gearshape")
                        .font(.system(size: 13, weight: .regular))
                        .foregroundColor(ForgeTheme.silver.opacity(0.7))
                }
                .buttonStyle(.plain)
                .help("Settings")
            }
            .padding(.horizontal, 12)
            .frame(height: 52)

            Divider()
                .background(ForgeTheme.glassBorder)
        }
        .background(ForgeTheme.dark950)
    }

    // MARK: - Source Switcher

    private var sourceSwitcher: some View {
        VStack(spacing: 0) {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    ForEach(accountStore.accounts) { source in
                        SourcePillButton(
                            source: source,
                            isSelected: selectedSourceID == source.id,
                            webUnreadCount: webUnreadBySourceID[source.id],
                            onTap: {
                                selectedSourceID = source.id
                                if source.type != .communicator {
                                    NotificationCenter.default.post(
                                        name: .forgeOpenWorkspaceSource,
                                        object: nil,
                                        userInfo: ["sourceID": source.id]
                                    )
                                }
                            }
                        )
                    }

                    Button(action: onOpenSettings) {
                        Image(systemName: "plus")
                            .font(.system(size: 12, weight: .semibold))
                            .foregroundColor(ForgeTheme.silver.opacity(0.6))
                            .frame(width: 28, height: 28)
                            .background(ForgeTheme.dark700.opacity(0.5))
                            .clipShape(Circle())
                    }
                    .buttonStyle(.plain)
                    .help("Add source")
                }
                .padding(.horizontal, 10)
            }
            .frame(height: 48)

            Divider()
                .background(ForgeTheme.glassBorder)
        }
        .background(ForgeTheme.dark900)
    }

    // MARK: - Search Bar

    private var searchBar: some View {
        HStack(spacing: 6) {
            Image(systemName: "magnifyingglass")
                .font(.system(size: 11, weight: .regular))
                .foregroundColor(ForgeTheme.silver.opacity(0.45))

            TextField("Search...", text: $searchText)
                .textFieldStyle(.plain)
                .font(.system(size: 12))
                .foregroundColor(ForgeTheme.white)
        }
        .padding(.horizontal, 8)
        .frame(height: 28)
        .background(ForgeTheme.dark800.opacity(0.8))
        .clipShape(RoundedRectangle(cornerRadius: 6, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 6, style: .continuous)
                .strokeBorder(ForgeTheme.glassBorder, lineWidth: 1)
        )
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
        .frame(height: 36)
    }

    // MARK: - Conversation List

    private var selectedSourceIsNonCommunicator: Bool {
        guard let id = selectedSourceID else { return false }
        return accountStore.accounts.first(where: { $0.id == id })?.type != .communicator
    }

    private var conversationList: some View {
        Group {
            // Bug 6: non-communicator sources don't have a conversation API
            if selectedSourceIsNonCommunicator {
                VStack(spacing: 16) {
                    Image(systemName: "safari")
                        .font(.system(size: 32))
                        .foregroundColor(ForgeTheme.silver.opacity(0.4))
                    Text("This app opens in the full workspace")
                        .font(.system(size: 12))
                        .foregroundColor(ForgeTheme.silver.opacity(0.5))
                        .multilineTextAlignment(.center)
                    Button("Open in Workspace") {
                        if let id = selectedSourceID {
                            NotificationCenter.default.post(
                                name: .forgeOpenWorkspaceSource,
                                object: nil,
                                userInfo: ["sourceID": id]
                            )
                        } else {
                            onOpenSettings()
                        }
                    }
                    .buttonStyle(.plain)
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundColor(ForgeTheme.primary)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .padding(20)
            } else if store.isLoading && store.conversations.isEmpty {
                // Bug 2: shimmer loading state
                VStack(spacing: 0) {
                    ForEach(0..<4, id: \.self) { _ in
                        HStack(spacing: 8) {
                            Circle()
                                .fill(ForgeTheme.dark700)
                                .opacity(shimmerPhase ? 0.6 : 0.3)
                                .frame(width: 34, height: 34)
                            VStack(alignment: .leading, spacing: 6) {
                                RoundedRectangle(cornerRadius: 4)
                                    .fill(ForgeTheme.dark700)
                                    .opacity(shimmerPhase ? 0.6 : 0.3)
                                    .frame(width: 90, height: 10)
                                RoundedRectangle(cornerRadius: 4)
                                    .fill(ForgeTheme.dark700)
                                    .opacity(shimmerPhase ? 0.6 : 0.3)
                                    .frame(width: 140, height: 8)
                            }
                            Spacer()
                        }
                        .padding(.horizontal, 10)
                        .padding(.vertical, 8)
                    }
                    Spacer()
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .onAppear {
                    withAnimation(.easeInOut(duration: 1.2).repeatForever()) {
                        shimmerPhase = true
                    }
                }
            } else {
                ScrollView(.vertical, showsIndicators: false) {
                    LazyVStack(spacing: 0, pinnedViews: [.sectionHeaders]) {
                        // Bug 3: grouped structure
                        // 1. Unread (non-collapsible)
                        if !unreadConversations.isEmpty {
                            Section {
                                ForEach(unreadConversations) { conversation in
                                    RailConversationRow(
                                        conversation: conversation,
                                        isSelected: store.selectedConversationID == conversation.channelID,
                                        onTap: { onOpenConversation(conversation) }
                                    )
                                }
                            } header: {
                                railSectionHeader("Unread", collapsible: false, groupKey: "unread")
                            }
                        }

                        // 2. Channels grouped by workspace
                        let channelGroups = groupedByWorkspace(channelConversations)
                        ForEach(channelGroups, id: \.0) { (workspace, convos) in
                            let key = "channel-\(workspace)"
                            let collapsed = collapsedGroups.contains(key)
                            Section {
                                if !collapsed {
                                    ForEach(convos) { conversation in
                                        RailConversationRow(
                                            conversation: conversation,
                                            isSelected: store.selectedConversationID == conversation.channelID,
                                            onTap: { onOpenConversation(conversation) }
                                        )
                                    }
                                }
                            } header: {
                                railSectionHeader("# \(workspace)", collapsible: true, groupKey: key)
                            }
                        }

                        // 3. Direct Messages grouped by workspace
                        let dmGroups = groupedByWorkspace(dmConversations)
                        ForEach(dmGroups, id: \.0) { (workspace, convos) in
                            let key = "dm-\(workspace)"
                            let collapsed = collapsedGroups.contains(key)
                            Section {
                                if !collapsed {
                                    ForEach(convos) { conversation in
                                        RailConversationRow(
                                            conversation: conversation,
                                            isSelected: store.selectedConversationID == conversation.channelID,
                                            onTap: { onOpenConversation(conversation) }
                                        )
                                    }
                                }
                            } header: {
                                railSectionHeader("@ \(workspace)", collapsible: true, groupKey: key)
                            }
                        }

                        // 4. Bridged sources (Slack/Discord) each get their own folder
                        let bridgeGroups = groupedByPlatform(bridgedConversations)
                        ForEach(bridgeGroups, id: \.0) { (platform, convos) in
                            let key = "bridge-\(platform)"
                            let collapsed = collapsedGroups.contains(key)
                            Section {
                                if !collapsed {
                                    ForEach(convos) { conversation in
                                        RailConversationRow(
                                            conversation: conversation,
                                            isSelected: store.selectedConversationID == conversation.channelID,
                                            onTap: { onOpenConversation(conversation) }
                                        )
                                    }
                                }
                            } header: {
                                railSectionHeader("⇄ \(platform)", collapsible: true, groupKey: key)
                            }
                        }

                        if filteredConversations.isEmpty {
                            Text(searchText.isEmpty ? "No conversations" : "No results")
                                .font(.system(size: 11))
                                .foregroundColor(ForgeTheme.silver.opacity(0.4))
                                .frame(maxWidth: .infinity)
                                .padding(.vertical, 24)
                        }
                    }
                }
                .frame(maxHeight: .infinity)
                .background(ForgeTheme.dark900)
            }
        }
    }

    private func railSectionHeader(_ title: String, collapsible: Bool, groupKey: String) -> some View {
        let collapsed = collapsedGroups.contains(groupKey)
        return HStack(spacing: 4) {
            if collapsible {
                Image(systemName: collapsed ? "chevron.right" : "chevron.down")
                    .font(.system(size: 8))
                    .foregroundColor(ForgeTheme.silver.opacity(0.45))
            }
            Text(title.uppercased())
                .font(.system(size: 9, weight: .semibold))
                .foregroundColor(ForgeTheme.silver.opacity(0.45))
                .tracking(0.8)
            Spacer()
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 4)
        .background(ForgeTheme.dark900)
        .contentShape(Rectangle())
        .onTapGesture {
            if collapsible {
                if collapsedGroups.contains(groupKey) {
                    collapsedGroups.remove(groupKey)
                } else {
                    collapsedGroups.insert(groupKey)
                }
            }
        }
    }

    // MARK: - User Footer

    private var userFooter: some View {
        VStack(spacing: 0) {
            Divider()
                .background(ForgeTheme.glassBorder)

            HStack(spacing: 8) {
                InitialsAvatar(
                    name: store.currentUserDisplayName ?? "Me",
                    size: 32
                )

                VStack(alignment: .leading, spacing: 1) {
                    Text(store.currentUserDisplayName ?? "Me")
                        .font(.system(size: 12, weight: .medium))
                        .foregroundColor(ForgeTheme.white)
                        .lineLimit(1)

                    HStack(spacing: 4) {
                        Circle()
                            .fill(ForgeTheme.statusOnline)
                            .frame(width: 6, height: 6)
                        Text("Online")
                            .font(.system(size: 10))
                            .foregroundColor(ForgeTheme.silver.opacity(0.55))
                    }
                }

                Spacer()

                Button(action: onOpenSettings) {
                    Image(systemName: "gearshape")
                        .font(.system(size: 12, weight: .regular))
                        .foregroundColor(ForgeTheme.silver.opacity(0.6))
                }
                .buttonStyle(.plain)
            }
            .padding(.horizontal, 12)
            .frame(height: 52)
        }
        .background(ForgeTheme.dark950)
    }
}

// MARK: - SourcePillButton

private struct SourcePillButton: View {
    let source: Source
    let isSelected: Bool
    var webUnreadCount: Int? = nil
    let onTap: () -> Void

    private var accentColor: Color {
        switch source.type {
        case .communicator: return ForgeTheme.primary
        case .whatsapp:     return ForgeTheme.green
        case .signal:       return ForgeTheme.primary
        case .telegram:     return Color(hex: "#29B6F6")
        case .irc:          return ForgeTheme.violet
        }
    }

    var body: some View {
        Button(action: onTap) {
            ZStack(alignment: .topTrailing) {
                HStack(spacing: 4) {
                    Image(systemName: iconName(for: source.type))
                        .font(.system(size: 11, weight: .medium))
                        .foregroundColor(isSelected ? .white : ForgeTheme.silver.opacity(0.7))
                }
                .padding(.horizontal, 10)
                .frame(height: 28)
                .background(isSelected ? accentColor : ForgeTheme.dark700.opacity(0.5))
                .clipShape(Capsule())
                .overlay(
                    Capsule()
                        .strokeBorder(
                            isSelected ? accentColor.opacity(0.4) : ForgeTheme.glassBorder,
                            lineWidth: 1
                        )
                )

                // Unread badge for web sources (WA/Telegram)
                if let count = webUnreadCount, count > 0 {
                    Text(count > 99 ? "99+" : "\(count)")
                        .font(.system(size: 8, weight: .bold))
                        .foregroundColor(.white)
                        .padding(.horizontal, 4)
                        .frame(minWidth: 14, minHeight: 14)
                        .background(ForgeTheme.coral)
                        .clipShape(Capsule())
                        .offset(x: 4, y: -4)
                }
            }
        }
        .buttonStyle(.plain)
        .help(source.displayName)
    }

    private func iconName(for type: SourceType) -> String {
        switch type {
        case .communicator: return "bubble.left.and.bubble.right.fill"
        case .whatsapp:     return "phone.bubble.left.fill"
        case .signal:       return "lock.shield.fill"
        case .telegram:     return "paperplane.fill"
        case .irc:          return "number"
        }
    }
}

// MARK: - RailConversationRow

private struct RailConversationRow: View {
    let conversation: CommunicatorConversation
    let isSelected: Bool
    let onTap: () -> Void

    @State private var isHovered = false

    private var rowBackground: Color {
        if isSelected { return ForgeTheme.primary.opacity(0.12) }
        if isHovered  { return ForgeTheme.dark700.opacity(0.4) }
        return .clear
    }

    var body: some View {
        HStack(spacing: 8) {
            InitialsAvatar(name: conversation.name, size: 34)

            VStack(alignment: .leading, spacing: 2) {
                Text(conversation.name)
                    .font(.system(size: 12, weight: conversation.unreadCount > 0 ? .bold : .medium))
                    .foregroundColor(conversation.unreadCount > 0 ? .white : ForgeTheme.silver.opacity(0.85))
                    .lineLimit(1)

                if let preview = conversation.lastMessage?.body, !preview.isEmpty {
                    Text(preview)
                        .font(.system(size: 11))
                        .foregroundColor(ForgeTheme.silver.opacity(0.55))
                        .lineLimit(1)
                } else {
                    Text("No messages yet")
                        .font(.system(size: 11))
                        .foregroundColor(ForgeTheme.silver.opacity(0.3))
                        .lineLimit(1)
                }
            }

            Spacer(minLength: 4)

            VStack(alignment: .trailing, spacing: 4) {
                if let lastMsg = conversation.lastMessage {
                    Text(relativeTimestamp(lastMsg.createdAt))
                        .font(.system(size: 10))
                        .foregroundColor(ForgeTheme.silver.opacity(0.4))
                }

                if conversation.unreadCount > 0 {
                    Text("\(conversation.unreadCount)")
                        .font(.system(size: 10, weight: .bold))
                        .foregroundColor(.white)
                        .padding(.horizontal, 5)
                        .frame(minWidth: 18, minHeight: 16)
                        .background(ForgeTheme.primary)
                        .clipShape(Capsule())
                }
            }
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .background(rowBackground)
        .contentShape(Rectangle())
        .onTapGesture(perform: onTap)
        .onHover { hovering in
            withAnimation(.easeInOut(duration: 0.12)) {
                isHovered = hovering
            }
        }
    }

    private func relativeTimestamp(_ date: Date) -> String {
        let now = Date()
        let diff = now.timeIntervalSince(date)

        if diff < 60 { return "now" }
        if diff < 3600 {
            let mins = Int(diff / 60)
            return "\(mins)m"
        }
        if diff < 86400 {
            let hours = Int(diff / 3600)
            return "\(hours)h"
        }
        let formatter = DateFormatter()
        formatter.dateFormat = "MMM d"
        return formatter.string(from: date)
    }
}

// MARK: - InitialsAvatar

private struct InitialsAvatar: View {
    let name: String
    let size: CGFloat

    private var initials: String {
        let parts = name.split(separator: " ").prefix(2)
        return parts.compactMap { $0.first.map { String($0).uppercased() } }.joined()
    }

    private var gradientColors: [Color] {
        let palette: [(Color, Color)] = [
            (ForgeTheme.primary, Color(hex: "#1C56B8")),
            (ForgeTheme.violet, Color(hex: "#4A34CC")),
            (ForgeTheme.green, Color(hex: "#1A8C5E")),
            (ForgeTheme.amber, Color(hex: "#CC8800")),
            (ForgeTheme.coral, Color(hex: "#CC3B3B")),
        ]
        let index = abs(name.hashValue) % palette.count
        return [palette[index].0, palette[index].1]
    }

    var body: some View {
        ZStack {
            LinearGradient(
                colors: gradientColors,
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )

            Text(initials.isEmpty ? "?" : initials)
                .font(.system(size: size * 0.38, weight: .semibold))
                .foregroundColor(.white)
        }
        .frame(width: size, height: size)
        .clipShape(Circle())
    }
}
