# Research on native macOS chat UI

[Wiki index](../README.md) · [Arity’s delivery model](../loop.md) · [Root README](../../README.md)

Arity already has a native conversation list, a bottom composer, streaming, folder context, task cards, and fork-from-message. The cheapest improvements are to preserve what the user is typing and reading, make send timing explicit, and give replies and tool activity different visual weight. These fit the native SwiftUI app and apply to both everyday chat and agent work.

## What ordinary chat gets right

| Reference | Documented interaction | What Arity can use |
| --- | --- | --- |
| Apple Messages on Mac | Send from the composer, reply to a particular message, and act on a sent message from its menu. | Make actions about the message under the pointer or keyboard focus. Put Copy and Fork there. [Send and reply](https://support.apple.com/en-mo/guide/messages/icht35827/mac) · [Edit and unsend](https://support.apple.com/en-ie/guide/messages/ichtd68328c6/mac) |
| Slack | Drafts can be resumed later. Messages have a common actions menu and keyboard shortcuts. | Give each conversation its own draft and make key actions reachable by context menu and keyboard. [Sending and drafts](https://slack.com/help/articles/201457107-Send-and-read-messages-in-Slack) · [Keyboard shortcuts](https://slack.com/help/articles/201374536-slack-keyboard-shortcuts) |
| Signal Desktop | Return sends in the normal composer, Shift-Return inserts a newline, and the expanded composer has its own send shortcut. Shortcuts cover finding conversations and jumping to the end. | Preserve the quick one-line send while making multi-paragraph writing deliberate. Provide a visible route back to the latest message. [Sending](https://support.signal.org/hc/en-us/articles/360007060212-Send-a-message) · [Desktop shortcuts](https://support.signal.org/hc/en-us/articles/360036517511-Signal-Desktop-Keyboard-Shortcuts) |

For an ordinary conversation, the same shell can support grouped consecutive messages, contextual timestamps, a draft per thread, and a jump to unread or latest. Reply-to, reactions, edit and unsend, and read receipts have value when there are multiple human participants and the backend supports them. Arity already has a more relevant action for exploring a different continuation in **Fork From Here**.

## What the agent apps share

This comparison uses public documentation and product screenshots available on November 21, 2026. Where a behavior is specific to an editor panel or a mobile client, the source surface is named.

| Product | Conversation and input | Activity and results |
| --- | --- | --- |
| Cursor | Composer supports model selection, attachments, and searchable `@` references. Agent docs describe steering and queuing during a run. [Prompting](https://prod.cursor.com/docs/agent/prompting) · [Agent overview](https://cursor.com/docs/agent/overview) | Changes can be inspected in a dedicated review view. Conversation search and side chats provide ways back to prior context. [Review](https://cursor.com/docs/agent/review) · [Agent overview](https://cursor.com/docs/agent/overview) |
| Codex app | Project-organized threads are the unit of parallel work, with local worktree support. OpenAI also documents steering and queuing in the remote client. [Desktop app](https://openai.com/index/introducing-the-codex-app/) · [Remote workflows](https://developers.openai.com/blog/mastering-codex-remote-for-engineering) | Thread-level change review, inline diff comments, and opening changes in an editor. [Desktop app](https://openai.com/index/introducing-the-codex-app/) |
| Claude desktop | In the Code tab, the row below the prompt holds environment, folder, permission mode, and model. The prompt accepts attachments and file mentions. Stop and sending a correction are distinct actions. [Claude Code desktop](https://code.claude.com/docs/en/desktop#use-the-prompt-box) | The Code tab has a Changes card and file-by-file diff review. General Claude chat has an artifact panel for working with longer outputs. [Desktop diff review](https://code.claude.com/docs/en/desktop#review-changes-with-diff-view) · [Claude Artifacts](https://support.claude.com/en/articles/17153992-what-are-artifacts-and-how-do-i-use-them) |
| Google Antigravity | The desktop app organizes work in conversations. The editor agent panel documents a compact prompt with model selection, `+`, `@`, and `/`. [Desktop overview](https://antigravity.google/docs/overview) · [Editor agent panel](https://antigravity.google/docs/ide/agent-side-panel) | The editor groups steps behind a turn summary. The desktop app opens Review from a changed-files card or the Overview panel and has a dedicated surface for artifacts. [Editor agent panel](https://antigravity.google/docs/ide/agent-side-panel) · [Desktop review](https://antigravity.google/docs/review-changes/) · [Artifacts](https://antigravity.google/docs/artifacts/) |

The product pattern to borrow is a persistent thread, a composer with visible context, a clear way to steer or stop a run, and a short path from the conversation to the result.

## Low-effort changes for Arity

| Order | Change | Implementation and scope |
| --- | --- | --- |
| 1 | **Keep a draft with its conversation.** | Key composer drafts by thread ID so switching conversations preserves in-progress text. |
| 2 | **Follow new output until the reader scrolls away.** | Use `ScrollPosition` + `onScrollGeometryChange` with a bottom-distance threshold. Show a floating **Jump to Latest** pill while detached. |
| 3 | **Give the composer a text row and a compact controls row.** | Place model/route choice, effort, folder chip, and loadout controls in a quiet row beneath the expanding text input. Preserve `Return` to send/steer and `⌥Return` for `after_turn`. |
| 4 | **Make the common message actions discoverable.** | Provide Copy and **Fork From Here** directly on durable entries via hover/menu and keyboard focus. |
| 5 | **Collapse contiguous tool activity.** | Render tool call + receipt as one readable summary line (`Edited runner.rs (+18 −4) · 34abdce`) that expands on click for diff/output and fork actions. |
| 6 | **Render the blocks people use.** | Support headings, lists, blockquotes, rules, and fenced code blocks with Copy Code—including unclosed fences mid-stream—inside a ~740pt reading column. |

## SwiftUI and AppKit choices

- **Transcript:** `NavigationSplitView`, `ScrollView`, and `LazyVStack` with `ScrollPosition` and `onScrollGeometryChange`, plus macOS 26+ Liquid Glass (`.glassEffect()`, `.buttonStyle(.glass)`) on floating chrome controls.
- **Composer:** Vertical `TextField` + `FocusState` (or wrapped `NSTextView` if IME marked-text handling requires it).
- **Markdown:** Native block parser handling streaming unclosed code fences cleanly alongside selectable text.

## SF Symbols

| Purpose | System symbol | Visible or accessibility label |
| --- | --- | --- |
| New conversation | `square.and.pencil` | New Thread |
| Search | `magnifyingglass` | Search Threads |
| Add context | `plus`, `paperclip` | Add Context |
| Folder scope | `folder.badge.plus`, `folder` | Workspace Folder |
| Send or stop | `arrow.up.circle.fill`, `stop.circle.fill` | Send, Stop |
| Timing | `bolt.fill`, `clock`, `tray` | Steer, After Turn, Inbox |
| Transcript position | `arrow.down` | Jump to Latest |
| Message actions | `doc.on.doc`, `arrow.triangle.branch` | Copy, Fork From Here |
| Tool activity | `terminal`, `doc.badge.gearshape` | Tool Step |
