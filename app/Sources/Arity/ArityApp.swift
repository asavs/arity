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
            CommandGroup(replacing: .appSettings) {
                Button("Settings…") { store.showSettings = true }
                    .keyboardShortcut(",")
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
        .sheet(isPresented: Bindable(store).showSettings) { SettingsSheet().environment(store) }
        .toolbar {
            ToolbarItem {
                Button("Scores", systemImage: "chart.bar") { showScores.toggle() }
                    .help("Average rating per model")
                    .popover(isPresented: $showScores, arrowEdge: .bottom) { Scores() }
            }
            ToolbarItem {
                Button("Papercut", systemImage: "bandage") { store.filingPapercut.toggle() }
                    .help("Note that something got in the way")
                    .popover(isPresented: Bindable(store).filingPapercut, arrowEdge: .bottom) {
                        PapercutForm { store.filingPapercut = false }.environment(store)
                    }
            }
            ToolbarItem {
                Button("Models", systemImage: "cpu") { store.showSettings = true }
                    .help("Your accounts, their keys and plans, and worker models (⌘,)")
            }
            ToolbarItem {
                Button("New Chat", systemImage: "square.and.pencil") { store.newChat() }
                    .help("New chat (⌘N)")
            }
        }
    }
}

/// A one-line papercut, filed on the user's own list against the selected thread.
private struct PapercutForm: View {
    @Environment(Store.self) private var store
    @State private var note = ""
    @State private var about = ""
    let done: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            TextField("What got in the way?", text: $note, axis: .vertical)
                .lineLimit(1...4)
                .onSubmit(file)
            TextField("About (optional): a tool, a repo, a URL", text: $about)
                .onSubmit(file)
            HStack {
                Spacer()
                Button("File", action: file)
                    .keyboardShortcut(.defaultAction)
                    .disabled(note.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .textFieldStyle(.roundedBorder)
        .frame(width: 300)
        .padding(14)
    }

    private func file() {
        guard !note.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        if store.filePapercut(note, about: about) { done() }
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
