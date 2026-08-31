"""A role is a denial set.

Not a personality and not a prompt: what a bot may not touch, read or reach, plus what it wants
in a model. Aptitude picks the model; denial is the role. Channel permission is per message
kind, which is what lets a record land somewhere its sender may not chat.
"""

HOME_PATHS = ("C:/Users/example", "/Users/example", "/home/example")
ASA_NAMES = ("Asa", "Schaeffer", "asa.schaeffer", "biograph")

class Denied(Exception):
    pass

class Role:
    def __init__(self, name, tier, os_user="nobody", harness="http", tools=(), channels=None,
                 deny_paths=(), deny_names=(), deny_hosts=(), aptitude=None, public=False):
        self.name, self.tier, self.harness, self.os_user = name, tier, harness, os_user
        self.tools, self.public = set(tools), public
        self.channels = channels or {}          # channel id -> the message kinds it may post
        self.deny_paths, self.deny_names, self.deny_hosts = deny_paths, deny_names, deny_hosts
        self.aptitude = aptitude or {"quality": 1.0, "speed": 1.0, "cost": 1.0}

def enforce(role, action, target, kind="text"):
    """Raise rather than allow. The caller doesn't get to decide what a soft no means."""
    if action == "tool":
        if target not in role.tools:
            raise Denied("%s may not use tool %r" % (role.name, target))
    elif action == "post":
        kinds = role.channels.get(target)
        if kinds is None:
            raise Denied("%s may not post to %s" % (role.name, target))
        if kind not in kinds:
            raise Denied("%s may post %s to %s but not %s"
                         % (role.name, "/".join(sorted(kinds)), target, kind))
    elif action == "path":
        low = str(target).replace("\\", "/").lower()
        for d in role.deny_paths:
            if d.replace("\\", "/").lower() in low:
                raise Denied("%s may not touch %s (denied: %s)" % (role.name, target, d))
    elif action == "host":
        for d in role.deny_hosts:
            if d in str(target):
                raise Denied("%s may not reach %s" % (role.name, d))
    else:
        raise Denied("unknown action %r" % action)
    return True

def build():
    """The roster. Leaves are denied the home directory and the person's name, by name."""
    voice = Role("voice", 0, "voice", tools=("handoff",),
                 channels={"dm:asa": {"text", "keepalive"}, "friction": {"text"},
                           "proj:brokie": {"text", "handoff", "entry"}},
                 aptitude={"quality": 1.0, "speed": 1.2, "cost": 0.6})
    builder = Role("builder", 2, "leaf", tools=("write_file", "read_file", "list_files"),
                   # may drop a record in the project channel; may not chat there
                   channels={"proj:brokie": {"handoff"}, "friction": {"text"}},
                   # traversal is caught by the tool, not by substring: two dots occur in prose
                   deny_paths=HOME_PATHS + ("/etc", "C:/Windows"), deny_names=ASA_NAMES,
                   deny_hosts=("generativelanguage.googleapis.com",),
                   aptitude={"quality": 1.0, "speed": 0.8, "cost": 1.4})
    archivist = Role("archivist", 1, "archivist", tools=(),     # it records, never resumes
                     channels={"proj:brokie": {"entry"}, "dm:asa": {"entry"},
                               "friction": {"entry"}},
                     aptitude={"quality": 1.4, "speed": 0.5, "cost": 0.8})
    # a public-facing triage role belongs here too, denied tier 0 outright; not built in v0
    return {r.name: r for r in (voice, builder, archivist)}
