import ArityCore
import SwiftUI

/// Where models come from: your accounts, each with a key and the plans it
/// opens, and which models tasks for agents may be given.
struct SettingsSheet: View {
    @Environment(Store.self) private var store
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        @Bindable var store = store
        VStack(spacing: 0) {
            Form {
                ForEach(store.accounts, id: \.name) { AccountSection(account: $0) }
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
                AddProviderMenu()
                if let error = store.error {
                    Label(error, systemImage: "exclamationmark.triangle").foregroundStyle(.red).lineLimit(2)
                }
                Spacer()
                Button("Done") { dismiss() }.keyboardShortcut(.defaultAction)
            }
            .padding(12)
        }
        .frame(width: 560, height: 680)
        .onAppear { store.checkKeys() }
        .sheet(item: $store.addingProvider) { AddProviderSheet(preset: $0) }
    }
}

/// An account: its key, if it takes one, then a row per plan.
private struct AccountSection: View {
    @Environment(Store.self) private var store
    let account: Account

    var body: some View {
        Section {
            // Plans that need no key come first, so the key sits just above the plans it opens.
            ForEach(account.plans.filter { $0.access != .key }, id: \.provider) { PlanRow(account: account, plan: $0) }
            if let owner = account.keyFor { KeyRow(account: account, owner: owner) }
            ForEach(account.plans.filter { $0.access == .key }, id: \.provider) { PlanRow(account: account, plan: $0) }
        } header: {
            HStack {
                Text(account.title)
                Spacer()
                if account.removable {
                    Button("Remove \(account.title)", systemImage: "minus.circle") { store.removeProvider(account.name) }
                        .labelStyle(.iconOnly).buttonStyle(.borderless)
                        .help("Remove this account and its key")
                }
            }
        }
    }
}

private struct KeyRow: View {
    @Environment(Store.self) private var store
    let account: Account
    /// The provider the key is stored for.
    let owner: String
    @State private var key = ""

    /// The key's own plan refused it, so a new one is wanted.
    private var refused: Bool { if case .rejected = store.keyChecks[owner] { return true }; return false }

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "key").foregroundStyle(.secondary).frame(width: 16)
            Text("API key")
            if !account.hasKey || refused, let page = account.keyPage.flatMap(URL.init(string:)) {
                Link("Get a key", destination: page).font(.caption)
            }
            Spacer()
            if account.hasKey && !refused {
                Text("Saved").font(.callout).foregroundStyle(.secondary)
                Button("Remove Key") { store.setKey(owner, "") }
            } else {
                SecureField("API key", text: $key, prompt: Text(refused ? "Paste a new key" : "Paste API key"))
                    .labelsHidden()
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 180)
                    .onSubmit(save)
                Button("Save", action: save).disabled(key.isEmpty)
            }
        }
        .padding(.vertical, 2)
    }

    private func save() {
        store.setKey(owner, key)
        key = ""
    }
}

/// A plan: whether it works, what it costs or why it is refused, and what to do about it.
private struct PlanRow: View {
    @Environment(Store.self) private var store
    let account: Account
    let plan: Plan

    private var check: KeyCheck? { store.keyChecks[plan.provider] }

    private var dot: Color {
        switch plan.access {
        case .open: return .green
        case .signIn(let who): return who == nil ? .secondary.opacity(0.4) : .green
        case .key:
            guard account.hasKey else { return .secondary.opacity(0.4) }
            switch check {
            case .works: return .green
            case .rejected: return .red
            case .unreachable: return .orange
            case .noKey, nil: return .secondary.opacity(0.4)
            }
        }
    }

    var body: some View {
        HStack(spacing: 10) {
            Circle().fill(dot).frame(width: 8, height: 8).frame(width: 16)
            VStack(alignment: .leading, spacing: 2) {
                Text(plan.title)
                if case .rejected(let reason) = check, account.hasKey {
                    Text(reason).font(.caption).foregroundStyle(.red).lineLimit(2)
                } else {
                    Text(plan.note).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                }
            }
            Spacer()
            trailing
        }
        .padding(.vertical, 2)
    }

    @ViewBuilder private var trailing: some View {
        switch plan.access {
        case .open:
            EmptyView()
        case .signIn(let who):
            if store.signingIn {
                ProgressView().controlSize(.small)
                Text("Finish in your browser…").font(.callout).foregroundStyle(.secondary)
                Button("Cancel") { store.cancelSignIn() }
            } else if let who {
                Text(who).font(.callout).foregroundStyle(.secondary).lineLimit(1)
                Button("Sign Out") { store.signOutOfChatGPT() }
            } else {
                Button("Sign In with ChatGPT") { store.signInToChatGPT() }.buttonStyle(.borderedProminent)
            }
        case .key:
            if !account.hasKey {
                Text("Needs the key").font(.callout).foregroundStyle(.secondary)
            } else {
                switch check {
                case .works: Text("Works").font(.callout).foregroundStyle(.secondary)
                case .rejected:
                    if let page = plan.page.flatMap(URL.init(string:)) { Link("Subscribe", destination: page).font(.callout) }
                case .unreachable(let reason):
                    Text("Couldn't check").font(.callout).foregroundStyle(.orange).help(reason)
                case .noKey, nil:
                    ProgressView().controlSize(.small)
                    Text("Checking…").font(.callout).foregroundStyle(.secondary)
                }
            }
        }
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
            ForEach(ProviderPreset.all.filter { p in !store.accounts.contains { $0.plans.contains { $0.provider == p.name } } }) { p in
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
