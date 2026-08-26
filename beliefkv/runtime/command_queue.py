from __future__ import annotations

import heapq
from dataclasses import dataclass, field

from beliefkv.runtime.protocol import (
    CommandKind,
    CommandQueueClass,
    ControlCommand,
    PhysicalPageAction,
    TransferDirection,
)


def command_transfer_direction(
    command: ControlCommand,
) -> TransferDirection | None:
    directions: set[TransferDirection] = set()
    bundle = command.physical_bundle
    if bundle is not None:
        for action in bundle.page_actions:
            if action.action == PhysicalPageAction.START_D2H:
                directions.add(TransferDirection.D2H)
            elif action.action == PhysicalPageAction.START_H2D:
                directions.add(TransferDirection.H2D)
    if len(directions) > 1:
        raise ValueError("one command cannot mix D2H and H2D page actions")
    if directions:
        return next(iter(directions))
    if command.kind == CommandKind.PREFETCH_CONTEXT:
        return TransferDirection.H2D
    if command.kind in {
        CommandKind.OFFLOAD_CONTEXT,
        CommandKind.SHADOW_CONTEXT,
    }:
        return TransferDirection.D2H
    return None


@dataclass(order=True)
class _QueueEntry:
    deadline_ms: float
    negative_priority: float
    sequence: int
    command: ControlCommand = field(compare=False)


class TransferCommandQueue:
    """Strict-priority urgent/shadow queue with deterministic tie breaking."""

    def __init__(self) -> None:
        self._urgent: list[_QueueEntry] = []
        self._shadow: list[_QueueEntry] = []
        self._sequence = 0
        self._active_ids: set[str] = set()
        self._cancelled_ids: set[str] = set()

    def put(self, command: ControlCommand) -> None:
        if command.command_id in self._active_ids:
            raise ValueError(f"duplicate command id: {command.command_id}")
        entry = _QueueEntry(
            deadline_ms=command.deadline_ms,
            negative_priority=-command.priority,
            sequence=self._sequence,
            command=command,
        )
        self._sequence += 1
        queue = (
            self._urgent
            if command.queue_class == CommandQueueClass.URGENT
            else self._shadow
        )
        heapq.heappush(queue, entry)
        self._active_ids.add(command.command_id)

    def pop(self, *, allow_shadow: bool = True) -> ControlCommand | None:
        for direction in (
            TransferDirection.H2D,
            TransferDirection.D2H,
            None,
        ):
            command = self.pop_lane(
                direction,
                allow_shadow=False,
            )
            if command is not None:
                return command
        if allow_shadow:
            for direction in (
                TransferDirection.H2D,
                TransferDirection.D2H,
                None,
            ):
                command = self._pop_matching(self._shadow, direction)
                if command is not None:
                    return command
        return None

    def pop_lane(
        self,
        direction: TransferDirection | None,
        *,
        allow_shadow: bool = True,
    ) -> ControlCommand | None:
        command = self._pop_matching(self._urgent, direction)
        if command is not None:
            return command
        if allow_shadow:
            return self._pop_matching(self._shadow, direction)
        return None

    def _pop_matching(
        self,
        queue: list[_QueueEntry],
        direction: TransferDirection | None,
    ) -> ControlCommand | None:
        if self._cancelled_ids:
            removed = {
                entry.command.command_id
                for entry in queue
                if entry.command.command_id in self._cancelled_ids
            }
            if removed:
                queue[:] = [
                    entry
                    for entry in queue
                    if entry.command.command_id not in removed
                ]
                heapq.heapify(queue)
                self._cancelled_ids.difference_update(removed)
        matches = (
            (entry, index)
            for index, entry in enumerate(queue)
            if entry.command.command_id not in self._cancelled_ids
            and command_transfer_direction(entry.command) == direction
        )
        selected = min(matches, default=None)
        if selected is None:
            return None
        _entry, index = selected
        command = queue.pop(index).command
        if index < len(queue):
            heapq.heapify(queue)
        self._active_ids.discard(command.command_id)
        return command

    def _pop_valid(self, queue: list[_QueueEntry]) -> ControlCommand | None:
        while queue:
            command = heapq.heappop(queue).command
            self._active_ids.discard(command.command_id)
            if command.command_id in self._cancelled_ids:
                self._cancelled_ids.discard(command.command_id)
                continue
            return command
        return None

    def cancel(self, command_id: str) -> bool:
        if command_id not in self._active_ids:
            return False
        self._cancelled_ids.add(command_id)
        self._active_ids.discard(command_id)
        return True

    @property
    def urgent_count(self) -> int:
        return sum(
            entry.command.command_id not in self._cancelled_ids
            for entry in self._urgent
        )

    @property
    def shadow_count(self) -> int:
        return sum(
            entry.command.command_id not in self._cancelled_ids
            for entry in self._shadow
        )

    def pending_commands(self) -> tuple[ControlCommand, ...]:
        """Return a deterministic, non-destructive queue snapshot."""

        entries = [
            *(
                (0, item)
                for item in self._urgent
                if item.command.command_id not in self._cancelled_ids
            ),
            *(
                (1, item)
                for item in self._shadow
                if item.command.command_id not in self._cancelled_ids
            ),
        ]
        return tuple(
            item.command
            for _, item in sorted(
                entries,
                key=lambda value: (
                    value[0],
                    value[1].deadline_ms,
                    value[1].negative_priority,
                    value[1].sequence,
                ),
            )
        )

    def get(self, command_id: str) -> ControlCommand | None:
        """Return an active queued command without changing queue order."""

        if command_id not in self._active_ids:
            return None
        for entry in (*self._urgent, *self._shadow):
            if (
                entry.command.command_id == command_id
                and command_id not in self._cancelled_ids
            ):
                return entry.command
        return None

    def __len__(self) -> int:
        return self.urgent_count + self.shadow_count
