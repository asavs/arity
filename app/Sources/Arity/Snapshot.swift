import AppKit

/// Development aid: `ARITY_SNAPSHOT=/path/shot.png` makes the app write a PNG of
/// its own window every two seconds, so a script (or Claude) can see the UI
/// without screen-recording permission. `ARITY_OPEN=<conversation>` selects one at launch;
/// `ARITY_SETTINGS=1` opens the settings sheet.
@MainActor
enum Snapshot {
    static func startIfAsked(store: Store) {
        let env = ProcessInfo.processInfo.environment
        if let conv = env["ARITY_OPEN"] { store.selection = conv }
        if env["ARITY_SETTINGS"] != nil { store.showSettings = true }
        guard let path = env["ARITY_SNAPSHOT"] else { return }
        Task {
            while true {
                try? await Task.sleep(for: .seconds(2))
                guard let view = NSApp.windows.last(where: \.isVisible)?.contentView?.superview else { continue }
                guard let rep = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { continue }
                view.cacheDisplay(in: view.bounds, to: rep)
                try? rep.representation(using: .png, properties: [:])?.write(to: URL(fileURLWithPath: path))
            }
        }
    }
}
