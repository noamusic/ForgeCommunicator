import Foundation
import UserNotifications

enum NotificationService {
    private static let deduper = NotificationDeduper()

    static func configure() {
        UNUserNotificationCenter.current().delegate = NotificationCenterDelegate.shared
        checkAndLogAuthorizationStatus()
    }

    static func checkAndLogAuthorizationStatus() {
        UNUserNotificationCenter.current().getNotificationSettings { settings in
            let status: String
            switch settings.authorizationStatus {
            case .authorized:    status = "authorized"
            case .denied:        status = "DENIED — open System Settings > Notifications to re-enable"
            case .notDetermined: status = "not yet requested"
            case .provisional:   status = "provisional"
            case .ephemeral:     status = "ephemeral"
            @unknown default:    status = "unknown(\(settings.authorizationStatus.rawValue))"
            }
            print("[NotificationService] Auth status: \(status) | alert=\(settings.alertSetting.rawValue) sound=\(settings.soundSetting.rawValue) badge=\(settings.badgeSetting.rawValue)")
        }
    }

    static func requestAuthorization() {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge]) { granted, error in
            if let error {
                print("[NotificationService] Authorization request failed: \(error)")
            } else if granted {
                print("[NotificationService] Notifications authorized")
            } else {
                print("[NotificationService] Permission DENIED — open System Settings > Notifications to enable")
            }
        }
    }

    static func post(title: String, body: String, sound: UNNotificationSound = .default) {
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = body
        content.sound = sound
        content.threadIdentifier = "forge-activity"

        let request = UNNotificationRequest(
            identifier: UUID().uuidString,
            content: content,
            trigger: nil
        )

        UNUserNotificationCenter.current().add(request) { error in
            if let error {
                print("[NotificationService] Failed to post '\(title)': \(error)")
            } else {
                print("[NotificationService] Posted: \(title) — \(body.prefix(80))")
            }
        }
    }

    static func postSourceActivity(
        sourceID: UUID,
        sourceName: String,
        providerName: String,
        body: String,
        dedupeHint: String,
        minimumInterval: TimeInterval = 20,
        sound: UNNotificationSound = .default
    ) {
        let normalizedBody = body.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !normalizedBody.isEmpty else { return }

        let normalizedProvider = providerName.lowercased()
        let normalizedHint = dedupeHint.lowercased()
        let normalizedBodyKey = normalizedBody.lowercased()
        let key = "\(sourceID.uuidString)|\(normalizedProvider)|\(normalizedBodyKey)|\(normalizedHint)"
        let crossEmitterKey = "\(sourceID.uuidString)|\(normalizedProvider)|\(normalizedBodyKey)"
        Task {
            // Check both keys atomically — avoids recording the specific key's timestamp
            // when the cross-emitter check would block delivery.
            guard await deduper.shouldDeliver(key: key, crossEmitterKey: crossEmitterKey, minimumInterval: minimumInterval) else { return }

            await MainActor.run {
                checkAndLogAuthorizationStatus()
                post(
                    title: "\(sourceName) • \(providerName)",
                    body: normalizedBody,
                    sound: sound
                )
            }
        }
    }
}

private actor NotificationDeduper {
    private var lastDeliveryByKey: [String: Date] = [:]

    /// Atomically checks both the specific key and the cross-emitter key.
    /// Only records timestamps if both checks pass, preventing the specific key
    /// from being poisoned when the cross-emitter check would block delivery.
    func shouldDeliver(key: String, crossEmitterKey: String, minimumInterval: TimeInterval) -> Bool {
        let now = Date()
        if let previous = lastDeliveryByKey[key], now.timeIntervalSince(previous) < minimumInterval {
            return false
        }
        if let previous = lastDeliveryByKey[crossEmitterKey], now.timeIntervalSince(previous) < minimumInterval {
            return false
        }
        lastDeliveryByKey[key] = now
        lastDeliveryByKey[crossEmitterKey] = now
        return true
    }
}

private final class NotificationCenterDelegate: NSObject, UNUserNotificationCenterDelegate {
    static let shared = NotificationCenterDelegate()

    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification,
        withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void
    ) {
        // Show banner + sound while the app is in the foreground; skip badge increment
        // since the dock badge is managed by applicationDidBecomeActive.
        completionHandler([.banner, .list, .sound])
    }

    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse,
        withCompletionHandler completionHandler: @escaping () -> Void
    ) {
        // Clear the dock badge when the user taps a notification.
        center.setBadgeCount(0) { _ in }
        completionHandler()
    }
}
