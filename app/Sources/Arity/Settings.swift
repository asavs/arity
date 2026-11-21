import ArityCore
import SwiftUI

/// Where models come from: the ChatGPT sign-in, OpenAI-compatible providers with
/// their keys, and which models tasks for agents may be given.
struct SettingsSheet: View {
    @Environment(Store.self) private var store
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        @Bindable var store = store
        VStack(spacing: 0) {
            Form {
                Section {
                    ChatGPTRow()
                } header: {
                    Text("ChatGPT")
                } footer: {
                    Text("Uses your Plus or Pro plan through OpenAI's sign-in for apps.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Section {
                    ForEach(store.providers, id: \.name) { ProviderRow(provider: $0) }
                } header: {
                    HStack {
                        Text("Providers")
                        Spacer()
                        AddProviderMenu()
                    }
                } footer: {
                    Text("Any OpenAI-compatible endpoint. Keys stay in keys.json in the data folder.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Section {
                    if store.models.isEmpty {
                        Text("Sign in or add a key to see models.").foregroundStyle(.secondary)
                    }
                    ForEach(store.models, id: \.self) { model in
                        Toggle(shortName(model), isOn: Binding(
                            get: { store.workers.contains(model) },
                            set: { store.setWorker(model, $0) }))
                        .help(model)
                    }
                } header: {
                    HStack {
                        Text("Workers")
                        Spacer()
                        Button("Refresh", systemImage: "arrow.clockwise") { store.loadModels(refresh: true) }
                            .labelStyle(.iconOnly).buttonStyle(.borderless)
                            .help("Fetch each provider's model list again")
                    }
                } footer: {
                    Text("A task for an agent goes to one of these, picked blind.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            .formStyle(.grouped)
            HStack {
                if let error = store.error {
                    Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(.red).lineLimit(2)
                }
                Spacer()
                Button("Done") { dismiss() }.keyboardShortcut(.defaultAction)
            }
            .padding(12)
        }
        .frame(width: 540, height: 620)
        .onAppear { store.checkKeys() }
        .sheet(item: $store.addingProvider) { AddProviderSheet(preset: $0) }
    }
}

private struct ChatGPTRow: View {
    @Environment(Store.self) private var store

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: store.chatgptAccount == nil ? "person.crop.circle" : "person.crop.circle.badge.checkmark")
                .font(.title2)
                .foregroundStyle(store.chatgptAccount == nil ? Color.secondary : Color.green)
            if store.signingIn {
                ProgressView().controlSize(.small)
                Text("Finish signing in in your browser…")
                Spacer()
                Button("Cancel") { store.cancelSignIn() }
            } else if let account = store.chatgptAccount {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Signed in")
                    Text(account).font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                Button("Sign Out") { store.signOutOfChatGPT() }
            } else {
                Text("Not signed in").foregroundStyle(.secondary)
                Spacer()
                Button("Sign In with ChatGPT") { store.signInToChatGPT() }
                    .buttonStyle(.borderedProminent)
            }
        }
        .padding(.vertical, 2)
    }
}

private struct ProviderRow: View {
    @Environment(Store.self) private var store
    let provider: Provider
    @State private var key = ""

    /// Friendlier names, and where to get a key, for the providers Arity comes with.
    static let titles = ["mlx": "MLX on this Mac", "nous": "Nous Portal", "opencode": "OpenCode Zen", "opencode-go": "OpenCode Go"]
    static let keyPages = [
        "nous": URL(string: "https://portal.nousresearch.com")!,
        "opencode": URL(string: "https://opencode.ai")!,
        "opencode-go": URL(string: "https://opencode.ai/go")!,
    ]

    private var check: KeyCheck? { store.keyChecks[provider.name] }
    private var rejected: Bool { if case .rejected = check { return true }; return false }

    private var dot: Color {
        if !provider.needsKey { return .green }
        guard provider.hasKey else { return .secondary.opacity(0.4) }
        switch check {
        case .works: return .green
        case .rejected: return .red
        case .unreachable: return .orange
        case .noKey, nil: return .secondary.opacity(0.4)
        }
    }

