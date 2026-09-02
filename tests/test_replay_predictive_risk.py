from dataclasses import replace

from beliefkv.policy.reference import MetadataSource, MetadataValue
from scripts.replay_predictive_risk import _candidate_local_policy_input
from tests.test_whatif_packer import _input


def test_frozen_online_candidate_scope_is_not_reselected() -> None:
    policy_input = _input(capacity=1_000, reserved=0)
    metadata = dict(policy_input.optional_metadata)
    metadata["beliefkv_predictive_candidate_scope"] = MetadataValue(
        MetadataSource.OBSERVED,
        {
            "beneficiary_request_id": "request-online",
            "victim_context_ids": ("context-online",),
        },
        "candidate_local_physicalizer",
    )
    policy_input = replace(policy_input, optional_metadata=metadata)

    replayed = _candidate_local_policy_input(
        policy_input,
        object(),
        object(),
    )

    assert replayed is policy_input
