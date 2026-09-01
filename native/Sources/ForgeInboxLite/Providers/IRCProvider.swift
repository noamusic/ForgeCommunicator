import SwiftUI
import WebKit

/// IRC source backed by Libera.Chat's hosted web client (Kiwi-based).
/// Sessions (nick, joined channels) persist in the per-source WKWebView store.
struct IRCProvider: SourceProvider {
    let type: SourceType = .irc
    let displayName: String = "IRC"

    private let sessionManager: WebSessionManager

    init(sessionManager: WebSessionManager) {
        self.sessionManager = sessionManager
    }

    func makeMainView(for source: Source, onProviderConfigUpdate: ((Data?) -> Void)? = nil) -> AnyView {
        let webView = sessionManager.webView(for: source)
        if webView.url == nil {
            webView.load(URLRequest(url: URL(string: "https://web.libera.chat/")!))
        }

        return AnyView(AccountWebContainerView(webView: webView))
    }
}
