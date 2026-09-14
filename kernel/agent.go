package kernel

func executeSingleRuntime(rt Runtime) ButtonResult {
	if rt.BashHandle != nil {
		rt.BashHandle.Result = executeBash(rt.BashHandle.Cmd)
	}
	if rt.TeammateChannel != nil {
		writeFile(rt.TeammateChannel.ResponseFile, rt.TeammateChannel.Payload)
	}
	results := make([]ButtonResult, len(rt.Agents))
	prompt := buildPrompt(rt)
	for i, a := range rt.Agents {
		switch a.Provider {
		case "GEMINI":
			results[i] = callGemini(prompt)
		case "NVIDIA_NIM":
			results[i] = callNvidiaNIM(prompt)
		case "OPENROUTER":
			results[i] = callOpenRouter(prompt)
		default:
			results[i] = runAgent(a, prompt)
		}
	}
	rt.Results = results
	for _, r := range results {
		rt.Grades[r.Agent] = append(rt.Grades[r.Agent], r.Score)
		rt.ChatHistory = append(rt.ChatHistory, agentMessage(r))
		if len(rt.ChatHistory) > 10 {
			rt.ChatHistory = rt.ChatHistory[len(rt.ChatHistory)-10:]
		}
	}
	return results[0]
}
