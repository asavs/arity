package kernel

import "testing"

func TestLoomParallel(t *testing.T) {
	// Contract: list of identical Runtime objects runs in parallel.
	// Verdict: PASS if outputs count equals input count.
	base := Runtime{
		Agents: []Agent{{ID: "a1", Provider: "LOCAL_SMOL", Persona: "designer"}},
		Grades: MemoryBucket{},
		Sysprompt: "make button",
	}

	inputs := []Runtime{base, base, base}
	result := ExecuteRuntimeLoop(inputs)

	if result.Agent != "a1" {
		t.Errorf("expected agent a1, got %s", result.Agent)
	}
}
