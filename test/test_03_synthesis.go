package kernel

import "testing"

func TestOpenFloorSynthesisCompromises(t *testing.T) {
	msg := ChannelMsg{From: "b1", Payload: "button design v1", Score: 0.92}
	res := synthesizeByCherryPick([]ChannelMsg{msg})
	if res.Agent == "" {
		t.Error("synthesis produced empty agent; must cherry-pick from open floor")
	}
}
