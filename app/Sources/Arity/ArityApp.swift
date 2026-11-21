import ArityCore
import SwiftUI

/// The core's thread, not Foundation's.
typealias Thread = ArityCore.Thread

@main
struct ArityApp: App {
    @State private var store = Store()

    var body: some Scene {
        WindowGroup("Arity") {
            ContentView()
                .environment(store)
                .frame(minWidth: 720, minHeight: 440)
                .task { Snapshot.startIfAsked(store: store) }
        }
        .commands {
            CommandGroup(replacing: .newItem) {
                Button("New Chat") { store.newChat() }
                    .keyboardShortcut("n")
            }
        }
    }
}

/// One list of threads on the left, the selected thread on the right.
struct ContentView: View {
    @Environment(Store.self) private var store
    @State private var showScores = false

    var body: some View {
        NavigationSplitView {
            Sidebar()
                .navigationSplitViewColumnWidth(min: 200, ideal: 250)
        } detail: {
            if store.selection != nil {
                ThreadView()
            } else {
                ContentUnavailableView("Nothing open", systemImage: "bubble.left.and.bubble.right",
                                       description: Text("Pick a thread on the left, or press ⌘N."))
            }
        }
        .toolbar {
            ToolbarItem {
                Button("Scores", systemImage: "chart.bar") { showScores.toggle() }
                    .help("Average rating per model")
                    .popover(isPresented: $showScores, arrowEdge: .bottom) { Scores() }
            }
            ToolbarItem {
                Button("New Chat", systemImage: "square.and.pencil") { store.newChat() }
                    .help("New chat (⌘N)")
            }
        }
    }
}

/// Rated runs per model. Unrated runs are left out, so they cannot be traced to a model.
private struct Scores: View {
    @Environment(Store.self) private var store

    var body: some View {
        Grid(alignment: .leading, horizontalSpacing: 14, verticalSpacing: 6) {
            if store.scores.isEmpty {
                Text("No rated runs yet").foregroundStyle(.secondary)
            }
            ForEach(store.scores, id: \.model) { s in
                GridRow {
                    Text(shortName(s.model))
                    Text(s.average, format: .number.precision(.fractionLength(1))).monospacedDigit().bold()
                    Text("\(s.rated) rated").font(.caption).monospacedDigit().foregroundStyle(.secondary)
                }
            }
        }
        .padding(14)
    }
}
