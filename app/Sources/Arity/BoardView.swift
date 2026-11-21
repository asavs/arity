import ArityCore
import SwiftUI

/// The shared board and the scores, as a light list. New tasks are drafted in
/// a conversation (New Task); clicking a task opens its card to edit it, see
/// how its run went, and rate it. The model stays hidden until it is rated.
struct BoardView: View {
    @Environment(Store.self) private var store
    @State private var open: Int64?

    var body: some View {
        List {
            Section {
                if store.tasks.isEmpty {
                    Text("No tasks yet").foregroundStyle(.secondary)
                }
                ForEach(store.tasks, id: \.id) { task in
                    TaskRow(task: task)
                        .contentShape(Rectangle())
                        .onTapGesture { open = task.id }
                        .popover(isPresented: Binding { open == task.id } set: { if !$0 { open = nil } },
                                 arrowEdge: .leading) {
                            TaskDetail(task: task)
                        }
                }
            } header: {
                HStack {
                    Text("Board")
                    Spacer()
                    Button("New Task", systemImage: "plus") { store.newTask() }
                        .labelStyle(.iconOnly).buttonStyle(.borderless)
                        .help("Draft a task with a model (⌘T)")
                }
            }
            if !store.scores.isEmpty {
                Section("Scores") {
                    ForEach(store.scores, id: \.model) { s in
                        HStack {
                            Text(shortName(s.model))
                            Spacer()
                            Text(s.average, format: .number.precision(.fractionLength(1)))
                                .monospacedDigit().bold()
                            Text("×\(s.rated)")
                                .font(.caption).monospacedDigit().foregroundStyle(.secondary)
                                .help("\(s.rated) rated runs")
                        }
                    }
                }
            }
        }
    }
}

private struct TaskRow: View {
    @Environment(Store.self) private var store
    let task: BoardTask

    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            icon.frame(width: 16)
            VStack(alignment: .leading, spacing: 2) {
                Text(task.fields.title)
                    .strikethrough(task.state == .done && task.fields.assignee != "agent")
                    .foregroundStyle(task.state == .done ? .secondary : .primary)
                    .lineLimit(2)
                let meta = details(task, me: store.me)
                if !meta.isEmpty {
                    Text(meta).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                }
            }
            Spacer()
            if let score = task.score {
                Text("\(score)/5").font(.caption).monospacedDigit().foregroundStyle(.secondary)
            }
        }
        .help("Task \(task.id), from \(task.createdBy)")
    }

    @ViewBuilder private var icon: some View {
        switch (task.state, task.runState) {
        case (_, .running?): ProgressView().controlSize(.mini)
        case (_, .failing?): Image(systemName: "exclamationmark.arrow.circlepath").foregroundStyle(.orange)
        case (_, .needsRating?): Image(systemName: "star.leadinghalf.filled").foregroundStyle(.yellow)
        case (_, .rated?): Image(systemName: "checkmark.seal.fill").foregroundStyle(.green)
        case (.open, nil): Image(systemName: "lock").foregroundStyle(.secondary).help("Waiting on its blockers")
        case (.ready, nil) where task.fields.assignee == "agent":
            Image(systemName: "sparkles").foregroundStyle(.secondary).help("Waiting for an agent")
        default:
            Button { store.setDone(task, task.state != .done) } label: {
                Image(systemName: task.state == .done ? "checkmark.circle.fill" : "circle")
                    .foregroundStyle(task.state == .done ? .green : .secondary)
            }
            .buttonStyle(.plain)
        }
    }
}

/// "An agent · running · High · Due 30 Nov · Local only · After #2 · #docs"
func details(_ task: BoardTask, me: String) -> String {
    let f = task.fields
    var parts: [String] = []
    if f.assignee == "agent" {
        parts.append("An agent")
        switch task.runState {
        case .running?: parts.append("running")
        case .failing?: parts.append("retrying")
        case .needsRating?: parts.append("needs rating")
        default: break
        }
    } else if f.assignee != me {
        parts.append(f.assignee)
    }
    if f.priority != .normal { parts.append(f.priority.name) }
    if let due = f.due { parts.append("Due \(due)") }
    if f.clearance == .local { parts.append("Local only") }
    if !f.blockedBy.isEmpty { parts.append("After " + f.blockedBy.map { "#\($0)" }.joined(separator: ", ")) }
    parts += f.tags.map { "#\($0)" }
    return parts.joined(separator: " · ")
}

/// A task's card, editable, with its run below: the result, the rating, and
/// the model once rated. Setting For to An agent hands it off.
private struct TaskDetail: View {
    @Environment(Store.self) private var store
    @Environment(\.dismiss) private var dismiss
    let task: BoardTask
    @State private var fields: TaskFields?

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            TaskCard(fields: Binding { fields ?? task.fields } set: { fields = $0 }, editing: task.id)
            HStack {
                Text("Task \(task.id) · \(stateName) · from \(task.createdBy)")
                    .font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button("Save") {
                    if let fields { store.updateTask(task.id, fields) }
                    dismiss()
                }
                .keyboardShortcut(.return, modifiers: .command)
                .buttonStyle(.borderedProminent)
                .disabled(fields == nil || fields == task.fields)
            }
            if task.run != nil {
                Divider()
                run
            }
        }
        .padding(14)
        .frame(width: 460)
    }

    private var stateName: String {
        switch task.state {
        case .open: "open, blocked"
        case .ready: "ready"
        case .assigned: "assigned"
        case .inGates: "in gates"
        case .done: "done"
        }
    }

    @ViewBuilder private var run: some View {
        if let error = task.error, task.runState == .failing {
            Text(error).font(.caption).foregroundStyle(.orange).textSelection(.enabled)
        }
        if let result = task.result {
            ScrollView { Text(result).font(.callout).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading) }
                .frame(maxHeight: 220)
        } else if task.runState == .running {
            Text("An agent is on it").font(.callout).foregroundStyle(.secondary)
        }
        if task.runState == .needsRating, let run = task.run {
            RateControl(run: run)
        }
        if task.runState == .rated, let run = task.run, let r = store.runs[run] {
            VStack(alignment: .leading, spacing: 2) {
                Text("Done by \(shortName(r.model))").font(.caption.bold())
                if let note = r.note, !note.isEmpty {
                    Text("“\(note)”").font(.caption).foregroundStyle(.secondary)
                }
            }
        }
        HStack {
            if let conv = task.conversation {
                Button("Open run") { store.selection = conv; dismiss() }
            }
            if task.createdBy.hasPrefix("conversations/") {
                Button("Open conversation") { store.selection = String(task.createdBy.dropFirst(14)); dismiss() }
            }
        }
        .buttonStyle(.link).font(.caption)
    }
}

/// Pick 1–5, add a note, rate. The model is revealed once the rating is in.
private struct RateControl: View {
    @Environment(Store.self) private var store
    let run: Int64
    @State private var score: Int64 = 0
    @State private var note = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 2) {
                ForEach(1...5, id: \.self) { n in
                    Button { score = Int64(n) } label: {
                        Image(systemName: Int64(n) <= score ? "star.fill" : "star")
                            .foregroundStyle(.yellow)
                    }
                    .buttonStyle(.plain)
                }
            }
            TextField("Why?", text: $note, axis: .vertical)
                .lineLimit(1...4)
            Button("Rate") { store.rate(run: run, score: score, note: note) }
                .disabled(score == 0)
        }
    }
}
