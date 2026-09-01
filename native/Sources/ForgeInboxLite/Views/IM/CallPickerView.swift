import SwiftUI

/// Compact popover that lets the user choose between Jitsi and FaceTime calls.
struct CallPickerView: View {
    let conversation: CommunicatorConversation
    let onStartJitsi: () -> Void
    let onStartFaceTime: (() -> Void)?   // nil for channels (FaceTime is DM-only)

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Start a call")
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(ForgeTheme.silver.opacity(0.6))
                .padding(.horizontal, 14)
                .padding(.top, 12)
                .padding(.bottom, 2)

            Divider()
                .background(ForgeTheme.glassBorder)

            callOption(
                icon: "video.fill",
                iconColor: ForgeTheme.green,
                title: "Jitsi Meet",
                subtitle: "Browser-based · no account needed · sends link to chat"
            ) {
                onStartJitsi()
            }

            if let facetime = onStartFaceTime {
                Divider()
                    .background(ForgeTheme.glassBorder)
                    .padding(.horizontal, 14)

                callOption(
                    icon: "facetime",
                    iconColor: ForgeTheme.primary,
                    title: "FaceTime",
                    subtitle: "Apple devices only · fully encrypted · no server"
                ) {
                    facetime()
                }
            }

            Divider()
                .background(ForgeTheme.glassBorder)

            Text("Both options are peer-to-peer — no Forge server involved.")
                .font(.system(size: 10))
                .foregroundStyle(ForgeTheme.silver.opacity(0.4))
                .padding(.horizontal, 14)
                .padding(.bottom, 12)
                .padding(.top, 4)
        }
        .frame(width: 300)
        .background(ForgeTheme.dark900)
    }

    private func callOption(
        icon: String,
        iconColor: Color,
        title: String,
        subtitle: String,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            HStack(spacing: 12) {
                ZStack {
                    RoundedRectangle(cornerRadius: 8, style: .continuous)
                        .fill(iconColor.opacity(0.15))
                        .frame(width: 36, height: 36)
                    Image(systemName: icon)
                        .font(.system(size: 15, weight: .medium))
                        .foregroundStyle(iconColor)
                }

                VStack(alignment: .leading, spacing: 2) {
                    Text(title)
                        .font(.system(size: 13, weight: .semibold))
                        .foregroundStyle(ForgeTheme.white)
                    Text(subtitle)
                        .font(.system(size: 11))
                        .foregroundStyle(ForgeTheme.silver.opacity(0.55))
                        .lineLimit(2)
                }

                Spacer()

                Image(systemName: "arrow.up.forward")
                    .font(.system(size: 11))
                    .foregroundStyle(ForgeTheme.silver.opacity(0.35))
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .background(
            Color.white.opacity(0.0)
                .onHover { _ in }  // enables hover tracking for cursor
        )
    }
}
