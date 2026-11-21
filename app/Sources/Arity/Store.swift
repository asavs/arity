import ArityCore
import Foundation
import Observation

/// Everything the window shows, read from the Rust core. The core owns the data;
/// this only mirrors it. Polling `revision()` keeps the window current, including
/// when the CLI writes to the same journal.
@MainActor @Observable
final class Store {
    let core = Arity(dataDir: nil)

    /// Chats and tasks in one list, in the order the sidebar shows them.
    var threads: [Thread] = []
    /// The selected thread's name.
    var selection: String? { didSet { if selection != oldValue { loadSelection() } } }
    var messages: [Message] = []
    /// The task card open in the selected chat, as last edited here or in the core.
    var draft: TaskFields?
    var scores: [Score] = []
    /// What the configured providers offer, and which of them tasks for agents are picked from.
    var models: [String] = []
    var workers: [String] = []
    /// OpenAI, OpenCode, Nous, this Mac, and any the user added, each with its plans.
    var accounts: [Account] = []
    /// What checking each provider's key found. Missing while a check runs.
    var keyChecks: [String: KeyCheck] = [:]
    var model = ""
    var showSettings = false
    /// The searchable list for adding a worker is open.
    var choosingWorker = false
    /// The add-provider form, open with these values.
    var addingProvider: ProviderPreset?
    /// Conversations with a turn running, here or in another process (the CLI, `arity work`).
    var running: Set<String> { starting.union(active) }
    /// Sends from this window not yet seen running in the core.
    private var starting: Set<String> = []
    private var active: Set<String> = []
    var error: String?
    /// The signed-in ChatGPT account's email, or nil.
    var chatgptAccount: String?
    /// A ChatGPT sign-in is waiting for the browser.
    var signingIn = false
    /// The user's page, `people/<login>`: the assignee for "Me".
    let me: String

    var thread: Thread? { threads.first { $0.name == selection } }
    var tasks: [BoardTask] { threads.compactMap(\.task) }

    private var revision = ""
    /// Card edits not yet written: the conversation and its fields. Written after
    /// a pause in typing, and always before a send, an add or a switch.
    private var pendingCard: (conv: String, fields: TaskFields)?
    private var pendingCardSave: Task<Void, Never>?

    init() {
        me = core.me()
        chatgptAccount = core.chatgptAccount()
        loadModels()
        refresh()
        startDispatcher()
        Task { [weak self] in
            var tick = 0
            while let self {
                try? await Task.sleep(for: .milliseconds(80))
                tick += 1
                // While the selected thread's reply grows, reread just its messages, often,
                // so the text flows; everything else is checked a couple of times a second.
                if let conv = self.selection, self.running.contains(conv) {
                    let latest = (try? self.core.messages(conversation: conv)) ?? self.messages
                    if latest != self.messages { self.messages = latest }
                }
                if tick % 5 == 0 { self.refresh() }
            }
        }
    }

    /// Reload from the journal if anything changed since last time.
    func refresh(force: Bool = false) {
        guard let rev = attempt({ try core.revision() }), force || rev != revision else { return }
        revision = rev
        threads = attempt { try core.threads() } ?? []
        active = Set(attempt { try core.running() } ?? [])
        scores = attempt { try core.scores() } ?? []
        if let selection {
            messages = attempt { try core.messages(conversation: selection) } ?? []
            if pendingCard?.conv != selection { draft = thread?.card }
        }
    }

    private func loadSelection() {
        error = nil
        flushCard()
        guard let selection else { messages = []; draft = nil; return }
        messages = attempt { try core.messages(conversation: selection) } ?? []
        draft = thread?.card
        if let last = attempt({ try core.lastModel(conversation: selection) }), let last {
            model = last
            if !models.contains(last) { models.append(last) }
        }
    }

    /// A chat exists once something is said in it, so a new one is only a name until the first send.
    func newChat() {
        let taken = Set(threads.map(\.name))
        selection = (1...).lazy.map { "chat-\($0)" }.first { !taken.contains($0) }
    }

    func send(_ text: String) {
        guard let conv = selection else { return }
        flushCard()
        run(conv) { core, model in try core.send(conversation: conv, model: model, text: text) }
    }

    /// Stop the turn running in the selected thread, wherever it runs. The reply so far is kept.
    func stop() {
        guard let conv = selection else { return }
        _ = attempt { try core.stop(conversation: conv) }
    }

    /// Carry on after a failed step. The journal knows where it stopped.
    func resume() {
        guard let conv = selection else { return }
        run(conv) { core, model in try core.resume(conversation: conv, model: model) }
    }

    func fork(at entry: Int64) {
        guard let conv = selection else { return }
        let taken = Set(threads.map(\.name))
        let name = (1...).lazy.map { "\(conv)-fork\($0)" }.first { !taken.contains($0) }!
        if attempt({ try core.fork(entry: entry, newConversation: name) }) != nil {
            refresh(force: true)
            selection = name
        }
    }

    // Task cards

    /// Attach an empty task card to the selected chat. The model's next job
    /// there is to help fill it in.
    func attachCard() {
        guard let conv = selection else { return }
        _ = attempt { try core.openDraft(conversation: conv) }
        refresh(force: true)
    }

    /// The person edited the card. Shown at once, written after a short pause.
    func editCard(_ fields: TaskFields) {
        guard let conv = selection, fields != draft else { return }
        draft = fields
        pendingCard = (conv, fields)
        pendingCardSave?.cancel()
        pendingCardSave = Task { [weak self] in
            try? await Task.sleep(for: .seconds(1.5))
            guard !Task.isCancelled else { return }
            self?.flushCard()
        }
    }

