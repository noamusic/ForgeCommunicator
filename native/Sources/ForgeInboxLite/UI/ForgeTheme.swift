import SwiftUI

enum ForgeTheme {
    // Primary palette (from design system screenshot)
    static let primary   = Color(hex: "#4DB6FF")   // Signal Blue
    static let amber     = Color(hex: "#FFC857")   // Forge Amber
    static let violet    = Color(hex: "#7C5CFF")   // System Violet
    static let green     = Color(hex: "#31C48D")   // Build Green
    static let coral     = Color(hex: "#FF6B6B")   // Alert Coral
    static let white     = Color(hex: "#FAF7F8")   // Soft White
    static let silver    = Color(hex: "#E6EAF1")

    // Semantic aliases
    static let accent    = amber
    static let cyan      = primary

    // Dark scale
    static let dark950   = Color(hex: "#080F17")   // Forge Black
    static let dark900   = Color(hex: "#121826")   // Graphite
    static let dark800   = Color(hex: "#1A2233")   // Carbon
    static let dark700   = Color(hex: "#2D3A50")
    static let dark600   = Color(hex: "#34445F")

    // Glass / surface tokens
    static let glassBorder       = silver.opacity(0.10)
    static let glassBorderActive = primary.opacity(0.34)
    static let glassFill         = dark800.opacity(0.72)
    static let overlayFill       = dark950.opacity(0.82)

    // Gradients
    static let brandGradient = LinearGradient(
        colors: [primary, Color(hex: "#1C56B8")],
        startPoint: .topLeading, endPoint: .bottomTrailing
    )
    static let shellGradient = LinearGradient(
        colors: [dark950, dark900, dark800],
        startPoint: .topLeading, endPoint: .bottomTrailing
    )
    static let deviceGradient = LinearGradient(
        colors: [Color(hex: "#493D3C"), dark950, dark800],
        startPoint: .topLeading, endPoint: .bottomTrailing
    )

    // Typography
    static func headingFont(size: CGFloat, weight: Font.Weight = .semibold) -> Font {
        .custom("SpaceGrotesk-SemiBold", size: size).weight(weight)
    }
    static func brandFont(size: CGFloat, weight: Font.Weight = .bold) -> Font {
        .custom("Orbitron", size: size).weight(weight)
    }
    static func monoFont(size: CGFloat) -> Font {
        .custom("JetBrainsMono-Regular", size: size)
    }

    // Badge colors
    static let badgeUnread    = primary
    static let badgeMention   = violet
    static let badgeDecision  = amber
    static let badgeBlocker   = coral
    static let badgeBuild     = green

    // Status colors
    static let statusOnline   = green
    static let statusAway     = amber
    static let statusDND      = coral
    static let statusFocus    = violet
    static let statusOffline  = Color(hex: "#475569")
}

// Convenience hex color initializer
extension Color {
    init(hex: String) {
        let h = hex.trimmingCharacters(in: CharacterSet.alphanumerics.inverted)
        var int: UInt64 = 0
        Scanner(string: h).scanHexInt64(&int)
        let r = Double((int >> 16) & 0xFF) / 255
        let g = Double((int >> 8) & 0xFF) / 255
        let b = Double(int & 0xFF) / 255
        self.init(red: r, green: g, blue: b)
    }
}
