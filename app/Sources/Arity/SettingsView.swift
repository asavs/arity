import ArityCore
import SwiftUI

struct SettingsView: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss

    @State private var showAddProvider = false
    @State private var refreshingLocal = false

    private var keyNamesNeedingKeys: [String] {
        var seen = Set<String>()
        var out: [String] = []
        for p in store.providers where p.needsKey && !p.keyName.isEmpty {
            if seen.insert(p.keyName).inserted {
                out.append(p.keyName)
            }
        }
        return out.sorted()
    }

    var body: some View {
        VStack(spacing: 0) {
            Form {
                Section {
                    ForEach(store.providers, id: \.name) { provider in
                        ProviderRow(provider: provider)
                    }
                } header: {
                    HStack {
                        Text("Providers")
                        Spacer()
                        Button {
                            refreshingLocal = true
                            Task {
                                await store.refreshLocalModels()
                                refreshingLocal = false
                            }
                        } label: {
                            if refreshingLocal {
                                ProgressView().controlSize(.mini)
                            } else {
                                Label("Refresh Local Models", systemImage: "arrow.clockwise")
                            }
                        }
                        .buttonStyle(.borderless)
                        .font(.caption)
                    }
                }

                Section("API Keys") {
                    ForEach(keyNamesNeedingKeys, id: \.self) { keyName in
                        ApiKeyRow(keyName: keyName)
                    }
                }

                Section("ChatGPT / Codex Sign-In") {
                    HStack {
                        Circle()
                            .fill(store.codexAccount != nil ? Color.green : Color.secondary.opacity(0.4))
                            .frame(width: 8, height: 8)
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Codex (ChatGPT OAuth)")
                            Text(store.codexAccount ?? "Not signed in")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        Spacer()
                        if store.signingInCodex {
                            ProgressView().controlSize(.small)
                            Button("Cancel") { store.cancelCodexLogin() }
                        } else if store.codexAccount != nil {
                            Button("Sign Out") {
                                Task { await store.signOutCodex() }
                            }
                        } else {
                            Button("Sign In with ChatGPT") {
                                store.signInCodex()
                            }
                            .buttonStyle(.borderedProminent)
                            .controlSize(.small)
                        }
                    }
                }

                if !store.rateLimits.isEmpty {
                    Section("Rate Limits") {
                        ForEach(store.rateLimits, id: \.self) { limit in
                            HStack {
                                Text("\(limit.provider) · \(limit.limitName)")
                                    .font(.callout)
                                Spacer()
                                Text("\(limit.remaining) / \(limit.limitVal)")
                                    .font(.caption.monospacedDigit())
                                    .foregroundStyle(.secondary)
                                if let reset = limit.resetSecs {
                                    Text("resets in \(reset)s")
                                        .font(.caption2)
                                        .foregroundStyle(.tertiary)
                                }
                            }
                        }
                    }
                }

            }
            .formStyle(.grouped)

            Divider()

            VStack(alignment: .leading, spacing: 10) {
                HStack(alignment: .center, spacing: 10) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Data Directory")
                            .font(.callout.weight(.medium))
                        Text(store.dataDirPath)
                            .font(.caption.monospaced())
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                            .truncationMode(.middle)
                            .textSelection(.enabled)
                    }
                    Spacer()
                }

                if let replay = store.replayCheckResult {
                    HStack(spacing: 6) {
                        Image(systemName: replay.ok ? "checkmark.circle.fill" : "xmark.circle.fill")
                            .foregroundStyle(replay.ok ? .green : .red)
                        Text(
                            replay.ok
                                ? "Replay verified: \(replay.threads) thread(s), \(replay.deliveries) delivery(ies)"
                                : "Replay mismatch detected"
                        )
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    }
                }

                HStack(spacing: 10) {
                    Button("Add / Edit Provider…", systemImage: "plus") {
                        showAddProvider = true
                    }
                    .buttonStyle(.bordered)
                    .accessibilityLabel("Add Provider")

                    Button("Verify Replay", systemImage: "checkmark.shield") {
                        Task { await store.runReplayCheck() }
                    }
                    .buttonStyle(.bordered)
                    .accessibilityLabel("Verify Replay")
                    .accessibilityIdentifier("verify-replay-button")

                    Spacer()

                    Button("Done") {
                        dismiss()
                    }
                    .buttonStyle(.borderedProminent)
                    .keyboardShortcut(.defaultAction)
                }
            }
            .padding(14)
        }
        .frame(width: 580, height: 600)
        .sheet(isPresented: $showAddProvider) {
            UpsertProviderSheet()
                .environment(store)
        }
    }
}

