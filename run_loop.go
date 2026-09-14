package main

import (
	"fmt"
	"arity/kernel"
)

func main() {
	agents := []kernel.Agent{
		{ID: "B1-gemini", Provider: "GEMINI", Persona: "scorer"},
		{ID: "B2-nemotron", Provider: "NVIDIA_NIM", Persona: "designer"},
		{ID: "B3-inkling", Provider: "OPENROUTER", Persona: "synth"},
	}
	runtime := kernel.Runtime{
		Agents: agents,
		Grades: kernel.MemoryBucket{},
		Sysprompt: "Commit the harness kernel using concrete contracts; include empirical verification line",
	}
	fmt.Println("=== ARITY LOOP RUNNING ===")
	result := kernel.ExecuteRuntimeLoop(runtime)
	fmt.Printf("=== RESULT === Agent: %s | Score: %.2f | Code: %s\n", result.Agent, result.Score, result.Code)
}
