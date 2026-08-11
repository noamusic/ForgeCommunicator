import Foundation
import UserNotifications
import AuthenticationServices
import AppKit

// Provides the NSWindow anchor required by ASWebAuthenticationSession on macOS.
private final class OAuthWindowAnchor: NSObject, ASWebAuthenticationPresentationContextProviding {
    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        NSApp.keyWindow ?? NSApp.mainWindow ?? NSApp.windows.first ?? NSWindow()
    }
}

@MainActor
final class NativeCommunicatorStore: ObservableObject {
    private struct ConversationSnapshot {
        let unreadCount: Int
        let lastMessageID: Int?
    }

    @Published var serverURL: String
    @Published var email: String
    @Published var password: String = ""
    @Published var token: String?
    @Published var currentUserDisplayName: String?
    @Published var currentUserID: Int?

    @Published private(set) var conversations: [CommunicatorConversation] = []
    @Published private(set) var groupedConversationKinds: [CommunicatorConversationGroupKind] = []
    @Published var selectedConversationID: Int?
    @Published private(set) var messages: [CommunicatorMessage] = []

    @Published var draft: String = ""
    @Published var isLoading: Bool = false
    @Published var errorMessage: String?

    private let oauthAnchor = OAuthWindowAnchor()
    private var activeOAuthSession: ASWebAuthenticationSession?

    private let source: Source
    private let onProviderConfigUpdate: ((Data?) -> Void)?
    private var pollingTask: Task<Void, Never>?
    private var conversationSnapshotByChannelID: [Int: ConversationSnapshot] = [:]
    private var hasPrimedNotificationSnapshot = false

    init(source: Source, onProviderConfigUpdate: ((Data?) -> Void)? = nil) {
        self.source = source
        self.onProviderConfigUpdate = onProviderConfigUpdate

        let config = source.communicatorConfig()
        self.serverURL = config.serverURL
        self.token = config.mobileAccessToken
        self.currentUserDisplayName = nil
        self.email = ""
    }

    deinit {
        pollingTask?.cancel()
    }

    func updateServerURL(_ value: String) {
        serverURL = value
        persistConfig()
    }

