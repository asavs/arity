#!/usr/bin/env python3
"""Builds the wiki diagrams as standalone SVG files.

Run from anywhere:  python3 .wiki/diagrams/build.py
Edit a diagram by changing its function below, then re-run.
All diagrams share a 680px-wide canvas and the same small set of shapes.
"""
from pathlib import Path

OUT = Path(__file__).parent

# name: (50, 100, 200, 600, 800) stops
RAMPS = {
    "purple": ("#EEEDFE", "#CECBF6", "#AFA9EC", "#534AB7", "#3C3489"),  # agents
    "teal": ("#E1F5EE", "#9FE1CB", "#5DCAA5", "#0F6E56", "#085041"),  # model routing
    "coral": ("#FAECE7", "#F5C4B3", "#F0997B", "#993C1D", "#712B13"),  # delivery and loadout
    "amber": ("#FAEEDA", "#FAC775", "#EF9F27", "#854F0B", "#633806"),  # reports and grading
}


def css():
    light = [
        "text{font-family:system-ui,-apple-system,sans-serif;dominant-baseline:central}",
        ".bg{fill:#FFFFFF}",
        ".th{font-size:14px;font-weight:500;fill:#2C2C2A}",
        ".ts{font-size:12px;fill:#5F5E5A}",
        ".box,.store{fill:#F1EFE8;stroke:#888780;stroke-width:.5}",
        ".frame{fill:none;stroke:#B4B2A9;stroke-width:.5}",
        ".arr{fill:none;stroke:#5F5E5A;stroke-width:1.5}",
        ".rep{fill:none;stroke:#BA7517;stroke-width:1.5}",
        ".mk{fill:none;stroke:#5F5E5A;stroke-width:1.5;stroke-linecap:round;stroke-linejoin:round}",
        ".mkr{fill:none;stroke:#BA7517;stroke-width:1.5;stroke-linecap:round;stroke-linejoin:round}",
    ]
    dark = [
        ".bg{fill:#1F1E1D}",
        ".th{fill:#F1EFE8}",
        ".ts{fill:#B4B2A9}",
        ".box,.store{fill:#2C2C2A;stroke:#888780}",
        ".frame{stroke:#5F5E5A}",
        ".arr,.mk{stroke:#B4B2A9}",
    ]
    for n, (c50, c100, c200, c600, c800) in RAMPS.items():
        light.append(f".c-{n} rect{{fill:{c50};stroke:{c600};stroke-width:.5}}")
        light.append(f".c-{n} .th{{fill:{c800}}}.c-{n} .ts{{fill:{c600}}}")
        dark.append(f".c-{n} rect{{fill:{c800};stroke:{c200}}}")
        dark.append(f".c-{n} .th{{fill:{c100}}}.c-{n} .ts{{fill:{c200}}}")
    return "".join(light) + "@media (prefers-color-scheme:dark){" + "".join(dark) + "}"


def txt(x, y, s, cls="ts", anchor="start"):
    return f'<text class="{cls}" x="{x}" y="{y}" text-anchor="{anchor}">{s}</text>'


def _labels(cx, cy, title, sub):
    if sub is None:
        return txt(cx, cy, title, "th", "middle")
    return txt(cx, cy - 9, title, "th", "middle") + txt(cx, cy + 10, sub, "ts", "middle")


def box(kind, x, y, w, h, title, sub=None):
    """kind is 'box' for neutral, or a ramp name."""
    inner = _labels(x + w / 2, y + h / 2, title, sub)
    if kind == "box":
        return f'<g><rect class="box" x="{x}" y="{y}" width="{w}" height="{h}" rx="8"/>{inner}</g>'
    return f'<g class="c-{kind}"><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="8"/>{inner}</g>'


def cyl(x, y, w, h, title, sub=None):
    """Database cylinder. y is the centre of the top ellipse, h the body height."""
    rx = w / 2
    body = f'<path class="store" d="M{x} {y} A{rx} 10 0 0 1 {x + w} {y} L{x + w} {y + h} A{rx} 10 0 0 1 {x} {y + h} Z"/>'
    lid = f'<path class="store" style="fill:none" d="M{x} {y} A{rx} 10 0 0 0 {x + w} {y}"/>'
    cx, cy = x + w / 2, y + h / 2 + 6
    return "<g>" + body + lid + _labels(cx, cy, title, sub) + "</g>"


