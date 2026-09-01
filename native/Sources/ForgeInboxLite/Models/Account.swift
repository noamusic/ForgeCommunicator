import Foundation

enum SourceType: String, Codable, CaseIterable, Identifiable {
    case communicator
    case whatsapp
    case signal
    case telegram
    case irc

    var id: String { rawValue }

    var displayLabel: String {
        switch self {
        case .communicator:
            return "Communicator"
        case .whatsapp:
            return "WhatsApp"
        case .signal:
            return "Signal"
        case .telegram:
            return "Telegram"
        case .irc:
            return "IRC"
        }
    }
}

struct Source: Identifiable, Codable, Equatable {
    let id: UUID
    var type: SourceType
    var displayName: String
    var profilePath: String
    var createdAt: Date
    var lastOpenedAt: Date?
    var providerConfig: Data? = nil
    var authReference: String? = nil
    var sortOrder: Int? = nil

    var sourceType: SourceType {
        get { type }
        set { type = newValue }
    }
}

struct PersistedSourceConfig: Codable {
    var schemaVersion: Int
    var sources: [Source]
    var selectedSourceID: UUID?

    private enum CodingKeys: String, CodingKey {
        case schemaVersion
        case sources
        case selectedSourceID
        case accounts
        case selectedAccountID
    }

    init(schemaVersion: Int, sources: [Source], selectedSourceID: UUID?) {
        self.schemaVersion = schemaVersion
        self.sources = sources
        self.selectedSourceID = selectedSourceID
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        schemaVersion = try container.decodeIfPresent(Int.self, forKey: .schemaVersion) ?? 1

        if let decodedSources = try container.decodeIfPresent([Source].self, forKey: .sources) {
            sources = decodedSources
            selectedSourceID = try container.decodeIfPresent(UUID.self, forKey: .selectedSourceID)
        } else {
            sources = try container.decodeIfPresent([Source].self, forKey: .accounts) ?? []
            selectedSourceID = try container.decodeIfPresent(UUID.self, forKey: .selectedAccountID)
        }

        if schemaVersion < 2 {
            schemaVersion = 2
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(schemaVersion, forKey: .schemaVersion)
        try container.encode(sources, forKey: .sources)
        try container.encodeIfPresent(selectedSourceID, forKey: .selectedSourceID)
    }

    var accounts: [Source] {
        get { sources }
        set { sources = newValue }
    }

    var selectedAccountID: UUID? {
        get { selectedSourceID }
        set { selectedSourceID = newValue }
    }

    static let empty = PersistedSourceConfig(schemaVersion: 2, sources: [], selectedSourceID: nil)
}

// Backward-compatible aliases while the view/store layers are being migrated.
typealias AccountType = SourceType
typealias Account = Source
typealias PersistedConfig = PersistedSourceConfig
