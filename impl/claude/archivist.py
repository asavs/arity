"""The impartial account.

The kernel's own report is the best evidence for what it *meant*; this is the best evidence for
what it *did*. Verification is deliberately not a judgement call — claims are matched against
the tool log and the filesystem in plain Python, and only the prose around the table comes from
a model. It records, never resumes; a claim that doesn't survive costs standing.
"""

import re
import time

EXTS = ("sql", "py", "md", "json", "txt", "csv", "yml", "yaml", "toml", "sh", "html", "js")
CLAIM_RE = re.compile(r"[A-Za-z0-9_\-./\\]+\.(?:" + "|".join(EXTS) + r")\b")

def extract_claims(text):
    """What files does this account say it changed? Paths are the only checkable claim."""
    out = []
    for m in CLAIM_RE.findall(text or ""):
        p = m.replace("\\", "/").strip("./")
        if p and p not in out:
            out.append(p)
    return out

class Archivist:
    def __init__(self, core):
        self.core, self.queue, self.entries = core, [], []

    def enqueue(self, env, report, reason):
        self.queue.append((env, report, reason))

    def drain(self):
        out = []
        while self.queue:
            out.append(self.write_entry(*self.queue.pop(0)))
        return out

    def check(self, env, report):
        """The deterministic half: the tool log and the disk, nobody's opinion."""
        wrote = set(str(c["args"].get("path", "")).replace("\\", "/").strip("./")
                    for c in env["tool_log"] if c["tool"] == "write_file" and c["ok"])
        if report is not None and report.status == "own":
            claims, source = extract_claims(report.body), "the kernel's own report"
        else:
            claims, source = sorted(wrote), "the tool log, because there is no report"
        checked = []
        for c in claims:
            on_disk = (self.core.store.workspace / c).exists()
            checked.append({"claim": c, "in_tool_log": c in wrote, "on_disk": on_disk,
                            "verified": bool(c in wrote and on_disk),
                            "evidence": ("workspace/" + c) if on_disk else None})
        return checked, source

    def write_entry(self, env, report, reason):
        checked, source = self.check(env, report)
        flags = []
        if report is None or report.status != "own":
            flags.append("REPORT_ABSENT: %s" % (report.body if report is not None else reason))
        flags += ["UNSUPPORTED CLAIM: %s (in tool log: %s, on disk: %s)"
                  % (c["claim"], c["in_tool_log"], c["on_disk"])
                  for c in checked if not c["verified"]]
        summary = self._render(env, report, checked, flags)
        entry = {"kernel": env["kernel"], "at": time.time(), "role": env["role"],
                 "model": env["model"], "seat": env["seat"], "identity": env["identity"],
                 "ended_by": env["ended_by"], "claims_from": source, "summary": summary,
                 "changes": checked, "flags": flags,
                 "sources": ["kernels/%s.json" % env["kernel"]]
                            + [c["evidence"] for c in checked if c["evidence"]]}
        self.core.tiers.write(env["tier"], "archivist_entry", entry, by="archivist")
        self.core.scorecard.record(
            self.core.roles[env["role"]], env["task_class"], env["model"],
            verified=sum(1 for c in checked if c["verified"]),
            unverified=sum(1 for c in checked if not c["verified"]),
            wall=env["died_at"] - env["born_at"], tokens=env["tokens_used"])
        try:
            self.core.redphone.post("proj:brokie", "archivist", "entry",
                                    {"summary": summary[:400], "flags": flags,
                                     "kernel": env["kernel"]})
        except KeyError:
            pass
        self.entries.append(entry)
        return entry

    def _render(self, env, report, checked, flags):
        """One real model call. The table is already decided; this only writes it up."""
        table = "\n".join("- %s | in tool log: %s | on disk: %s | %s"
                          % (c["claim"], c["in_tool_log"], c["on_disk"],
                             "VERIFIED" if c["verified"] else "NOT SUPPORTED")
                          for c in checked) or "- (no file claims at all)"
        own = report.body if (report is not None and report.status == "own") else "(ABSENT)"
        task = {"want": ("Write a short third-person entry about a kernel that has just died. "
                         "Two or three sentences. Say what it did, name the verified files, and "
                         "if anything is flagged say so plainly. Do not offer to continue the "
                         "work and do not congratulate anyone."),
                "project": "brokie", "class": "archive",
                "context": ("kernel %s held the role %s on model %s; it ended because: %s.\n"
                            "Its own account:\n%s\n\nWhat the artifacts show:\n%s\n\nFlags:\n%s"
                            % (env["kernel"], env["role"], env["model"], env["ended_by"], own,
                               table, "\n".join(flags) or "(none)"))}
        try:
            k = self.core.cast(task, self.core.roles["archivist"], convo=None, archive=False)
            text = k.turn(task["want"], use_tools=False).text.strip()
            k.die("entry written")
            return text or ("(the archivist's turn came back empty)\n" + table)
        except Exception as e:
            # an archivist that can't reach a seat still has to leave the record
            return ("archivist prose unavailable (%s: %s). Verified artifacts:\n%s"
                    % (type(e).__name__, e, table))
