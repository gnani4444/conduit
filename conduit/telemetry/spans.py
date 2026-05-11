"""OTel span attribute constants for Conduit."""

# Standard OTel GenAI attributes
GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
GEN_AI_TOOL_NAME = "gen_ai.tool.name"
GEN_AI_SYSTEM = "gen_ai.system"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
GEN_AI_RESPONSE_FINISH_REASON = "gen_ai.response.finish_reason"

# Operation name values
OP_EXECUTE_TOOL = "execute_tool"
OP_CHAT = "chat"
OP_INVOKE_AGENT = "invoke_agent"

# Conduit extensions
CONDUIT_HOOK_PHASE = "conduit.hook_phase"
CONDUIT_TOOL_VERSION = "conduit.tool.version"
CONDUIT_TOOL_STEP_INDEX = "conduit.tool.step_index"
CONDUIT_VALIDATION_RESULT = "conduit.validation.result"
CONDUIT_VALIDATION_CORRECTIONS = "conduit.validation.corrections"
CONDUIT_VALIDATION_ERRORS = "conduit.validation.errors"
CONDUIT_FAILURE_CLASS = "conduit.failure.class"
CONDUIT_FAILURE_SEVERITY = "conduit.failure.severity"
CONDUIT_RECOVERY_ACTION = "conduit.recovery.action"
CONDUIT_RECOVERY_ATTEMPT = "conduit.recovery.attempt"
CONDUIT_LATENCY_VALIDATION_MS = "conduit.latency.validation_ms"

# Agent lifecycle
CONDUIT_AGENT_TASK_ID = "conduit.agent.task_id"
CONDUIT_AGENT_STEP_COUNT = "conduit.agent.step_count"
CONDUIT_AGENT_LOOP_COUNT = "conduit.agent.loop_count"
CONDUIT_AGENT_OUTCOME = "conduit.agent.outcome"

# Context
CONDUIT_CONTEXT_TOKEN_COUNT = "conduit.context.token_count"
CONDUIT_CONTEXT_BUDGET_PCT = "conduit.context.budget_pct"

# Hook phase values
PHASE_PRE = "pre"
PHASE_AROUND = "around"
PHASE_POST = "post"

# Validation result values
VALIDATION_PASS = "pass"
VALIDATION_CORRECTED = "corrected"
VALIDATION_GATED = "gated"
VALIDATION_SKIPPED = "skipped"

# Failure class values
FAILURE_NONE = "none"
FAILURE_SCHEMA_ERROR = "schema_error"
FAILURE_TOOL_ERROR = "tool_error"
FAILURE_TIMEOUT = "timeout"
FAILURE_AGENT_LOOP = "agent_loop"

# Severity values
SEV_LOW = "low"
SEV_MEDIUM = "medium"
SEV_HIGH = "high"
SEV_CRITICAL = "critical"

# Recovery action values
RECOVERY_NONE = "none"
RECOVERY_RETRY = "retry"
RECOVERY_RETRY_CORRECTED = "retry_corrected"
RECOVERY_RETRY_BACKOFF = "retry_backoff"
RECOVERY_REROUTE = "reroute"
RECOVERY_REPLAN = "replan"
RECOVERY_ESCALATE = "escalate"
RECOVERY_DEGRADE = "degrade"
