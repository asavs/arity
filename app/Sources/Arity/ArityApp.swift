import ArityCore
import SwiftUI

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
                Button("New Conversation") { store.newConversation() }
                    .keyboardShortcut("n")
                Button("New Task") { store.newTask() }
                    .keyboardShortcut("t")
            }
        }
    }
}

struct ContentView: View {
    @Environment(Store.self) private var store
    @State private var showBoard = true

    var body: some View {
        @Bindable var store = store
        NavigationSplitView {
            Sidebar()
                .navigationSplitViewColumnWidth(min: 180, ideal: 220)
        } detail: {
            if store.selection != nil {
                ChatView()
            } else {
                ContentUnavailableView("No conversation", systemImage: "bubble.left.and.bubble.right",
                                       description: Text("Pick one on the left, or press ⌘N."))
            }
        }
        .inspector(isPresented: $showBoard) {
            BoardView()
                .inspectorColumnWidth(min: 200, ideal: 260)
        }
        .toolbar {
            ToolbarItem {
                Button("New Conversation", systemImage: "square.and.pencil") { store.newConversation() }
            }
            ToolbarItem {
                Button("New Task", systemImage: "plus.rectangle.on.rectangle") { store.newTask() }
                    .help("Draft a task with the selected model (⌘T)")
            }
            ToolbarItem {
                Button("Board", systemImage: "checklist") { showBoard.toggle() }
            }
        }
    }
}

struct Sidebar: View {
    @Environment(Store.self) private var store

    var body: some View {
        @Bindable var store = store
        List(selection: $store.selection) {
            // A new conversation has no entries yet, so the journal doesn't list it.
            if let s = store.selection, !store.conversations.contains(where: { $0.name == s }) {
                Label(s, systemImage: "plus.bubble").tag(s)
            }
            let runConversations = Dictionary(store.runs.values.map { ($0.conversation, $0) }, uniquingKeysWith: { a, _ in a })
            Section("Conversations") {
                ForEach(store.conversations.filter { runConversations[$0.name] == nil }, id: \.name) { c in
                    row(c, title: c.name)
                }
            }
            let runs = store.conversations.filter { runConversations[$0.name] != nil }
            if !runs.isEmpty {
                Section("Runs") {
                    ForEach(runs, id: \.name) { c in
                        row(c, title: runConversations[c.name]?.title ?? c.name)
                    }
                }
            }
        }
        .safeAreaInset(edge: .bottom) {
            if !store.signedInToChatGPT {
                Button("Sign in with ChatGPT") { store.signInToChatGPT() }
                    .padding(10)
            }
        }
    }
}

extension Sidebar {
    func row(_ c: Conversation, title: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack {
                Text(title).lineLimit(1)
                if store.running.contains(c.name) { ProgressView().controlSize(.mini) }
            }
            Text(relative(c.updated)).font(.caption).foregroundStyle(.secondary)
        }
        .tag(c.name)
    }
}

/// The journal stores UTC as "YYYY-MM-DD HH:MM:SS".
private func relative(_ utc: String) -> String {
    let f = DateFormatter()
    f.dateFormat = "yyyy-MM-dd HH:mm:ss"
    f.timeZone = TimeZone(identifier: "UTC")
    guard let date = f.date(from: utc) else { return utc }
    return date.formatted(.relative(presentation: .named))
}
