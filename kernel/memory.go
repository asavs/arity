package kernel

func evolvePersona(agent Agent, grades MemoryBucket, cycles int) Agent {
	history := grades[agent.ID]
	avg := 0.0
	if len(history) > 0 {
		start := 0
		if len(history) > cycles {
			start = len(history) - cycles
		}
		count := 0
		sum := 0.0
		for _, g := range history[start:] {
			sum += g
			count++
		}
		if count > 0 {
			avg = sum / float64(count)
		}
		switch {
		case avg < 0.4:
			agent.Persona = "cautious_debugger"
		case avg > 0.85:
			agent.Persona = "confident_scorer"
		default:
			agent.Persona = "balanced_designer"
		}
	}
	agent.Memory = append(agent.Memory, formatGradeCycle(cycles, avg))
	return agent
}
