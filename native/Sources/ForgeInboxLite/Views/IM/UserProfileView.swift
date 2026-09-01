import SwiftUI

/// Compact in-app profile card, shown as a popover/sheet without leaving the app.
struct UserProfileView: View {
    @ObservedObject var store: NativeCommunicatorStore
    let userID: Int
    let fallbackName: String
    /// Present when opened from a member list; enables the "Message" button.
    var workspaceID: Int? = nil
    var onOpenConversation: ((CommunicatorConversation) -> Void)? = nil

    @State private var profile: CommunicatorMemberProfile?
    @State private var isLoading = true
    @State private var isOpeningDM = false

    private var displayName: String { profile?.displayName ?? fallbackName }

    var body: some View {
        VStack(spacing: 12) {
            ProfileInitialsAvatar(name: displayName, size: 56)

            VStack(spacing: 3) {
                Text(displayName)
                    .font(.system(size: 15, weight: .bold))
                    .foregroundStyle(ForgeTheme.white)

                if let title = profile?.title, !title.isEmpty {
                    Text(title)
                        .font(.system(size: 12))
                        .foregroundStyle(ForgeTheme.silver.opacity(0.7))
                }

                if let email = profile?.email, !email.isEmpty {
                    Text(email)
                        .font(.system(size: 11))
                        .foregroundStyle(ForgeTheme.primary)
                        .textSelection(.enabled)
                }
            }

            if let status = profile?.status {
                HStack(spacing: 5) {
                    Circle()
                        .fill(status == "active" ? ForgeTheme.statusOnline : ForgeTheme.silver.opacity(0.4))
                        .frame(width: 7, height: 7)
                    Text(profile?.statusMessage?.isEmpty == false ? profile!.statusMessage! : status.capitalized)
                        .font(.system(size: 11))
                        .foregroundStyle(ForgeTheme.silver.opacity(0.6))
                }
            }

            if let bio = profile?.bio, !bio.isEmpty {
                Text(bio)
                    .font(.system(size: 11))
                    .foregroundStyle(ForgeTheme.silver.opacity(0.75))
                    .multilineTextAlignment(.center)
                    .lineLimit(4)
                    .padding(.horizontal, 4)
            }

            if isLoading {
                ProgressView()
                    .controlSize(.small)
            }

            if let workspaceID, let onOpenConversation {
                Button {
                    guard !isOpeningDM else { return }
                    isOpeningDM = true
                    Task {
                        defer { isOpeningDM = false }
                        if let conversation = try? await store.openDM(workspaceID: workspaceID, userID: userID) {
                            onOpenConversation(conversation)
                        }
                    }
                } label: {
                    HStack(spacing: 6) {
                        if isOpeningDM {
                            ProgressView().controlSize(.mini)
                        } else {
                            Image(systemName: "bubble.left.fill")
                                .font(.system(size: 11))
                        }
                        Text("Message")
                            .font(.system(size: 12, weight: .semibold))
                    }
                    .foregroundStyle(.white)
                    .padding(.horizontal, 16)
                    .padding(.vertical, 7)
                    .background(ForgeTheme.primary)
                    .clipShape(Capsule())
                }
                .buttonStyle(.plain)
                .disabled(isOpeningDM)
            }
        }
        .padding(18)
        .frame(width: 260)
        .background(ForgeTheme.dark900)
        .task {
            profile = try? await store.fetchUserProfile(userID: userID)
            isLoading = false
        }
    }
}

/// Local copy of the initials avatar so this file has no private-type dependencies.
struct ProfileInitialsAvatar: View {
    let name: String
    let size: CGFloat

    private var initials: String {
        let parts = name.split(separator: " ").prefix(2)
        return parts.compactMap { $0.first.map { String($0).uppercased() } }.joined()
    }

    private var gradientColors: [Color] {
        let palette: [(Color, Color)] = [
            (ForgeTheme.primary, Color(hex: "#1C56B8")),
            (ForgeTheme.violet, Color(hex: "#4A34CC")),
            (ForgeTheme.green, Color(hex: "#1A8C5E")),
            (ForgeTheme.amber, Color(hex: "#CC8800")),
            (ForgeTheme.coral, Color(hex: "#CC3B3B")),
        ]
        let index = abs(name.hashValue) % palette.count
        return [palette[index].0, palette[index].1]
    }

    var body: some View {
        ZStack {
            LinearGradient(colors: gradientColors, startPoint: .topLeading, endPoint: .bottomTrailing)
            Text(initials.isEmpty ? "?" : initials)
                .font(.system(size: size * 0.38, weight: .semibold))
                .foregroundColor(.white)
        }
        .frame(width: size, height: size)
        .clipShape(Circle())
    }
}