def diamond(cx, cy, hw, hh, title):
    d = f"M{cx - hw} {cy} L{cx} {cy - hh} L{cx + hw} {cy} L{cx} {cy + hh} Z"
    return f'<g><path class="box" d="{d}"/>{txt(cx, cy, title, "th", "middle")}</g>'


def frame(x, y, w, h, dashed=True):
    dash = ' stroke-dasharray="4 4"' if dashed else ""
    return f'<rect class="frame" x="{x}" y="{y}" width="{w}" height="{h}" rx="12"{dash}/>'


def line(pts, cls="arr", end=True, start=False, dashed=False):
    m = "a" if cls == "arr" else "r"
    d = "M" + " L".join(f"{x} {y}" for x, y in pts)
    attrs = (f' marker-end="url(#{m})"' if end else "") + (f' marker-start="url(#{m})"' if start else "")
    if dashed:
        attrs += ' stroke-dasharray="4 4" stroke-width="0.75"'
    return f'<path class="{cls}" d="{d}"{attrs}/>'


def svg(name, height, title, parts):
    marker = (
        '<marker id="{i}" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" '
        'orient="auto-start-reverse"><path class="{c}" d="M2 1L8 5L2 9"/></marker>'
    )
    head = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 680 {height}" width="680" role="img">'
        f"<title>{title}</title><style>{css()}</style>"
        f'<defs>{marker.format(i="a", c="mk")}{marker.format(i="r", c="mkr")}</defs>'
        f'<rect class="bg" width="680" height="{height}" rx="12"/>'
    )
    (OUT / f"{name}.svg").write_text(head + "\n" + "\n".join(parts) + "\n</svg>\n")
    print("wrote", name + ".svg")


def baseline():
    p = [box("box", 290, 40, 130, 50, "User")]
    for y, name in ((150, "Secretary"), (260, "Project agent"), (370, "Subagent")):
        p.append(box("purple", 290, y, 130, 50, name))
    # neighbour hops: messages both ways, reports up only
    p += [txt(329, 120, "message", anchor="end"), txt(381, 120, "report")]
    for top in (94, 204, 314):
        p.append(line([(337, top), (337, top + 52)], start=True))
        p.append(line([(373, top + 54), (373, top)], "rep"))
    # the user's direct line to any agent, and those agents' reports back
    p += [
        txt(250, 50, "to any agent", anchor="end"),
        line([(290, 65), (256, 65), (256, 395), (286, 395)]),
        line([(256, 285), (286, 285)]),
        txt(452, 50, "back to user"),
        line([(420, 395), (446, 395), (446, 65), (424, 65)], "rep"),
        line([(420, 285), (446, 285)], "rep", end=False),
    ]
    # every message: delivery depends on whether the recipient is live
    p += [
        frame(40, 130, 190, 252),
        txt(52, 148, "Every message"),
        diamond(135, 200, 60, 28, "Live?"),
        txt(143, 239, "yes"),
        line([(135, 228), (135, 248)]),
        txt(203, 188, "no"),
        line([(195, 200), (212, 200), (212, 343), (186, 343)]),
        box("coral", 52, 252, 130, 50, "Message timing", "No router"),
        # what happens inside this box is its own diagram: instantiate()
        box("teal", 52, 318, 130, 50, "Instantiate", "Spawn or wake"),
    ]
    # every report: graded into the leaderboard, which instantiation reads
    p += [
        frame(480, 130, 160, 252),
        txt(492, 148, "Every report"),
        box("amber", 500, 172, 120, 56, "Graded", "By its recipient"),
        line([(560, 228), (560, 272)]),
        cyl(500, 287, 120, 54, "Leaderboard", "Blind scores"),
        line([(560, 353), (560, 452), (117, 452), (117, 372)]),
        txt(338, 470, "Feeds the next pick", anchor="middle"),
    ]
    svg("baseline", 496, "Baseline: agents, messages, reports", p)


