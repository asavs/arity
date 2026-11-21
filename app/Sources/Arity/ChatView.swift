import ArityCore
import SwiftUI

struct ChatView: View {
    @Environment(Store.self) private var store
    @State private var message = ""
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
            if let card = store.draft {
                cardComposer(card)
            } else {
                composer(prompt: "Message", lines: 1...8).padding(10)
            }
        }
        .navigationTitle(store.selection ?? "")
        .navigationSubtitle(shortName(store.model))
        .onAppear { composing = true }
    }

    /// The message field and model picker. Collapsed to a line or so while a card is open.
    private func composer(prompt: String, lines: ClosedRange<Int>) -> some View {
        @Bindable var store = store
        return HStack(alignment: .bottom, spacing: 8) {
            Picker("Model", selection: $store.model) {
                ForEach(store.models, id: \.self) { Text(shortName($0)).tag($0) }
            }
            .labelsHidden()
            .fixedSize()

            TextField(prompt, text: $message, axis: .vertical)
                .textFieldStyle(.plain)
                .lineLimit(lines)
                .focused($composing)
                .onSubmit(send)

            Button("Send", systemImage: "arrow.up.circle.fill", action: send)
                .labelStyle(.iconOnly)
                .font(.title2)
                .buttonStyle(.borderless)
                .disabled(busy || message.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
        }
    }

    /// A task drafted in this conversation, where the composer usually sits.
    /// The person edits it directly or asks the model to; Add puts it on the board.
    private func cardComposer(_ card: TaskFields) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            composer(prompt: "Ask \(shortName(store.model)) to change the card", lines: 1...3)
            Divider()
            TaskCard(fields: Binding { store.draft ?? card } set: { store.editCard($0) })
                .disabled(busy)
            HStack {
                Button("Dismiss") { store.dismissCard() }
                Spacer()
                Text(card.assignee == "agent" ? "An agent is picked blind and reports back here"
                                              : "Goes on your own list")
                    .font(.caption).foregroundStyle(.secondary)
                Button("Add to Board") { store.addCard() }
                    .keyboardShortcut(.return, modifiers: .command)
                    .buttonStyle(.borderedProminent)
                    .disabled(busy || card.title.trimmingCharacters(in: .whitespaces).isEmpty)
                    .help("Add to the board (⌘↩)")
            }
        }
        .padding(14)
        .background(.background.secondary, in: RoundedRectangle(cornerRadius: 12))
        .overlay(RoundedRectangle(cornerRadius: 12).strokeBorder(.separator))
        .padding(10)
    }

    private func send() {
        let text = message.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !busy else { return }
        message = ""
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
        case .report:
            VStack(alignment: .leading, spacing: 4) {
                Label("Report", systemImage: "tray.and.arrow.down").font(.caption).foregroundStyle(.secondary)
                Text(message.text).textSelection(.enabled)
            }
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(.quaternary.opacity(0.5), in: RoundedRectangle(cornerRadius: 10))
        case .notice:
            Label(message.text, systemImage: "info.circle")
                .font(.callout)
                .foregroundStyle(.orange)
        case .taskCard:
            Label(message.text, systemImage: "rectangle.and.pencil.and.ellipsis")
                .font(.callout)
                .foregroundStyle(.secondary)
                .frame(maxWidth: .infinity)
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
