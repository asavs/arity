import ArityCore
import SwiftUI

struct DiffSheet: View {
    @Environment(AppStore.self) private var store
    @Environment(\.dismiss) private var dismiss

    @State private var renderedPatch = AttributedString()

    private var summary: UiThreadSummary? { store.selectedSummary }
    private var isRunning: Bool { summary?.isRunning ?? false }
    private var isApplied: Bool { summary?.status == "applied" }
    private var hasFiles: Bool { !(store.currentDiff?.files.isEmpty ?? true) }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 10) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Workspace Changes")
                        .font(.title3.weight(.semibold))
                    if let diff = store.currentDiff {
                        Text("\(shortSha(diff.baseTreeSha)) → \(shortSha(diff.targetTreeSha)) · \(diff.files.count) file\(diff.files.count == 1 ? "" : "s")")
                            .font(.caption.monospaced())
                            .foregroundStyle(.secondary)
                    } else if let folder = folderDisplayName(summary?.sourceDir) {
                        Text(folder)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                Spacer()
                Button("Refresh", systemImage: "arrow.clockwise") {
                    Task { await store.loadDiffForSelectedThread() }
                }
                .controlSize(.small)
                .disabled(store.diffLoading)

                Button("Close") {
                    dismiss()
                }
                .controlSize(.small)
                .keyboardShortcut(.cancelAction)

                if hasFiles && !isApplied {
                    Button("Apply Changes", systemImage: "square.and.arrow.down.on.square") {
                        Task { await store.applySelectedThread() }
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.small)
                    .keyboardShortcut(.defaultAction)
                    .disabled(isRunning || store.applyingChanges)
                    .accessibilityLabel("Apply Changes")
                    .accessibilityIdentifier("apply-changes-button")
                }
            }

            if let msg = store.applyMessage {
                Label(msg, systemImage: "checkmark.circle.fill")
                    .font(.callout)
                    .foregroundStyle(.green)
                    .padding(10)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Color.green.opacity(0.10), in: RoundedRectangle(cornerRadius: 8))
            }

            if let err = store.diffError {
                Label(err, systemImage: "exclamationmark.triangle.fill")
                    .font(.callout)
                    .foregroundStyle(.red)
                    .textSelection(.enabled)
                    .padding(10)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Color.red.opacity(0.10), in: RoundedRectangle(cornerRadius: 8))
            }

            if store.diffLoading {
                Spacer()
                HStack {
                    Spacer()
                    ProgressView("Computing diff…")
                    Spacer()
                }
                Spacer()
            } else if let diff = store.currentDiff {
                if diff.files.isEmpty {
                    ContentUnavailableView(
                        "No Workspace Changes",
                        systemImage: "checkmark.circle",
                        description: Text("The isolated worktree matches the base snapshot.")
                    )
                } else {
                    VStack(alignment: .leading, spacing: 4) {
                        ForEach(diff.files, id: \.path) { file in
                            HStack(spacing: 8) {
                                Image(systemName: statusSymbol(file.status))
                                    .foregroundStyle(statusTint(file.status))
                                    .frame(width: 14)
                                Text(file.status)
                                    .font(.caption.monospaced())
                                    .foregroundStyle(statusTint(file.status))
                                    .frame(width: 68, alignment: .leading)
                                Text(file.path)
                                    .font(.callout.monospaced())
                                    .lineLimit(1)
                                    .truncationMode(.middle)
                                Spacer()
                            }
                        }
                    }
                    .padding(10)
                    .background(.background.secondary, in: RoundedRectangle(cornerRadius: 8))

                    ScrollView([.horizontal, .vertical]) {
                        Text(renderedPatch)
                            .font(.system(.caption, design: .monospaced))
                            .textSelection(.enabled)
                            .fixedSize()
                            .padding(10)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .background(.background, in: RoundedRectangle(cornerRadius: 8))
                    .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(.separator))
                }
            } else {
                Spacer()
            }

            if isApplied || isRunning {
                HStack {
                    if isApplied {
                        Label("Applied to checkout", systemImage: "checkmark.circle.fill")
                            .font(.callout)
                            .foregroundStyle(.green)
                    } else if isRunning {
                        Label("Stop the active turn before applying changes.", systemImage: "hourglass")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    Spacer()
                }
            }
        }
        .padding(18)
        .frame(width: 720, height: 540)
        .task(id: store.currentDiff?.patch) {
            renderedPatch = Self.highlightDiff(store.currentDiff?.patch ?? "")
        }
    }

    private func statusSymbol(_ status: String) -> String {
        switch status {
        case "added": return "plus.circle.fill"
        case "deleted": return "minus.circle.fill"
        case "renamed": return "arrow.right.circle.fill"
        default: return "pencil.circle.fill"
        }
    }

    private func statusTint(_ status: String) -> Color {
        switch status {
        case "added": return .green
        case "deleted": return .red
        case "renamed": return .cyan
        default: return .orange
        }
    }

    private static func highlightDiff(_ patch: String) -> AttributedString {
        var out = AttributedString()
        for line in patch.split(separator: "\n", omittingEmptySubsequences: false) {
            var run = AttributedString(line + "\n")
            if line.hasPrefix("+++") || line.hasPrefix("---") || line.hasPrefix("diff ") || line.hasPrefix("index ")
                || line.hasPrefix("new file") || line.hasPrefix("deleted file")
                || line.hasPrefix("similarity") || line.hasPrefix("rename ")
            {
                run.foregroundColor = .secondary
                if line.hasPrefix("diff ") {
                    run.font = .system(.caption, design: .monospaced).bold()
                }
            } else if line.hasPrefix("@@") {
                run.foregroundColor = .cyan
            } else if line.hasPrefix("+") {
                run.foregroundColor = .green
                run.backgroundColor = Color.green.opacity(0.12)
            } else if line.hasPrefix("-") {
                run.foregroundColor = .red
                run.backgroundColor = Color.red.opacity(0.12)
            }
            out += run
        }
        return out
    }
}
