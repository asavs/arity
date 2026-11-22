import ArityCore
import SwiftUI

/// The fields of a task, editable in place. The same card sits under a
/// conversation as a draft and opens from the board to edit a task.
struct TaskCard: View {
    @Environment(Store.self) private var store
    @Binding var fields: TaskFields
    /// The task being edited, so it is not offered as its own blocker.
    var editing: Int64?

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            TextField("Task title", text: $fields.title)
                .textFieldStyle(.plain)
                .font(.title3.weight(.semibold))
            TextField("Brief: everything whoever does it needs to know", text: $fields.brief, axis: .vertical)
                .textFieldStyle(.plain)
                .lineLimit(2...8)
            Divider()
            Grid(alignment: .leading, horizontalSpacing: 10, verticalSpacing: 8) {
                GridRow {
                    label("For")
                    Picker("For", selection: forAgent) {
                        Text("Me").tag(false)
                        Text("An agent").tag(true)
                    }
                    .pickerStyle(.segmented).labelsHidden().fixedSize()
                    label("Model")
                    Picker("Model", selection: $fields.choice) {
                        ForEach(Choice.all, id: \.self) { Text($0.title).tag($0).help($0.blurb) }
                    }
                    .pickerStyle(.segmented).labelsHidden().fixedSize()
                    .help(fields.choice.blurb)
                }
                GridRow {
                    label("Priority")
                    Picker("Priority", selection: $fields.priority) {
                        ForEach([Priority.low, .normal, .high, .urgent], id: \.self) { Text($0.name).tag($0) }
                    }
                    .labelsHidden().fixedSize()
                    label("Due")
                    DueField(due: $fields.due)
                }
                GridRow {
                    label("Blocked by")
                    BlockersMenu(blockedBy: $fields.blockedBy, editing: editing)
                    label("Tags")
                    TagsField(tags: $fields.tags)
                }
            }
            .controlSize(.small)
            if let dir = fields.dir {
                Label(dir, systemImage: "folder")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .help("Its run reads this folder, and changes nothing in it")
            }
        }
    }

    private var forAgent: Binding<Bool> {
        Binding { fields.assignee == "agent" } set: { fields.assignee = $0 ? "agent" : store.me }
    }

    private func label(_ text: String) -> some View {
        Text(text).font(.caption).foregroundStyle(.secondary).gridColumnAlignment(.trailing)
    }
}

extension Priority {
    var name: String {
        switch self {
        case .low: "Low"
        case .normal: "Normal"
        case .high: "High"
        case .urgent: "Urgent"
        }
    }
}

/// A date, or none. Stored as YYYY-MM-DD.
private struct DueField: View {
    @Binding var due: String?

    var body: some View {
        if let due, let date = Self.format.date(from: due) {
            HStack(spacing: 2) {
                DatePicker("Due", selection: Binding { date } set: { self.due = Self.format.string(from: $0) },
                           displayedComponents: .date)
                    .labelsHidden().datePickerStyle(.field)
                Button("No due date", systemImage: "xmark.circle.fill") { self.due = nil }
                    .labelStyle(.iconOnly).buttonStyle(.plain).foregroundStyle(.secondary)
            }
        } else {
            Button("Add date") { due = Self.format.string(from: .now) }
                .buttonStyle(.link)
        }
    }

    static let format: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        f.locale = Locale(identifier: "en_US_POSIX")
        return f
    }()
}

/// Pick the tasks that must be done first, from those not done yet.
private struct BlockersMenu: View {
    @Environment(Store.self) private var store
    @Binding var blockedBy: [Int64]
    var editing: Int64?

    var body: some View {
        let candidates = store.tasks.filter { $0.id != editing && ($0.state != .done || blockedBy.contains($0.id)) }
        Menu(blockedBy.isEmpty ? "Nothing" : blockedBy.map { "#\($0)" }.joined(separator: ", ")) {
            if candidates.isEmpty { Text("No open tasks") }
            ForEach(candidates, id: \.id) { t in
                Toggle("#\(t.id) \(t.fields.title)", isOn: Binding {
                    blockedBy.contains(t.id)
                } set: { on in
                    blockedBy.removeAll { $0 == t.id }
                    if on { blockedBy.append(t.id) }
                })
            }
        }
        .fixedSize()
    }
}

/// Comma-separated. Keeps its own text so a trailing comma survives typing.
private struct TagsField: View {
    @Binding var tags: [String]
    @State private var text = ""

    var body: some View {
        TextField("comma, separated", text: $text)
            .textFieldStyle(.roundedBorder)
            .frame(minWidth: 110)
            .onAppear { text = tags.joined(separator: ", ") }
            .onChange(of: text) { tags = Self.parse(text) }
            .onChange(of: tags) { if Self.parse(text) != tags { text = tags.joined(separator: ", ") } }
    }

    static func parse(_ s: String) -> [String] {
        s.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
    }
}
