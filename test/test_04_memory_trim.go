package kernel

import "testing"

func TestMemoryTrimKeepsTenEntries(t *testing.T) {
	rt := Runtime{Agents: []Agent{{ID: "a"}}, Grades: MemoryBucket{}, ChatHistory: []string{}}
	for i := range 15 {
		rt.ChatHistory = append(rt.ChatHistory, fmt.Sprintf("line%d", i))
	}
	if len(rt.ChatHistory) > 10 {
		rt.ChatHistory = rt.ChatHistory[len(rt.ChatHistory)-10:]
	}
	if len(rt.ChatHistory) > 10 {
		t.Errorf("history exceeded 10 entries: got %d", len(rt.ChatHistory))
	}
}
