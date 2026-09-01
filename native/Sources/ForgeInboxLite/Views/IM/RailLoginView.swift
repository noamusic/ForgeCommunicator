import SwiftUI

struct RailLoginView: View {
    @ObservedObject var store: NativeCommunicatorStore
    var onOpenSettings: () -> Void

    var body: some View {
        ScrollView {
            VStack(spacing: 20) {
                // Header
                VStack(spacing: 6) {
                    ForgeLogoIcon(size: 36)
                    Text("Sign in to Forge")
                        .font(ForgeTheme.headingFont(size: 15, weight: .semibold))
                        .foregroundStyle(ForgeTheme.white)
                    Text("Connect to your Forge Communicator server")
                        .font(.system(size: 11))
                        .foregroundStyle(ForgeTheme.silver.opacity(0.55))
                        .multilineTextAlignment(.center)
                }
                .padding(.top, 24)

                // Fields
                VStack(spacing: 10) {
                    loginField(
                        placeholder: "Server URL",
                        text: $store.serverURL,
                        systemImage: "server.rack"
                    )
                    .onSubmit { store.updateServerURL(store.serverURL) }

                    loginField(
                        placeholder: "Email",
                        text: $store.email,
                        systemImage: "envelope"
                    )

                    secureLoginField(
                        placeholder: "Password",
                        text: $store.password,
                        systemImage: "lock"
                    )
                }

                // Error
                if let error = store.errorMessage {
                    Text(error)
                        .font(.system(size: 11))
                        .foregroundStyle(ForgeTheme.coral)
                        .multilineTextAlignment(.center)
                        .padding(.horizontal, 4)
                }

                // Sign in button
                Button {
                    Task { await store.signIn() }
                } label: {
                    Group {
                        if store.isLoading {
                            ProgressView()
                                .controlSize(.small)
                                .tint(.white)
                        } else {
                            Text("Sign In")
                                .font(.system(size: 13, weight: .semibold))
                        }
                    }
                    .frame(maxWidth: .infinity)
                    .frame(height: 34)
                }
                .buttonStyle(.plain)
                .background(ForgeTheme.primary)
                .foregroundStyle(.white)
                .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
                .disabled(store.isLoading || store.serverURL.isEmpty || store.email.isEmpty || store.password.isEmpty)

                // Divider
                HStack {
                    Rectangle().fill(ForgeTheme.glassBorder).frame(height: 1)
                    Text("or").font(.system(size: 10)).foregroundStyle(ForgeTheme.silver.opacity(0.4))
                    Rectangle().fill(ForgeTheme.glassBorder).frame(height: 1)
                }

                // Google sign-in
                Button {
                    Task { await store.signInWithGoogle() }
                } label: {
                    HStack(spacing: 6) {
                        Image(systemName: "globe")
                            .font(.system(size: 12))
                        Text("Continue with Google")
                            .font(.system(size: 12, weight: .medium))
                    }
                    .frame(maxWidth: .infinity)
                    .frame(height: 34)
                    .foregroundStyle(ForgeTheme.white)
                    .background(ForgeTheme.dark700.opacity(0.7))
                    .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
                    .overlay(
                        RoundedRectangle(cornerRadius: 8, style: .continuous)
                            .strokeBorder(ForgeTheme.glassBorder, lineWidth: 1)
                    )
                }
                .buttonStyle(.plain)
                .disabled(store.isLoading)

                // Open full workspace link
                Button("Manage sources in workspace") {
                    onOpenSettings()
                }
                .buttonStyle(.plain)
                .font(.system(size: 11))
                .foregroundStyle(ForgeTheme.silver.opacity(0.5))
                .padding(.bottom, 20)
            }
            .padding(.horizontal, 16)
        }
        .background(ForgeTheme.dark900)
    }

    private func loginField(placeholder: String, text: Binding<String>, systemImage: String) -> some View {
        HStack(spacing: 8) {
            Image(systemName: systemImage)
                .font(.system(size: 11))
                .foregroundStyle(ForgeTheme.silver.opacity(0.5))
                .frame(width: 16)
            TextField(placeholder, text: text)
                .textFieldStyle(.plain)
                .font(.system(size: 12))
                .foregroundStyle(ForgeTheme.white)
                .autocorrectionDisabled()
        }
        .padding(.horizontal, 10)
        .frame(height: 34)
        .background(ForgeTheme.dark800.opacity(0.8))
        .clipShape(RoundedRectangle(cornerRadius: 7, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 7, style: .continuous)
                .strokeBorder(ForgeTheme.glassBorder, lineWidth: 1)
        )
    }

    private func secureLoginField(placeholder: String, text: Binding<String>, systemImage: String) -> some View {
        HStack(spacing: 8) {
            Image(systemName: systemImage)
                .font(.system(size: 11))
                .foregroundStyle(ForgeTheme.silver.opacity(0.5))
                .frame(width: 16)
            SecureField(placeholder, text: text)
                .textFieldStyle(.plain)
                .font(.system(size: 12))
                .foregroundStyle(ForgeTheme.white)
        }
        .padding(.horizontal, 10)
        .frame(height: 34)
        .background(ForgeTheme.dark800.opacity(0.8))
        .clipShape(RoundedRectangle(cornerRadius: 7, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 7, style: .continuous)
                .strokeBorder(ForgeTheme.glassBorder, lineWidth: 1)
        )
    }
}
