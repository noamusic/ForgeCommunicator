import CryptoKit
import Foundation
import Security

enum ConfigStoreError: Error {
    case keychainFailure(OSStatus)
}

/// Tries the macOS Keychain first; falls back to a file-based key when the app
/// runs without a signed bundle (e.g. `swift run` during development).
final class KeychainKeyStore {
    private let service = "com.forgeinbox.lite.config"
    private let account = "aes-gcm-key"
    private let legacyFolderName = "ForgeInboxLite"
    private let currentFolderName = "ForgeCommunicator"
    private let fileManager = FileManager.default

    func fetchOrCreateKeyData() throws -> Data {
        // In unsigned/local builds, prefer file-backed key storage to avoid
        // repetitive keychain authentication prompts for each rebuilt binary.
        if !shouldUseKeychain {
            if let existing = fileFetch() {
                return existing
            }

            let newKeyData = Data((0..<32).map { _ in UInt8.random(in: 0...255) })
            try fileSave(newKeyData)
            return newKeyData
        }

        // Try Keychain
        if let existing = keychainFetch() {
            return existing
        }

        // Fallback to file key if present (for unsigned local runs).
        if let existing = fileFetch() {
            return existing
        }

        let newKeyData = Data((0..<32).map { _ in UInt8.random(in: 0...255) })

        // Persist: prefer Keychain, fall back to a protected file
        if !keychainSave(newKeyData) {
            try fileSave(newKeyData)
        }

        return newKeyData
    }

    private var shouldUseKeychain: Bool {
        let bundleURL = Bundle.main.bundleURL
        let signatureFile = bundleURL
            .appendingPathComponent("Contents", isDirectory: true)
            .appendingPathComponent("_CodeSignature", isDirectory: true)
            .appendingPathComponent("CodeResources", isDirectory: false)
        return fileManager.fileExists(atPath: signatureFile.path)
    }

    // MARK: - Keychain (best-effort)

    private func keychainFetch() -> Data? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne
        ]
        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        guard status == errSecSuccess else { return nil }
        return item as? Data
    }

    @discardableResult
    private func keychainSave(_ data: Data) -> Bool {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecValueData as String: data,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlock
        ]
        return SecItemAdd(query as CFDictionary, nil) == errSecSuccess
    }

    // MARK: - File fallback (dev / unsigned binary)

    private func fileFetch() -> Data? {
        guard let url = try? fileKeyURL() else { return nil }
        return try? Data(contentsOf: url)
    }

    private func fileSave(_ data: Data) throws {
        let url = try fileKeyURL()
        try data.write(to: url, options: .atomic)
        // Restrict permissions to owner-only
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
    }

    private func fileKeyURL() throws -> URL {
        guard let appSupport = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first else {
            throw CocoaError(.fileNoSuchFile)
        }
        let currentDir = appSupport.appendingPathComponent(currentFolderName, isDirectory: true)
        if !FileManager.default.fileExists(atPath: currentDir.path) {
            let legacyDir = appSupport.appendingPathComponent(legacyFolderName, isDirectory: true)
            if FileManager.default.fileExists(atPath: legacyDir.path) {
                try? FileManager.default.moveItem(at: legacyDir, to: currentDir)
            }
        }

        let dir = currentDir
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir.appendingPathComponent(".key")
    }
}

actor EncryptedConfigStore {
    private let fileManager = FileManager.default
    private let keyStore = KeychainKeyStore()
    private let encoder = JSONEncoder()
    private let decoder = JSONDecoder()
    private let legacyFolderName = "ForgeInboxLite"
    private let currentFolderName = "ForgeCommunicator"

    init() {
        encoder.dateEncodingStrategy = .iso8601
        decoder.dateDecodingStrategy = .iso8601
    }

    func load() async throws -> PersistedConfig {
        let fileURL = try configFileURL()
        guard fileManager.fileExists(atPath: fileURL.path) else {
            return .empty
        }

        let encryptedBlob = try Data(contentsOf: fileURL)
        let keyData = try keyStore.fetchOrCreateKeyData()
        let key = SymmetricKey(data: keyData)
        let sealedBox = try AES.GCM.SealedBox(combined: encryptedBlob)
        let plaintext = try AES.GCM.open(sealedBox, using: key)
        let config = try decoder.decode(PersistedConfig.self, from: plaintext)

        // One-time migration rewrite from legacy key shape.
        if let json = String(data: plaintext, encoding: .utf8),
           json.contains("\"accounts\"") || json.contains("\"selectedAccountID\"") {
            try await save(config)
        }

        return config
    }

    func save(_ config: PersistedConfig) async throws {
        let fileURL = try configFileURL()
        let plainData = try encoder.encode(config)
        let keyData = try keyStore.fetchOrCreateKeyData()
        let key = SymmetricKey(data: keyData)
        let sealed = try AES.GCM.seal(plainData, using: key)

        guard let combined = sealed.combined else {
            throw CocoaError(.coderInvalidValue)
        }

        try combined.write(to: fileURL, options: .atomic)
    }

    func profilesRootURL() throws -> URL {
        let base = try applicationSupportRootURL()
        let profiles = base.appendingPathComponent("profiles", isDirectory: true)
        try fileManager.createDirectory(at: profiles, withIntermediateDirectories: true)
        return profiles
    }

    private func configFileURL() throws -> URL {
        let root = try applicationSupportRootURL()
        return root.appendingPathComponent("config.enc")
    }

    private func applicationSupportRootURL() throws -> URL {
        guard let appSupport = fileManager.urls(for: .applicationSupportDirectory, in: .userDomainMask).first else {
            throw CocoaError(.fileNoSuchFile)
        }

        let root = appSupport.appendingPathComponent(currentFolderName, isDirectory: true)
        if !fileManager.fileExists(atPath: root.path) {
            let legacy = appSupport.appendingPathComponent(legacyFolderName, isDirectory: true)
            if fileManager.fileExists(atPath: legacy.path) {
                try? fileManager.moveItem(at: legacy, to: root)
            }
        }

        try fileManager.createDirectory(at: root, withIntermediateDirectories: true)
        return root
    }
}
