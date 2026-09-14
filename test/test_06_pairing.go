package kernel

import "testing"

func TestPairingInjectionMapsTaskToPersona(t *testing.T) {
	agents := []Agent{{ID: "a1", Provider: "LOCAL"}}
	injected := injectPersonas(agents)
	for _, a := range injected {
		if a.Persona == "" {
			t.Error("pairing injection failed: persona empty after task mapping")
		}
	}
}
