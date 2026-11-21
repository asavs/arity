import ArityCore
import SwiftUI

struct ChatView: View {
    @Environment(Store.self) private var store
    @State private var draft = ""
    @FocusState private var composing: Bool

    private var busy: Bool { store.selection.map(store.running.contains) ?? false }

    var body: some View {
        @Bindable var store = store
        VStack(spacing: 0) {
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 10) {
                        ForEach(store.messages, id: \.id) { MessageRow(message: $0) }
                        if busy {
                            ProgressView().controlSize(.small).id("busy")
                        }
                    }
                    .padding()
                }
                .defaultScrollAnchor(.bottom)
                .onChange(of: store.messages.count) {
                    withAnimation { proxy.scrollTo(busy ? "busy" : store.messages.last?.id, anchor: .bottom) }
                }
            }

            if let error = store.error {
                HStack(alignment: .top) {
                    Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
                    Text(error).font(.callout).textSelection(.enabled)
                    Spacer()
                    Button("Retry") { store.resume() }.disabled(busy)
                    Button("Dismiss") { store.error = nil }
                }
                .padding(10)
                .background(.orange.opacity(0.1))
            }

            Divider()
            HStack(alignment: .bottom, spacing: 8) {
                Picker("Model", selection: $store.model) {
                    ForEach(store.models, id: \.self) { Text(shortName($0)).tag($0) }
                }
                .labelsHidden()
                .fixedSize()

                TextField("Message", text: $draft, axis: .vertical)
                    .textFieldStyle(.plain)
                    .lineLimit(1...8)
                    .focused($composing)
                    .onSubmit(send)

                Button("Send", systemImage: "arrow.up.circle.fill", action: send)
                    .labelStyle(.iconOnly)
                    .font(.title2)
                    .buttonStyle(.borderless)
                    .disabled(busy || draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
            .padding(10)
        }
        .navigationTitle(store.selection ?? "")
        .navigationSubtitle(shortName(store.model))
        .onAppear { composing = true }
    }

    private func send() {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !busy else { return }
        draft = ""
        store.send(text)
    }
}

struct MessageRow: View {
    @Environment(Store.self) private var store
    let message: Message

    var body: some View {
        content
            .contextMenu {
                Button("Fork From Here") { store.fork(at: message.entry) }
                Button("Copy") {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(message.text, forType: .string)
                }
            }
            .help("Entry #\(message.entry)")
    }

    @ViewBuilder private var content: some View {
        switch message.kind {
        case .user:
            HStack {
                Spacer(minLength: 60)
                Text(message.text)
                    .textSelection(.enabled)
                    .padding(.horizontal, 12).padding(.vertical, 8)
                    .background(.tint.opacity(0.15), in: RoundedRectangle(cornerRadius: 12))
            }
        case .assistant:
            VStack(alignment: .leading, spacing: 3) {
                Text(shortName(message.model)).font(.caption).foregroundStyle(.secondary)
                Text(markdown(message.text)).textSelection(.enabled)
            }
            .padding(.trailing, 60)
        case .toolCall:
            Label(message.text, systemImage: "wrench.and.screwdriver")
                .font(.callout.monospaced())
                .foregroundStyle(.secondary)
        case .toolResult:
            Text("→ " + message.text)
                .font(.callout.monospaced())
                .foregroundStyle(.secondary)
                .textSelection(.enabled)
                .padding(.leading, 26)
        }
    }
}

/// "mlx:mlx-community/Qwen3.5-4B-MLX-4bit" -> "Qwen3.5-4B-MLX-4bit · mlx"
func shortName(_ spec: String?) -> String {
    guard let spec, let colon = spec.firstIndex(of: ":") else { return spec ?? "model" }
    let provider = spec[..<colon]
    let name = spec[spec.index(after: colon)...].split(separator: "/").last.map(String.init) ?? ""
    return "\(name) · \(provider)"
}

private func markdown(_ text: String) -> AttributedString {
    (try? AttributedString(markdown: text, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)))
        ?? AttributedString(text)
}
