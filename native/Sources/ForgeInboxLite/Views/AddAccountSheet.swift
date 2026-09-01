import SwiftUI
import AppKit

struct AddAccountSheet: View {
    enum Mode {
        case add
        case rename(UUID)
    }

    let mode: Mode
    let initialType: AccountType
    let initialName: String
    let onSave: (AccountType, String) -> Void

    @State private var selectedType: AccountType
    @State private var displayName: String
    @Environment(\.dismiss) private var dismiss
    @FocusState private var nameFieldFocused: Bool

    init(mode: Mode, initialType: AccountType = .whatsapp, initialName: String = "", onSave: @escaping (AccountType, String) -> Void) {
        self.mode = mode
        self.initialType = initialType
        self.initialName = initialName
        self.onSave = onSave
        _selectedType = State(initialValue: initialType)
        _displayName = State(initialValue: initialName)
    }

    private var canSave: Bool {
        !displayName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    var body: some View {
        ZStack {
            ForgeBackgroundLayer()

            VStack(alignment: .leading, spacing: 16) {
                HStack(spacing: 10) {
                    ForgeBadgeIcon(size: 30)
                    VStack(alignment: .leading, spacing: 1) {
                        Text(titleText)
                            .font(ForgeTheme.brandFont(size: 14, weight: .bold))
                            .tracking(1.4)
                            .foregroundStyle(ForgeTheme.silver)
                        Text("Source control")
                            .font(.caption)
                            .foregroundStyle(ForgeTheme.amber)
                    }
                }

                if case .add = mode {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("Source Type")
                            .font(.caption)
                            .foregroundStyle(.secondary)

                        Picker("Source Type", selection: $selectedType) {
                            ForEach(AccountType.allCases) { type in
                                Text(type.displayLabel).tag(type)
                            }
                        }
                        .labelsHidden()
                        .pickerStyle(.segmented)
                    }
                }

                TextField("Display name", text: $displayName)
                    .textFieldStyle(.roundedBorder)
                    .focused($nameFieldFocused)

                HStack {
                    Spacer()

                    Button("Cancel") {
                        dismiss()
                    }

                    Button("Save") {
                        onSave(selectedType, displayName.trimmingCharacters(in: .whitespacesAndNewlines))
                        dismiss()
                    }
                    .keyboardShortcut(.defaultAction)
                    .disabled(!canSave)
                }
            }
            .padding(20)
            .frame(width: 380)
            .forgeGlassSurface()
        }
        .frame(width: 420)
        .preferredColorScheme(.dark)
        .onAppear {
            // Re-activate the app (needed when launched from terminal without a bundle)
            // and delay focus so the sheet window is fully key before we request it.
            NSApp.activate(ignoringOtherApps: true)
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) {
                nameFieldFocused = true
            }
        }
    }

    private var titleText: String {
        if case .add = mode {
            return "Add Source"
        }
        return "Rename Source"
    }
}
