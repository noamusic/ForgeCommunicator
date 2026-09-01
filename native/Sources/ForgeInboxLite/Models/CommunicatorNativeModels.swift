import Foundation

struct CommunicatorAuthUser: Codable, Equatable {
    let id: Int
    let email: String
    let displayName: String

    private enum CodingKeys: String, CodingKey {
        case id
        case email
        case displayName = "display_name"
    }
}

struct CommunicatorUserProfile: Codable, Equatable, Identifiable {
    let id: Int
    let displayName: String
    let avatarURL: String?

    private enum CodingKeys: String, CodingKey {
        case id
        case displayName = "display_name"
        case avatarURL = "avatar_url"
    }
}

struct CommunicatorAuthResponse: Codable, Equatable {
    let token: String
    let user: CommunicatorAuthUser
}

struct CommunicatorOAuthStartResponse: Codable {
    let authURL: String
    let state: String

    private enum CodingKeys: String, CodingKey {
        case authURL = "auth_url"
        case state
    }
}

struct CommunicatorConversationAuthor: Codable, Equatable {
    let id: Int
    let displayName: String
    let avatarURL: String?

    private enum CodingKeys: String, CodingKey {
        case id
        case displayName = "display_name"
        case avatarURL = "avatar_url"
    }
}

struct CommunicatorConversationMessage: Codable, Equatable {
    let id: Int
    let body: String
    let createdAt: Date
    let author: CommunicatorConversationAuthor?

    private enum CodingKeys: String, CodingKey {
        case id
        case body
        case createdAt = "created_at"
        case author
    }
}

struct CommunicatorConversation: Codable, Identifiable, Equatable {
    let channelID: Int
    let workspaceID: Int
    let workspaceName: String
    let name: String
    let isDM: Bool
    var unreadCount: Int
    let bridgedPlatform: String?
    let lastMessage: CommunicatorConversationMessage?
    let members: [CommunicatorUserProfile]

    var id: Int { channelID }

    private enum CodingKeys: String, CodingKey {
        case channelID = "channel_id"
        case workspaceID = "workspace_id"
        case workspaceName = "workspace_name"
        case name
        case isDM = "is_dm"
        case unreadCount = "unread_count"
        case bridgedPlatform = "bridged_platform"
        case lastMessage = "last_message"
        case members
    }

    var groupKind: CommunicatorConversationGroupKind {
        if isDM && bridgedPlatform == nil {
            return .directMessages
        }

        if let bridgedPlatform {
            return .bridged(platform: bridgedPlatform.lowercased())
        }

        return .channels
    }
}

enum CommunicatorConversationGroupKind: Equatable, Hashable {
    case directMessages
    case channels
    case bridged(platform: String)

    var title: String {
        switch self {
        case .directMessages:
            return "DMs"
        case .channels:
            return "Channels"
        case let .bridged(platform):
            return platform.capitalized
        }
    }

    var systemImage: String {
        switch self {
        case .directMessages:
            return "person.2.fill"
        case .channels:
            return "number"
        case let .bridged(platform):
            return platform == "slack" ? "message.fill" : "bubble.left"
        }
    }
}

struct CommunicatorMessageAuthor: Codable, Equatable {
    let id: Int
    let displayName: String
    let avatarURL: String?

    private enum CodingKeys: String, CodingKey {
        case id
        case displayName = "display_name"
        case avatarURL = "avatar_url"
    }
}

struct CommunicatorMessage: Codable, Identifiable, Equatable {
    let id: Int
    let channelID: Int
    let userID: Int?
    let body: String
    let parentID: Int?
    let createdAt: Date
    let editedAt: Date?
    let isEdited: Bool
    let author: CommunicatorMessageAuthor?

    private enum CodingKeys: String, CodingKey {
        case id
        case channelID = "channel_id"
        case userID = "user_id"
        case body
        case parentID = "parent_id"
        case createdAt = "created_at"
        case editedAt = "edited_at"
        case isEdited = "is_edited"
        case author
    }
}

struct CommunicatorSendMessageRequest: Codable {
    let body: String
}

/// Full member/user profile from /workspaces/{id}/members and /users/{id}.
struct CommunicatorMemberProfile: Codable, Equatable, Identifiable {
    let id: Int
    let email: String
    let displayName: String
    let bio: String?
    let title: String?
    let avatarURL: String?
    let status: String?
    let statusMessage: String?

    private enum CodingKeys: String, CodingKey {
        case id
        case email
        case displayName = "display_name"
        case bio
        case title
        case avatarURL = "avatar_url"
        case status
        case statusMessage = "status_message"
    }
}

/// Minimal channel payload returned by POST /workspaces/{id}/dm.
struct CommunicatorChannelSummary: Codable, Equatable {
    let id: Int
    let workspaceID: Int
    let name: String
    let displayName: String
    let isDM: Bool

    private enum CodingKeys: String, CodingKey {
        case id
        case workspaceID = "workspace_id"
        case name
        case displayName = "display_name"
        case isDM = "is_dm"
    }
}
