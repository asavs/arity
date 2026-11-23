import ArityCore
import SwiftUI

/// Every thread, chats and tasks alike, grouped by what needs attention.
struct Sidebar: View {
    @Environment(Store.self) private var store

    private static let groups: [(ThreadGroup, String)] =
        [(.needsYou, "Needs you"), (.running, "Running"), (.open, "Open"), (.chat, "Chats"), (.done, "Done")]

    var body: some View {
        @Bindable var store = store
        List(selection: $store.selection) {
            ForEach(Self.groups, id: \.0) { group, title in
                let threads = store.threads.filter { $0.group == group }
                let unlisted = group == .chat ? store.unlistedChats : []
                if !threads.isEmpty || !unlisted.isEmpty {
                    Section(title) {
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
                            .tag(name)
                        }
                        ForEach(threads, id: \.name) { ThreadRow(thread: $0).tag($0.name) }
                    }
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
        if thread.pendingApprovals > 0 && thread.task == nil {
            let n = thread.pendingApprovals
            return n == 1 ? "A tool call needs your approval" : "\(n) tool calls need your approval"
        }
        if let task = thread.task { return details(task, me: store.me) }
        return thread.card != nil ? "Task card open" : relative(thread.updated)
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
