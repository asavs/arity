import ArityCore
import SwiftUI

/// Full paths identify folders even when two checkouts share a name.
struct Sidebar: View {
    @Environment(Store.self) private var store
    @State private var collapsed: Set<String> = []

    var body: some View {
        @Bindable var store = store
        let grouped = Dictionary(grouping: store.threads) { $0.folder ?? "" }
        let unlisted = store.unlistedChats
        let paths = Set(grouped.keys).union(unlisted.isEmpty ? [] : [""]).sorted {
            if $0.isEmpty || $1.isEmpty { return !$0.isEmpty }
            return $0.localizedStandardCompare($1) == .orderedAscending
        }
        let names = Dictionary(grouping: paths, by: folderName)
        List(selection: $store.selection) {
            ForEach(paths, id: \.self) { path in
                let threads = (grouped[path] ?? []).sorted {
                    $0.updated == $1.updated ? $0.name < $1.name : $0.updated > $1.updated
                }
                let expanded = Binding(
                    get: { !collapsed.contains(path) },
                    set: { if $0 { collapsed.remove(path) } else { collapsed.insert(path) } }
                )
                Section(isExpanded: expanded) {
                    if path.isEmpty {
                        ForEach(unlisted, id: \.self) { name in
                            Label {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text("New chat")
                                    if let preview = store.draftPreview(for: name) {
                                        Text(preview).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                                    }
                                }
                            } icon: {
                                Image(systemName: "plus.bubble")
                            }
                            .listRowBackground(rowBackground(selected: store.selection == name))
                            .tag(name)
                        }
                    }
                    ForEach(threads, id: \.name) { thread in
                        ThreadRow(thread: thread)
                            .listRowBackground(rowBackground(selected: store.selection == thread.name))
                            .tag(thread.name)
                    }
                } header: {
                    let name = folderName(path)
                    let title = !path.isEmpty && (names[name]?.count ?? 0) > 1
                        ? (path as NSString).abbreviatingWithTildeInPath : name
                    HStack(spacing: 5) {
                        Image(systemName: path.isEmpty ? "tray" : "folder")
                        Text(title).lineLimit(1).truncationMode(.middle)
                        if !expanded.wrappedValue {
                            let count = threads.filter { $0.group == .needsYou }.count
                            if count > 0 {
                                Text("\(count) needs you")
                                    .font(.caption).foregroundStyle(.secondary)
                                    .fixedSize()
                            }
                        }
                    }
                    .help(path.isEmpty ? "Threads without an attached folder" : path)
                }
            }
        }
        .safeAreaInset(edge: .bottom) {
            if store.models.isEmpty {
                Button("Connect Models…", systemImage: "cpu") { store.showSettings = true }
                    .padding(10)
            }
        }
    }

    private func folderName(_ path: String) -> String {
        path.isEmpty ? "No folder" : URL(fileURLWithPath: path).lastPathComponent
    }

    private func rowBackground(selected: Bool) -> some View {
        RoundedRectangle(cornerRadius: 6)
            .fill(selected ? Color(nsColor: .unemphasizedSelectedContentBackgroundColor) : .clear)
            .padding(.horizontal, 6)
    }
}

private struct ThreadRow: View {
    @Environment(Store.self) private var store
    let thread: Thread

    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            icon.frame(width: 16)
            VStack(alignment: .leading, spacing: 2) {
                Text(thread.title)
                    .foregroundStyle(thread.group == .done ? .secondary : .primary)
                    .lineLimit(1)
                Text(caption).font(.caption).foregroundStyle(.secondary).lineLimit(1)
            }
            Spacer(minLength: 0)
            if let score = thread.task?.score {
                Text("\(score)/5").font(.caption).monospacedDigit().foregroundStyle(.secondary)
            }
        }
    }

    private var caption: String {
        if thread.pendingApprovals > 0 || thread.task?.runState == .needsApproval { return "Approve tool call" }
        if thread.group == .needsYou {
            if thread.task?.runState == .needsRating { return "Rate result" }
            if thread.task?.runState == .paused { return "Continue task" }
            if thread.card != nil { return "Review task card" }
        }
        if store.running.contains(thread.name) { return "Running" }
        if let task = thread.task { return details(task, me: store.me) }
        if let preview = store.draftPreview(for: thread.name) { return "Draft · " + preview }
        return thread.updated.isEmpty ? "New conversation" : relative(thread.updated)
    }

    @ViewBuilder private var icon: some View {
        if thread.pendingApprovals > 0 {
            Image(systemName: "exclamationmark.shield.fill")
                .foregroundStyle(Color.amber)
                .help("A tool call waits for you to allow or deny it")
        } else if store.running.contains(thread.name) {
            ProgressView().controlSize(.mini)
        } else if let task = thread.task {
            switch (task.state, task.runState) {
            case (_, .running?): ProgressView().controlSize(.mini)
            case (_, .waiting?): Image(systemName: "hourglass").foregroundStyle(.secondary).help("Waiting on its sub-tasks")
            case (_, .needsApproval?):
                Image(systemName: "exclamationmark.shield.fill").foregroundStyle(Color.amber)
                    .help("A tool call waits for you to allow or deny it")
            case (_, .failing?): Image(systemName: "exclamationmark.arrow.circlepath").foregroundStyle(.orange)
            case (_, .paused?): Image(systemName: "pause.circle").foregroundStyle(.secondary).help("Stopped; continue it to carry on")
            case (.done, _): Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
            case (_, .needsRating?): Image(systemName: "star.leadinghalf.filled").foregroundStyle(.yellow)
            case (.open, _): Image(systemName: "lock").foregroundStyle(.secondary).help("Waiting on its blockers")
            case _ where task.fields.assignee == "agent":
                Image(systemName: "sparkles").foregroundStyle(.secondary).help("Waiting for an agent")
            default: Image(systemName: "circle").foregroundStyle(.secondary)
            }
        } else {
            Image(systemName: thread.card != nil ? "rectangle.and.pencil.and.ellipsis" : "bubble.left")
                .foregroundStyle(.secondary)
        }
    }
}

/// "Task 3 · An agent · needs rating · High · Due 2026-11-30 · Local only · After #2 · #docs"
func details(_ task: BoardTask, me: String) -> String {
    let f = task.fields
    var parts = ["Task \(task.id)"]
    if f.assignee == "agent" {
        parts.append("An agent")
        switch task.runState {
        case .running?: parts.append("running")
        case .waiting?: parts.append("waiting on sub-tasks")
        case .needsApproval?: parts.append("needs your approval")
        case .failing?: parts.append("retrying")
        case .paused?: parts.append("stopped")
        case .needsRating? where task.state != .done: parts.append("needs rating")
        default: break
        }
    } else if f.assignee != me {
        parts.append(f.assignee)
    }
    if f.priority != .normal { parts.append(f.priority.name) }
    if let due = f.due { parts.append("Due \(due)") }
    if f.choice != .smart { parts.append(f.choice.title) }
    if !f.blockedBy.isEmpty { parts.append("After " + f.blockedBy.map { "#\($0)" }.joined(separator: ", ")) }
    parts += f.tags.map { "#\($0)" }
    return parts.joined(separator: " · ")
}

/// The journal stores UTC as "YYYY-MM-DD HH:MM:SS".
private func relative(_ utc: String) -> String {
    let f = DateFormatter()
    f.dateFormat = "yyyy-MM-dd HH:mm:ss"
    f.timeZone = TimeZone(identifier: "UTC")
    guard let date = f.date(from: utc) else { return utc }
    return date.formatted(.relative(presentation: .named))
}
