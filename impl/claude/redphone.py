"""Everything that happens in the system happens as a message in a channel.

A channel has an id, a visibility, members who are humans and roles, and the transports its
human members reach it by. A DM is a channel with two members; a phone number is a transport
into the DM with the Voice. Posting never blocks on a reply. Handoffs are the interesting case:
a record may land in a channel its sender may not chat in, and the reply comes back the same
way — which is why permission is per message kind.
"""

import time
import uuid

import roles as roles_mod

MAX_DEPTH = 3

class HandoffRefused(roles_mod.Denied):
    pass

class Channel:
    def __init__(self, cid, visibility, members, transports=None):
        self.id, self.visibility = cid, visibility
        self.members = list(members)               # humans as "asa", roles as role names
        self.transports = transports or {}         # human -> [transport names]

class Transport:
    """The comms seam. Today every wire is a line on stdout with its name on the front."""

    def __init__(self, name, sink):
        self.name, self.sink = name, sink

    def egress(self, who, msg):
        b = msg["body"]
        if isinstance(b, dict):
            b = "; ".join("%s=%s" % (k, str(v)[:70]) for k, v in b.items()
                          if k in ("want", "result", "summary", "problem"))
        self.sink("      [%s -> %s] %s" % (self.name, who, str(b)[:220]))

class RedPhone:
    def __init__(self, store, sink=print):
        self.store, self.sink, self.channels, self.core = store, sink, {}, None
        self.transports = {n: Transport(n, sink) for n in ("sms", "email", "web")}

    def channel(self, cid, visibility, members, transports=None):
        self.channels[cid] = Channel(cid, visibility, members, transports)
        return self.channels[cid]

    def post(self, channel_id, sender, kind, body, sender_role=None):
        ch = self.channels[channel_id]
        if sender_role is not None:
            roles_mod.enforce(sender_role, "post", channel_id, kind)
        msg = {"id": "m_" + uuid.uuid4().hex[:8], "channel": channel_id, "sender": sender,
               "kind": kind, "at": time.time(), "body": body}
        self.store.log_message(msg)
        for m in ch.members:
            if self.core and m in self.core.roles:
                k = self.core.registry.holder(self.core.roles[m])
                if k and k.role.name != sender:
                    k.enqueue_turn(msg)
            else:
                for t in ch.transports.get(m, []):
                    self.transports[t].egress(m, msg)
        return msg

    def dm(self, role_name, sender, body, kind="text", sender_role=None):
        """Addresses whichever kernel holds that role right now, not a particular kernel."""
        cid = "dm:asa" if role_name == "voice" else "dm:" + role_name
        return self.post(cid, sender, kind, body, sender_role)

    def handoff(self, from_kernel, record):
        """A structured record, bounded by depth and budget, posted to a channel."""
        if record["depth"] > MAX_DEPTH:
            raise HandoffRefused("handoff refused: depth %d over the limit" % record["depth"])
        if record["budget"] <= 0:
            raise HandoffRefused("handoff refused: no budget left")
        return self.post(record["channel"], from_kernel.role.name, "handoff", record,
                         sender_role=from_kernel.role)

    def escalate(self, problem, level, channel_id="dm:asa"):
        """leaf -> dispatcher -> red phone -> Asa by email, and say so in the channel."""
        self.sink("      [escalation/%s] %s" % (level, str(problem)[:160]))
        return self.post(channel_id, "system", "text",
                         {"problem": str(problem), "level": level,
                          "why": "the staff could not proceed on its own"})

def task_record(from_role, to_role, want, project, channel, tier=2, budget=1, depth=1, ev=None):
    return {"from": from_role, "to_role": to_role, "want": want, "project": project,
            "channel": channel, "tier": tier, "budget": budget, "depth": depth,
            "evidence": ev or []}
