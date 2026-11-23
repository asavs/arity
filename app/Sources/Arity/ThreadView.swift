import ArityCore
import SwiftUI

/// The selected thread: what was said in it, and below that whatever it needs
/// next. For a chat that is the composer, with a task card when one is being
/// drafted. For a task it is the task: its card, how its run is going, or the
/// stars to rate the result.
struct ThreadView: View {
    @Environment(Store.self) private var store
    @FocusState private var composing: Bool

    private var busy: Bool { store.selection.map(store.running.contains) ?? false }

    var body: some View {
        VStack(spacing: 0) {
            TranscriptView(messages: store.messages, busy: busy)
                .id(store.selection)

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

            if let approval = store.pendingApprovals.first {
                Divider()
                ApprovalBanner(approval: approval, more: store.pendingApprovals.count - 1).id(approval.id)
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
        return Menu {
            Picker("Routing", selection: $store.choice) {
                ForEach(Choice.all, id: \.self) { Text($0.title).tag(Optional($0)).help($0.blurb) }
                if store.choice == nil {
                    Text(shortName(store.model)).tag(nil as Choice?)
                }
            }
            Button("Choose a Model…") { store.choosingModel = true }
        } label: {
            Text(store.choice?.title ?? shortName(store.model))
                .lineLimit(1)
        }
        .menuStyle(.borderlessButton)
        .frame(maxWidth: 120)
        .accessibilityLabel("Model: \(store.choice?.title ?? shortName(store.model))")
        .help(store.choice?.blurb ?? "Choose the model for this chat")
        .popover(isPresented: $store.choosingModel, arrowEdge: .bottom) {
            ModelChooser(models: store.models) { store.chooseModel($0) }
        }
    }

    /// The folder attached to this chat, as a chip with × to detach it, or a button to attach one.
    @ViewBuilder private var folderButton: some View {
        if let folder = store.folder {
            HStack(spacing: 3) {
                Image(systemName: "folder")
                Text(URL(fileURLWithPath: folder).lastPathComponent)
                    .lineLimit(1)
                    .truncationMode(.middle)
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
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: 150)
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

    private func composer(prompt: String, lines: ClosedRange<Int>) -> some View {
        @Bindable var store = store
        let empty = store.messageDraft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        return VStack(alignment: .leading, spacing: 10) {
            TextField(prompt, text: $store.messageDraft, axis: .vertical)
                .textFieldStyle(.plain)
                .lineLimit(lines)
                .focused($composing)
                .onSubmit { store.sendDraft() }
                .onKeyPress(.return, phases: .down) { press in
                    if press.modifiers == .option {
                        store.sendDraft(how: .afterTurn)
                        return .handled
                    }
                    return .ignored
                }

            HStack(spacing: 8) {
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
                Spacer(minLength: 0)
                deliveryActions(empty: empty)
                    .fixedSize(horizontal: true, vertical: false)
            }
        }
        .padding(10)
        .background(.background.secondary, in: RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(.separator))
    }

    private func deliveryActions(empty: Bool) -> some View {
        HStack(spacing: 6) {
            Menu("Delivery Options", systemImage: "ellipsis.circle") {
                Button("Leave a Note", systemImage: "tray") { store.sendDraft(how: .inbox) }
            }
            .labelStyle(.iconOnly)
            .menuStyle(.borderlessButton)
            .menuIndicator(.hidden)
            .disabled(empty)
            .help("Leave a note without starting a reply")

            if busy {
                Button("Stop", systemImage: "stop.circle.fill") { store.stop() }
                    .keyboardShortcut(.cancelAction)
                    .help("Stop this reply (Esc)")
                Button("Queue", systemImage: "clock") { store.sendDraft(how: .afterTurn) }
                    .disabled(empty)
                    .help("Send after this turn (⌥↩)")
                Button("Steer", systemImage: "arrow.turn.up.right") { store.sendDraft() }
                    .buttonStyle(.borderedProminent)
                    .disabled(empty)
                    .help("Cut in with this message (↩)")
            } else {
                Button("Send", systemImage: "arrow.up.circle.fill") { store.sendDraft() }
                    .buttonStyle(.borderedProminent)
                    .disabled(empty)
                    .help("Send (↩)")
            }
        }
        .controlSize(.small)
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

}

private struct TranscriptView: View {
    let messages: [Message]
    let busy: Bool

    @State private var followingLatest = true
    @State private var scrollPhase = ScrollPhase.idle
    @State private var position = ScrollPosition(edge: .bottom)

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 10) {
                ForEach(rows) { row in
                    switch row {
                    case .message(let message):
                        MessageRow(message: message)
                    case .activity(let messages):
                        ToolActivityRow(messages: messages)
                    }
                }
                if busy { ProgressView().controlSize(.small) }
            }
            .padding()
        }
        .defaultScrollAnchor(.bottom, for: .initialOffset)
        .defaultScrollAnchor(followingLatest ? .bottom : nil, for: .sizeChanges)
        .scrollPosition($position)
        .onScrollGeometryChange(for: Geometry.self, of: Geometry.init) { old, new in
            guard old.offset != new.offset else { return }
            if Self.isUserScrolling(scrollPhase) {
                followingLatest = new.nearBottom
            } else if scrollPhase == .idle && position.isPositionedByUser && old.extent == new.extent {
                // A mouse wheel or scrollbar can move the view between scroll phases.
                followingLatest = new.nearBottom
                if followingLatest { position.scrollTo(edge: .bottom) }
            }
        }
        .onScrollPhaseChange { old, new, context in
            scrollPhase = new
            if new == .idle && Self.isUserScrolling(old) {
                followingLatest = Geometry(context.geometry).nearBottom
                if followingLatest { position.scrollTo(edge: .bottom) }
            }
        }
        .overlay(alignment: .bottomTrailing) {
            if !followingLatest {
                Button("Jump to Latest", systemImage: "arrow.down.circle") {
                    followingLatest = true
                    position.scrollTo(edge: .bottom)
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.small)
                .padding(12)
            }
        }
    }

    private static func isUserScrolling(_ phase: ScrollPhase) -> Bool {
        phase == .interacting || phase == .decelerating
    }

    private struct Geometry: Equatable {
        let offset: CGFloat
        let extent: CGFloat

        var nearBottom: Bool { offset >= extent - 40 }

        init(_ geometry: ScrollGeometry) {
            offset = geometry.contentOffset.y
            extent = geometry.contentSize.height + geometry.contentInsets.bottom - geometry.containerSize.height
        }
    }

    private enum Row: Identifiable {
        case message(Message)
        case activity([Message])

        var id: String {
            switch self {
            case .message(let message): message.id
            case .activity(let messages): "activity-" + messages[0].id
            }
        }
    }

    private var rows: [Row] {
        var rows: [Row] = []
        var activity: [Message] = []
        for message in messages {
            if message.kind == .toolCall || message.kind == .toolResult {
                activity.append(message)
            } else {
                if !activity.isEmpty {
                    rows.append(.activity(activity))
                    activity = []
                }
                rows.append(.message(message))
            }
        }
        if !activity.isEmpty { rows.append(.activity(activity)) }
        return rows
    }
}

private struct ToolActivityRow: View {
    let messages: [Message]
    @State private var expanded = false

    private var title: String {
        let calls = messages.count(where: { $0.kind == .toolCall })
        if calls == 0 { return messages.count == 1 ? "Tool result" : "\(messages.count) tool results" }
        return calls == 1 ? "1 tool call" : "\(calls) tool calls"
    }

    var body: some View {
        DisclosureGroup(isExpanded: $expanded) {
            VStack(alignment: .leading, spacing: 6) {
                ForEach(messages, id: \.id) { MessageRow(message: $0) }
            }
            .padding(.top, 4)
        } label: {
            Label(title, systemImage: "wrench.and.screwdriver")
                .font(.callout)
                .foregroundStyle(.secondary)
        }
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

extension Color {
    /// Tool calls waiting for the user's approval.
    static let amber = Color(red: 1.0, green: 0.69, blue: 0.0)
}

/// A tool call a hook parked, above the composer: which tool, the hook that asked and why, its
/// arguments folded away, and Allow or Deny with an optional note the model reads.
private struct ApprovalBanner: View {
    @Environment(Store.self) private var store
    let approval: PendingApproval
    /// Calls waiting behind this one in the same thread.
    let more: Int
    @State private var expanded = false
    @State private var note = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Image(systemName: "exclamationmark.shield.fill")
                    .font(.title3)
                    .foregroundStyle(Color.amber)
                Text("Approval needed · \(approval.tool)").fontWeight(.semibold)
                Text(approval.hook)
                    .font(.caption.monospaced())
                    .padding(.horizontal, 6).padding(.vertical, 2)
                    .background(Color.amber.opacity(0.18), in: Capsule())
                    .help("The hook that asked")
                if more > 0 {
                    Text("+\(more) more").font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
            }
            Text(approval.reason).font(.callout).textSelection(.enabled)
            Button { withAnimation(.snappy(duration: 0.18)) { expanded.toggle() } } label: {
                HStack(spacing: 6) {
                    Image(systemName: "chevron.right")
                        .font(.caption2.weight(.semibold))
                        .rotationEffect(.degrees(expanded ? 90 : 0))
                    if expanded {
                        Text("Arguments").font(.caption)
                    } else {
                        Text(approval.args).font(.caption.monospaced()).lineLimit(1).truncationMode(.tail)
                    }
                }
                .foregroundStyle(.secondary)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .help(expanded ? "Hide the arguments" : "Show the arguments in full")
            if expanded {
                ScrollView([.horizontal, .vertical]) {
                    Text(prettyArgs)
                        .font(.system(.caption, design: .monospaced))
                        .textSelection(.enabled)
                        .fixedSize()
                        .padding(8)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .frame(maxHeight: 180)
                .background(.background, in: RoundedRectangle(cornerRadius: 8))
                .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(.separator))
                .transition(.opacity.combined(with: .move(edge: .top)))
            }
            HStack(spacing: 8) {
                TextField("Note for the model (optional)", text: $note)
                    .textFieldStyle(.roundedBorder)
                Button("Deny", role: .destructive) { store.denyTool(approval, note: note) }
                    .help("The model is told the call was denied, with your note")
                Button("Allow") { store.approveTool(approval, note: note) }
                    .buttonStyle(.borderedProminent)
                    .tint(Color.amber)
                    .help("Run this call once, as it is")
            }
            .controlSize(.small)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.amber.opacity(0.08))
    }

    private var prettyArgs: String {
        guard let data = approval.args.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data),
              let pretty = try? JSONSerialization.data(
                  withJSONObject: object, options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]),
              let text = String(data: pretty, encoding: .utf8)
        else { return approval.args }
        return text
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
            case .needsApproval?:
                Label("Waiting for you to allow or deny a tool call", systemImage: "exclamationmark.shield")
                    .foregroundStyle(Color.amber)
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
                .textSelection(.enabled)
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
