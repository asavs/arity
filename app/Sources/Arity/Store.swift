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
    /// The folder attached to the selected chat, enabling file and shell tools in it.
    var folder: String?
    /// What the selected run changed in its own git worktree, for review before it is applied.
    /// Nil for a chat, or a run whose folder is not in a git repo.
    var worktreeDiff: WorktreeDiffInfo? = nil
    /// An apply or discard is running, off the main thread.
    var worktreeBusy = false
    /// Tokens the selected thread used and how full its context is, as its providers reported them.
    var conversationUsage: ConversationUsage?
    var scores: [Score] = []
    /// What the configured providers offer, and which of them tasks for agents are picked from.
    var models: [String] = []
    var workers: [String] = []
    /// User-configured hooks from `config.json`, built-in hooks, and registered tools.
    var hooks: [HookConfig] = []
    var builtinHooks: [BuiltinHookInfo] = []
    var tools: [ToolInfo] = []
    /// OpenAI, OpenCode, Nous, this Mac, and any the user added, each with its plans.
    var accounts: [Account] = []
    /// What checking each provider's key found. Missing while a check runs.
    var keyChecks: [String: KeyCheck] = [:]
    /// The model this chat uses: picked from `choice` at its first message, or named with Other….
    var model = ""
    /// Smart, fast, free or private; nil when a model was named. Remembered between launches.
    var choice: Choice? = Choice(stored: UserDefaults.standard.string(forKey: "choice")) {
        didSet {
            UserDefaults.standard.set(choice?.stored ?? "other", forKey: "choice")
            if choice != oldValue { choiceChanged = true }
        }
    }
    /// Why the pick came to `model`, in a few words, until another chat is selected.
    var pickReason: String?
    /// The choice changed since this chat's model was picked: the next message picks again.
    private var choiceChanged = false
    /// The searchable list for naming a model (Other…) is open.
    var choosingModel = false
    /// A message that could not be sent, handed back to the composer.
    var unsent: String?
    var showSettings = false
    /// The papercut popover is open.
    var filingPapercut = false
    /// The searchable list for adding a worker is open.
    var choosingWorker = false
    /// The add-provider form, open with these values.
    var addingProvider: ProviderPreset?
    /// The hook editor sheet is open; `editingHook` holds the hook being edited, or nil for a new one.
    var addingHook = false
    var editingHook: HookConfig?
    /// Conversations with a turn running, here or in another process (the CLI, `arity work`).
    var running: Set<String> { starting.union(active) }
    /// Sends from this window not yet seen running in the core.
    private var starting: Set<String> = []
    private var active: Set<String> = []
    var error: String? { didSet { if error == nil { errorIsWorktree = false } } }
    /// The error came from applying or discarding a worktree, which Retry would not help.
    private(set) var errorIsWorktree = false
    /// The signed-in ChatGPT account's email, or nil.
    var chatgptAccount: String?
    /// A ChatGPT sign-in is waiting for the browser.
    var signingIn = false
    /// The user's page, `people/<login>`: the assignee for "Me".
    let me: String

    var thread: Thread? { threads.first { $0.name == selection } }
    /// The selected thread stopped short and nothing runs in it: a reply cut off, a message or
    /// task result not answered yet, or a run stopped or failing. Continue carries it on.
    var canContinue: Bool { thread?.canContinue ?? false }
    /// Runs below the selected thread that are going or about to start, at any depth.
    var activeChildren: UInt32 { thread?.activeChildren ?? 0 }
    /// Selected thread is a task with an agent run; its model is the trial's, kept inside the core.
    var isWorkerRun: Bool { thread?.task?.run != nil }
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
            folder = attempt { try core.folder(conversation: selection) } ?? nil
            conversationUsage = attempt { try core.usage(conversation: selection) }
            loadWorktreeDiff()
        }
    }

    private var diffLoading = false
    private var diffStale = false

    /// Reread what the selected run changed in its worktree. Git does the work, so it runs off
    /// the main thread; a request made while one runs makes it go round again once it is done.
    private func loadWorktreeDiff() {
        guard let conv = selection, thread?.task?.run != nil else { worktreeDiff = nil; return }
        if diffLoading { diffStale = true; return }
        diffLoading = true
        let core = self.core
        Task.detached(priority: .utility) {
            let result = Result { try core.worktreeDiff(conversation: conv) }
            await MainActor.run {
                self.diffLoading = false
                if self.selection == conv {
                    let diff = (try? result.get()) ?? nil
                    if diff != self.worktreeDiff { self.worktreeDiff = diff }
                }
                if self.diffStale { self.diffStale = false; self.loadWorktreeDiff() }
            }
        }
    }

    /// Apply the selected run's changes to the real checkout and remove its worktree.
    /// A conflict with edits already in the checkout shows as the error, and nothing changes.
    func applyWorktree() {
        guard let conv = selection else { return }
        changeWorktree(conv) { _ = try $0.applyWorktree(conversation: conv) }
    }

    /// Throw the selected run's worktree away without applying it.
    func discardWorktree() {
        guard let conv = selection else { return }
        changeWorktree(conv) { _ = try $0.discardWorktree(conversation: conv) }
    }

    private func changeWorktree(_ conv: String, _ work: @escaping @Sendable (Arity) throws -> Void) {
        guard !worktreeBusy else { return }
        error = nil
        worktreeBusy = true
        let core = self.core
        Task.detached {
            let result = Result { try work(core) }
            await MainActor.run {
                self.worktreeBusy = false
                if case .failure(let e) = result, self.selection == conv {
                    self.error = Self.describe(e)
                    self.errorIsWorktree = true
                }
                self.refresh(force: true)
            }
        }
    }

    private func loadSelection() {
        error = nil
        flushCard()
        pickReason = nil
        choiceChanged = false
        worktreeDiff = nil
        guard let selection else { messages = []; draft = nil; folder = nil; conversationUsage = nil; return }
        messages = attempt { try core.messages(conversation: selection) } ?? []
        folder = attempt { try core.folder(conversation: selection) } ?? nil
        conversationUsage = attempt { try core.usage(conversation: selection) }
        loadWorktreeDiff()
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

    /// A chat keeps the model its first message was picked for, so a conversation
    /// does not change model part way; a new choice picks again from the next message.
    /// `how` is when it joins a turn already running there, as for any participant's
    /// message. Inbox starts nothing: it waits there for the chat's next turn.
    func send(_ text: String, how: Delivery = .steer) {
        guard let conv = selection else { return }
        flushCard()
        let worker = isWorkerRun
        if how == .inbox {
            _ = attempt { try core.sendAs(conversation: conv, model: worker ? "" : model, text: text, how: .inbox) }
            refresh(force: true)
            return
        }
        let pick = worker ? nil : choice.flatMap { messages.isEmpty || choiceChanged || model.isEmpty ? $0 : nil }
        if !worker { choiceChanged = false }
        run(conv, pick: pick, fixedModel: worker ? "" : nil, unsent: text) { core, model in
            try core.sendAs(conversation: conv, model: model, text: text, how: how)
        }
    }

    /// Other…: use this model for the chat from the next message.
    func chooseModel(_ name: String) {
        choice = nil
        choiceChanged = false
        model = name
        pickReason = nil
        choosingModel = false
    }

    /// Stop the turn running in the selected thread, wherever it runs. The reply so far is kept.
    func stop() {
        guard let conv = selection else { return }
        _ = attempt { try core.stop(conversation: conv) }
    }

    /// Stop the selected thread and every task it handed out, at any depth. Stopped runs wait
    /// to be continued; tasks not started yet go back to the user's own list.
    func stopAll() {
        guard let conv = selection else { return }
        _ = attempt { try core.stopSubtree(conversation: conv) }
        refresh(force: true)
    }

    /// Carry on after a failed step. The journal knows where it stopped.
    func resume() {
        guard let conv = selection else { return }
        let worker = isWorkerRun
        run(conv, fixedModel: worker ? "" : nil) { core, model in
            try core.resume(conversation: conv, model: model)
        }
    }

    /// Carry on where the selected thread stopped short, writing nothing to ask for it:
    /// a reply cut off by Stop, an unanswered message or task result, a stopped run.
    func continueTurn() {
        guard let conv = selection else { return }
        let worker = isWorkerRun
        run(conv, fixedModel: worker ? "" : nil) { core, model in
            _ = try core.continueTurn(conversation: conv, model: model.isEmpty ? nil : model)
        }
    }

    /// The error banner's Retry: continue where it stopped when there is something to carry on.
    func retry() {
        if canContinue { continueTurn() } else { resume() }
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

    // Context

    /// Replace what the model reads in the selected thread with a summary it writes. The thread
    /// keeps every message. Blocks on a model call, so it runs off the main thread.
    func compact(instructions: String? = nil) {
        guard let conv = selection else { return }
        error = nil
        starting.insert(conv)
        let core = self.core
        Task.detached {
            let result = Result { try core.compact(conversation: conv, instructions: instructions) }
            await MainActor.run {
                self.starting.remove(conv)
                if case .failure(let e) = result, self.selection == conv { self.error = Self.describe(e) }
                self.refresh(force: true)
            }
        }
    }

    /// The model starts afresh from here; the thread keeps every message and can still be forked.
    func resetContext() {
        guard let conv = selection else { return }
        error = nil
        _ = attempt { try core.resetContext(conversation: conv, note: nil) }
        refresh(force: true)
    }

    // Task cards

    /// Attach an empty task card to the selected chat. The model's next job
    /// there is to help fill it in.
    /// Attach a folder to the selected chat, or with nil take it away. Works before the first message.
    func setFolder(_ dir: String?) {
        guard let conv = selection else { return }
        _ = attempt { try core.setFolder(conversation: conv, dir: dir) }
        folder = attempt { try core.folder(conversation: conv) } ?? nil
    }

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

    /// A papercut: a note that something got in the way, onto the user's own
    /// list. Filed in the selected chat, if one is open; a task's thread is
    /// named in About instead, since filing in a run's conversation would make
    /// it the run's. Leaves the selection and any open card as they are.
    func filePapercut(_ note: String, about: String) -> Bool {
        let chat = thread.flatMap { $0.task == nil ? $0.name : nil }
        var about = about.trimmingCharacters(in: .whitespacesAndNewlines)
        if about.isEmpty, let task = thread?.task { about = "task \(task.id), \"\(task.fields.title)\"" }
        let filed = attempt { try core.filePapercut(conversation: chat, note: note, about: about.isEmpty ? nil : about) }
        refresh(force: true)
        return filed != nil
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
        loadHarness()
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

    // Hooks & tools

    func loadHarness() {
        builtinHooks = core.builtinHooks()
        hooks = attempt { try core.hooks() } ?? hooks
        tools = attempt { try core.tools(conversation: selection) } ?? tools
    }

    @discardableResult
    func saveHook(_ hook: HookConfig) -> Bool {
        error = nil
        guard attempt({ try core.saveHook(hook: hook) }) != nil else { return false }
        hooks = attempt { try core.hooks() } ?? hooks
        return true
    }

    func removeHook(_ name: String) {
        _ = attempt { try core.removeHook(name: name) }
        hooks = attempt { try core.hooks() } ?? hooks
    }

    func setHookEnabled(_ name: String, _ enabled: Bool) {
        _ = attempt { try core.setHookEnabled(name: name, enabled: enabled) }
        hooks = attempt { try core.hooks() } ?? hooks
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
    /// With `pick`, the model is picked first; if that fails nothing is sent and
    /// `unsent` goes back to the composer.
    private func run(_ conv: String, pick: Choice? = nil, fixedModel: String? = nil, unsent: String? = nil,
                     _ work: @escaping @Sendable (Arity, String) throws -> Void) {
        error = nil
        starting.insert(conv)
        let core = self.core, named = fixedModel ?? self.model
        Task.detached {
            var model = named
            if let pick {
                switch Result(catching: { try core.pick(choice: pick) }) {
                case .success(let p):
                    model = p.model
                    await MainActor.run {
                        if self.selection == conv { self.model = p.model; self.pickReason = p.reason }
                    }
                case .failure(let e):
                    await MainActor.run {
                        self.starting.remove(conv)
                        if self.selection == conv { self.error = Self.describe(e); self.unsent = unsent }
                    }
                    return
                }
            }
            let chosen = model
            let result = Result { try work(core, chosen) }
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

extension Choice {
    static let all: [Choice] = [.smart, .fast, .free, .private]

    init?(stored: String?) {
        switch stored {
        case "fast": self = .fast
        case "free": self = .free
        case "private": self = .private
        case "other": return nil
        default: self = .smart
        }
    }

    var stored: String {
        switch self {
        case .smart: "smart"
        case .fast: "fast"
        case .free: "free"
        case .private: "private"
        }
    }

    var title: String { stored.capitalized }

    /// What it promises, for the subtitle before the first message and the tooltip.
    var blurb: String {
        switch self {
        case .smart: "The best model you can reach"
        case .fast: "The quickest model you can reach"
        case .free: "Local models, or a subscription with quota left; never a paid key"
        case .private: "Only models on this Mac"
        }
    }
}
