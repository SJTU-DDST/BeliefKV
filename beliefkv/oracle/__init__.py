"""Perfect-future oracle contracts.

This package is independent from the legacy offline oracle in
``beliefkv.policy.joint_oracle``. Importing it does not load a truth artifact
or alter the online scheduler.
"""

from beliefkv.oracle.contracts import (
    FROZEN_AGENT_DEMAND_SCHEMA_VERSION,
    FrozenActionBoundary,
    FrozenActionBoundaryKind,
    FrozenAgentDemand,
    FrozenContextMode,
    FrozenDemandProvenance,
    FrozenInvocationDemand,
    FrozenInvocationRelation,
    FrozenJoinDemand,
    FrozenJoinKey,
    FrozenJoinMode,
    FrozenLLMCallDemand,
    FrozenToolDemand,
    FrozenToolKey,
    FrozenToolOutcome,
    LogicalInvocationKey,
    OracleArmCapability,
    OracleCallPhase,
    OracleFutureView,
    OracleInvocationProgress,
    OracleReplayCursor,
    PerfectFutureOracleArm,
    capability_for_arm,
)
from beliefkv.oracle.truth_provider import (
    AgentFutureField,
    KVFutureField,
    OracleFutureAccessViolation,
    OracleReplayCursorError,
    OracleTruthAccessRecord,
    OracleTruthProvider,
)

from beliefkv.oracle.physical_sidecar import (
    FROZEN_PHYSICAL_SIDECAR_SCHEMA_VERSION,
    FrozenPhysicalCall,
    FrozenPhysicalSidecar,
)

__all__ = [
    "FROZEN_AGENT_DEMAND_SCHEMA_VERSION",
    "FROZEN_PHYSICAL_SIDECAR_SCHEMA_VERSION",
    "AgentFutureField",
    "FrozenActionBoundary",
    "FrozenActionBoundaryKind",
    "FrozenAgentDemand",
    "FrozenContextMode",
    "FrozenDemandProvenance",
    "FrozenInvocationDemand",
    "FrozenInvocationRelation",
    "FrozenJoinDemand",
    "FrozenJoinKey",
    "FrozenJoinMode",
    "FrozenLLMCallDemand",
    "FrozenPhysicalCall",
    "FrozenPhysicalSidecar",
    "FrozenToolDemand",
    "FrozenToolKey",
    "FrozenToolOutcome",
    "KVFutureField",
    "LogicalInvocationKey",
    "OracleArmCapability",
    "OracleCallPhase",
    "OracleFutureAccessViolation",
    "OracleFutureView",
    "OracleInvocationProgress",
    "OracleReplayCursor",
    "OracleReplayCursorError",
    "OracleTruthAccessRecord",
    "OracleTruthProvider",
    "PerfectFutureOracleArm",
    "capability_for_arm",
]