private struct ProviderRow: View {
    let provider: UiProviderConfig

    var body: some View {
        HStack(alignment: .center, spacing: 10) {
            Circle()
                .fill(provider.ready ? Color.green : Color.secondary.opacity(0.4))
                .frame(width: 8, height: 8)
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(provider.name)
                        .fontWeight(.medium)
                    Text(provider.wire)
                        .font(.caption2.monospaced())
                        .foregroundStyle(.secondary)
                        .padding(.horizontal, 5)
                        .padding(.vertical, 1)
                        .background(.quaternary, in: Capsule())
                    if provider.local {
                        Text("local")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                }
                Text(provider.url)
                    .font(.caption.monospaced())
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
            Spacer()
            Text(provider.ready ? "Ready" : "Key needed")
                .font(.caption)
                .foregroundStyle(provider.ready ? Color.secondary : Color.orange)
        }
        .padding(.vertical, 2)
    }
}

private struct ApiKeyRow: View {
    @Environment(AppStore.self) private var store
    let keyName: String
    @State private var input = ""
    @State private var isSet = false

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "key")
                .foregroundStyle(.secondary)
                .frame(width: 16)
            Text(keyName)
                .font(.callout.monospaced())
            Spacer()
            if isSet {
                Text("Saved")
                    .font(.caption)
                    .foregroundStyle(.green)
                Button("Remove") {
                    Task {
                        await store.setKey(keyName: keyName, key: "")
                        isSet = store.hasKey(keyName)
                    }
                }
                .controlSize(.small)
            } else {
                SecureField("API key", text: $input, prompt: Text("Paste \(keyName) key"))
                    .labelsHidden()
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 190)
                    .onSubmit(save)
                Button("Save", action: save)
                    .controlSize(.small)
                    .disabled(input.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .onAppear {
            isSet = store.hasKey(keyName)
        }
    }

    private func save() {
        let value = input.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !value.isEmpty else { return }
        Task {
            await store.setKey(keyName: keyName, key: value)
            input = ""
            isSet = store.hasKey(keyName)
        }
    }
}

private struct UpsertProviderSheet: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss

    @State private var name = ""
    @State private var url = "http://127.0.0.1:8080/v1"
    @State private var wire = "chat_completions"
    @State private var needsKey = false
    @State private var keyName = ""
    @State private var sessionHeader = ""
    @State private var modelsText = ""
    @State private var saving = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Add or Update Provider")
                .font(.title3.weight(.semibold))

            Form {
                TextField("Name (e.g. local-mlx)", text: $name)
                TextField("Base URL (e.g. http://127.0.0.1:8080/v1)", text: $url)
                Picker("Wire Codec", selection: $wire) {
                    Text("Chat Completions").tag("chat_completions")
                    Text("Responses").tag("responses")
                }
                Toggle("Requires API Key", isOn: $needsKey)
                TextField("Key Name (defaults to provider name)", text: $keyName)
                TextField("Session Header (optional)", text: $sessionHeader)
                TextField("Models (comma-separated)", text: $modelsText)
            }
            .formStyle(.grouped)

            HStack {
                Button("Cancel") { dismiss() }
                    .keyboardShortcut(.cancelAction)
                Spacer()
                Button("Save Provider") {
                    saving = true
                    let models = modelsText
                        .split(separator: ",")
                        .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
                        .filter { !$0.isEmpty }
                    let sess = sessionHeader.trimmingCharacters(in: .whitespacesAndNewlines)
                    Task {
                        let ok = await store.upsertProvider(
                            name: name,
                            url: url,
                            wire: wire,
                            needsKey: needsKey,
                            keyName: keyName.isEmpty ? name : keyName,
                            sessionHeader: sess.isEmpty ? nil : sess,
                            models: models
                        )
                        saving = false
                        if ok { dismiss() }
                    }
                }
                .buttonStyle(.borderedProminent)
                .keyboardShortcut(.defaultAction)
                .disabled(
                    saving
                        || name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                        || url.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                )
            }
        }
        .padding(18)
        .frame(width: 480, height: 390)
    }
}
