import ArityCore
import SwiftUI

struct SidebarView: View {
    @Environment(AppStore.self) private var store

    var body: some View {
        @Bindable var store = store
        List(selection: $store.selectedThreadId) {
            if store.threads.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    Text("No threads yet")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                    Text("Press ⌘N to start a thread.")
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                }
                .padding(.vertical, 8)
            } else {
                ForEach(store.threads, id: \.id) { thread in
                    ThreadSidebarRow(thread: thread)
                        .tag(thread.id)
                }
            }
        }
        .listStyle(.sidebar)
        .safeAreaInset(edge: .bottom) {
            if !store.models.contains(where: { $0.ready }) {
                Button("Configure Providers…", systemImage: "gearshape") {
                    store.openSettings()
                }
                .buttonStyle(.borderless)
                .font(.caption)
                .padding(10)
            }
        }
    }
}

private struct ThreadSidebarRow: View {
    let thread: UiThreadSummary

    var body: some View {
        HStack(alignment: .center, spacing: 8) {
            statusIcon
                .frame(width: 16, height: 16)

            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(thread.id)
                        .font(.body)
                        .lineLimit(1)
                        .truncationMode(.middle)
                    Spacer(minLength: 4)
                    badge
                }

                Text(captionText)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.tail)
            }
        }
        .padding(.vertical, 2)
        .accessibilityElement(children: .combine)
    }

    private var captionText: String {
        var parts: [String] = []
        if let folder = folderDisplayName(thread.sourceDir) {
            parts.append(folder)
        }
        parts.append(shortModelName(thread.model))
        return parts.joined(separator: " · ")
    }

    @ViewBuilder
    private var statusIcon: some View {
        if thread.isRunning {
            ProgressView()
                .controlSize(.mini)
        } else if thread.status == "halted" {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.caption)
                .foregroundStyle(.orange)
        } else if thread.status == "applied" {
            Image(systemName: "checkmark.circle.fill")
                .font(.caption)
                .foregroundStyle(.green)
        } else if thread.sourceDir != nil {
            Image(systemName: "folder")
                .font(.caption)
                .foregroundStyle(.secondary)
        } else {
            Image(systemName: "bubble.left")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    @ViewBuilder
    private var badge: some View {
        if thread.status == "halted" {
            badgePill("halted", tint: .orange)
        } else if thread.status == "applied" {
            badgePill("applied", tint: .green)
        } else if thread.hasChanges {
            badgePill("changes", tint: .accentColor)
        }
    }

    private func badgePill(_ text: String, tint: Color) -> some View {
        Text(text)
            .font(.caption2.weight(.medium))
            .foregroundStyle(tint)
            .padding(.horizontal, 6)
            .padding(.vertical, 1.5)
            .background(tint.opacity(0.14), in: Capsule())
            .fixedSize()
    }
}
