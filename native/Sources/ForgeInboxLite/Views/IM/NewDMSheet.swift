import SwiftUI

/// Rail sheet for starting a new DM or group DM: pick a workspace, pick member(s).
struct NewDMSheet: View {
    @ObservedObject var store: NativeCommunicatorStore
    var onOpenConversation: (CommunicatorConversation) -> Void

    @Environment(\.dismiss) private var dismiss

    @State private var selectedWorkspaceID: Int?
    @State private var members: [CommunicatorMemberProfile] = []
    @State private var isLoadingMembers = false
    @State private var searchText = ""
    @State private var errorMessage: String?
    @State private var creatingDMForUserID: Int?
    @State private var profileUserID: Int?

    @State private var isGroupMode = false
    @State private var selectedUserIDs: Set<Int> = []
    @State private var isCreatingGroup = false

    private var filteredMembers: [CommunicatorMemberProfile] {
        guard !searchText.isEmpty else { return members }
        let q = searchText.lowercased()
        return members.filter {
            $0.displayName.lowercased().contains(q) || $0.email.lowercased().contains(q)
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            header
            modeToggle
            workspacePicker
            searchBar
            memberList
            if isGroupMode {
                groupFooter
            }
        }
        .frame(width: 300, height: isGroupMode ? 470 : 420)
        .background(ForgeTheme.dark900)
        .onAppear {
            if selectedWorkspaceID == nil {
                selectedWorkspaceID = store.knownWorkspaces.first?.id
            }
            loadMembers()
        }
    }

    // MARK: - Sections

