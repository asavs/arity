import ArityCore
import Foundation
import Observation

@MainActor
@Observable
final class AppStore {
    let client: ArityClient?
    var initError: String?

    var threads: [UiThreadSummary] = []
    var selectedThreadId: String? {
        didSet {
            if selectedThreadId != oldValue {
                selectedDetail = nil
                currentDiff = nil
                diffError = nil
                applyMessage = nil
                if let id = selectedThreadId {
                    if let summary = threads.first(where: { $0.id == id }) {
                        selectedModelOverride[id] = nil
                        lastUsedModel = summary.model
                    }
                    Task { await self.refreshSelectedDetail(threadId: id) }
                }
            }
        }
    }
    var selectedDetail: UiThreadDetail?

    var models: [UiModelOption] = []
    var providers: [UiProviderConfig] = []
    var rateLimits: [UiRateLimit] = []
    var codexAccount: String?
    var signingInCodex = false
    var replayCheckResult: UiReplayCheck?

    // Composer & per-thread drafts
    var drafts: [String: String] = [:]
    var deliveryMode: String = "steer"
    var selectedModelOverride: [String: String] = [:]
    var lastUsedModel: String = "mlx:mlx-community/Qwen3.5-4B-MLX-4bit"

    // Sheets & transient banners
    var showNewThreadSheet = false
    var showSettingsSheet = false
    var showDiffSheet = false

    var currentDiff: UiWorkspaceDiff?
    var diffLoading = false
    var diffError: String?
    var applyingChanges = false
    var applyMessage: String?

    var errorBanner: String?
    var statusBanner: String?

    private var pollingTask: Task<Void, Never>?
    private var lastIntentDate: Date = .distantPast
    private var started = false

    init() {
        let env = ProcessInfo.processInfo.environment
        let dataDir = Self.parseArg("--data-dir") ?? env["ARITY_HOME"]
        let workerBin = Self.parseArg("--worker-bin") ?? env["ARITY_WORKER_BIN"]
        do {
            let c = try ArityClient(dataDir: dataDir, workerBin: workerBin)
            self.client = c
            self.initError = nil
        } catch {
            self.client = nil
            self.initError = error.localizedDescription
            self.errorBanner = "Failed to initialize Arity client: \(error.localizedDescription)"
        }
    }

    private static func parseArg(_ flag: String) -> String? {
        let args = CommandLine.arguments
        for i in args.indices {
            if args[i] == flag, i + 1 < args.count {
                return args[i + 1]
            }
            if args[i].hasPrefix(flag + "=") {
                return String(args[i].dropFirst(flag.count + 1))
            }
        }
        return nil
    }

    var dataDirPath: String {
        client?.dataDir() ?? ""
    }

    var selectedSummary: UiThreadSummary? {
        if let detail = selectedDetail, detail.summary.id == selectedThreadId {
            return detail.summary
        }
        guard let id = selectedThreadId else { return nil }
        return threads.first { $0.id == id }
    }

    var activeModelForSelectedThread: String {
        guard let id = selectedThreadId else { return defaultModelSpec }
        if let override = selectedModelOverride[id], !override.isEmpty {
            return override
        }
        return selectedSummary?.model ?? defaultModelSpec
    }

    var defaultModelSpec: String {
        if let readyLocal = models.first(where: { $0.local && $0.ready }) {
            return readyLocal.spec
        }
        if let ready = models.first(where: { $0.ready }) {
            return ready.spec
        }
        return models.first?.spec ?? lastUsedModel
    }

    var draftForSelectedThread: String {
        get {
            guard let id = selectedThreadId else { return "" }
            return drafts[id] ?? ""
        }
        set {
            guard let id = selectedThreadId else { return }
            drafts[id] = newValue
        }
    }

    func start() {
        guard !started else { return }
        started = true
        Task {
            await loadInitialData()
            startPollingLoop()
        }
    }

