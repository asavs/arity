Collapse kernel execution into a recursive data loop

The kernel splits loom branching, scoring, synthesis, memory updates, and agent execution across separate functions even though each operation consumes the same runtime-shaped data. Route Runtime, lists of Runtime branches, and ChannelMsg inputs through loop() so recursion is represented by nested input data rather than specialized control-flow paths. Terminal UI, persona pairing, and multiplexing policy remain outside this minimal loop model.

Pseudo specification diff: 240 lines -> 39 lines; loop() covers Runtime, list[Runtime], and ChannelMsg paths.

Author: asavs
