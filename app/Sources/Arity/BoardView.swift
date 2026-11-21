import ArityCore
import SwiftUI

/// The shared board. Read-only for now: models change it through their tools.
struct BoardView: View {
    @Environment(Store.self) private var store

    var body: some View {
        List {
            Section("Board") {
                if store.tasks.isEmpty {
                    Text("No tasks yet").foregroundStyle(.secondary)
                }
                ForEach(store.tasks, id: \.id) { task in
                    Label {
                        Text(task.title)
                            .strikethrough(task.done)
                            .foregroundStyle(task.done ? .secondary : .primary)
                    } icon: {
                        Image(systemName: task.done ? "checkmark.circle.fill" : "circle")
                            .foregroundStyle(task.done ? .green : .secondary)
                    }
                    .help("Task \(task.id)")
                }
            }
        }
    }
}
