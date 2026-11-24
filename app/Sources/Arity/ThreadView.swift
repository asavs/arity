import AppKit
import ArityCore
import SwiftUI

private extension View {
    func chatColumn() -> some View {
        frame(maxWidth: 760)
            .padding(.horizontal, 20)
            .frame(maxWidth: .infinity)
    }
}

struct ThreadView: View {
    @Environment(AppStore.self) private var store
    @Environment(\.colorScheme) private var colorScheme
    @FocusState private var composerFocused: Bool

    private var summary: UiThreadSummary? { store.selectedSummary }
    private var detail: UiThreadDetail? { store.selectedDetail }
    private var isRunning: Bool { summary?.isRunning ?? false }
    private var isApplied: Bool { summary?.status == "applied" }

    var body: some View {
        VStack(spacing: 0) {
            headerBar
            Divider()

            if let banner = store.errorBanner {
                alertBanner(text: banner, icon: "exclamationmark.triangle.fill", tint: .orange) {
                    store.errorBanner = nil
                }
                Divider()
            } else if let status = store.statusBanner {
                alertBanner(text: status, icon: "checkmark.circle.fill", tint: .green) {
                    store.statusBanner = nil
                }
                Divider()
            }

            if let summary, summary.status == "halted", !isRunning {
                haltedBanner(summary)
                Divider()
            } else if let detail, detail.needsWork, !isRunning, detail.summary.status != "applied" {
                resumeBanner
                Divider()
            }

            TranscriptScrollView(
                items: detail?.items ?? [],
                partialText: detail?.partialText ?? "",
                activeModel: summary?.model ?? store.activeModelForSelectedThread,
                isRunning: isRunning,
                onFork: { entryId in
                    store.forkThread(fromEntryId: entryId)
                }
            )
            .id(store.selectedThreadId)

            Divider()

            composerBar
                .chatColumn()
                .padding(.top, 10)
                .padding(.bottom, 14)
        }
        .navigationTitle(summary?.id ?? "Thread")
        .navigationSubtitle(subtitleText)
        .onAppear { composerFocused = true }
    }

    private var subtitleText: String {
        guard let summary else { return "" }
        var parts: [String] = [shortModelName(summary.model)]
        if let folder = folderDisplayName(summary.sourceDir) {
            parts.append(folder)
        }
        if summary.isRunning {
            parts.append("running")
        } else if summary.status != "idle" {
            parts.append(summary.status)
        }
        return parts.joined(separator: " · ")
    }

    // MARK: - Thread Header Bar

