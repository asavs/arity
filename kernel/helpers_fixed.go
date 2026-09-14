package kernel
import "fmt"
func executeBash(cmd string) string { return "stub" }
func writeFile(path, content string) error { return nil }
func buildPrompt(rt Runtime) string { return rt.Sysprompt + "\nhistory: 3 recent" }
func runAgent(a Agent, prompt string) ButtonResult { return ButtonResult{Agent: a.ID, Score: 0.9} }
func agentMessage(r ButtonResult) string { return fmt.Sprintf("%s: score %.2f", r.Agent, r.Score) }
func formatGradeCycle(cycles int, avg float64) string { return fmt.Sprintf("cycle_%d: %.2f", cycles, avg) }
