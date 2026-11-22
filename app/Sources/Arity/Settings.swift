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
                    if store.workers.isEmpty {
                        Text("No workers yet. Tasks for agents wait until you add one.").foregroundStyle(.secondary)
                    }
                    ForEach(store.workers, id: \.self) { model in
                        HStack {
                            Text(shortName(model)).help(model)
                            Spacer()
                            if !store.models.contains(model) {
                                Text("Not offered now").font(.caption).foregroundStyle(.tertiary)
                                    .help("None of your accounts lists this model at the moment.")
                            }
                            Button("Remove \(shortName(model))", systemImage: "minus.circle") { store.setWorker(model, false) }
                                .labelStyle(.iconOnly).buttonStyle(.borderless)
                        }
                    }
                    AddWorkerButton()
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
                ToolsSection()
                HooksSection()
            }
            .formStyle(.grouped)
            .sheet(isPresented: $store.addingHook, onDismiss: { store.editingHook = nil }) {
                AddHookSheet(existing: store.editingHook)
            }
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
        .onAppear {
            store.checkKeys()
            store.loadHarness()
        }
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

    private var usable: Bool {
        switch plan.access {
        case .open: return true
        case .signIn(let who): return who != nil
        case .key: return account.hasKey
        }
    }

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
                UsageLine(usage: plan.usage, account: account.title, usable: usable)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
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

/// What is left of a plan's limits, or why that cannot be shown. The same for every plan.
private struct UsageLine: View {
    let usage: Usage
    let account: String
    /// Set up, so a missing reading means no reply yet rather than no way to send one.
    let usable: Bool

    var body: some View {
        switch usage {
        case .unlimited:
            EmptyView()
        case .noneYet where !usable:
            EmptyView()
        case .notReported:
            Label("Usage not reported", systemImage: "gauge.with.dots.needle.bottom.0percent")
                .font(.caption).foregroundStyle(.tertiary)
                .help("\(account) doesn't tell other apps how much of this plan is used.")
        case .noneYet:
            Label("Usage shows after the first reply", systemImage: "gauge.with.dots.needle.bottom.50percent")
                .font(.caption).foregroundStyle(.tertiary)
        case .limits(let limits, let age):
            // The two closest to running out, as bars of what is used; the numbers are in the tooltip.
            let left = { (l: Limit) in l.limit == 0 ? 1 : Double(l.remaining) / Double(l.limit) }
            let tightest = limits.sorted { (left($0), $0.name) < (left($1), $1.name) }.prefix(2)
            HStack(spacing: 14) {
                ForEach(Array(tightest), id: \.name) { l in
                    HStack(spacing: 6) {
                        Text(Self.name(l.name)).font(.caption).foregroundStyle(.secondary).fixedSize()
                        UsageBar(left: left(l))
                    }
                }
            }
            .help(limits.map(Self.detail).joined(separator: "\n") + "\nAs of \(Self.duration(age)) ago.")
        }
    }

    /// "requests-1h" -> "requests an hour"; plain names are the provider's shortest window.
    static func name(_ raw: String) -> String {
        let parts = raw.split(separator: "-", maxSplits: 1).map(String.init)
        guard parts.count == 2 else { return raw }
        switch parts[1] {
        case "1h": return "\(parts[0]) an hour"
        case "1d": return "\(parts[0]) a day"
        case "1m", "1min": return "\(parts[0]) a minute"
        default: return "\(parts[0]) (\(parts[1]))"
        }
    }

    static func detail(_ l: Limit) -> String {
        let reset = l.resetsInSecs.map { ", full again in \(duration($0))" } ?? ""
        return "\(l.remaining.formatted()) of \(l.limit.formatted()) \(name(l.name)) left\(reset)"
    }

    static func duration(_ secs: UInt64) -> String {
        secs < 90 ? "\(secs)s" : secs < 5400 ? "\(secs / 60) min" : "\(secs / 3600) h"
    }
}

/// Opens a searchable list of every model your accounts offer, to add one as a worker.
private struct AddWorkerButton: View {
    @Environment(Store.self) private var store

