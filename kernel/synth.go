package kernel

func synthesizeByCherryPick(messages []ChannelMsg) ButtonResult {
	best := make([]string, 0)
	for _, m := range messages {
		score := scoreText(m.Payload)
		if score > 0.8 {
			best = append(best, m.Payload)
		}
	}
	if len(best) == 0 {
		return ButtonResult{Agent: messages[0].From, Code: messages[0].Payload, Score: 0.5}
	}
	return ButtonResult{Agent: "judge", Code: mergeStrings(best), Score: 0.9}
}

func mergeResults(results []ButtonResult) ButtonResult {
	if len(results) == 0 {
		return ButtonResult{}
	}
	best := results[0]
	for _, r := range results[1:] {
		if r.Score > best.Score {
			best = r
		}
	}
	return best
}
