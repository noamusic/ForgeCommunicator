import SwiftUI
import WebKit

extension Notification.Name {
    /// Posted by WebTitleNotificationTracker when a WKWebView source gains unread messages.
    /// userInfo keys: "sourceID" (UUID), "sourceName" (String), "unreadCount" (Int), "body" (String)
    static let forgeWebSourceUnread = Notification.Name("forge.webSourceUnread")
}

final class WebSessionManager {
    private var webViews: [UUID: WKWebView] = [:]
    private var delegates: [UUID: SessionNavigationDelegate] = [:]
    private var notificationTrackers: [UUID: WebTitleNotificationTracker] = [:]
    private let desktopSafariUserAgent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15"

    func webView(for account: Account) -> WKWebView {
        if let existing = webViews[account.id] {
            return existing
        }

        let config = WKWebViewConfiguration()
        config.websiteDataStore = websiteDataStore(for: account)
        config.defaultWebpagePreferences.preferredContentMode = .desktop
        config.defaultWebpagePreferences.allowsContentJavaScript = true
        config.preferences.javaScriptCanOpenWindowsAutomatically = true
        config.preferences.isTextInteractionEnabled = true
        config.allowsAirPlayForMediaPlayback = false
        config.mediaTypesRequiringUserActionForPlayback = []

        if account.type == .telegram {
            let telegramDesktopLayoutScript = WKUserScript(
                source: Self.telegramDesktopLayoutScript,
                injectionTime: .atDocumentEnd,
                forMainFrameOnly: true
            )
            config.userContentController.addUserScript(telegramDesktopLayoutScript)
        }

        let view = WKWebView(frame: .zero, configuration: config)
        view.allowsBackForwardNavigationGestures = true
        view.allowsMagnification = true
        view.customUserAgent = desktopSafariUserAgent
        view.setValue(true, forKey: "drawsBackground")

        if #unavailable(macOS 14.0) {
            let delegate = SessionNavigationDelegate(account: account)
            view.navigationDelegate = delegate
            delegates[account.id] = delegate
            delegate.restoreCookies(to: view)
        }

        notificationTrackers[account.id] = WebTitleNotificationTracker(
            source: account,
            webView: view
        )

        webViews[account.id] = view
        return view
    }

        private static let telegramDesktopLayoutScript = """
        (function() {
            function enforceDesktopWidth() {
                try {
                    var root = document.documentElement;
                    var body = document.body;
                    if (root) {
                        root.style.maxWidth = 'none';
                        root.style.width = '100%';
                    }
                    if (body) {
                        body.style.maxWidth = 'none';
                        body.style.width = '100%';
                        body.style.minWidth = '0';
                    }

                    var selectors = [
                        '#auth-pages',
                        '.auth-pages',
                        '.auth-pages__container',
                        '.page_wrap',
                        '.page-main',
                        '.page-content'
                    ];

                    selectors.forEach(function(sel) {
                        document.querySelectorAll(sel).forEach(function(el) {
                            el.style.maxWidth = 'none';
                            el.style.width = '100%';
                            el.style.minWidth = '0';
                        });
                    });
                } catch (_) {}
            }

            enforceDesktopWidth();
            setTimeout(enforceDesktopWidth, 150);
            setTimeout(enforceDesktopWidth, 500);
            setTimeout(enforceDesktopWidth, 1200);
        })();
        """

    func removeWebsiteData(for account: Account, completion: (() -> Void)? = nil) {
        webViews.removeValue(forKey: account.id)
        delegates.removeValue(forKey: account.id)
        notificationTrackers.removeValue(forKey: account.id)

        if #available(macOS 14.0, *) {
            let dataStore = WKWebsiteDataStore(forIdentifier: account.id)
            dataStore.removeData(
                ofTypes: WKWebsiteDataStore.allWebsiteDataTypes(),
                modifiedSince: Date.distantPast
            ) {
                completion?()
            }
        } else {
            let cookieFile = URL(fileURLWithPath: account.profilePath).appendingPathComponent("cookies.json")
            try? FileManager.default.removeItem(at: cookieFile)
            completion?()
        }
    }

    private func websiteDataStore(for account: Account) -> WKWebsiteDataStore {
        if #available(macOS 14.0, *) {
            // macOS 14+ supports persistent, isolated stores keyed by UUID.
            return WKWebsiteDataStore(forIdentifier: account.id)
        }

        // macOS 13 lacks persistent isolated stores, so use an isolated in-memory store
        // and restore account-specific cookies manually.
        return .nonPersistent()
    }
}

private final class SessionNavigationDelegate: NSObject, WKNavigationDelegate {
    private let account: Account

    init(account: Account) {
        self.account = account
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        persistCookies(from: webView)
    }

    func restoreCookies(to webView: WKWebView) {
        let cookieFile = URL(fileURLWithPath: account.profilePath).appendingPathComponent("cookies.json")
        guard
            let data = try? Data(contentsOf: cookieFile),
            let records = try? JSONDecoder().decode([CodableCookie].self, from: data)
        else {
            return
        }

        let cookieStore = webView.configuration.websiteDataStore.httpCookieStore
        records
            .compactMap { $0.httpCookie }
            .forEach { cookieStore.setCookie($0) }
    }

    private func persistCookies(from webView: WKWebView) {
        let cookieStore = webView.configuration.websiteDataStore.httpCookieStore
        cookieStore.getAllCookies { cookies in
            let records = cookies.map(CodableCookie.init(cookie:))
            let fileURL = URL(fileURLWithPath: self.account.profilePath).appendingPathComponent("cookies.json")

            do {
                let data = try JSONEncoder().encode(records)
                try data.write(to: fileURL, options: .atomic)
            } catch {
                print("Failed to persist cookies for \(self.account.displayName): \(error)")
            }
        }
    }
}

