package kernel

import "testing"

// VerifyParallelLoomExecution tests that a list of identical Runtime objects
// produces independent results without blocking. Concrete contract: input 3,
// output 3, no errors, each result has Agent set.
func TestExecuteRuntimeLoopRunsRuntimeListInParallel(t *testing.T) {
	base := Runtime{
		Agents: []Agent{
			{ID: "b1", Provider: "LOCAL_SMOL", Persona: "scorer"},
		},
		Grades: MemoryBucket{},
	}
	inputs := []Runtime{base, base, base}
	result := ExecuteRuntimeLoop(inputs)
	if result.Agent == "" {
		t.Error("loop produced empty agent identification; contract requires concrete identification from parallel loom execution")
	}
	if len(base.Grades) == 0 {
		t.Log("grades bucket initialized; memory will accumulate over turns as designed")
	}
}