    private func loadInitialData() async {
        guard let client else { return }
        let snapshot = await Task.detached(priority: .userInitiated) { () -> ([UiThreadSummary], [UiModelOption], [UiProviderConfig], [UiRateLimit], String?) in
            let threads = (try? client.listThreads()) ?? []
            let models = (try? client.availableModels()) ?? []
            let providers = (try? client.listProviders()) ?? []
            let limits = (try? client.listRateLimits()) ?? []
            let codex = client.codexAccount()
            return (threads, models, providers, limits, codex)
        }.value

        self.threads = snapshot.0
        self.models = snapshot.1
        self.providers = snapshot.2
        self.rateLimits = snapshot.3
        self.codexAccount = snapshot.4

        if self.selectedThreadId == nil, let first = self.threads.first {
            self.selectedThreadId = first.id
        }
        if let sel = self.selectedThreadId {
            await refreshSelectedDetail(threadId: sel)
        }

        // Probe local /v1/models asynchronously with 800ms timeout so UI opens instantly.
        Task.detached(priority: .utility) { [weak self] in
            guard let refreshed = try? client.refreshLocalModels() else { return }
            await MainActor.run {
                self?.models = refreshed
            }
        }
    }

    private func startPollingLoop() {
        pollingTask?.cancel()
        pollingTask = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                let anyRunning = self.threads.contains(where: { $0.isRunning })
                    || (self.selectedDetail?.summary.isRunning ?? false)
                    || Date().timeIntervalSince(self.lastIntentDate) < 2.0
                let sleepMs: UInt64 = anyRunning ? 120 : 500
                try? await Task.sleep(for: .milliseconds(sleepMs))
                if Task.isCancelled { return }
                await self.pollOnce()
            }
        }
    }

    func pollOnce() async {
        guard let client else { return }
        let targetId = self.selectedThreadId
        let result = await Task.detached(priority: .userInitiated) { () -> ([UiThreadSummary]?, UiThreadDetail?) in
            let list = try? client.listThreads()
            let detail: UiThreadDetail?
            if let tid = targetId {
                detail = try? client.threadDetail(threadId: tid)
            } else {
                detail = nil
            }
            return (list, detail)
        }.value

        if let list = result.0, list != self.threads {
            self.threads = list
        }
        if let tid = targetId, tid == self.selectedThreadId, let detail = result.1 {
            if detail != self.selectedDetail {
                self.selectedDetail = detail
                // Clear model override once the thread actually adopted that model.
                if let pending = self.selectedModelOverride[tid], pending == detail.summary.model {
                    self.selectedModelOverride[tid] = nil
                }
            }
        }
    }

    func refreshSelectedDetail(threadId: String) async {
        guard let client else { return }
        let detail = await Task.detached(priority: .userInitiated) {
            try? client.threadDetail(threadId: threadId)
        }.value
        if self.selectedThreadId == threadId, let detail {
            self.selectedDetail = detail
        }
    }

    // MARK: - Navigation / Sheets

    func openNewThreadSheet() {
        errorBanner = nil
        showNewThreadSheet = true
    }

    func openSettings() {
        errorBanner = nil
        showSettingsSheet = true
        Task { await refreshSettingsData() }
    }

    func openDiffSheet() {
        guard selectedThreadId != nil else { return }
        diffError = nil
        applyMessage = nil
        showDiffSheet = true
        Task { await loadDiffForSelectedThread() }
    }

    // MARK: - Thread Intents

    func setModelOverride(_ spec: String) {
        guard let id = selectedThreadId else { return }
        lastUsedModel = spec
        if selectedSummary?.model == spec {
            selectedModelOverride[id] = nil
        } else {
            selectedModelOverride[id] = spec
        }
    }

    func createThread(name: String?, model: String, sourceDir: String?) async -> Bool {
        guard let client else { return false }
        let trimmedName = name?.trimmingCharacters(in: .whitespacesAndNewlines)
        let finalName = (trimmedName?.isEmpty ?? true) ? nil : trimmedName
        let trimmedDir = sourceDir?.trimmingCharacters(in: .whitespacesAndNewlines)
        let finalDir = (trimmedDir?.isEmpty ?? true) ? nil : trimmedDir
        let trimmedModel = model.trimmingCharacters(in: .whitespacesAndNewlines)

        do {
            let newId = try await Task.detached(priority: .userInitiated) {
                try client.createThread(
                    threadId: finalName,
                    model: trimmedModel,
                    sourceDir: finalDir,
                    requestId: nil
                )
            }.value

            self.lastUsedModel = trimmedModel
            self.lastIntentDate = Date()
            self.errorBanner = nil
            await self.pollOnce()
            self.selectedThreadId = newId
            await self.refreshSelectedDetail(threadId: newId)
            return true
        } catch {
            self.errorBanner = error.localizedDescription
            return false
        }
    }

    func sendDraft(mode overrideMode: String? = nil) {
        guard let client, let threadId = selectedThreadId else { return }
        let raw = drafts[threadId] ?? ""
        let text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }

        let mode = overrideMode ?? deliveryMode
        let modelOverride = selectedModelOverride[threadId]
        let reqId = UUID().uuidString.lowercased()

        drafts[threadId] = ""
        errorBanner = nil
        lastIntentDate = Date()

        Task {
            do {
                _ = try await Task.detached(priority: .userInitiated) {
                    try client.sendMessage(
                        threadId: threadId,
                        text: text,
                        mode: mode,
                        modelOverride: modelOverride,
                        requestId: reqId
                    )
                }.value
                await self.pollOnce()
            } catch {
                self.drafts[threadId] = text
                self.errorBanner = error.localizedDescription
            }
        }
    }

    func stopSelectedThread() {
        guard let client, let threadId = selectedThreadId else { return }
        errorBanner = nil
        lastIntentDate = Date()
        Task {
            do {
                try await Task.detached(priority: .userInitiated) {
                    try client.stopThread(threadId: threadId, requestId: nil)
                }.value
                await self.pollOnce()
            } catch {
                self.errorBanner = error.localizedDescription
            }
        }
    }

    func resumeSelectedThread() {
        guard let client, let threadId = selectedThreadId else { return }
        errorBanner = nil
        lastIntentDate = Date()
        Task {
            do {
                _ = try await Task.detached(priority: .userInitiated) {
                    try client.spawnWorker(threadId: threadId)
                }.value
                await self.pollOnce()
            } catch {
                self.errorBanner = error.localizedDescription
            }
        }
    }

    func forkThread(fromEntryId: Int64, modelOverride: String? = nil) {
        guard let client else { return }
        errorBanner = nil
        lastIntentDate = Date()
        Task {
            do {
                let forkedId = try await Task.detached(priority: .userInitiated) {
                    try client.forkThread(
                        fromEntryId: fromEntryId,
                        newThreadId: nil,
                        modelOverride: modelOverride,
                        requestId: nil
                    )
                }.value
                await self.pollOnce()
                self.selectedThreadId = forkedId
                await self.refreshSelectedDetail(threadId: forkedId)
                self.statusBanner = "Forked into \(forkedId) at entry #\(fromEntryId)"
            } catch {
                self.errorBanner = error.localizedDescription
            }
        }
    }

    // MARK: - Workspace Diff & Apply

    func loadDiffForSelectedThread() async {
        guard let client, let threadId = selectedThreadId else { return }
        diffLoading = true
        diffError = nil
        do {
            let diff = try await Task.detached(priority: .userInitiated) {
                try client.diffThread(threadId: threadId)
            }.value
            self.currentDiff = diff
        } catch {
            self.currentDiff = nil
            self.diffError = error.localizedDescription
        }
        diffLoading = false
    }

    func applySelectedThread() async {
        guard let client, let threadId = selectedThreadId else { return }
        applyingChanges = true
        diffError = nil
        applyMessage = nil
        errorBanner = nil
        do {
            let res = try await Task.detached(priority: .userInitiated) {
                try client.applyThread(threadId: threadId, requestId: nil)
            }.value
            self.applyMessage = res.summary
            self.statusBanner = res.summary
            await self.pollOnce()
        } catch {
            self.diffError = error.localizedDescription
            if !self.showDiffSheet {
                self.errorBanner = error.localizedDescription
            }
        }
        applyingChanges = false
    }

    // MARK: - Settings & Diagnostics

    func refreshSettingsData() async {
        guard let client else { return }
        let res = await Task.detached(priority: .userInitiated) { () -> ([UiProviderConfig], [UiModelOption], [UiRateLimit], String?) in
            let providers = (try? client.listProviders()) ?? []
            let models = (try? client.availableModels()) ?? []
            let limits = (try? client.listRateLimits()) ?? []
            let codex = client.codexAccount()
            return (providers, models, limits, codex)
        }.value
        self.providers = res.0
        self.models = res.1
        self.rateLimits = res.2
        self.codexAccount = res.3
    }

    func refreshLocalModels() async {
        guard let client else { return }
        do {
            let refreshed = try await Task.detached(priority: .userInitiated) {
                try client.refreshLocalModels()
            }.value
            self.models = refreshed
            await self.refreshSettingsData()
        } catch {
            self.errorBanner = error.localizedDescription
        }
    }

    func hasKey(_ keyName: String) -> Bool {
        client?.hasKey(keyName: keyName) ?? false
    }

    func setKey(keyName: String, key: String) async {
        guard let client else { return }
        do {
            try await Task.detached(priority: .userInitiated) {
                try client.setKey(keyName: keyName, key: key)
            }.value
            await self.refreshSettingsData()
        } catch {
            self.errorBanner = error.localizedDescription
        }
    }

    func upsertProvider(
        name: String,
        url: String,
        wire: String,
        needsKey: Bool,
        keyName: String,
        sessionHeader: String?,
        models: [String]
    ) async -> Bool {
        guard let client else { return false }
        do {
            try await Task.detached(priority: .userInitiated) {
                try client.upsertProvider(
                    name: name,
                    url: url,
                    wire: wire,
                    needsKey: needsKey,
                    keyName: keyName,
                    sessionHeader: sessionHeader,
                    models: models
                )
            }.value
            await self.refreshSettingsData()
            return true
        } catch {
            self.errorBanner = error.localizedDescription
            return false
        }
    }

    func signInCodex() {
        guard let client, !signingInCodex else { return }
        signingInCodex = true
        errorBanner = nil
        Task {
            do {
                let who = try await Task.detached(priority: .userInitiated) {
                    try client.codexLogin()
                }.value
                self.codexAccount = who
                await self.refreshSettingsData()
            } catch {
                self.errorBanner = error.localizedDescription
            }
            self.signingInCodex = false
        }
    }

    func cancelCodexLogin() {
        client?.codexCancelLogin()
    }

    func signOutCodex() async {
        guard let client else { return }
        do {
            try await Task.detached(priority: .userInitiated) {
                try client.codexLogout()
            }.value
            self.codexAccount = nil
            await self.refreshSettingsData()
        } catch {
            self.errorBanner = error.localizedDescription
        }
    }

    func runReplayCheck() async {
        guard let client else { return }
        do {
            let result = try await Task.detached(priority: .userInitiated) {
                try client.replayCheck()
            }.value
            self.replayCheckResult = result
            await self.pollOnce()
        } catch {
            self.errorBanner = error.localizedDescription
        }
    }
}

// MARK: - Formatting helpers

/// "mlx:mlx-community/Qwen3.5-4B-MLX-4bit" -> "Qwen3.5-4B-MLX-4bit · mlx"
func shortModelName(_ spec: String) -> String {
    guard let colon = spec.firstIndex(of: ":") else { return spec }
    let provider = spec[..<colon]
    let rawModel = spec[spec.index(after: colon)...]
    let modelLeaf = rawModel.split(separator: "/").last.map(String.init) ?? String(rawModel)
    return "\(modelLeaf) · \(provider)"
}

func shortSha(_ sha: String?) -> String {
    guard let sha, !sha.isEmpty else { return "-" }
    return String(sha.prefix(8))
}

func folderDisplayName(_ path: String?) -> String? {
    guard let path, !path.isEmpty else { return nil }
    return URL(fileURLWithPath: path).lastPathComponent
}

func relativeTimestamp(_ ms: Int64) -> String {
    guard ms > 0 else { return "" }
    let date = Date(timeIntervalSince1970: Double(ms) / 1000.0)
    return date.formatted(.relative(presentation: .named))
}
