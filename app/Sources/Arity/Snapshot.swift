import AppKit

/// Development aid: `ARITY_SNAPSHOT=/path/shot.png` makes the app write a PNG of
/// its own window every two seconds, so a script (or Claude) can see the UI
/// without screen-recording permission. `ARITY_OPEN=<conversation>` selects one at launch;
/// `ARITY_SETTINGS=1` opens the models sheet (`add`: with the add-provider form; `signin`: as if signing in; `workers`: with the add-worker list open);
/// `ARITY_PAPERCUT=1` opens the papercut popover.
@MainActor
enum Snapshot {
    static func startIfAsked(store: Store) {
        let env = ProcessInfo.processInfo.environment
        if let conv = env["ARITY_OPEN"] { store.selection = conv }
        if let settings = env["ARITY_SETTINGS"] {
            store.showSettings = true
            if settings == "add" { store.addingProvider = ProviderPreset.all.first }
            if settings == "signin" { store.signingIn = true }
            if settings == "workers" {
                Task { try? await Task.sleep(for: .seconds(1)); store.choosingWorker = true }
            }
        }
        if env["ARITY_PAPERCUT"] != nil {
            Task { try? await Task.sleep(for: .seconds(1)); store.filingPapercut = true }
        }
        guard let path = env["ARITY_SNAPSHOT"] else { return }
        Task {
            while true {
                try? await Task.sleep(for: .seconds(2))
                // The front-most window at `path`; any others (popovers, the window under a sheet) beside it as -1, -2, …
                let windows = NSApp.windows.filter(\.isVisible).reversed()
                for (i, window) in windows.enumerated() {
                    guard let view = window.contentView?.superview,
                          let rep = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { continue }
                    view.cacheDisplay(in: view.bounds, to: rep)
                    let file = i == 0 ? path : path.replacingOccurrences(of: ".png", with: "-\(i).png")
                    try? rep.representation(using: .png, properties: [:])?.write(to: URL(fileURLWithPath: file))
                }
            }
        }
    }
}
