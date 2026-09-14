package kernel

import "testing"

func TestTerminalMinimalDisplaysState(t *testing.T) {
	runtime := Runtime{Agents: []Agent{{ID: "a1"}}, Grades: MemoryBucket{}, Results: []ButtonResult{{Agent: "a1", Score: 0.9}}}
	drawTerminal(runtime)
}
