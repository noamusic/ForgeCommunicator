import Foundation

// Preserves the HTTP method (POST, PATCH, etc.) when URLSession follows a
// redirect. Without this, URLSession converts POST to GET on 301/302 responses
// which causes a 405 from the server.
private final class RedirectPreservingDelegate: NSObject, URLSessionTaskDelegate {
    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        willPerformHTTPRedirection response: HTTPURLResponse,
        newRequest request: URLRequest,
        completionHandler: @escaping (URLRequest?) -> Void
    ) {
        guard let original = task.originalRequest else {
            completionHandler(request)
            return
        }

        // Preserve POST/PUT/PATCH bodies only for redirects that keep the same endpoint
        // semantics (e.g., http -> https on the same path) or explicit 307/308 redirects.
        let statusCode = response.statusCode
        let shouldPreserve: Bool
        if statusCode == 307 || statusCode == 308 {
            shouldPreserve = true
        } else if statusCode == 301 || statusCode == 302 {
            let originalPath = original.url?.path ?? ""
            let redirectedPath = request.url?.path ?? ""
            shouldPreserve = !originalPath.isEmpty && originalPath == redirectedPath
        } else {
            shouldPreserve = false
        }

        guard shouldPreserve else {
            completionHandler(request)
            return
        }

        var preserved = request
        preserved.httpMethod = original.httpMethod
        if original.httpMethod != "GET", original.httpMethod != "HEAD" {
            preserved.httpBody = original.httpBody
            if let ct = original.value(forHTTPHeaderField: "Content-Type") {
                preserved.setValue(ct, forHTTPHeaderField: "Content-Type")
            }
        }
        completionHandler(preserved)
    }
}

struct CommunicatorAPIClient {
    enum APIError: LocalizedError {
        case invalidServerURL
        case invalidResponse
        case unauthorized
        case serverError(status: Int, message: String)

        var errorDescription: String? {
            switch self {
            case .invalidServerURL:
                return "Invalid server URL."
            case .invalidResponse:
                return "Invalid server response."
            case .unauthorized:
                return "Authentication failed."
            case let .serverError(status, message):
                return "Server error (\(status)): \(message)"
            }
        }
    }

    private let baseURL: URL
    private let session: URLSession
    private let decoder: JSONDecoder
    private let encoder: JSONEncoder

    init(serverURL: String) throws {
        var value = serverURL.trimmingCharacters(in: .whitespacesAndNewlines)
        if !value.hasPrefix("http://") && !value.hasPrefix("https://") {
            value = "https://\(value)"
        }

        guard var components = URLComponents(string: value),
              let scheme = components.scheme,
              let host = components.host
        else {
            throw APIError.invalidServerURL
        }

        // Normalize to origin only so API paths are always rooted correctly.
        components.scheme = scheme
        components.host = host
        components.path = ""
        components.query = nil
        components.fragment = nil

        guard let url = components.url else {
            throw APIError.invalidServerURL
        }

        self.baseURL = url
        self.session = URLSession(
            configuration: .default,
            delegate: RedirectPreservingDelegate(),
            delegateQueue: nil
        )

        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        self.decoder = decoder

        self.encoder = JSONEncoder()
    }

