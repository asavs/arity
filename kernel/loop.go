package kernel

// ExecuteRuntimeLoop is the essential loop: same code receives
// Runtime, list[Runtime] (loom), or ChannelMsg (open floor).
// Recursion is implicit in nested Runtime input objects; no awareness.
func ExecuteRuntimeLoop(input interface{}) ButtonResult {
	switch v := input.(type) {
	case []Runtime:
		outputs := make([]ButtonResult, len(v))
		for i, sub := range v {
			outputs[i] = ExecuteRuntimeLoop(sub)
		}
		return mergeResults(outputs)
	case ChannelMsg:
		return synthesizeByCherryPick([]ChannelMsg{v})
	case Runtime:
		return executeSingleRuntime(v)
	default:
		panic("unknown input shape for loop")
	}
}
