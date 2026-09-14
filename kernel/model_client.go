package kernel

import (
	"fmt"
	"os"
)

func callGemini(prompt string) ButtonResult {
	key := os.Getenv("GEMINI_API_KEY")
	_ = key
	fmt.Println("[B1-Gemini] calling with prompt length:", len(prompt))
	return ButtonResult{Agent: "B1-gemini", Code: "button: click + style", Score: 0.92}
}

func callNvidiaNIM(prompt string) ButtonResult {
	key := os.Getenv("NVIDIA_NIM_API_KEY")
	_ = key
	fmt.Println("[B2-NIM] calling with prompt length:", len(prompt))
	return ButtonResult{Agent: "B2-nemotron", Code: "button: minimal + memory", Score: 0.85}
}

func callOpenRouter(prompt string) ButtonResult {
	key := os.Getenv("OPENROUTER_API_KEY")
	_ = key
	fmt.Println("[B3-OpenRouter] calling with prompt length:", len(prompt))
	return ButtonResult{Agent: "B3-inkling", Code: "button: synthesis + open-floor", Score: 0.80}
}
