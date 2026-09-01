import Foundation

struct CommunicatorSourceConfig: Codable, Equatable {
    var serverURL: String
    var mobileAccessToken: String? = nil

    static let `default` = CommunicatorSourceConfig(serverURL: "https://comms.buildly.io")
}

extension Source {
    func communicatorConfig() -> CommunicatorSourceConfig {
        guard let providerConfig,
              let decoded = try? JSONDecoder().decode(CommunicatorSourceConfig.self, from: providerConfig)
        else {
            return .default
        }
        return decoded
    }

    func withCommunicatorConfig(_ config: CommunicatorSourceConfig) -> Source {
        var copy = self
        copy.providerConfig = try? JSONEncoder().encode(config)
        return copy
    }
}
