import AppKit
import ArityCore
import SwiftUI

struct NewThreadSheet: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss

    @State private var threadName = ""
    @State private var selectedModel = ""
    @State private var customModel = ""
    @State private var workspaceFolder = ""
    @State private var creating = false
    @State private var localError: String?

    private var effectiveModel: String {
        let custom = customModel.trimmingCharacters(in: .whitespacesAndNewlines)
        return custom.isEmpty ? selectedModel : custom
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("New Thread")
                .font(.title3.weight(.semibold))

            Form {
                Section("Thread") {
                    TextField("Auto (e.g. chat-1)", text: $threadName)
                        .accessibilityLabel("Thread Name")

                    Picker("Model", selection: $selectedModel) {
                        ForEach(store.models, id: \.spec) { option in
                            Text("\(shortModelName(option.spec))\(option.ready ? "" : " (key needed)")")
                                .tag(option.spec)
                        }
                    }
                    .accessibilityLabel("New Thread Model")

                    TextField("Custom model spec (optional, e.g. provider:model)", text: $customModel)
                        .accessibilityLabel("Custom Model")
                }

                Section {
                    HStack(spacing: 8) {
                        TextField("Optional git repository path", text: $workspaceFolder)
                            .accessibilityLabel("Workspace Folder")
                        Button("Browse…") {
                            chooseFolder()
                        }
                        .accessibilityLabel("Browse Workspace Folder")
                    }
                } header: {
                    Text("Workspace")
                } footer: {
                    Text("Attaching a git repository runs tools inside an isolated detached worktree so your checkout stays untouched until you apply.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .formStyle(.grouped)

            if let err = localError {
                Label(err, systemImage: "exclamationmark.triangle.fill")
                    .font(.callout)
                    .foregroundStyle(.red)
                    .padding(.horizontal, 4)
            }

            HStack {
                Button("Cancel") {
                    dismiss()
                }
                .keyboardShortcut(.cancelAction)

                Spacer()

                if creating {
                    ProgressView()
                        .controlSize(.small)
                }

                Button("Create Thread") {
                    submit()
                }
                .buttonStyle(.borderedProminent)
                .keyboardShortcut(.defaultAction)
                .disabled(creating || effectiveModel.isEmpty)
                .accessibilityLabel("Create Thread")
            }
        }
        .padding(18)
        .frame(width: 520, height: 460)
        .onAppear {
            if selectedModel.isEmpty {
                selectedModel = store.defaultModelSpec
            }
        }
    }

    private func chooseFolder() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.prompt = "Attach"
        panel.message = "Select a git repository folder to attach to this thread."
        if panel.runModal() == .OK, let url = panel.url {
            workspaceFolder = url.path
        }
    }

    private func submit() {
        localError = nil
        creating = true
        Task {
            let ok = await store.createThread(
                name: threadName,
                model: effectiveModel,
                sourceDir: workspaceFolder
            )
            creating = false
            if ok {
                dismiss()
            } else {
                localError = store.errorBanner ?? "Failed to create thread."
            }
        }
    }
}