    var body: some View {
        HStack(spacing: 10) {
            Circle().fill(dot).frame(width: 8, height: 8)
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(Self.titles[provider.name] ?? provider.name)
                    if provider.needsKey && (!provider.hasKey || rejected), let link = Self.keyPages[provider.name] {
                        Link("Get a key", destination: link).font(.caption)
                    }
                }
                if case .rejected(let reason) = check {
                    Text(reason).font(.caption).foregroundStyle(.red).lineLimit(2)
                } else {
                    Text(provider.url).font(.caption).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
                }
            }
            Spacer()
            if !provider.needsKey {
                Text("No key needed").font(.callout).foregroundStyle(.secondary)
            } else if provider.hasKey && !rejected {
                status
                Button("Remove Key") { store.setKey(provider.name, "") }
            } else {
                SecureField("API key", text: $key, prompt: Text(rejected ? "Paste a new key" : "Paste API key"))
                    .labelsHidden()
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 160)
                    .onSubmit(save)
                Button("Save", action: save).disabled(key.isEmpty)
            }
            if !provider.builtin {
                Button("Remove \(provider.name)", systemImage: "minus.circle") { store.removeProvider(provider.name) }
                    .labelStyle(.iconOnly).buttonStyle(.borderless)
                    .help("Remove this provider and its key")
            }
        }
        .padding(.vertical, 2)
    }

    @ViewBuilder private var status: some View {
        switch check {
        case .works: Text("Key works").font(.callout).foregroundStyle(.secondary)
        case .unreachable(let reason):
            Text("Couldn't check").font(.callout).foregroundStyle(.orange).help(reason)
        default:
            HStack(spacing: 6) {
                ProgressView().controlSize(.small)
                Text("Checking…").font(.callout).foregroundStyle(.secondary)
            }
        }
    }

    private func save() {
        store.setKey(provider.name, key)
        key = ""
    }
}

/// Well-known OpenAI-compatible endpoints, to save looking up URLs.
struct ProviderPreset: Identifiable {
    var title: String
    var name: String
    var url: String
    var needsKey: Bool
    var id: String { title }

    static let custom = ProviderPreset(title: "Custom", name: "", url: "https://", needsKey: true)
    static let all = [
        ProviderPreset(title: "OpenRouter", name: "openrouter", url: "https://openrouter.ai/api/v1", needsKey: true),
        ProviderPreset(title: "OpenAI API", name: "openai", url: "https://api.openai.com/v1", needsKey: true),
        ProviderPreset(title: "Groq", name: "groq", url: "https://api.groq.com/openai/v1", needsKey: true),
        ProviderPreset(title: "Mistral", name: "mistral", url: "https://api.mistral.ai/v1", needsKey: true),
        ProviderPreset(title: "DeepSeek", name: "deepseek", url: "https://api.deepseek.com/v1", needsKey: true),
        ProviderPreset(title: "Together", name: "together", url: "https://api.together.xyz/v1", needsKey: true),
        ProviderPreset(title: "LM Studio", name: "lmstudio", url: "http://127.0.0.1:1234/v1", needsKey: false),
        ProviderPreset(title: "Ollama", name: "ollama", url: "http://127.0.0.1:11434/v1", needsKey: false),
    ]
}

private struct AddProviderMenu: View {
    @Environment(Store.self) private var store

    var body: some View {
        Menu {
            ForEach(ProviderPreset.all.filter { p in !store.providers.contains { $0.name == p.name } }) { p in
                Button(p.title) { store.addingProvider = p }
            }
            Divider()
            Button("Other Endpoint…") { store.addingProvider = .custom }
        } label: {
            Label("Add Provider", systemImage: "plus")
        }
        .menuStyle(.borderlessButton)
        .fixedSize()
    }
}

private struct AddProviderSheet: View {
    @Environment(Store.self) private var store
    @Environment(\.dismiss) private var dismiss
    let preset: ProviderPreset
    @State private var name = ""
    @State private var url = ""
    @State private var needsKey = true
    @State private var key = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Form {
                Section {
                    TextField("Name", text: $name, prompt: Text("e.g. groq"))
                    TextField("Base URL", text: $url, prompt: Text("https://…/v1"))
                    Toggle("Needs an API key", isOn: $needsKey)
                    if needsKey {
                        SecureField("API key", text: $key, prompt: Text("Can be added later"))
                    }
                } header: {
                    Text(preset.title == "Custom" ? "Add an OpenAI-compatible endpoint" : "Add \(preset.title)")
                } footer: {
                    Text("Models are \(name.isEmpty ? "name" : name):<model>. The list comes from the endpoint's /models.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            .formStyle(.grouped)
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction)
                Button("Add") {
                    if store.addProvider(name, url, needsKey: needsKey, key: key) { dismiss() }
                }
                .keyboardShortcut(.defaultAction)
                .disabled(name.isEmpty || url.count < 10)
            }
            .padding(12)
        }
        .frame(width: 440)
        .onAppear {
            name = preset.name
            url = preset.url
            needsKey = preset.needsKey
        }
    }
}
