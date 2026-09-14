package kernel

// Runtime is the single recursive data-shape for agent execution,
// parallel loom branching, and open-floor synthesis.
// No hidden framework branches: tool state lives inside this struct.
type Runtime struct {
	Agents           []Agent
	Grades           MemoryBucket
	Sysprompt        string // embedded rules/non-goals
	ChatHistory      []string
	Multiplexer      MuxHandle
	BashHandle       *BashCall
	TeammateChannel  *TeammateMsg
	Results          []ButtonResult
}

// Agent represents a model instance (B1-inkling, B2-chatgpt, B3-nemotron, scorer, etc.).
type Agent struct {
	ID        string
	Provider  string // OPENAI, ANTHROPIC, LOCAL_SMOL, etc.
	Persona   string // scorer, designer, synth, committer
	Memory        []string // persistent grades / notes (#5 persona/memory)
}

// BashCall: minimal tool handle (open bash only).
type BashCall struct {
	Cmd    string
	Result string
	ExitCode int
}

// TeammateMsg: single teammate message file handoff (file-based truth).
type TeammateMsg struct {
	To     string
	Payload string
	ResponseFile string
}

// MemoryBucket: grades feed persona memory and calibration (#5).
type MemoryBucket map[string][]float64

// ButtonResult: what a parallel agent produces.
type ButtonResult struct {
	Agent string
	Code  string
	Score float64
}
