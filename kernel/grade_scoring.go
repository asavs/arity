package kernel

func ScoreButtonResult(button ButtonResult, task string) float64 {
	if task == "button_task" {
		if contains(button.Code, "button") && contains(button.Code, "click") {
			return 0.9 + randomNoise(0.05)
		}
		return 0.3 + randomNoise(0.05)
	}
	return 0.75
}
