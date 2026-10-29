
### Failed or incomplete summary streams (#880)

A compaction summary requires a successful provider completion and nonempty text.
Partial deltas preceding an error, a stream without done, and length/tool-use or
interrupted completions are rejected. Retries start with empty output and preserve
the provider's retryability. The existing tree failure path retains original
messages instead of committing partial summary text. Bare legacy done events remain
accepted; a provider must report its actual failure as an error or interrupted stop.
