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
                    if !store.errorIsWorktree { Button("Retry") { store.retry() }.disabled(busy) }
                    Button("Dismiss") { store.error = nil }
                }
                .padding(10)
                .background(.orange.opacity(0.1))
            } else if store.canContinue && !busy {
                continueBar
            }

            Divider()
            if let task = store.thread?.task {
                if task.runState != nil {
                    composer(prompt: workerPrompt(task), lines: 1...5)
                        .padding(.horizontal, 14)
                        .padding(.top, 10)
                        .padding(.bottom, 6)
                    Divider()
                }
                if let diff = store.worktreeDiff {
                    WorktreeDiffBar(diff: diff, running: busy || task.runState == .running || task.runState == .failing)
                        .id(task.id)
                    Divider()
                }
                TaskDock(task: task).id(task.id)
            } else if let card = store.draft {
                cardComposer(card)
            } else {
                composer(prompt: "Message", lines: 1...8).padding(10)
            }
        }
        .navigationTitle(store.thread?.title ?? "New chat")
        .navigationSubtitle(store.thread?.task.map { details($0, me: store.me) } ?? chatSubtitle)
        .toolbar {
            ToolbarItemGroup {
                if let usage = store.conversationUsage, usage.repliesWithUsage > 0 {
                    UsagePill(usage: usage)
                }
                Menu("Thread Actions", systemImage: "ellipsis.circle") {
                    Group {
                        Button("Compact Context") { store.compact() }
                            .help("Replace what the model reads with a summary it writes; every message stays here")
                        Button("Reset Context") { store.resetContext() }
                            .help("The model starts fresh from here; every message stays here and can be forked")
                    }
                    .disabled(busy || store.messages.isEmpty)
                    if store.activeChildren > 0 {
                        Divider()
                        Button(stopAllTitle) { store.stopAll() }
                            .help("Stop this thread and every task it handed out; each can be continued later")
                    }
                }
                .help("Compact or reset the model's context, or stop its sub-tasks")
            }
        }
        .onAppear { composing = true }
        .onChange(of: store.unsent) {
            if let text = store.unsent { message = text; store.unsent = nil }
        }
    }

    private var stopAllTitle: String {
        let n = store.activeChildren
        return "Stop All, Including \(n) Sub-task\(n == 1 ? "" : "s")"
    }

    /// Under the transcript when the thread stopped short: why, and Continue, which carries it on
    /// from there without sending anything.
    private var continueBar: some View {
        HStack(spacing: 8) {
            Image(systemName: "pause.circle").foregroundStyle(.secondary)
            Text(continueReason).font(.callout).foregroundStyle(.secondary)
            Spacer()
            Button("Continue", systemImage: "play.fill") { store.continueTurn() }
                .buttonStyle(.borderedProminent)
                .controlSize(.small)
                .keyboardShortcut("r", modifiers: [.command, .shift])
                .help("Carry on from where it stopped, without sending a message (⇧⌘R)")
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 6)
    }

    private var continueReason: String {
        if store.thread?.task?.runState == .paused { return "The agent was stopped part way" }
        if store.thread?.task?.runState == .failing { return "The last attempt failed" }
        switch store.messages.last?.kind {
        case .report?: return "A task result is waiting for an answer"
        case .user?: return "Your message has not been answered"
        case .toolResult?: return "Stopped between steps"
        default: return "Stopped part way"
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

    /// The folder attached to this chat, as a chip with × to detach it, or a button to attach one.
    @ViewBuilder private var folderButton: some View {
        if let folder = store.folder {
            HStack(spacing: 3) {
                Image(systemName: "folder")
                Text(URL(fileURLWithPath: folder).lastPathComponent).lineLimit(1)
                Button("Detach Folder", systemImage: "xmark.circle.fill") { store.setFolder(nil) }
                    .labelStyle(.iconOnly)
                    .buttonStyle(.plain)
                    .foregroundStyle(.secondary)
                    .disabled(busy)
                    .help("Detach the folder")
            }
            .font(.caption)
            .padding(.horizontal, 7).padding(.vertical, 3)
            .background(.quaternary, in: Capsule())
            .fixedSize()
            .help("Attached to \(folder). Gives this chat and the tasks filed here file and shell tools (list_files, read_file, search_files, write_file, edit_file, bash).")
        } else {
            Button("Attach a Folder", systemImage: "folder.badge.plus") { chooseFolder() }
                .labelStyle(.iconOnly)
                .buttonStyle(.borderless)
                .disabled(busy)
                .help("Attach a folder to give this chat, and the tasks filed in it, file and shell tools (list_files, read_file, search_files, write_file, edit_file, bash)")
        }
    }

    private func chooseFolder() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.prompt = "Attach"
        panel.message = "Gives this chat, and tasks filed in it, file and shell tools (list_files, read_file, search_files, write_file, edit_file, bash) in this folder."
        if panel.runModal() == .OK, let url = panel.url { store.setFolder(url.path) }
    }

    private func workerPrompt(_ task: BoardTask) -> String {
        switch task.runState {
        case .needsRating?: "Ask the agent before rating"
        case .rated?: "Ask \(shortName(task.model))"
        default: "Message the agent"
        }
    }

    /// The message field and model choice. Collapsed to a line or so while a card is open.
    private func composer(prompt: String, lines: ClosedRange<Int>) -> some View {
        @Bindable var store = store
        return HStack(alignment: .bottom, spacing: 8) {
            if !store.isWorkerRun {
                modelChoice
                folderButton

                if store.draft == nil {
                    Button("Add a Task Card", systemImage: "checklist") { store.attachCard() }
                        .labelStyle(.iconOnly)
                        .buttonStyle(.borderless)
                        .keyboardShortcut("t")
                        .disabled(busy)
                        .help("Draft a task in this chat (⌘T)")
                }
            }

            TextField(prompt, text: $message, axis: .vertical)
                .textFieldStyle(.plain)
                .lineLimit(lines)
                .focused($composing)
                .onSubmit { send() }
                .onKeyPress(.return, phases: .down) { press in
                    // ⌥↩ after this turn; ↩ alone steers.
                    if press.modifiers.contains(.option) { send(.afterTurn); return .handled }
                    return .ignored
                }
                .onExitCommand { if busy { store.stop() } }

            // While a turn runs, Stop sits where Send is, until something is typed to send after it.
            if busy && message.isEmpty && store.activeChildren > 0 {
                // Its sub-tasks run on their own: Stop leaves them going; Stop All stops them too.
                Menu {
                    Button("Stop This Reply  ⎋") { store.stop() }
                    Button(stopAllTitle) { store.stopAll() }
                } label: {
                    Image(systemName: "stop.circle.fill")
                } primaryAction: {
                    store.stop()
                }
                .menuStyle(.button)
                .menuIndicator(.visible)
                .font(.title2)
                .buttonStyle(.borderless)
                .fixedSize()
                .keyboardShortcut(.cancelAction)
                .help("Stop the reply (Esc); or stop it and every task it handed out")
            } else if busy && message.isEmpty {
                Button("Stop", systemImage: "stop.circle.fill") { store.stop() }
                    .labelStyle(.iconOnly)
                    .font(.title2)
                    .buttonStyle(.borderless)
                    .keyboardShortcut(.cancelAction)
                    .help("Stop the reply (Esc)")
            } else {
                Menu {
                    if busy {
                        Button("Steer: Cut In Now  ↩") { send(.steer) }
                        Button("Send After This Turn  ⌥↩") { send(.afterTurn) }
                    }
                    Button("Leave a Note for Its Next Turn") { send(.inbox) }
                } label: {
                    Image(systemName: "arrow.up.circle.fill")
                } primaryAction: {
                    send()
                }
                .menuStyle(.button)
                .menuIndicator(.visible)
                .font(.title2)
                .buttonStyle(.borderless)
                .fixedSize()
                .disabled(message.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                .help(busy ? "Steer: cut in now (↩); after this turn (⌥↩); or leave a note" : "Send (↩); or leave a note without a reply")
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

    /// While a turn runs, `how` says when the message joins it: the same
    /// timings an agent's messages and reports have.
    private func send(_ how: Delivery = .steer) {
        let text = message.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        message = ""
        store.send(text, how: how)
    }
}

/// What a coding run changed in its own git worktree, right above the rating stars: the files with
/// their line counts, the diff itself folded away, and Apply to Repo or Discard. The checkout is
/// untouched until Apply, so the changes can be read before the run is rated.
private struct WorktreeDiffBar: View {
    @Environment(Store.self) private var store
    let diff: WorktreeDiffInfo
    /// The run is going now: its worktree is in use, so it can be neither applied nor discarded.
    let running: Bool
    @State private var expanded = false
    @State private var confirmingDiscard = false
    @State private var rendered = AttributedString()

    private var active: Bool { diff.status == "active" }
    private var hasChanges: Bool { diff.filesChanged > 0 }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 10) {
                Button { withAnimation(.snappy(duration: 0.18)) { expanded.toggle() } } label: {
                    HStack(spacing: 6) {
                        Image(systemName: "chevron.right")
                            .font(.caption2.weight(.semibold))
                            .rotationEffect(.degrees(expanded ? 90 : 0))
                            .opacity(active && hasChanges ? 1 : 0)
                        summary
                    }
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .disabled(!(active && hasChanges))
                .help(active ? "Isolated git worktree at \(diff.workDir). \(repoName) is untouched until you apply it."
                             : "Applied or discarded; the worktree is gone")
                Spacer(minLength: 8)
                if active {
                    Button("Discard") { confirmingDiscard = true }
                        .controlSize(.small)
                        .disabled(running || store.worktreeBusy)
                        .help(running ? "Wait for the run to stop before discarding its worktree"
                                      : "Throw this run's changes away; \(repoName) is not touched")
                        .confirmationDialog("Discard this run's changes?", isPresented: $confirmingDiscard) {
                            Button("Discard Worktree", role: .destructive) { store.discardWorktree() }
                        } message: {
                            Text("The \(diff.filesChanged) changed file\(diff.filesChanged == 1 ? "" : "s") in its worktree are deleted. \(repoName) is not touched.")
                        }
                    if hasChanges {
                        Button("Apply to Repo", systemImage: "square.and.arrow.down.on.square") { store.applyWorktree() }
                            .buttonStyle(.borderedProminent)
                            .controlSize(.small)
                            .disabled(running || store.worktreeBusy)
                            .help(running ? "Wait for the run to stop before applying its changes"
                                          : "Apply these changes to \(diff.repoRoot)")
                    }
                    if store.worktreeBusy { ProgressView().controlSize(.small) }
                }
            }
            if expanded && active && hasChanges {
                details
                    .transition(.opacity.combined(with: .move(edge: .top)))
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.background.secondary)
        .task(id: diff.patch) { rendered = Self.highlight(diff.patch) }
    }

    private var repoName: String { URL(fileURLWithPath: diff.repoRoot).lastPathComponent }

    @ViewBuilder private var summary: some View {
        switch diff.status {
        case "applied":
            Label("Applied to repo", systemImage: "checkmark.circle.fill")
                .foregroundStyle(.green)
        case "discarded":
            Label("Worktree discarded", systemImage: "trash")
                .foregroundStyle(.secondary)
        default:
            HStack(spacing: 8) {
                Image(systemName: "arrow.triangle.branch").foregroundStyle(.secondary)
                if hasChanges {
                    Text("\(diff.filesChanged) file\(diff.filesChanged == 1 ? "" : "s") changed").fontWeight(.medium)
                    Text("+\(diff.insertions)").foregroundStyle(.green).monospacedDigit()
                    Text("−\(diff.deletions)").foregroundStyle(.red).monospacedDigit()
                } else {
                    Text("No changes yet").foregroundStyle(.secondary)
                }
                Text("in its own worktree").font(.caption).foregroundStyle(.tertiary)
            }
        }
    }

    private var details: some View {
        VStack(alignment: .leading, spacing: 8) {
            VStack(alignment: .leading, spacing: 3) {
                ForEach(diff.files, id: \.path) { file in
                    HStack(spacing: 8) {
                        Image(systemName: Self.symbol(file.status))
                            .foregroundStyle(Self.tint(file.status))
                            .frame(width: 14)
                            .help(file.status.capitalized)
                        Text(file.path).font(.callout.monospaced()).lineLimit(1).truncationMode(.middle)
                        Spacer(minLength: 8)
                        Text("+\(file.insertions)").foregroundStyle(.green)
                        Text("−\(file.deletions)").foregroundStyle(.red)
                    }
                    .font(.caption.monospacedDigit())
                }
            }
            ScrollView([.horizontal, .vertical]) {
                Text(rendered)
                    .font(.system(.caption, design: .monospaced))
                    .textSelection(.enabled)
                    .fixedSize()
                    .padding(8)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(maxHeight: 260)
            .background(.background, in: RoundedRectangle(cornerRadius: 8))
            .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(.separator))
            if diff.truncated {
                Label("The diff is cut at 64 KiB; the file list above is complete.", systemImage: "scissors")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    private static func symbol(_ status: String) -> String {
        switch status {
        case "added": "plus.circle.fill"
        case "deleted": "minus.circle.fill"
        case "renamed": "arrow.right.circle.fill"
        default: "pencil.circle.fill"
        }
    }

    private static func tint(_ status: String) -> Color {
        switch status {
        case "added": .green
        case "deleted": .red
        case "renamed": .cyan
        default: .orange
        }
    }

    /// Green for added lines, red for removed, cyan for hunk headers, quiet for file headers.
    private static func highlight(_ patch: String) -> AttributedString {
        var out = AttributedString()
        for line in patch.split(separator: "\n", omittingEmptySubsequences: false) {
            var run = AttributedString(line + "\n")
            if line.hasPrefix("+++") || line.hasPrefix("---") || line.hasPrefix("diff ") || line.hasPrefix("index ")
                || line.hasPrefix("new file") || line.hasPrefix("deleted file") || line.hasPrefix("similarity")
                || line.hasPrefix("rename ") || line.hasPrefix("old mode") || line.hasPrefix("new mode") {
                run.foregroundColor = .secondary
                if line.hasPrefix("diff ") { run.font = .system(.caption, design: .monospaced).bold() }
            } else if line.hasPrefix("@@") {
                run.foregroundColor = .cyan
            } else if line.hasPrefix("+") {
                run.foregroundColor = .green
                run.backgroundColor = Color.green.opacity(0.12)
            } else if line.hasPrefix("-") {
                run.foregroundColor = .red
                run.backgroundColor = Color.red.opacity(0.12)
            }
            out += run
        }
        return out
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
            case .paused?:
                Label("Stopped. Continue it, message the agent, or mark it done.", systemImage: "pause.circle")
                    .foregroundStyle(.secondary)
                if task.state != .done {
                    Button("Mark Done") { store.setDone(task, true) }
                        .help("Close the task; the stopped run ends without a result")
                }
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
        case .rating:
            Label(message.text, systemImage: "star.fill")
                .font(.callout)
                .foregroundStyle(.secondary)
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
        case .compaction:
            CompactionRow(text: message.text)
        }
    }
}

/// Where the model's context was compacted or reset: one line, with the summary or note folded away.
private struct CompactionRow: View {
    let text: String
    @State private var expanded = false

    var body: some View {
        let split = text.range(of: "\n\n")
        let head = split.map { String(text[..<$0.lowerBound]) } ?? text
        let detail = split.map { String(text[$0.upperBound...]) }
        let reset = head.hasPrefix("Context reset")
        VStack(alignment: .leading, spacing: 6) {
            Label(head, systemImage: reset ? "arrow.counterclockwise" : "rectangle.compress.vertical")
                .font(.callout)
                .foregroundStyle(.secondary)
            if let detail, !detail.isEmpty {
                DisclosureGroup(reset ? "Note" : "Summary", isExpanded: $expanded) {
                    Text(detail)
                        .font(.callout)
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.top, 4)
                }
                .font(.caption)
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(.separator, style: StrokeStyle(lineWidth: 1, dash: [4, 3])))
    }
}

/// How full the context is and how much of it the provider's cache served, e.g. "18.4k ctx · 72% cached".
private struct UsagePill: View {
    let usage: ConversationUsage

    var body: some View {
        Text(label)
            .font(.caption.monospacedDigit())
            .foregroundStyle(.secondary)
            .padding(.horizontal, 7).padding(.vertical, 3)
            .background(.quaternary, in: Capsule())
            .fixedSize()
            .help(breakdown)
    }

    private var label: String {
        if usage.lastInputTokens > 0 {
            return "\(tokenCount(usage.lastInputTokens)) ctx · \(percent(usage.lastCachedTokens, of: usage.lastInputTokens)) cached"
        }
        return usage.compactedEntries > 0 ? "Context compacted" : "\(tokenCount(usage.inputTokens)) in"
    }

    private var breakdown: String {
        var lines: [String] = []
        if usage.lastInputTokens > 0 {
            lines.append("Context now: \(usage.lastInputTokens.formatted()) tokens, \(usage.lastCachedTokens.formatted()) cached (\(percent(usage.lastCachedTokens, of: usage.lastInputTokens)))")
        }
        lines.append("Whole thread: \(usage.inputTokens.formatted()) in, \(usage.cachedTokens.formatted()) cached (\(percent(usage.cachedTokens, of: usage.inputTokens))), \(usage.outputTokens.formatted()) out over \(usage.repliesWithUsage) replies")
        if usage.compactedEntries > 0 {
            lines.append("\(usage.compactedEntries) earlier entries are no longer in the model's context")
        }
        return lines.joined(separator: "\n")
    }
}

/// 18400 -> "18.4k"
private func tokenCount(_ n: UInt64) -> String {
    switch n {
    case ..<1_000: "\(n)"
    case ..<1_000_000: String(format: "%.1fk", Double(n) / 1e3)
    default: String(format: "%.2fM", Double(n) / 1e6)
    }
}

private func percent(_ part: UInt64, of whole: UInt64) -> String {
    whole == 0 ? "0%" : "\(Int((Double(part) * 100 / Double(whole)).rounded()))%"
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
