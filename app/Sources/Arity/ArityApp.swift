import ArityCore
import SwiftUI

@main
struct ArityApp: App {
    @State private var store = AppStore()

    var body: some Scene {
        WindowGroup("Arity") {
            ContentView()
                .environment(store)
                .frame(minWidth: 760, minHeight: 480)
                .task {
                    store.start()
                }
        }
        .commands {
            CommandGroup(replacing: .newItem) {
                Button("New Thread") {
                    store.openNewThreadSheet()
                }
                .keyboardShortcut("n", modifiers: .command)
            }
            CommandGroup(replacing: .appSettings) {
                Button("Settings…") {
                    store.openSettings()
                }
                .keyboardShortcut(",", modifiers: .command)
            }
            CommandMenu("Thread") {
                Button("Review Changes") {
                    store.openDiffSheet()
                }
                .keyboardShortcut("d", modifiers: [.command, .shift])
                .disabled(store.selectedSummary?.sourceDir == nil)
            }
        }
    }
}

struct ContentView: View {
    @Environment(AppStore.self) private var store

    var body: some View {
        @Bindable var store = store
        NavigationSplitView {
            SidebarView()
                .navigationSplitViewColumnWidth(min: 210, ideal: 250, max: 340)
        } detail: {
            if store.selectedThreadId != nil {
                ThreadView()
            } else {
                emptyDetail
            }
        }
        .toolbar {
            ToolbarItem(placement: .navigation) {
                Button {
                    store.openNewThreadSheet()
                } label: {
                    Label("New Thread", systemImage: "square.and.pencil")
                }
                .accessibilityLabel("New Thread")
                .help("New Thread (⌘N)")
            }
            ToolbarItem(placement: .automatic) {
                Button {
                    store.openSettings()
                } label: {
                    Label("Settings", systemImage: "gearshape")
                }
                .accessibilityLabel("Settings")
                .help("Providers, API keys, and diagnostics (⌘,)")
            }
        }
        .sheet(isPresented: $store.showNewThreadSheet) {
            NewThreadSheet()
                .environment(store)
        }
        .sheet(isPresented: $store.showSettingsSheet) {
            SettingsView()
                .environment(store)
        }
        .sheet(isPresented: $store.showDiffSheet) {
            DiffSheet()
                .environment(store)
        }
    }

    private var emptyDetail: some View {
        VStack(spacing: 16) {
            ContentUnavailableView(
                "No Thread Selected",
                systemImage: "bubble.left.and.bubble.right",
                description: Text("Pick a thread in the sidebar or create a new one (⌘N).")
            )
            Button("New Thread", systemImage: "square.and.pencil") {
                store.openNewThreadSheet()
            }
            .buttonStyle(.borderedProminent)
            .accessibilityLabel("New Thread")
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