    private func flushCard() {
        pendingCardSave?.cancel()
        guard let (conv, fields) = pendingCard else { return }
        pendingCard = nil
        _ = attempt { try core.editDraft(conversation: conv, fields: fields) }
    }

    func addCard() {
        guard let conv = selection else { return }
        flushCard()
        _ = attempt { try core.addDraft(conversation: conv) }
        refresh(force: true)
    }

    func dismissCard() {
        guard let conv = selection else { return }
        pendingCardSave?.cancel()
        pendingCard = nil
        _ = attempt { try core.dismissDraft(conversation: conv) }
        refresh(force: true)
    }

    // Tasks

    func updateTask(_ id: Int64, _ fields: TaskFields) {
        _ = attempt { try core.updateTask(id: id, fields: fields) }
        refresh(force: true)
    }

    func setDone(_ task: BoardTask, _ done: Bool) {
        _ = attempt { try core.setTaskDone(id: task.id, done: done) }
        refresh(force: true)
    }

    func rate(run: Int64, score: Int64, note: String) {
        _ = attempt { try core.rate(run: run, score: score, note: note) }
        refresh(force: true)
    }

    /// While the app is open it runs tasks meant for agents and delivers their
    /// results. `arity work` can run alongside; leases keep them from doubling up.
    private func startDispatcher() {
        let core = self.core
        Task.detached(priority: .utility) {
            while true {
                let did = (try? core.workOnce()) ?? nil
                if did == nil { try? await Task.sleep(for: .seconds(1)) }
            }
        }
    }

    // Providers, keys and models

    /// Model lists are fetched from each provider and cached, so this runs off the main thread.
    func loadModels(refresh: Bool = false) {
        accounts = attempt { try core.accounts() } ?? []
        workers = attempt { try core.workerModels() } ?? []
        let core = self.core
        Task.detached {
            let result = Result { try core.models(refresh: refresh) }
            await MainActor.run {
                switch result {
                case .success(let list):
                    let current = self.model
                    self.models = list
                    if !current.isEmpty && !list.contains(current) { self.models.append(current) }
                    if self.model.isEmpty { self.model = list.first ?? "" }
                case .failure(let e): self.error = Self.describe(e)
                }
            }
        }
    }

    /// Store a provider's API key (empty removes it), then check it. Its models then appear.
    func setKey(_ provider: String, _ key: String) {
        _ = attempt { try core.setKey(provider: provider, key: key) }
        loadModels(refresh: true)
        checkKeys()
    }

    /// Check every saved key, off the main thread, without running a model.
    func checkKeys() {
        let core = self.core
        for a in accounts where a.hasKey {
            for p in a.plans where p.access == .key {
                keyChecks[p.provider] = nil
                Task.detached {
                    let check = (try? core.checkProvider(name: p.provider)) ?? .unreachable(reason: "the check failed")
                    await MainActor.run { self.keyChecks[p.provider] = check }
                }
            }
        }
    }

    func setWorker(_ model: String, _ on: Bool) {
        _ = attempt { try core.setWorker(model: model, on: on) }
        workers = attempt { try core.workerModels() } ?? workers
    }

    /// Opens the browser; the sheet shows progress until the sign-in comes back or is cancelled.
    func signInToChatGPT() {
        error = nil
        signingIn = true
        let core = self.core
        Task.detached {
            let result = Result { try core.signInChatgpt() }
            await MainActor.run {
                self.signingIn = false
                if case .failure(let e) = result, !Self.describe(e).contains("cancelled") { self.error = Self.describe(e) }
                self.chatgptAccount = core.chatgptAccount()
                self.loadModels(refresh: true)
            }
        }
    }

    func cancelSignIn() {
        core.cancelChatgptSignIn()
    }

    func signOutOfChatGPT() {
        _ = attempt { try core.signOutChatgpt() }
        chatgptAccount = core.chatgptAccount()
        loadModels()
    }

    /// Add a provider, and its key if one was given. False if the core refused it.
    func addProvider(_ name: String, _ url: String, needsKey: Bool, key: String) -> Bool {
        error = nil
        guard attempt({ try core.addProvider(name: name, url: url, needsKey: needsKey) }) != nil else { return false }
        if needsKey && !key.isEmpty { _ = attempt { try core.setKey(provider: name, key: key) } }
        loadModels(refresh: true)
        return true
    }

    func removeProvider(_ name: String) {
        _ = attempt { try core.removeProvider(name: name) }
        loadModels()
    }

    /// Model calls block, so they run off the main thread. Progress, the reply
    /// growing included, shows up through polling. A send while a turn is
    /// running returns at once; the running turn answers it next.
    private func run(_ conv: String, _ work: @escaping @Sendable (Arity, String) throws -> Void) {
        error = nil
        starting.insert(conv)
        let core = self.core, model = self.model
        Task.detached {
            let result = Result { try work(core, model) }
            await MainActor.run {
                self.starting.remove(conv)
                if case .failure(let e) = result, self.selection == conv { self.error = Self.describe(e) }
                self.refresh(force: true)
            }
        }
    }

    private func attempt<T>(_ body: () throws -> T) -> T? {
        do { return try body() } catch { self.error = Self.describe(error); return nil }
    }

    nonisolated static func describe(_ error: Error) -> String {
        if case ArityError.Failed(let message) = error { return message }
        return error.localizedDescription
    }
}