    func signIn() async {
        let normalizedEmail = email.trimmingCharacters(in: .whitespacesAndNewlines)
        let normalizedPassword = password.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !normalizedEmail.isEmpty, !normalizedPassword.isEmpty else {
            errorMessage = "Email and password are required."
            return
        }

        isLoading = true
        defer { isLoading = false }

        do {
            let client = try CommunicatorAPIClient(serverURL: serverURL)
            let auth = try await client.login(email: normalizedEmail, password: normalizedPassword)
            token = auth.token
            currentUserDisplayName = auth.user.displayName
            currentUserID = auth.user.id
            email = auth.user.email
            password = ""
            persistConfig()
            try await refreshAll()
            startPolling()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func signOut() {
        token = nil
        conversations = []
        selectedConversationID = nil
        messages = []
        currentUserDisplayName = nil
        currentUserID = nil
        draft = ""
        pollingTask?.cancel()
        pollingTask = nil
        conversationSnapshotByChannelID = [:]
        hasPrimedNotificationSnapshot = false
        activeOAuthSession?.cancel()
        activeOAuthSession = nil
        persistConfig()
    }

    func signInWithGoogle() async {
        isLoading = true
        defer { isLoading = false }

        do {
            let client = try CommunicatorAPIClient(serverURL: serverURL)
            let start = try await client.oauthStart(provider: "google")
            guard let authURL = URL(string: start.authURL) else {
                errorMessage = "Invalid OAuth URL from server."
                return
            }

            let oauthToken = try await withCheckedThrowingContinuation { (cont: CheckedContinuation<String, Error>) in
                let session = ASWebAuthenticationSession(
                    url: authURL,
                    callbackURLScheme: "forge"
                ) { callbackURL, error in
                    if let error {
                        cont.resume(throwing: error)
                        return
                    }
                    guard let callbackURL,
                          let components = URLComponents(url: callbackURL, resolvingAgainstBaseURL: false),
                          let t = components.queryItems?.first(where: { $0.name == "token" })?.value
                    else {
                        cont.resume(throwing: CommunicatorAPIClient.APIError.invalidResponse)
                        return
                    }
                    cont.resume(returning: t)
                }
                session.presentationContextProvider = self.oauthAnchor
                session.prefersEphemeralWebBrowserSession = false
                self.activeOAuthSession = session
                session.start()
            }
            activeOAuthSession = nil

            let profile = try await client.fetchMyProfile(token: oauthToken)
            token = oauthToken
            currentUserDisplayName = profile.displayName
            currentUserID = profile.id
            email = profile.email
            password = ""
            persistConfig()
            try await refreshAll()
            startPolling()
        } catch {
            activeOAuthSession = nil
            let authError = error as? ASWebAuthenticationSessionError
            if authError?.code != .canceledLogin {
                errorMessage = error.localizedDescription
            }
        }
    }

    func onAppear() {
        guard let token else { return }
        Task {
            if currentUserID == nil {
                if let client = try? CommunicatorAPIClient(serverURL: serverURL),
                   let profile = try? await client.fetchMyProfile(token: token) {
                    currentUserID = profile.id
                    currentUserDisplayName = profile.displayName
                }
            }
            do {
                try await refreshAll()
            } catch {
                handlePollingOrRefreshError(error)
            }
            startPolling()
        }
    }

    func refreshAll() async throws {
        guard let token else { return }

        let client = try CommunicatorAPIClient(serverURL: serverURL)
        let nextConversations = try await client.listConversations(token: token, includeChannels: true)
        notifyOnConversationDeltas(nextConversations)
        conversations = nextConversations
        groupedConversationKinds = Self.computeGroupKinds(from: nextConversations)

        let totalUnread = nextConversations.reduce(0) { $0 + $1.unreadCount }
        try? await UNUserNotificationCenter.current().setBadgeCount(totalUnread)

        // Do NOT auto-select or auto-load messages here: the private
        // loadMessages(for:) marks the conversation read on the server,
        // which silently wiped unread state for the first conversation on
        // every poll. Floating windows load and mark read explicitly.
    }

    func selectConversation(_ conversation: CommunicatorConversation) {
        selectedConversationID = conversation.channelID
        Task {
            do {
                try await loadMessages(for: conversation)
            } catch {
                errorMessage = error.localizedDescription
            }
        }
    }

    func sendDraftMessage() async {
        guard let token, let conversation = selectedConversation else { return }

        let body = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !body.isEmpty else { return }

        do {
            let client = try CommunicatorAPIClient(serverURL: serverURL)
            let sent = try await client.sendMessage(
                token: token,
                workspaceID: conversation.workspaceID,
                channelID: conversation.channelID,
                body: body
            )
            draft = ""
            messages.append(sent)
            try await client.markRead(token: token, workspaceID: conversation.workspaceID, channelID: conversation.channelID)
        } catch {
            handlePollingOrRefreshError(error)
        }
    }

    func loadMessages() async {
        guard let conversation = selectedConversation else { return }
        do {
            try await loadMessages(for: conversation)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func markRead() async throws {
        guard let token, let conversation = selectedConversation else { return }
        try await markRead(for: conversation.channelID)
    }

    func markRead(for channelID: Int) async throws {
        guard let token,
              let conversation = conversations.first(where: { $0.channelID == channelID })
        else { return }
        let client = try CommunicatorAPIClient(serverURL: serverURL)
        try await client.markRead(token: token, workspaceID: conversation.workspaceID, channelID: conversation.channelID)
        // Immediately zero the unread count locally so the rail/badge update without waiting for the next poll.
        conversations = conversations.map { c in
            guard c.channelID == channelID else { return c }
            var updated = c
            updated.unreadCount = 0
            return updated
        }
        let totalUnread = conversations.reduce(0) { $0 + $1.unreadCount }
        try? await UNUserNotificationCenter.current().setBadgeCount(totalUnread)
    }

    // MARK: - Members & new DMs

    /// Unique workspaces derived from the conversation list, for the new-DM picker.
    var knownWorkspaces: [(id: Int, name: String)] {
        var seen = Set<Int>()
        var result: [(Int, String)] = []
        for c in conversations where !seen.contains(c.workspaceID) {
            seen.insert(c.workspaceID)
            result.append((c.workspaceID, c.workspaceName.isEmpty ? "Workspace \(c.workspaceID)" : c.workspaceName))
        }
        return result.sorted { $0.1.localizedCaseInsensitiveCompare($1.1) == .orderedAscending }
    }

    func loadMembers(workspaceID: Int) async throws -> [CommunicatorMemberProfile] {
        guard let token else { return [] }
        let client = try CommunicatorAPIClient(serverURL: serverURL)
        let members = try await client.listMembers(token: token, workspaceID: workspaceID)
        // Exclude self from the pick list.
        return members.filter { $0.id != currentUserID }
    }

    /// Create (or find) a DM with the given user and return the conversation for it.
    func openDM(workspaceID: Int, userID: Int) async throws -> CommunicatorConversation? {
        try await openDM(workspaceID: workspaceID, userIDs: [userID])
    }

    /// Create (or find) a DM/group-DM with the given users and return the conversation for it.
    func openDM(workspaceID: Int, userIDs: [Int]) async throws -> CommunicatorConversation? {
        guard let token, !userIDs.isEmpty else { return nil }
        let client = try CommunicatorAPIClient(serverURL: serverURL)
        let channel = try await client.createDM(token: token, workspaceID: workspaceID, userIDs: userIDs)
        try await refreshAll()
        return conversations.first(where: { $0.channelID == channel.id })
    }

    func fetchUserProfile(userID: Int) async throws -> CommunicatorMemberProfile? {
        guard let token else { return nil }
        let client = try CommunicatorAPIClient(serverURL: serverURL)
        return try await client.fetchUser(token: token, userID: userID)
    }

    // MARK: - Per-window message loading (Bug 4: isolated per FloatingChatWindowView)

    func loadMessages(for conversationID: Int) async throws -> [CommunicatorMessage] {
        guard let token else { return [] }
        guard let conversation = conversations.first(where: { $0.channelID == conversationID }) else { return [] }
        let client = try CommunicatorAPIClient(serverURL: serverURL)
        let messages = try await client.listMessages(token: token, workspaceID: conversation.workspaceID, channelID: conversation.channelID)
        // Defensively sort ascending: the server should already return
        // send order, but external/bridged messages can carry a backdated
        // timestamp, and trusting raw response order caused messages to
        // render out of order in floating chat windows.
        return messages.sorted { $0.id < $1.id }
    }

    /// Sends a message and returns it so the caller can append it locally
    /// instead of doing a second round-trip reload (which raced against
    /// polling and could make the just-sent message flash in and out).
    @discardableResult
    func sendMessage(to conversationID: Int, body: String) async throws -> CommunicatorMessage? {
        guard let token else { return nil }
        guard let conversation = conversations.first(where: { $0.channelID == conversationID }) else { return nil }
        let client = try CommunicatorAPIClient(serverURL: serverURL)
        let sent = try await client.sendMessage(token: token, workspaceID: conversation.workspaceID, channelID: conversation.channelID, body: body)
        try await client.markRead(token: token, workspaceID: conversation.workspaceID, channelID: conversation.channelID)
        return sent
    }

    var selectedConversation: CommunicatorConversation? {
        guard let selectedConversationID else { return nil }
        return conversations.first(where: { $0.channelID == selectedConversationID })
    }

    private func loadMessages(for conversation: CommunicatorConversation) async throws {
        guard let token else { return }

        let client = try CommunicatorAPIClient(serverURL: serverURL)
        let nextMessages = try await client.listMessages(
            token: token,
            workspaceID: conversation.workspaceID,
            channelID: conversation.channelID
        )
        messages = nextMessages
        try await client.markRead(token: token, workspaceID: conversation.workspaceID, channelID: conversation.channelID)
    }

    private func startPolling() {
        pollingTask?.cancel()
        guard token != nil else { return }

        pollingTask = Task {
            while !Task.isCancelled {
                do {
                    try await refreshAll()
                } catch {
                    handlePollingOrRefreshError(error)
                }
                try? await Task.sleep(nanoseconds: 5_000_000_000)
            }
        }
    }

    private func handlePollingOrRefreshError(_ error: Error) {
        if isExpiredTokenError(error) {
            resetExpiredSession()
            return
        }
        errorMessage = error.localizedDescription
    }

    private func isExpiredTokenError(_ error: Error) -> Bool {
        if case CommunicatorAPIClient.APIError.unauthorized = error {
            return true
        }

        if case let CommunicatorAPIClient.APIError.serverError(status, message) = error {
            if status != 401 {
                return false
            }

            let normalized = message.lowercased()
            return normalized.contains("invalid or expired token")
        }

        return false
    }

    private func resetExpiredSession() {
        pollingTask?.cancel()
        pollingTask = nil
        token = nil
        conversations = []
        groupedConversationKinds = []
        selectedConversationID = nil
        messages = []
        draft = ""
        conversationSnapshotByChannelID = [:]
        hasPrimedNotificationSnapshot = false
        persistConfig()
        errorMessage = "Session expired. Please sign in again."
    }

    private func notifyOnConversationDeltas(_ nextConversations: [CommunicatorConversation]) {
        let nextSnapshot: [Int: ConversationSnapshot] = Dictionary(
            uniqueKeysWithValues: nextConversations.map {
                ($0.channelID, ConversationSnapshot(unreadCount: $0.unreadCount, lastMessageID: $0.lastMessage?.id))
            }
        )

        defer {
            conversationSnapshotByChannelID = nextSnapshot
            hasPrimedNotificationSnapshot = true
        }

        guard hasPrimedNotificationSnapshot else { return }

        for conversation in nextConversations {
            let previous = conversationSnapshotByChannelID[conversation.channelID]

            let didUnreadIncrease = conversation.unreadCount > (previous?.unreadCount ?? 0)
            let previousMessageID = previous?.lastMessageID ?? 0
            let currentMessageID = conversation.lastMessage?.id ?? 0
            let hasNewLastMessage = currentMessageID > previousMessageID
            let isCurrentlyOpen = selectedConversationID == conversation.channelID

            guard didUnreadIncrease || (hasNewLastMessage && !isCurrentlyOpen) else {
                continue
            }

            let preview = conversation.lastMessage?.body.trimmingCharacters(in: .whitespacesAndNewlines)
            let body = (preview?.isEmpty == false)
                ? preview!
                : "New activity in \(conversation.name)."

            NotificationService.postSourceActivity(
                sourceID: source.id,
                sourceName: source.displayName,
                providerName: "Communicator",
                body: body,
                dedupeHint: "native:\(conversation.channelID):\(currentMessageID):\(conversation.unreadCount)"
            )
            MessageSoundPlayer.shared.play()
        }
    }

    private func persistConfig() {
        let config = CommunicatorSourceConfig(
            serverURL: serverURL,
            mobileAccessToken: token
        )
        let encoded = try? JSONEncoder().encode(config)
        onProviderConfigUpdate?(encoded)
    }

    static func computeGroupKinds(from conversations: [CommunicatorConversation]) -> [CommunicatorConversationGroupKind] {
        var kinds: [CommunicatorConversationGroupKind] = []

        func appendUnique(_ kind: CommunicatorConversationGroupKind) {
            if !kinds.contains(kind) {
                kinds.append(kind)
            }
        }

        conversations.forEach { conversation in
            appendUnique(conversation.groupKind)
        }

        let priority: [CommunicatorConversationGroupKind] = [
            .directMessages,
            .channels,
        ]

        kinds.sort { left, right in
            let leftIndex = priority.firstIndex(of: left) ?? Int.max
            let rightIndex = priority.firstIndex(of: right) ?? Int.max

            if leftIndex != rightIndex {
                return leftIndex < rightIndex
            }

            return left.title < right.title
        }

        return kinds
    }
}
