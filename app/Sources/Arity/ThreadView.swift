import ArityCore
import SwiftUI

/// The selected thread: what was said in it, and below that whatever it needs
/// next. For a chat that is the composer, with a task card when one is being
/// drafted. For a task it is the task: its card, how its run is going, or the
/// stars to rate the result.
struct ThreadView: View {
    @Environment(Store.self) private var store
    @State private var message = ""
    @FocusState private var composing: Bool
    /// The view is scrolled to the end, so a growing reply keeps it there. Scrolling up lets go.
    @State private var pinned = true

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
                .onScrollGeometryChange(for: Bool.self) { g in
                    g.contentOffset.y + g.containerSize.height >= g.contentSize.height - 40
                } action: { _, atEnd in pinned = atEnd }
                .onChange(of: store.messages.last?.text) {
                    if pinned { proxy.scrollTo(busy ? "busy" : store.messages.last?.id, anchor: .bottom) }
                }
                .onChange(of: store.messages.count) {
                    if pinned { proxy.scrollTo(busy ? "busy" : store.messages.last?.id, anchor: .bottom) }
                }
                .onChange(of: store.selection) { pinned = true }
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
            if let task = store.thread?.task {
                TaskDock(task: task).id(task.id)
            } else if let card = store.draft {
                cardComposer(card)
            } else {
                composer(prompt: "Message", lines: 1...8).padding(10)
            }
        }
        .navigationTitle(store.thread?.title ?? "New chat")
        .navigationSubtitle(store.thread?.task.map { details($0, me: store.me) } ?? chatSubtitle)
        .onAppear { composing = true }
        .onChange(of: store.unsent) {
            if let text = store.unsent { message = text; store.unsent = nil }
        }
    }

    /// Which model answers, quietly: the one picked and why, or what the choice
    /// will pick before the chat's first message.
    private var chatSubtitle: String {
        if let choice = store.choice, store.messages.isEmpty || store.model.isEmpty { return choice.blurb }
        return [shortName(store.model), store.pickReason].compactMap { $0 }.joined(separator: " · ")
    }

    /// Smart, fast, free or private, and Other… to name a model. Arity picks the
    /// model when the chat's first message is sent.
    private var modelChoice: some View {
        @Bindable var store = store
        return HStack(spacing: 4) {
            Picker("Model", selection: $store.choice) {
                ForEach(Choice.all, id: \.self) { Text($0.title).tag(Optional($0)).help($0.blurb) }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .fixedSize()
            .help(store.choice?.blurb ?? "Pick for me instead of naming a model")

            Button(store.choice == nil ? shortName(store.model) : "Other…") { store.choosingModel = true }
                .buttonStyle(.borderless)
                .foregroundStyle(store.choice == nil ? .primary : .secondary)
                .lineLimit(1)
                .help("Name a model yourself")
                .popover(isPresented: $store.choosingModel, arrowEdge: .bottom) {
                    ModelChooser(models: store.models) { store.chooseModel($0) }
                }
        }
    }

    /// The message field and model choice. Collapsed to a line or so while a card is open.
    private func composer(prompt: String, lines: ClosedRange<Int>) -> some View {
        @Bindable var store = store
        return HStack(alignment: .bottom, spacing: 8) {
            modelChoice

            if store.draft == nil {
                Button("Add a Task Card", systemImage: "checklist") { store.attachCard() }
                    .labelStyle(.iconOnly)
                    .buttonStyle(.borderless)
                    .keyboardShortcut("t")
                    .disabled(busy)
                    .help("Draft a task in this chat (⌘T)")
            }

            TextField(prompt, text: $message, axis: .vertical)
                .textFieldStyle(.plain)
                .lineLimit(lines)
                .focused($composing)
                .onSubmit(send)
                .onExitCommand { if busy { store.stop() } }

            // While a turn runs, Stop sits where Send is, until something is typed to send after it.
            if busy && message.isEmpty {
                Button("Stop", systemImage: "stop.circle.fill") { store.stop() }
                    .labelStyle(.iconOnly)
                    .font(.title2)
                    .buttonStyle(.borderless)
                    .keyboardShortcut(.cancelAction)
                    .help("Stop the reply (Esc)")
            } else {
                Button("Send", systemImage: "arrow.up.circle.fill", action: send)
                    .labelStyle(.iconOnly)
                    .font(.title2)
                    .buttonStyle(.borderless)
                    .disabled(message.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
    }

    /// A task drafted in this conversation, where the composer usually sits.
    /// The person edits it directly or asks the model to; Add puts it on the board.
    private func cardComposer(_ card: TaskFields) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            composer(prompt: store.model.isEmpty ? "Ask to change the card" : "Ask \(shortName(store.model)) to change the card", lines: 1...3)
            Divider()
            TaskCard(fields: Binding { store.draft ?? card } set: { store.editCard($0) })
                .disabled(busy)
            HStack {
                Button("Dismiss") { store.dismissCard() }
                Spacer()
                Text(card.assignee == "agent" ? "An agent is picked blind; you rate its result"
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
        guard !text.isEmpty else { return }
        message = ""
        store.send(text)
    }
}

/// What sits under a task's thread instead of a composer.
private struct TaskDock: View {
    @Environment(Store.self) private var store
    let task: BoardTask
    @State private var edited: TaskFields?

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            switch task.runState {
            case nil: card
            case .running?: Label("An agent is on it", systemImage: "sparkles").foregroundStyle(.secondary)
            case .waiting?: Label("Waiting on its sub-tasks", systemImage: "hourglass").foregroundStyle(.secondary)
            case .failing?:
                Label(task.error ?? "The last attempt failed", systemImage: "exclamationmark.arrow.circlepath")
                    .foregroundStyle(.orange).textSelection(.enabled)
            case .needsRating?: RateControl(task: task)
            case .rated?: rating
            }
            if let origin = task.origin {
                Button("Filed in \(origin)") { store.selection = origin }
                    .buttonStyle(.link).font(.caption)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(14)
    }

    /// Not started, or the user's own: the card, editable. For: An agent hands it off.
    @ViewBuilder private var card: some View {
        let mine = task.fields.assignee != "agent"
        TaskCard(fields: Binding { edited ?? task.fields } set: { edited = $0 }, editing: task.id)
        HStack {
            if mine || task.state == .done {
                Button(task.state == .done ? "Reopen" : "Mark Done") { store.setDone(task, task.state != .done) }
            }
            Spacer()
            Button("Save") {
                if let edited { store.updateTask(task.id, edited) }
                edited = nil
            }
            .keyboardShortcut(.return, modifiers: .command)
            .buttonStyle(.borderedProminent)
            .disabled(edited == nil || edited == task.fields)
        }
    }

    /// A finished run: its stars, who did it, and the note.
    private var rating: some View {
        let score = Int(task.score ?? 0)
        return HStack(spacing: 8) {
            Text(String(repeating: "★", count: score) + String(repeating: "☆", count: 5 - score))
                .foregroundStyle(.yellow)
            Text("Done by \(shortName(task.model))").bold()
            if let note = task.note, !note.isEmpty { Text("“\(note)”").foregroundStyle(.secondary) }
        }
        .font(.callout)
    }
}

/// Pick 1–5, say why, rate. The model is revealed once the rating is in.
/// Rating is never required: Skip closes the task without one, and it can still be rated later.
private struct RateControl: View {
    @Environment(Store.self) private var store
    let task: BoardTask
    @State private var score: Int64 = 0
    @State private var note = ""

    var body: some View {
        HStack(spacing: 10) {
            HStack(spacing: 2) {
                ForEach(1...5, id: \.self) { n in
                    Button { score = Int64(n) } label: {
                        Image(systemName: Int64(n) <= score ? "star.fill" : "star").foregroundStyle(.yellow)
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("\(n) of 5")
                }
            }
            .font(.title3)
            TextField("How did it do?", text: $note, axis: .vertical)
                .textFieldStyle(.plain)
                .lineLimit(1...4)
                .onSubmit(rate)
            if task.state != .done {
                Button("Skip") { store.setDone(task, true) }
                    .buttonStyle(.link)
                    .help("Close the task without rating it")
            }
            Button("Rate", action: rate)
                .buttonStyle(.borderedProminent)
                .disabled(score == 0)
        }
    }

    private func rate() {
        guard score > 0, let run = task.run else { return }
        store.rate(run: run, score: score, note: note)
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
                Label("Task result", systemImage: "tray.and.arrow.down").font(.caption).foregroundStyle(.secondary)
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