def work():
    p = [
        box("box", 40, 110, 150, 56, "User"),
        line([(194, 138), (261, 138)], start=True),
        box("purple", 265, 110, 150, 56, "Secretary", "Chooses a route"),
        txt(413, 54, "writes a task", anchor="middle"),
        line([(340, 110), (340, 68), (486, 68)]),
        cyl(490, 50, 150, 42, "Task board", "In the database"),
        line([(565, 104), (565, 186)]),
        txt(555, 146, "wakes a good fit", anchor="end"),
        box("purple", 490, 190, 150, 56, "Agent", "Does the work"),
        line([(340, 166), (340, 218), (486, 218)]),
        txt(350, 234, "patches you in"),
        txt(350, 250, "with a brief"),
        line([(115, 166), (115, 284), (565, 284), (565, 250)], start=True),
        txt(340, 300, "then you talk directly", anchor="middle"),
    ]
    svg("work", 320, "How work starts: the board or a patch-in", p)


def fork():
    p = [
        box("purple", 40, 90, 170, 56, "Parent", "Has the full context"),
        txt(378, 54, "a child", anchor="middle"),
        line([(214, 108), (320, 108), (320, 68), (436, 68)]),
        txt(378, 154, "a sibling", anchor="middle"),
        line([(214, 128), (320, 128), (320, 168), (436, 168)]),
        box("purple", 440, 40, 200, 56, "Fresh agent", "Knows only the brief"),
        box("purple", 440, 140, 200, 56, "Fork", "Knows what the parent knows"),
    ]
    svg("fork", 230, "Fresh agent or fork", p)


def instantiate():
    # (title, subtitle, store read by this step or None)
    steps = [
        ("Resolve role", "From the request", "Registry"),
        ("Pick variant", "Model router, blind", "Endpoint pool"),
        ("Load context", "Task and profiles", "Database"),
        ("Get computer", "If the task requires one", "Computer pool"),
        ("Grant access", "Scoped to the task", "Vault"),
        ("Assemble", "Prompt and loadout", None),
    ]
    p = [
        box("box", 100, 30, 200, 50, "Message arrives", "Names a role and a task"),
        line([(200, 80), (200, 106)]),
    ]
    for i, (title, sub, store) in enumerate(steps):
        y = 110 + i * 64
        p.append(box("teal", 100, y, 200, 50, title, sub))
        p.append(line([(200, y + 50), (200, y + 60)]))
        if store:
            p.append(line([(416, y + 25), (304, y + 25)]))
            p.append(cyl(420, y + 10, 140, 30, store))
    # pick variant also reads scores from the database
    p.append(line([(420, 275), (360, 275), (360, 213), (304, 213)]))
    p.append(box("purple", 100, 494, 200, 50, "Agent is live", "Receives the message"))
    svg("instantiate", 564, "The instantiate step", p)


def access():
    p = [
        box("purple", 40, 50, 150, 56, "Agent", "Never sees the secret"),
        txt(227, 62, "asks", anchor="middle"),
        line([(194, 78), (261, 78)]),
        box("teal", 265, 50, 150, 56, "Access broker", "Checks the grant"),
        txt(452, 62, "fills in", anchor="middle"),
        line([(419, 78), (486, 78)]),
        box("purple", 490, 50, 150, 56, "Workspace", "Signed in"),
        line([(340, 178), (340, 110)]),
        cyl(270, 192, 140, 40, "Vault"),
        txt(452, 198, "use logged", anchor="middle"),
        line([(414, 214), (486, 214)]),
        cyl(490, 192, 150, 40, "Database"),
    ]
    svg("access", 260, "Using a credential without seeing it", p)


def computers():
    p = [
        box("coral", 40, 40, 160, 56, "Task", "Lists requirements"),
        line([(204, 68), (256, 68)]),
        box("teal", 260, 40, 160, 56, "Get a computer", "Match requirements"),
        line([(424, 68), (476, 68)]),
        box("purple", 480, 40, 160, 56, "Workspace", "Sleeps with its agent"),
        line([(340, 160), (340, 100)]),
        frame(140, 160, 400, 110),
        txt(156, 180, "Computer pool", "th"),
        box("box", 156, 198, 180, 56, "Cloud machines", "On demand, paid by hour"),
        box("box", 344, 198, 180, 56, "Your own machines", "Mac with Xcode, RTX box"),
    ]
    svg("computers", 290, "Matching a task to a computer", p)


