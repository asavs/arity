import ArityCore
import Foundation
import Observation

/// Everything the window shows, read from the Rust core. The core owns the data;
/// this only mirrors it. Polling `revision()` keeps the window current, including
/// when the CLI writes to the same journal.
@MainActor @Observable
final class Store {
    let core = Arity(dataDir: nil)

    var conversations: [Conversation] = []
    var selection: String? { didSet { if selection != oldValue { loadSelection() } } }
    var messages: [Message] = []
    /// The task card open in the selected conversation, as last edited here or in the core.
    var draft: TaskFields?
    var tasks: [BoardTask] = []
    var runs: [Int64: Run] = [:]
    var scores: [Score] = []
    var models: [String] = []
    var model = ""
    /// Conversations with a model run in flight.
    var running: Set<String> = []
    var error: String?
    var signedInToChatGPT = false
    /// The user's page, `people/<login>`: the assignee for "Me".
    let me: String

    private var revision = ""
    /// Card edits not yet written: the conversation and its fields. Written after
    /// a pause in typing, and always before a send, an add or a switch.
    private var pendingCard: (conv: String, fields: TaskFields)?
    private var pendingCardSave: Task<Void, Never>?

    init() {
        me = core.me()
        models = core.suggestedModels()
        model = models.first ?? ""
        signedInToChatGPT = core.chatgptSignedIn()
        refresh()
        startDispatcher()
        Task { [weak self] in
            while let self {
                try? await Task.sleep(for: .milliseconds(400))
                self.refresh()
            }
        }
    }

    /// Reload from the journal if anything changed since last time.
    func refresh(force: Bool = false) {
        guard let rev = attempt({ try core.revision() }), force || rev != revision else { return }
        revision = rev
        conversations = attempt { try core.conversations() } ?? []
        tasks = attempt { try core.tasks() } ?? []
        runs = Dictionary(uniqueKeysWithValues: (attempt { try core.runs() } ?? []).map { ($0.id, $0) })
        scores = attempt { try core.scores() } ?? []
        if let selection {
            messages = attempt { try core.messages(conversation: selection) } ?? []
            if pendingCard?.conv != selection { draft = attempt { try core.draft(conversation: selection) } ?? nil }
        }
    }

    private func loadSelection() {
        error = nil
        flushCard()
        guard let selection else { messages = []; draft = nil; return }
        messages = attempt { try core.messages(conversation: selection) } ?? []
        draft = attempt { try core.draft(conversation: selection) } ?? nil
        if let last = attempt({ try core.lastModel(conversation: selection) }), let last {
            model = last
            if !models.contains(last) { models.append(last) }
        }
    }

    /// A conversation exists once it has an entry, so a new one is only a name until the first send.
    func newConversation() {
        let taken = Set(conversations.map(\.name))
        selection = (1...).lazy.map { "chat-\($0)" }.first { !taken.contains($0) }
    }

    func send(_ text: String) {
        guard let conv = selection else { return }
        flushCard()
        run(conv) { core, model in try core.send(conversation: conv, model: model, text: text) }
    }

    /// Carry on after a failed step. The journal knows where it stopped.
    func resume() {
        guard let conv = selection else { return }
        run(conv) { core, model in try core.resume(conversation: conv, model: model) }
    }

    func fork(at entry: Int64) {
        guard let conv = selection else { return }
        let taken = Set(conversations.map(\.name))
        let name = (1...).lazy.map { "\(conv)-fork\($0)" }.first { !taken.contains($0) }!
        if attempt({ try core.fork(entry: entry, newConversation: name) }) != nil {
            refresh(force: true)
            selection = name
        }
    }

    // Task cards

    /// A new conversation with the selected model and an empty card. The
    /// model's first job there is to help fill it in.
    func newTask() {
        newConversation()
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

    // The board

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

    func signInToChatGPT() {
        let core = self.core
        Task.detached {
            let result = Result { try core.signInChatgpt() }
            await MainActor.run {
                if case .failure(let e) = result { self.error = Self.describe(e) }
                self.signedInToChatGPT = core.chatgptSignedIn()
            }
        }
    }

    /// Model calls block, so they run off the main thread. Progress shows up through polling.
    private func run(_ conv: String, _ work: @escaping @Sendable (Arity, String) throws -> Void) {
        error = nil
        running.insert(conv)
        let core = self.core, model = self.model
        Task.detached {
            let result = Result { try work(core, model) }
            await MainActor.run {
                self.running.remove(conv)
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
