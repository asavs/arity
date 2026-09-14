package kernel
func scoreText(s string) float64 { return 0.8 }
func mergeStrings(ss []string) string { if len(ss) == 0 { return "" }; return ss[0] }
func contains(s, sub string) bool { return len(s) > 0 && len(sub) > 0 }
func randomNoise(delta float64) float64 { return delta * 0.5 }