def database():
    p = [
        txt(340, 24, "or does it directly", anchor="middle"),
        line([(115, 60), (115, 38), (565, 38), (565, 56)]),
        box("box", 40, 60, 150, 56, "Person", "people/asas"),
        txt(227, 72, "works on", anchor="middle"),
        line([(194, 88), (261, 88)]),
        box("box", 265, 60, 150, 56, "Project", "projects/arity"),
        txt(452, 72, "has", anchor="middle"),
        line([(419, 88), (486, 88)]),
        box("coral", 490, 60, 150, 56, "Task", "A todo with fields"),
        line([(565, 116), (565, 196)]),
        txt(575, 158, "done by"),
        box("purple", 490, 200, 150, 56, "Agent run", "Ratings, papercuts"),
        txt(452, 212, "ran on", anchor="middle"),
        line([(486, 228), (419, 228)]),
        box("teal", 265, 200, 150, 56, "Model", "Sealed until graded"),
    ]
    svg("database", 290, "The database as a graph", p)


def blind_testing():
    p = [
        # top row: the run
        box("teal", 40, 40, 160, 56, "Pick variant", "Model router"),
        line([(204, 68), (256, 68)]),
        box("purple", 260, 40, 160, 56, "Agent runs", "Variant hidden"),
        line([(424, 68), (476, 68)]),
        box("amber", 480, 40, 160, 56, "Report", "To parent or user"),
        # bottom row: the score finding its way back
        line([(560, 96), (560, 186)]),
        box("amber", 480, 190, 160, 56, "Grade", "Hook plus metrics"),
        txt(440, 206, "grade", anchor="middle"),
        line([(476, 220), (404, 220)]),
        cyl(280, 200, 120, 40, "Trial record"),
        txt(230, 206, "score", anchor="middle"),
        line([(276, 220), (184, 220)]),
        cyl(60, 200, 120, 40, "Leaderboard"),
        line([(90, 188), (90, 100)]),
        # the router seals which variant it picked
        line([(170, 96), (170, 140), (310, 140), (310, 186)]),
        txt(240, 128, "sealed variant", anchor="middle"),
        # what a variant is
        frame(40, 290, 600, 90),
        txt(56, 308, "A variant is one choice for each of these"),
    ]
    for i, name in enumerate(("Model", "Harness", "Skills", "MCP servers", "Persona")):
        p.append(box("coral", 56 + i * 116, 324, 104, 40, name))
    svg("blind-testing", 400, "The blind testing loop", p)


def ratings():
    p = [
        box("box", 40, 50, 180, 56, "Asker", "User or parent agent"),
        box("purple", 460, 50, 180, 56, "Doer", "Agent, variant hidden"),
        txt(340, 54, "message", anchor="middle"),
        line([(224, 68), (456, 68)]),
        line([(456, 90), (224, 90)], "rep"),
        txt(340, 105, "reply or report", anchor="middle"),
        line([(130, 106), (130, 186)]),
        txt(142, 146, "rates the doer, 1 to 5"),
        line([(550, 106), (550, 186)]),
        txt(538, 146, "rates the asker, 1 to 5", anchor="end"),
        cyl(60, 200, 140, 44, "Leaderboard", "Scores per variant"),
        cyl(480, 200, 140, 44, "Profiles", "People and models"),
    ]
    svg("ratings", 280, "Ratings in both directions", p)


def slots():
    p = [
        box("coral", 40, 100, 180, 56, "Slot: computer use", "Named by the role"),
        line([(224, 128), (256, 128)]),
        box("teal", 260, 100, 160, 56, "Pick variant", "One option per slot"),
        line([(424, 118), (466, 66)], end=False, dashed=True),
        line([(424, 128), (466, 128)]),
        line([(424, 138), (466, 190)], end=False, dashed=True),
        box("box", 470, 40, 170, 50, "MCP server A"),
        box("teal", 470, 103, 170, 50, "MCP server B", "Picked, hidden"),
        box("box", 470, 166, 170, 50, "CLI tool"),
    ]
    svg("slots", 240, "Slots with interchangeable options", p)


