import ArityCore
import SwiftUI

/// Providers from config.json with a field for each one's API key, and every
/// model they offer with a switch for whether tasks for agents may get it.
struct SettingsSheet: View {
    @Environment(Store.self) private var store
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Form {
                Section("Providers") {
                    ForEach(store.providers, id: \.name) { ProviderRow(provider: $0) }
                }
                Section {
                    ForEach(store.models, id: \.self) { model in
                        Toggle(shortName(model), isOn: Binding(
                            get: { store.workers.contains(model) },
                            set: { store.setWorker(model, $0) }))
                        .help(model)
                    }
                } header: {
                    HStack {
                        Text("Workers: models a task for an agent may be given, picked blind")
                        Spacer()
                        Button("Refresh", systemImage: "arrow.clockwise") { store.loadModels(refresh: true) }
                            .labelStyle(.iconOnly).buttonStyle(.borderless)
                            .help("Fetch each provider's model list again")
                    }
                }
            }
            .formStyle(.grouped)
            HStack {
                Text("Keys stay in keys.json in the data folder.").font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button("Done") { dismiss() }.keyboardShortcut(.defaultAction)
            }
            .padding(12)
        }
        .frame(width: 520, height: 480)
    }
}

private struct ProviderRow: View {
    @Environment(Store.self) private var store
    let provider: Provider
    @State private var key = ""

    var body: some View {
        LabeledContent {
            if provider.needsKey {
                HStack {
                    SecureField(provider.hasKey ? "Key saved" : "API key", text: $key)
                        .frame(minWidth: 160)
                        .onSubmit(save)
                    Button("Save", action: save).disabled(key.isEmpty)
                    if provider.hasKey {
                        Button("Remove") { store.setKey(provider.name, "") }
                    }
                }
            } else {
                Text("No key needed").foregroundStyle(.secondary)
            }
        } label: {
            Text(provider.name)
            Text(provider.url).font(.caption).foregroundStyle(.secondary)
        }
    }

    private func save() {
        store.setKey(provider.name, key)
        key = ""
    }
}