    private var headerBar: some View {
        HStack(spacing: 10) {
            if let folder = folderDisplayName(summary?.sourceDir) {
                HStack(spacing: 5) {
                    Image(systemName: "folder")
                        .foregroundStyle(.secondary)
                    Text(folder)
                        .font(.callout)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
                .help(summary?.sourceDir ?? "")
            }

            if let sha = summary?.lastTreeSha, !sha.isEmpty {
                Text(shortSha(sha))
                    .font(.caption.monospaced())
                    .foregroundStyle(.secondary)
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(.quaternary, in: Capsule())
                    .help("Current workspace tree SHA: \(sha)")
            }

            Spacer(minLength: 8)

            modelPickerMenu

            if summary?.sourceDir != nil {
                Button {
                    store.openDiffSheet()
                } label: {
                    Label(
                        summary?.hasChanges == true ? "Changes" : "Diff",
                        systemImage: "arrow.triangle.branch"
                    )
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
                .tint(summary?.hasChanges == true ? .accentColor : nil)
                .accessibilityLabel("Review Changes")
                .help("Inspect isolated worktree diff and apply changes (⌘⇧D)")

                if summary?.hasChanges == true && !isRunning && !isApplied {
                    Button {
                        Task { await store.applySelectedThread() }
                    } label: {
                        Label("Apply", systemImage: "square.and.arrow.down.on.square")
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.small)
                    .disabled(store.applyingChanges)
                    .accessibilityLabel("Apply Workspace Changes")
                    .accessibilityIdentifier("apply-workspace-changes-button")
                    .help("Apply isolated worktree changes directly to checkout")
                }
            }

            if isRunning {
                Button {
                    store.stopSelectedThread()
                } label: {
                    Label("Stop", systemImage: "stop.fill")
                }
                .buttonStyle(.bordered)
                .tint(.orange)
                .controlSize(.small)
                .accessibilityLabel("Stop Turn")
                .help("Request the active worker to stop cleanly")
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
        .background(.bar)
    }

    private var modelPickerMenu: some View {
        let current = store.activeModelForSelectedThread
        let grouped = Dictionary(grouping: store.models, by: \.provider)
        let providerNames = grouped.keys.sorted()

        return Menu {
            ForEach(providerNames, id: \.self) { provider in
                Section(provider) {
                    ForEach(grouped[provider] ?? [], id: \.spec) { option in
                        Button {
                            store.setModelOverride(option.spec)
                        } label: {
                            HStack {
                                Text(option.modelId)
                                if option.spec == current {
                                    Image(systemName: "checkmark")
                                }
                            }
                        }
                    }
                }
            }
            Divider()
            Button("Refresh Local Models") {
                Task { await store.refreshLocalModels() }
            }
        } label: {
            HStack(spacing: 4) {
                Text(shortModelName(current))
                    .font(.callout)
                    .lineLimit(1)
                Image(systemName: "chevron.down")
                    .font(.system(size: 9, weight: .semibold))
                    .foregroundStyle(.secondary)
            }
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(.quaternary.opacity(0.6), in: RoundedRectangle(cornerRadius: 6))
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.hidden)
        .fixedSize(horizontal: true, vertical: false)
        .accessibilityLabel("Model Picker")
        .help("Choose model for the next turn (\(current))")
    }

    // MARK: - Banners

    private func alertBanner(
        text: String,
        icon: String,
        tint: Color,
        dismiss: @escaping () -> Void
    ) -> some View {
        HStack(alignment: .center, spacing: 8) {
            Image(systemName: icon)
                .foregroundStyle(tint)
            Text(text)
                .font(.callout)
                .textSelection(.enabled)
            Spacer()
            Button("Dismiss", action: dismiss)
                .buttonStyle(.borderless)
                .font(.caption)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
        .background(tint.opacity(0.10))
    }

    private func haltedBanner(_ summary: UiThreadSummary) -> some View {
        HStack(alignment: .center, spacing: 10) {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundStyle(.orange)
            VStack(alignment: .leading, spacing: 2) {
                Text("Thread halted")
                    .font(.callout.weight(.medium))
                if let err = summary.lastError, !err.isEmpty {
                    Text(err)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .textSelection(.enabled)
                        .lineLimit(3)
                }
            }
            Spacer()
            if detail?.needsWork == true {
                Button("Resume", systemImage: "play.fill") {
                    store.resumeSelectedThread()
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.small)
                .accessibilityLabel("Resume Turn")
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
        .background(Color.orange.opacity(0.10))
    }

    private var resumeBanner: some View {
        HStack(spacing: 10) {
            Image(systemName: "pause.circle")
                .foregroundStyle(.secondary)
            Text("Pending work is ready to resume.")
                .font(.callout)
                .foregroundStyle(.secondary)
            Spacer()
            Button("Resume", systemImage: "play.fill") {
                store.resumeSelectedThread()
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.small)
            .accessibilityLabel("Resume Turn")
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 7)
        .background(.background.secondary)
    }

    // MARK: - Pill Composer

    private var composerBar: some View {
        @Bindable var store = store
        let draftBinding = Binding<String>(
            get: { store.draftForSelectedThread },
            set: { store.draftForSelectedThread = $0 }
        )
        let isEmpty = store.draftForSelectedThread.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        let shell = RoundedRectangle(cornerRadius: 24, style: .continuous)

        return VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .bottom, spacing: 10) {
                TextField(
                    isApplied
                        ? "Workspace applied — fork an entry to continue"
                        : (isRunning ? "Steer the active turn…" : "Message Arity…"),
                    text: draftBinding,
                    axis: .vertical
                )
                .textFieldStyle(.plain)
                .font(.system(size: 15))
                .lineLimit(1...8)
                .focused($composerFocused)
                .disabled(isApplied)
                .accessibilityLabel("Message Input")
                .onSubmit {
                    if !isEmpty && !isApplied {
                        store.sendDraft()
                    }
                }
                .padding(.leading, 8)
                .padding(.vertical, 4)

                deliveryModePicker

                if isRunning {
                    Button {
                        store.stopSelectedThread()
                    } label: {
                        Image(systemName: "stop.fill")
                            .font(.system(size: 12, weight: .semibold))
                            .foregroundStyle(.white)
                            .frame(width: 30, height: 30)
                            .background(Color.orange, in: Circle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("Stop Turn")
                    .help("Stop active turn")
                }

                Button {
                    store.sendDraft()
                } label: {
                    Image(systemName: isRunning ? "arrow.turn.down.right" : "arrow.up")
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundStyle(isEmpty || isApplied ? Color.secondary : .white)
                        .frame(width: 30, height: 30)
                        .background(
                            isEmpty || isApplied ? Color.secondary.opacity(0.14) : Color.accentColor,
                            in: Circle()
                        )
                }
                .buttonStyle(.plain)
                .disabled(isEmpty || isApplied)
                .accessibilityLabel("Send Message")
                .help(isRunning ? "Send steer / delivery (↩)" : "Send message (↩)")
            }
            .padding(9)
            .background {
                shell.fill(colorScheme == .dark ? Color(white: 0.19) : .white)
                    .shadow(color: .black.opacity(0.10), radius: 4, y: 2)
            }
            .overlay(shell.strokeBorder(.primary.opacity(0.10)).allowsHitTesting(false))
        }
    }

    private var deliveryModePicker: some View {
        @Bindable var store = store
        return Menu {
            Picker("Delivery Mode", selection: $store.deliveryMode) {
                Text("Steer").tag("steer")
                Text("After Turn").tag("after_turn")
                Text("Inbox Note").tag("inbox")
            }
        } label: {
            Text(deliveryModeLabel(store.deliveryMode))
                .font(.caption)
                .foregroundStyle(.secondary)
                .padding(.horizontal, 7)
                .padding(.vertical, 4)
                .background(.quaternary.opacity(0.5), in: Capsule())
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.hidden)
        .fixedSize()
        .accessibilityLabel("Delivery Mode")
        .help("Delivery mode: Steer interrupts immediately, After Turn queues for after current turn, Inbox stores without starting a turn")
    }

    private func deliveryModeLabel(_ mode: String) -> String {
        switch mode {
        case "after_turn": return "After Turn"
        case "inbox": return "Inbox"
        default: return "Steer"
        }
    }
}

// MARK: - Transcript Scroll View

private struct TranscriptScrollView: View {
    let items: [UiTranscriptItem]
    let partialText: String
    let activeModel: String
    let isRunning: Bool
    let onFork: (Int64) -> Void

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 14) {
                    if items.isEmpty && partialText.isEmpty && !isRunning {
                        VStack(alignment: .leading, spacing: 6) {
                            Text("Ready")
                                .font(.headline)
                            Text("Send a message below to start a turn.")
                                .font(.callout)
                                .foregroundStyle(.secondary)
                        }
                        .padding(.top, 24)
                    }

                    ForEach(Array(items.enumerated()), id: \.offset) { _, item in
                        TranscriptItemRow(item: item, onFork: onFork)
                    }

                    if isRunning {
                        LiveStreamingRow(model: activeModel, partialText: partialText)
                            .id("live-stream-tail")
                    }

                    Color.clear
                        .frame(height: 1)
                        .id("transcript-bottom")
                }
                .chatColumn()
                .padding(.vertical, 18)
            }
            .onChange(of: items.count) { _, _ in
                withAnimation(.easeOut(duration: 0.15)) {
                    proxy.scrollTo("transcript-bottom", anchor: .bottom)
                }
            }
            .onChange(of: partialText.count) { _, _ in
                proxy.scrollTo("transcript-bottom", anchor: .bottom)
            }
        }
    }
}

private struct LiveStreamingRow: View {
    let model: String
    let partialText: String

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                ProgressView()
                    .controlSize(.mini)
                Text(shortModelName(model))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            if !partialText.isEmpty {
                Text(renderMarkdown(partialText))
                    .font(.system(size: 15))
                    .lineSpacing(2)
                    .textSelection(.enabled)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct TranscriptItemRow: View {
    let item: UiTranscriptItem
    let onFork: (Int64) -> Void

    var body: some View {
        switch item {
        case let .userMessage(entryId, text, mode, queued, modelOverride, _):
            HStack {
                Spacer(minLength: 60)
                VStack(alignment: .trailing, spacing: 4) {
                    Text(text)
                        .font(.system(size: 15))
                        .lineSpacing(2)
                        .textSelection(.enabled)
                        .padding(.horizontal, 12)
                        .padding(.vertical, 8)
                        .background(.tint.opacity(0.15), in: RoundedRectangle(cornerRadius: 12))

                    HStack(spacing: 6) {
                        if let modelOverride, !modelOverride.isEmpty {
                            Text(shortModelName(modelOverride))
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                        if queued {
                            Label("Queued (\(mode))", systemImage: "clock")
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        } else if mode == "inbox" {
                            Label("Inbox", systemImage: "tray")
                                .font(.caption2)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            }
            .contextMenu {
                if !queued {
                    Button("Fork From Here") { onFork(entryId) }
                }
                Button("Copy") { copyToPasteboard(text) }
            }

        case let .assistantMessage(entryId, model, text, interrupted, interruptReason, inputTokens, cachedTokens, outputTokens, _):
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 8) {
                    Text(shortModelName(model))
                        .font(.caption)
                        .foregroundStyle(.secondary)

                    if interrupted {
                        let reasonText = interruptReason.map { "interrupted (\($0))" } ?? "interrupted"
                        Text(reasonText)
                            .font(.caption2)
                            .foregroundStyle(.orange)
                            .padding(.horizontal, 5)
                            .padding(.vertical, 1)
                            .background(Color.orange.opacity(0.12), in: Capsule())
                    }

                    if let inp = inputTokens, let out = outputTokens {
                        let cachedStr = (cachedTokens ?? 0) > 0 ? " · \(cachedTokens!) cached" : ""
                        Text("\(inp) in / \(out) out\(cachedStr)")
                            .font(.caption2.monospacedDigit())
                            .foregroundStyle(.tertiary)
                    }

                    Spacer()

                    Button {
                        onFork(entryId)
                    } label: {
                        Label("Fork", systemImage: "tuningfork")
                            .font(.caption)
                    }
                    .buttonStyle(.borderless)
                    .foregroundStyle(.secondary)
                    .accessibilityLabel("Fork Here")
                    .help("Fork a new thread from entry #\(entryId)")
                }

                Text(renderMarkdown(text))
                    .font(.system(size: 15))
                    .lineSpacing(2)
                    .textSelection(.enabled)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .contextMenu {
                Button("Fork From Here") { onFork(entryId) }
                Button("Copy") { copyToPasteboard(text) }
            }

        case let .toolStep(tool, _):
            ToolStepCard(tool: tool, onFork: onFork)

        case let .notice(_, kind, text, _):
            HStack(spacing: 6) {
                Image(systemName: noticeIcon(kind))
                    .foregroundStyle(noticeColor(kind))
                Text(text)
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            .background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 8))
        }
    }

    private func noticeIcon(_ kind: String) -> String {
        switch kind {
        case "forked": return "arrow.triangle.branch"
        case "applied": return "checkmark.circle.fill"
        case "interrupted": return "pause.circle"
        default: return "info.circle"
        }
    }

    private func noticeColor(_ kind: String) -> Color {
        switch kind {
        case "applied": return .green
        case "interrupted": return .orange
        default: return .secondary
        }
    }
}

// MARK: - Monospace Tool Card

private struct ToolStepCard: View {
    let tool: UiToolItem
    let onFork: (Int64) -> Void
    @State private var expanded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Button {
                    withAnimation(.easeInOut(duration: 0.14)) {
                        expanded.toggle()
                    }
                } label: {
                    HStack(spacing: 6) {
                        Image(systemName: "chevron.right")
                            .font(.caption2.weight(.semibold))
                            .rotationEffect(.degrees(expanded ? 90 : 0))
                            .foregroundStyle(.secondary)

                        statusIndicator

                        Text(tool.name)
                            .font(.callout.monospaced().weight(.medium))

                        Text(compactArgs(tool.arguments))
                            .font(.caption.monospaced())
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                            .truncationMode(.tail)
                    }
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)

                Spacer(minLength: 6)

                if let sha = tool.treeSha, !sha.isEmpty {
                    Text(shortSha(sha))
                        .font(.caption2.monospaced())
                        .foregroundStyle(.tertiary)
                        .help("Snapshot tree SHA: \(sha)")
                }

                statusBadge

                if let resultEntryId = tool.resultEntryId {
                    Button {
                        onFork(resultEntryId)
                    } label: {
                        Label("Fork", systemImage: "tuningfork")
                            .font(.caption)
                    }
                    .buttonStyle(.borderless)
                    .foregroundStyle(.secondary)
                    .accessibilityLabel("Fork Here")
                    .help("Fork a new thread from this tool step (entry #\(resultEntryId))")
                }
            }

            if expanded {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Arguments")
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(.secondary)
                    Text(prettyJson(tool.arguments))
                        .font(.system(.caption, design: .monospaced))
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(8)
                        .background(.background, in: RoundedRectangle(cornerRadius: 6))

                    if !tool.output.isEmpty {
                        HStack {
                            Text("Output")
                                .font(.caption2.weight(.semibold))
                                .foregroundStyle(.secondary)
                            if let code = tool.exitCode {
                                Text("exit \(code)")
                                    .font(.caption2.monospaced())
                                    .foregroundStyle(code == 0 ? Color.secondary : Color.orange)
                            }
                        }
                        ScrollView([.vertical, .horizontal]) {
                            Text(tool.output)
                                .font(.system(.caption, design: .monospaced))
                                .textSelection(.enabled)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(8)
                        }
                        .frame(maxHeight: 220)
                        .background(.background, in: RoundedRectangle(cornerRadius: 6))
                    }
                }
                .padding(.top, 2)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.background.secondary, in: RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(.separator.opacity(0.6)))
        .contextMenu {
            if let resultEntryId = tool.resultEntryId {
                Button("Fork From Here") { onFork(resultEntryId) }
            }
            Button("Copy Output") { copyToPasteboard(tool.output) }
        }
    }

    @ViewBuilder
    private var statusIndicator: some View {
        switch tool.status {
        case "running":
            ProgressView().controlSize(.mini)
        case "ok":
            Image(systemName: "checkmark.circle.fill").foregroundStyle(.green).font(.caption)
        case "error":
            Image(systemName: "xmark.circle.fill").foregroundStyle(.red).font(.caption)
        case "unknown":
            Image(systemName: "questionmark.circle.fill").foregroundStyle(.orange).font(.caption)
        case "aborted":
            Image(systemName: "slash.circle.fill").foregroundStyle(.secondary).font(.caption)
        default:
            Image(systemName: "wrench.and.screwdriver").foregroundStyle(.secondary).font(.caption)
        }
    }

    private var statusBadge: some View {
        let color: Color = switch tool.status {
        case "ok": .green
        case "error": .red
        case "unknown": .orange
        case "running": .accentColor
        default: .secondary
        }
        return Text(tool.status)
            .font(.caption2.monospaced())
            .foregroundStyle(color)
            .padding(.horizontal, 6)
            .padding(.vertical, 1.5)
            .background(color.opacity(0.14), in: Capsule())
    }

    private func compactArgs(_ raw: String) -> String {
        let trimmed = raw.replacingOccurrences(of: "\n", with: " ").trimmingCharacters(in: .whitespaces)
        return trimmed == "{}" ? "" : trimmed
    }

    private func prettyJson(_ raw: String) -> String {
        guard let data = raw.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data),
              let pretty = try? JSONSerialization.data(
                  withJSONObject: obj,
                  options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
              ),
              let str = String(data: pretty, encoding: .utf8)
        else { return raw }
        return str
    }
}

private func renderMarkdown(_ text: String) -> AttributedString {
    (try? AttributedString(
        markdown: text,
        options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)
    )) ?? AttributedString(text)
}

private func copyToPasteboard(_ text: String) {
    NSPasteboard.general.clearContents()
    NSPasteboard.general.setString(text, forType: .string)
}