def papercuts():
    p = [
        box("box", 40, 40, 160, 56, "Papercut", "From a user or agent"),
        line([(204, 68), (256, 68)]),
        cyl(260, 50, 140, 40, "Papercut log"),
        line([(404, 68), (456, 68)]),
        box("amber", 460, 40, 180, 56, "Pick up", "From the task board"),
        line([(550, 96), (550, 186)]),
        box("amber", 460, 190, 180, 56, "Propose a fix", "Skill, tool or prompt"),
        line([(456, 218), (404, 218)]),
        cyl(260, 200, 140, 40, "Registry", "As a new variant"),
        line([(256, 218), (204, 218)]),
        box("teal", 40, 190, 160, 56, "Blind test", "Old against new"),
        txt(120, 266, "Winner becomes the default", anchor="middle"),
    ]
    svg("papercuts", 290, "Papercuts: the improvement loop", p)


def router():
    p = [
        cyl(280, 52, 120, 44, "Leaderboard"),
        line([(340, 108), (340, 146)]),
        txt(350, 127, "quality"),
        box("box", 40, 150, 160, 56, "Request", "Spawn or wake"),
        line([(204, 178), (266, 178)]),
        box("teal", 270, 150, 140, 56, "Model router", "Weighs both"),
        line([(414, 178), (476, 178)]),
        box("purple", 480, 150, 160, 56, "Agent runs", "Model hidden"),
        line([(340, 270), (340, 210)]),
        txt(350, 240, "cost and headroom"),
        frame(40, 270, 600, 150),
        txt(56, 290, "Endpoint pool", "th"),
        txt(624, 290, "Shared by every agent", anchor="end"),
        box("teal", 56, 308, 180, 56, "API keys", "Pay per token"),
        box("teal", 250, 308, 180, 56, "Subscriptions", "Quota that renews"),
        box("teal", 444, 308, 180, 56, "Local endpoints", "Free, limited capacity"),
        txt(340, 394, "Rule: spend quota that is about to renew first", anchor="middle"),
    ]
    svg("router", 440, "Inside the model router", p)


def loadout():
    p = [
        box("teal", 75, 40, 160, 56, "Model router", "Picks hidden model"),
        line([(155, 100), (155, 200)]),
        txt(165, 122, "model"),
        cyl(373, 50, 160, 38, "Capability registry"),
        line([(453, 100), (453, 174)]),
        txt(463, 122, "loadout"),
        frame(40, 140, 600, 150, dashed=False),
        txt(56, 160, "Agent", "th"),
        txt(624, 160, "Built at spawn or wake", anchor="end"),
        box("purple", 60, 204, 190, 52, "Model", "Hidden"),
        frame(284, 178, 338, 94),
        txt(294, 191, "Loadout"),
        box("coral", 294, 204, 100, 52, "Skills", "How to do it"),
        box("coral", 403, 204, 100, 52, "MCP servers", "External apps"),
        box("coral", 512, 204, 100, 52, "Tools", "Basic actions"),
    ]
    svg("loadout", 310, "What an agent is assembled from", p)


def roles():
    p = [
        frame(40, 40, 360, 232),
        txt(56, 60, "Role catalogue", "th"),
        box("box", 56, 80, 170, 44, "Software engineer"),
        line([(76, 124), (76, 162), (92, 162)]),
        line([(76, 162), (76, 216), (92, 216)]),
        box("box", 96, 140, 160, 44, "Python developer"),
        box("box", 96, 194, 160, 44, "iOS developer"),
        txt(96, 254, "Children inherit, then add"),
        box("box", 280, 80, 104, 44, "Writer"),
        box("box", 280, 140, 104, 44, "Namer"),
        box("box", 280, 194, 104, 44, "Designer"),
        line([(402, 156), (426, 156)]),
        frame(430, 40, 210, 232),
        txt(446, 60, "One role contains", "th"),
        box("coral", 446, 80, 178, 36, "System prompt"),
        box("coral", 446, 126, 178, 36, "Skills"),
        box("coral", 446, 172, 178, 36, "MCP servers"),
        box("coral", 446, 218, 178, 36, "Tools"),
    ]
    svg("roles", 292, "Roles: catalogue and contents", p)


if __name__ == "__main__":
    baseline()
    work()
    fork()
    instantiate()
    database()
    computers()
    access()
    blind_testing()
    ratings()
    slots()
    papercuts()
    router()
    loadout()
    roles()
