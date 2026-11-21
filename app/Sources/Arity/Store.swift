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
    var tasks: [BoardTask] = []
    var models: [String] = []
    var model = ""
    /// Conversations with a model run in flight.
    var running: Set<String> = []
    var error: String?
    var signedInToChatGPT = false

    private var revision = ""

    init() {
        models = core.suggestedModels()
        model = models.first ?? ""
        signedInToChatGPT = core.chatgptSignedIn()
        refresh()
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
        if let selection { messages = attempt { try core.messages(conversation: selection) } ?? [] }
    }

    private func loadSelection() {
        error = nil
        guard let selection else { messages = []; return }
        messages = attempt { try core.messages(conversation: selection) } ?? []
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
