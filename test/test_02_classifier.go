package kernel

import "testing"

func TestScoreButtonResultIsGradeFunctionNotAnxious(t *testing.T) {
	b := ButtonResult{Agent: "test", Code: "button click", Score: 0}
	score := ScoreButtonResult(b, "button_task")
	if score < 0.0 || score > 1.0 {
		t.Errorf("grade out of [0,1] range: got %.2f", score)
	}
}