private struct CodableCookie: Codable {
    let properties: [String: String]

    init(cookie: HTTPCookie) {
        var values: [String: String] = [:]
        values[HTTPCookiePropertyKey.name.rawValue] = cookie.name
        values[HTTPCookiePropertyKey.value.rawValue] = cookie.value
        values[HTTPCookiePropertyKey.domain.rawValue] = cookie.domain
        values[HTTPCookiePropertyKey.path.rawValue] = cookie.path
        if let expires = cookie.expiresDate {
            values[HTTPCookiePropertyKey.expires.rawValue] = ISO8601DateFormatter().string(from: expires)
        }
        values[HTTPCookiePropertyKey.secure.rawValue] = cookie.isSecure ? "TRUE" : "FALSE"
        properties = values
    }

    var httpCookie: HTTPCookie? {
        var casted: [HTTPCookiePropertyKey: Any] = [:]
        let formatter = ISO8601DateFormatter()

        for (key, value) in properties {
            let propertyKey = HTTPCookiePropertyKey(rawValue: key)

            if propertyKey == .expires, let date = formatter.date(from: value) {
                casted[propertyKey] = date
            } else if propertyKey == .secure {
                casted[propertyKey] = (value.uppercased() == "TRUE")
            } else {
                casted[propertyKey] = value
            }
        }

        return HTTPCookie(properties: casted)
    }
}



struct AccountWebContainerView: NSViewRepresentable {
    let webView: WKWebView

    func makeNSView(context: Context) -> WebViewHostContainer {
        let container = WebViewHostContainer()
        container.host(webView)
        return container
    }

    func updateNSView(_ nsView: WebViewHostContainer, context: Context) {
        nsView.host(webView)
    }
}

final class WebViewHostContainer: NSView {
    private weak var hostedView: WKWebView?

    func host(_ webView: WKWebView) {
        guard hostedView !== webView else { return }

        hostedView?.removeFromSuperview()
        hostedView = webView

        webView.translatesAutoresizingMaskIntoConstraints = false
        addSubview(webView)

        NSLayoutConstraint.activate([
            webView.leadingAnchor.constraint(equalTo: leadingAnchor),
            webView.trailingAnchor.constraint(equalTo: trailingAnchor),
            webView.topAnchor.constraint(equalTo: topAnchor),
            webView.bottomAnchor.constraint(equalTo: bottomAnchor)
        ])
    }
}

private final class WebTitleNotificationTracker {
    private let source: Source
    private weak var webView: WKWebView?
    private var titleObservation: NSKeyValueObservation?
    private var urlObservation: NSKeyValueObservation?
    private var lastUnreadCount = 0
    private var lastDeliveredTitle = ""
    private var lastDeliveryDate = Date.distantPast

    init(source: Source, webView: WKWebView) {
        self.source = source
        self.webView = webView

        titleObservation = webView.observe(\.title, options: [.new]) { [weak self] view, _ in
            self?.handleChange(title: view.title ?? "", url: view.url)
        }

        urlObservation = webView.observe(\.url, options: [.new]) { [weak self] view, _ in
            self?.handleChange(title: view.title ?? "", url: view.url)
        }
    }

    private func handleChange(title: String, url: URL?) {
        let unread = unreadCount(from: title)

        if unread == 0 {
            // If we previously had unread messages, post a clear so the rail badge resets.
            if lastUnreadCount > 0 {
                lastUnreadCount = 0
                DispatchQueue.main.async {
                    NotificationCenter.default.post(
                        name: .forgeWebSourceUnread,
                        object: nil,
                        userInfo: [
                            "sourceID": self.source.id,
                            "sourceName": self.source.displayName,
                            "unreadCount": 0,
                            "body": ""
                        ]
                    )
                }
            }
            return
        }

        let now = Date()
        let didIncrease = unread > lastUnreadCount
        let didChangeTitle = title != lastDeliveredTitle
        let cooldownElapsed = now.timeIntervalSince(lastDeliveryDate) > 30

        guard didIncrease || (didChangeTitle && cooldownElapsed) else {
            lastUnreadCount = unread
            return
        }

        lastUnreadCount = unread
        lastDeliveredTitle = title
        lastDeliveryDate = now

        let sourceLabel = source.displayName
        let providerLabel = source.type.displayLabel
        let body = normalizedBody(from: title, host: url?.host)

        NotificationService.postSourceActivity(
            sourceID: source.id,
            sourceName: sourceLabel,
            providerName: providerLabel,
            body: body,
            dedupeHint: "title:\(unread):\(body)"
        )

        DispatchQueue.main.async {
            MessageSoundPlayer.shared.play()
            NotificationCenter.default.post(
                name: .forgeWebSourceUnread,
                object: nil,
                userInfo: [
                    "sourceID": self.source.id,
                    "sourceName": sourceLabel,
                    "unreadCount": unread,
                    "body": body
                ]
            )
        }
    }

    private func unreadCount(from title: String) -> Int {
        guard let regex = try? NSRegularExpression(pattern: "^\\((\\d+)\\)", options: []) else {
            return 0
        }

        let range = NSRange(title.startIndex..<title.endIndex, in: title)
        guard
            let match = regex.firstMatch(in: title, options: [], range: range),
            let countRange = Range(match.range(at: 1), in: title),
            let count = Int(title[countRange])
        else {
            return 0
        }

        return count
    }

    private func normalizedBody(from title: String, host: String?) -> String {
        let cleaned = title.replacingOccurrences(
            of: "^\\(\\d+\\)\\s*",
            with: "",
            options: .regularExpression
        )

        if cleaned.isEmpty {
            if let host {
                return "New message activity on \(host)."
            }
            return "New message activity detected."
        }

        return cleaned
    }
}