    var body: some View {
        @Bindable var store = store
        Button("Add Worker…", systemImage: "plus") { store.choosingWorker = true }
            .buttonStyle(.borderless)
            .disabled(store.models.isEmpty)
            .popover(isPresented: $store.choosingWorker, arrowEdge: .bottom) {
                ModelChooser(models: store.models.filter { !store.workers.contains($0) }) { model in
                    store.setWorker(model, true)
                    store.choosingWorker = false
                }
            }
    }
}

/// Every model, grouped by provider and filtered as you type. A List only
/// builds the rows on screen, so hundreds of models cost nothing until seen.
struct ModelChooser: View {
    let models: [String]
    let choose: (String) -> Void
    @State private var query = ""

    private var groups: [(provider: String, models: [String])] {
        let words = query.lowercased().split(separator: " ")
        let shown = models.filter { m in words.allSatisfy { m.lowercased().contains($0) } }
        let byProvider = Dictionary(grouping: shown) { String($0.prefix { $0 != ":" }) }
        return byProvider.keys.sorted().map { ($0, byProvider[$0]!) }
    }

    var body: some View {
        VStack(spacing: 0) {
            TextField("Search \(models.count) models", text: $query)
                .textFieldStyle(.roundedBorder)
                .padding(10)
            List {
                ForEach(groups, id: \.provider) { group in
                    Section("\(group.provider) · \(group.models.count)") {
                        ForEach(group.models, id: \.self) { model in
                            Button { choose(model) } label: {
                                Text(model.split(separator: ":", maxSplits: 1).last.map(String.init) ?? model)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    .contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }
            }
            .listStyle(.sidebar)
        }
        .frame(width: 360, height: 420)
    }
}

/// How much of a limit is used: fills up as it is used, orange then red as it runs low.
private struct UsageBar: View {
    /// The share still left, 0 to 1.
    let left: Double

    var body: some View {
        let used = min(max(1 - left, 0), 1)
        ZStack(alignment: .leading) {
            Capsule().fill(.quaternary)
            Capsule().fill(left < 0.1 ? Color.red : left < 0.3 ? .orange : .green)
                .frame(width: max(used * 72, used > 0 ? 4 : 0))
        }
        .frame(width: 72, height: 5)
        .accessibilityLabel("\(Int((used * 100).rounded()))% used")
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

private struct ToolsSection: View {
    @Environment(Store.self) private var store

    private var contextLabel: String {
        if let name = store.selection {
            return store.folder == nil ? "\(name) · no folder" : "\(name) · folder attached"
        }
        return "Default chat · no folder"
    }

    var body: some View {
        Section {
            ForEach(store.tools, id: \.name) { tool in
                ToolRow(tool: tool)
            }
        } header: {
            HStack {
                Text("Tools")
                Spacer()
                Text(contextLabel)
                    .font(.caption)
                    .foregroundStyle(.tertiary)
            }
        } footer: {
            Text("Green dots are offered to the current thread. File and bash tools turn on automatically when a folder is attached; atomic tools run inside the journal transaction, while external tools commit intent first.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }
}

private struct ToolRow: View {
    let tool: ToolInfo

    private var shortDescription: String {
        if let dot = tool.description.firstIndex(of: ".") {
            return String(tool.description[...dot])
        }
        return tool.description
    }

    var body: some View {
        HStack(spacing: 10) {
            Circle()
                .fill(tool.available ? Color.green : Color.secondary.opacity(0.35))
                .frame(width: 8, height: 8)
                .frame(width: 16)
                .help(tool.available ? "Available in this thread" : "Not offered in this thread (needs a folder or worker run)")

            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(tool.name)
                        .font(.body.monospaced())
                        .foregroundStyle(tool.available ? .primary : .secondary)
                    Text(tool.execution)
                        .font(.caption2.monospaced())
                        .foregroundStyle(.secondary)
                        .padding(.horizontal, 5)
                        .padding(.vertical, 1)
                        .background(.quaternary, in: Capsule())
                }
                if !shortDescription.isEmpty {
                    Text(shortDescription)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                        .truncationMode(.tail)
                        .help(tool.description)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.vertical, 2)
    }
}

private struct HooksSection: View {
    @Environment(Store.self) private var store

    var body: some View {
        Section {
            ForEach(store.builtinHooks, id: \.name) { hook in
                BuiltinHookRow(hook: hook)
            }
            ForEach(store.hooks, id: \.name) { hook in
                HookRow(hook: hook)
            }
            Button("Add Hook…", systemImage: "plus") {
                store.editingHook = nil
                store.addingHook = true
            }
            .buttonStyle(.borderless)
        } header: {
            Text("Hooks")
        } footer: {
            Text("Custom hooks run as shell commands with JSON context on stdin: exit non-zero to block before_tool or retry on_yield, or print JSON on stdout to rewrite args or write journal notes.")
                .font(.caption).foregroundStyle(.secondary)
        }
    }
}

private struct BuiltinHookRow: View {
    let hook: BuiltinHookInfo

    var body: some View {
        HStack(spacing: 10) {
            Circle()
                .fill(Color.green)
                .frame(width: 8, height: 8)
                .frame(width: 16)
                .help("Built-in hook (always active)")

            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(hook.name)
                    Text("built-in")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .padding(.horizontal, 5)
                        .padding(.vertical, 1)
                        .background(.quaternary.opacity(0.6), in: Capsule())
                    Text(hook.event.badge)
                        .font(.caption2.monospaced())
                        .foregroundStyle(.secondary)
                        .padding(.horizontal, 5)
                        .padding(.vertical, 1)
                        .background(.quaternary, in: Capsule())
                    if let tool = hook.tool, !tool.isEmpty {
                        Text(tool)
                            .font(.caption2.monospaced())
                            .foregroundStyle(.secondary)
                            .padding(.horizontal, 5)
                            .padding(.vertical, 1)
                            .background(.quaternary.opacity(0.7), in: Capsule())
                            .lineLimit(1)
                    }
                }
                Text(hook.description)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.tail)
                    .help(hook.description)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(.vertical, 2)
    }
}

private struct HookRow: View {
    @Environment(Store.self) private var store
    let hook: HookConfig

    var body: some View {
        HStack(spacing: 10) {
            Toggle("Enable \(hook.name)", isOn: Binding(
                get: { hook.enabled },
                set: { store.setHookEnabled(hook.name, $0) }
            ))
            .labelsHidden()
            .controlSize(.small)

            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(hook.name)
                        .foregroundStyle(hook.enabled ? .primary : .secondary)
                    Text(hook.event.badge)
                        .font(.caption2.monospaced())
                        .foregroundStyle(.secondary)
                        .padding(.horizontal, 5)
                        .padding(.vertical, 1)
                        .background(.quaternary, in: Capsule())
                    if let tool = hook.tool, !tool.isEmpty {
                        Text(tool)
                            .font(.caption2.monospaced())
                            .foregroundStyle(.secondary)
                            .padding(.horizontal, 5)
                            .padding(.vertical, 1)
                            .background(.quaternary.opacity(0.7), in: Capsule())
                            .lineLimit(1)
                    }
                }
                Text(hook.command)
                    .font(.caption.monospaced())
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.tail)
                    .help(hook.command)
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            Button("Edit \(hook.name)", systemImage: "pencil") {
                store.editingHook = hook
                store.addingHook = true
            }
            .labelStyle(.iconOnly)
            .buttonStyle(.borderless)
            .help("Edit hook")

            Button("Remove \(hook.name)", systemImage: "minus.circle") {
                store.removeHook(hook.name)
            }
            .labelStyle(.iconOnly)
            .buttonStyle(.borderless)
            .help("Remove hook")
        }
        .padding(.vertical, 2)
    }
}

private struct HookPreset: Identifiable {
    var title: String
    var name: String
    var event: HookEvent
    var tool: String
    var command: String
    var id: String { name }

    static let all = [
        HookPreset(
            title: "Read-only guard",
            name: "read-only",
            event: .beforeTool,
            tool: "write_file,edit_file,bash",
            command: #"echo "Read-only mode: edits and shell commands are disabled." >&2; exit 1"#
        ),
        HookPreset(
            title: "Cargo check on yield",
            name: "cargo-check",
            event: .onYield,
            tool: "",
            command: "cargo check"
        ),
        HookPreset(
            title: "Format Rust on edit",
            name: "rustfmt",
            event: .afterTool,
            tool: "edit_file,write_file",
            command: "cargo fmt"
        ),
    ]
}

private struct AddHookSheet: View {
    @Environment(Store.self) private var store
    @Environment(\.dismiss) private var dismiss
    let existing: HookConfig?
    @State private var name: String
    @State private var event: HookEvent
    @State private var tool: String
    @State private var command: String
    @State private var enabled: Bool
    @State private var saveError: String?

    init(existing: HookConfig?) {
        self.existing = existing
        _name = State(initialValue: existing?.name ?? "")
        _event = State(initialValue: existing?.event ?? .beforeTool)
        _tool = State(initialValue: existing?.tool ?? "")
        _command = State(initialValue: existing?.command ?? "")
        _enabled = State(initialValue: existing?.enabled ?? true)
    }

    private var canSave: Bool {
        !name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            && !command.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Form {
                Section {
                    HStack(spacing: 8) {
                        ForEach(HookPreset.all) { preset in
                            Button(preset.title) {
                                name = preset.name
                                event = preset.event
                                tool = preset.tool
                                command = preset.command
                                enabled = true
                                saveError = nil
                            }
                            .controlSize(.small)
                        }
                    }
                } header: {
                    Text("Quick Presets")
                }

                Section {
                    TextField("Name", text: $name, prompt: Text("e.g. cargo-check"))
                    Picker("Event", selection: $event) {
                        ForEach(HookEvent.all, id: \.self) { e in
                            Text(e.title).tag(e)
                        }
                    }
                    TextField("Tool filter", text: $tool, prompt: Text("All tools, or e.g. bash, edit_file"))
                    VStack(alignment: .leading, spacing: 6) {
                        Text("Command")
                        TextField("Command", text: $command, prompt: Text("Shell command (receives JSON on stdin)"), axis: .vertical)
                            .labelsHidden()
                            .font(.body.monospaced())
                            .lineLimit(3...6)
                            .textFieldStyle(.roundedBorder)
                    }
                    Toggle("Enabled", isOn: $enabled)
                } header: {
                    Text(existing == nil ? "Add Hook" : "Edit Hook")
                } footer: {
                    Text("Names use letters, digits, dashes, or underscores. Commands receive JSON hook context on stdin.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            .formStyle(.grouped)
            HStack {
                if let saveError {
                    Label(saveError, systemImage: "exclamationmark.triangle")
                        .foregroundStyle(.red)
                        .font(.caption)
                        .lineLimit(2)
                }
                Spacer()
                Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction)
                Button("Save") { save() }
                    .keyboardShortcut(.defaultAction)
                    .disabled(!canSave)
            }
            .padding(12)
        }
        .frame(width: 480)
    }

    private func save() {
        let trimmedName = name.trimmingCharacters(in: .whitespacesAndNewlines)
        let trimmedTool = tool.trimmingCharacters(in: .whitespacesAndNewlines)
        let trimmedCommand = command.trimmingCharacters(in: .whitespacesAndNewlines)
        let hook = HookConfig(
            name: trimmedName,
            event: event,
            tool: trimmedTool.isEmpty ? nil : trimmedTool,
            command: trimmedCommand,
            enabled: enabled
        )
        if store.saveHook(hook) {
            if let oldName = existing?.name, oldName != trimmedName {
                store.removeHook(oldName)
            }
            dismiss()
        } else {
            saveError = store.error
        }
    }
}

private extension HookEvent {
    static let all: [HookEvent] = [.beforeTool, .afterTool, .onYield, .afterTurn]

    var badge: String {
        switch self {
        case .beforeTool: "before_tool"
        case .afterTool: "after_tool"
        case .onYield: "on_yield"
        case .afterTurn: "after_turn"
        }
    }

    var title: String {
        switch self {
        case .beforeTool: "Before Tool"
        case .afterTool: "After Tool"
        case .onYield: "On Yield"
        case .afterTurn: "After Turn"
        }
    }
}
