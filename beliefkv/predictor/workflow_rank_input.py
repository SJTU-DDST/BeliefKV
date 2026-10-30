"""Bounded, explicit semantic input for future workflow ranking models."""

from dataclasses import dataclass


KIND = "workflow_task_and_delegation_metadata_v1"
TASK_CHARS = 4096
DELEGATION_CHARS = 512
ROLE_CHARS = 128


@dataclass(frozen=True)
class WorkflowRankInput:
    task_instructions: str = ""
    delegation_instructions: str = ""
    agent_role: str = ""
    task_truncated: bool = False
    delegation_truncated: bool = False
    role_truncated: bool = False

    @classmethod
    def from_instructions(cls, *, task_instructions="", delegation_instructions="", agent_role=""):
        values = (task_instructions, delegation_instructions, agent_role)
        if any(type(value) is not str for value in values):
            raise ValueError("workflow ranking accepts explicit instruction strings only")
        return cls(
            task_instructions[:TASK_CHARS], delegation_instructions[:DELEGATION_CHARS],
            agent_role[:ROLE_CHARS], len(task_instructions) > TASK_CHARS,
            len(delegation_instructions) > DELEGATION_CHARS, len(agent_role) > ROLE_CHARS,
        )

    def to_dict(self):
        return {"kind": KIND, **self.__dict__}

    @classmethod
    def from_dict(cls, values):
        if not isinstance(values, dict) or values.keys() != {
            "kind", *cls.__dataclass_fields__,
        } or values.get("kind") != KIND:
            raise ValueError("reject undeclared workflow prediction inputs")
        strings = ("task_instructions", "delegation_instructions", "agent_role")
        if any(
            type(values[name]) is not str or len(values[name]) > limit
            for name, limit in zip(strings, (TASK_CHARS, DELEGATION_CHARS, ROLE_CHARS))
        ) or any(
            type(values[name]) is not bool for name in (
                "task_truncated", "delegation_truncated", "role_truncated",
            )
        ):
            raise ValueError("workflow prediction input exceeds its explicit contract")
        return cls(**{name: values[name] for name in cls.__dataclass_fields__})

    def model_text(self):
        return "\n".join(
            label + value for label, value in (
                ("Task: ", self.task_instructions),
                ("Delegation: ", self.delegation_instructions),
                ("Role: ", self.agent_role),
            ) if value
        )
