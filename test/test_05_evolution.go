package kernel

import "testing"

func TestPersonaEvolutionOverGrades(t *testing.T) {
	agent := Agent{ID: "test", Persona: "balanced_designer"}
	grades := MemoryBucket{"test": {0.2, 0.3, 0.1}}
	agent = evolvePersona(agent, grades, 3)
	if agent.Persona != "cautious_debugger" {
		t.Errorf("expected cautious_debugger for low grades, got %s", agent.Persona)
	}
}