    func login(email: String, password: String) async throws -> CommunicatorAuthResponse {
        var request = URLRequest(url: endpoint("/mobile/v1/auth/login"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")

        let body: [String: String] = [
            "email": email,
            "password": password,
            "device_name": "ForgeCommunicator-macOS"
        ]
        request.httpBody = try JSONSerialization.data(withJSONObject: body)

        return try await perform(request, as: CommunicatorAuthResponse.self)
    }

    func listConversations(token: String, includeChannels: Bool = true) async throws -> [CommunicatorConversation] {
        var components = URLComponents(url: endpoint("/mobile/v1/conversations"), resolvingAgainstBaseURL: false)
        if includeChannels {
            components?.queryItems = [URLQueryItem(name: "include_channels", value: "true")]
        }

        var request = URLRequest(url: components?.url ?? endpoint("/mobile/v1/conversations"))
        request.httpMethod = "GET"
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")

        return try await perform(request, as: [CommunicatorConversation].self)
    }

    func listMessages(token: String, workspaceID: Int, channelID: Int) async throws -> [CommunicatorMessage] {
        var request = URLRequest(url: endpoint("/mobile/v1/workspaces/\(workspaceID)/channels/\(channelID)/messages"))
        request.httpMethod = "GET"
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")

        return try await perform(request, as: [CommunicatorMessage].self)
    }

    func sendMessage(token: String, workspaceID: Int, channelID: Int, body: String) async throws -> CommunicatorMessage {
        var request = URLRequest(url: endpoint("/mobile/v1/workspaces/\(workspaceID)/channels/\(channelID)/messages"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.httpBody = try encoder.encode(CommunicatorSendMessageRequest(body: body))

        return try await perform(request, as: CommunicatorMessage.self)
    }

    func markRead(token: String, workspaceID: Int, channelID: Int) async throws {
        var request = URLRequest(url: endpoint("/mobile/v1/workspaces/\(workspaceID)/channels/\(channelID)/read"))
        request.httpMethod = "POST"
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")

        _ = try await performRaw(request)
    }

    func listMembers(token: String, workspaceID: Int) async throws -> [CommunicatorMemberProfile] {
        var request = URLRequest(url: endpoint("/mobile/v1/workspaces/\(workspaceID)/members"))
        request.httpMethod = "GET"
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")

        return try await perform(request, as: [CommunicatorMemberProfile].self)
    }

    func createDM(token: String, workspaceID: Int, userIDs: [Int]) async throws -> CommunicatorChannelSummary {
        var components = URLComponents(url: endpoint("/mobile/v1/workspaces/\(workspaceID)/dm"), resolvingAgainstBaseURL: false)
        components?.queryItems = userIDs.map { URLQueryItem(name: "user_ids", value: String($0)) }

        var request = URLRequest(url: components?.url ?? endpoint("/mobile/v1/workspaces/\(workspaceID)/dm"))
        request.httpMethod = "POST"
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")

        return try await perform(request, as: CommunicatorChannelSummary.self)
    }

    func fetchUser(token: String, userID: Int) async throws -> CommunicatorMemberProfile {
        var request = URLRequest(url: endpoint("/mobile/v1/users/\(userID)"))
        request.httpMethod = "GET"
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")

        return try await perform(request, as: CommunicatorMemberProfile.self)
    }

    func oauthStart(provider: String) async throws -> CommunicatorOAuthStartResponse {
        var request = URLRequest(url: endpoint("/mobile/v1/auth/oauth/\(provider)/start"))
        request.httpMethod = "GET"
        return try await perform(request, as: CommunicatorOAuthStartResponse.self)
    }

    func fetchMyProfile(token: String) async throws -> CommunicatorAuthUser {
        var request = URLRequest(url: endpoint("/mobile/v1/me"))
        request.httpMethod = "GET"
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        return try await perform(request, as: CommunicatorAuthUser.self)
    }

    private func endpoint(_ path: String) -> URL {
        let cleaned = path.hasPrefix("/") ? String(path.dropFirst()) : path
        return baseURL.appending(path: cleaned)
    }

    private func performRaw(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        var req = request
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        let (data, response) = try await session.data(for: req)
        guard let http = response as? HTTPURLResponse else {
            throw APIError.invalidResponse
        }

        switch http.statusCode {
        case 200 ... 299:
            return (data, http)
        case 401:
            // Show the server's actual detail (e.g. "Invalid email or password")
            // rather than the generic "Authentication failed."
            if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let detail = json["detail"] as? String {
                throw APIError.serverError(status: 401, message: detail)
            }
            throw APIError.unauthorized
        default:
            // Try to extract a human-readable message from a JSON error body.
            let message: String
            if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
               let detail = json["detail"] as? String {
                message = detail
            } else {
                message = String(data: data, encoding: .utf8) ?? "Unknown error"
            }
            throw APIError.serverError(status: http.statusCode, message: message)
        }
    }

    private func perform<T: Decodable>(_ request: URLRequest, as type: T.Type) async throws -> T {
        let (data, _) = try await performRaw(request)
        return try decoder.decode(type, from: data)
    }
}