    private var header: some View {
        HStack {
            Text(isGroupMode ? "New Group" : "New Message")
                .font(.system(size: 13, weight: .semibold))
                .foregroundStyle(ForgeTheme.white)
            Spacer()
            Button {
                dismiss()
            } label: {
                Image(systemName: "xmark")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(ForgeTheme.silver.opacity(0.6))
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal, 14)
        .frame(height: 42)
        .background(ForgeTheme.dark950)
    }

    private var modeToggle: some View {
        HStack(spacing: 0) {
            modeButton(title: "Direct Message", selected: !isGroupMode) {
                isGroupMode = false
                selectedUserIDs = []
            }
            modeButton(title: "Group", selected: isGroupMode) {
                isGroupMode = true
            }
        }
        .padding(.horizontal, 12)
        .padding(.top, 8)
    }

    private func modeButton(title: String, selected: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title)
                .font(.system(size: 11, weight: .semibold))
                .foregroundColor(selected ? .white : ForgeTheme.silver.opacity(0.55))
                .frame(maxWidth: .infinity)
                .padding(.vertical, 6)
                .background(selected ? ForgeTheme.primary : ForgeTheme.dark700.opacity(0.5))
                .clipShape(RoundedRectangle(cornerRadius: 6, style: .continuous))
        }
        .buttonStyle(.plain)
    }

    private var workspacePicker: some View {
        HStack(spacing: 8) {
            Text("Workspace")
                .font(.system(size: 11))
                .foregroundStyle(ForgeTheme.silver.opacity(0.55))

            Picker("Workspace", selection: Binding(
                get: { selectedWorkspaceID ?? -1 },
                set: { newValue in
                    selectedWorkspaceID = newValue == -1 ? nil : newValue
                    selectedUserIDs = []
                    loadMembers()
                }
            )) {
                ForEach(store.knownWorkspaces, id: \.id) { ws in
                    Text(ws.name).tag(ws.id)
                }
            }
            .labelsHidden()
            .pickerStyle(.menu)
            .frame(maxWidth: .infinity)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
    }

    private var searchBar: some View {
        HStack(spacing: 6) {
            Image(systemName: "magnifyingglass")
                .font(.system(size: 11))
                .foregroundColor(ForgeTheme.silver.opacity(0.45))
            TextField("Search people...", text: $searchText)
                .textFieldStyle(.plain)
                .font(.system(size: 12))
                .foregroundColor(ForgeTheme.white)
        }
        .padding(.horizontal, 8)
        .frame(height: 28)
        .background(ForgeTheme.dark800.opacity(0.8))
        .clipShape(RoundedRectangle(cornerRadius: 6, style: .continuous))
        .padding(.horizontal, 12)
        .padding(.bottom, 6)
    }

    private var memberList: some View {
        Group {
            if isLoadingMembers {
                VStack {
                    Spacer()
                    ProgressView()
                    Spacer()
                }
            } else if let errorMessage {
                VStack(spacing: 8) {
                    Spacer()
                    Image(systemName: "exclamationmark.triangle")
                        .foregroundStyle(ForgeTheme.coral)
                    Text(errorMessage)
                        .font(.system(size: 11))
                        .foregroundStyle(ForgeTheme.silver.opacity(0.6))
                        .multilineTextAlignment(.center)
                    Spacer()
                }
                .padding(16)
            } else if filteredMembers.isEmpty {
                VStack {
                    Spacer()
                    Text(searchText.isEmpty ? "No other members in this workspace" : "No matches")
                        .font(.system(size: 11))
                        .foregroundStyle(ForgeTheme.silver.opacity(0.5))
                    Spacer()
                }
            } else {
                ScrollView {
                    LazyVStack(spacing: 0) {
                        ForEach(filteredMembers) { member in
                            memberRow(member)
                        }
                    }
                    .padding(.vertical, 4)
                }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var groupFooter: some View {
        VStack(spacing: 0) {
            Divider().background(ForgeTheme.glassBorder)
            HStack {
                Text(selectedUserIDs.isEmpty ? "Select people to start a group" : "\(selectedUserIDs.count) selected")
                    .font(.system(size: 11))
                    .foregroundStyle(ForgeTheme.silver.opacity(0.55))
                Spacer()
                Button {
                    startGroup()
                } label: {
                    HStack(spacing: 6) {
                        if isCreatingGroup {
                            ProgressView().controlSize(.mini)
                        }
                        Text("Start Group")
                            .font(.system(size: 12, weight: .semibold))
                    }
                    .foregroundStyle(.white)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 6)
                    .background(selectedUserIDs.count >= 2 ? ForgeTheme.primary : ForgeTheme.primary.opacity(0.35))
                    .clipShape(Capsule())
                }
                .buttonStyle(.plain)
                .disabled(selectedUserIDs.count < 2 || isCreatingGroup)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 10)
        }
        .background(ForgeTheme.dark950)
    }

    private func memberRow(_ member: CommunicatorMemberProfile) -> some View {
        HStack(spacing: 8) {
            if isGroupMode {
                Image(systemName: selectedUserIDs.contains(member.id) ? "checkmark.circle.fill" : "circle")
                    .font(.system(size: 15))
                    .foregroundStyle(selectedUserIDs.contains(member.id) ? ForgeTheme.primary : ForgeTheme.silver.opacity(0.3))
            }

            ProfileInitialsAvatar(name: member.displayName, size: 30)

            VStack(alignment: .leading, spacing: 1) {
                Text(member.displayName)
                    .font(.system(size: 12, weight: .medium))
                    .foregroundColor(ForgeTheme.white)
                    .lineLimit(1)
                Text(member.title?.isEmpty == false ? member.title! : member.email)
                    .font(.system(size: 10))
                    .foregroundColor(ForgeTheme.silver.opacity(0.5))
                    .lineLimit(1)
            }

            Spacer()

            if !isGroupMode {
                // Profile peek (DM mode only — group mode taps toggle selection)
                Button {
                    profileUserID = member.id
                } label: {
                    Image(systemName: "person.crop.circle")
                        .font(.system(size: 13))
                        .foregroundStyle(ForgeTheme.silver.opacity(0.5))
                }
                .buttonStyle(.plain)
                .help("View profile")
                .popover(isPresented: Binding(
                    get: { profileUserID == member.id },
                    set: { if !$0 { profileUserID = nil } }
                ), arrowEdge: .trailing) {
                    UserProfileView(
                        store: store,
                        userID: member.id,
                        fallbackName: member.displayName,
                        workspaceID: selectedWorkspaceID,
                        onOpenConversation: { conversation in
                            profileUserID = nil
                            dismiss()
                            onOpenConversation(conversation)
                        }
                    )
                }
            }

            if creatingDMForUserID == member.id {
                ProgressView().controlSize(.small)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 6)
        .contentShape(Rectangle())
        .onTapGesture {
            if isGroupMode {
                toggleSelection(member.id)
            } else {
                startDM(with: member)
            }
        }
    }

    // MARK: - Actions

    private func toggleSelection(_ userID: Int) {
        if selectedUserIDs.contains(userID) {
            selectedUserIDs.remove(userID)
        } else {
            selectedUserIDs.insert(userID)
        }
    }

    private func loadMembers() {
        guard let workspaceID = selectedWorkspaceID else { return }
        isLoadingMembers = true
        errorMessage = nil
        Task {
            do {
                members = try await store.loadMembers(workspaceID: workspaceID)
            } catch {
                errorMessage = error.localizedDescription
                members = []
            }
            isLoadingMembers = false
        }
    }

    private func startDM(with member: CommunicatorMemberProfile) {
        guard let workspaceID = selectedWorkspaceID, creatingDMForUserID == nil else { return }
        creatingDMForUserID = member.id
        Task {
            defer { creatingDMForUserID = nil }
            do {
                if let conversation = try await store.openDM(workspaceID: workspaceID, userID: member.id) {
                    dismiss()
                    onOpenConversation(conversation)
                } else {
                    errorMessage = "DM created but conversation not found — try refreshing."
                }
            } catch {
                errorMessage = error.localizedDescription
            }
        }
    }

    private func startGroup() {
        guard let workspaceID = selectedWorkspaceID, selectedUserIDs.count >= 2, !isCreatingGroup else { return }
        isCreatingGroup = true
        Task {
            defer { isCreatingGroup = false }
            do {
                if let conversation = try await store.openDM(workspaceID: workspaceID, userIDs: Array(selectedUserIDs)) {
                    dismiss()
                    onOpenConversation(conversation)
                } else {
                    errorMessage = "Group created but conversation not found — try refreshing."
                }
            } catch {
                errorMessage = error.localizedDescription
            }
        }
    }
}
