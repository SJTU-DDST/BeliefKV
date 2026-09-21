from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import blake2b

from beliefkv.control.causal_graph import RuntimeCausalContextGraph
from beliefkv.policy.leases import BundleLease, CausalLeaseProjector, LeaseKind
from beliefkv.runtime.page_index import PageOwnershipIndex, PhysicalPageRecord
from beliefkv.runtime.protocol import (
    CommandKind,
    PageHandle,
    PhysicalBundleIntent,
    PhysicalPageAction,
    PhysicalResidency,
    ResolvedPageAction,
    TransferBlocker,
    TransferBlockerCode,
)


class BundleScope(str, Enum):
    """Cross-context impact of the extents changed by one bundle action."""

    EXCLUSIVE_SUFFIX = "exclusive_suffix"
    SHARED_SUBTREE = "shared_subtree"


@dataclass(frozen=True)
class PhysicalBundle:
    bundle_id: str
    handles: tuple[PageHandle, ...]
    owner_context_ids: tuple[str, ...]
    scope: BundleScope
    exclusive_action_bytes: int
    cross_context_action_bytes: int
    foreign_owner_context_ids: tuple[str, ...]
    physical_unique_bytes: int
    gpu_bytes: int
    cpu_bytes: int
    marginal_reclaimable_bytes: int
    closure_bytes: int
    locked_bytes: int
    residency: str
    generation_fingerprint: str
    lease: BundleLease


@dataclass(frozen=True)
class PhysicalBundlePreview:
    command_kind: CommandKind
    context_id: str
    context_epoch: int
    bundle: PhysicalBundle
    page_actions: tuple[ResolvedPageAction, ...]
    blockers: tuple[TransferBlocker, ...]
    copy_bytes: int

    @property
    def eligible(self) -> bool:
        return bool(self.page_actions) and not self.blockers

    def intent(self) -> PhysicalBundleIntent:
        if not self.eligible:
            raise ValueError("a blocked physical bundle cannot become an intent")
        return PhysicalBundleIntent(
            bundle_id=self.bundle.bundle_id,
            closure_handles=self.bundle.handles,
            page_actions=self.page_actions,
            generation_fingerprint=self.bundle.generation_fingerprint,
            closure_bytes=self.bundle.closure_bytes,
            expected_reclaimable_bytes=self.bundle.marginal_reclaimable_bytes,
            locked_bytes=self.bundle.locked_bytes,
        )


@dataclass(frozen=True)
class BundlePreviewEvent:
    kind: str
    ts_ms: float
    fields: dict[str, object]


class PhysicalBundleBuilder:
    """Build immutable, closure-complete transfer candidates from physical facts."""

    def __init__(
        self,
        graph: RuntimeCausalContextGraph,
        page_index: PageOwnershipIndex,
        leases: CausalLeaseProjector | None = None,
    ) -> None:
        self.graph = graph
        self.page_index = page_index
        self.leases = leases or CausalLeaseProjector(graph)

    def previews_for_context(
        self,
        command_kind: CommandKind,
        context_id: str,
        context_epoch: int,
        *,
        now_ms: float,
        allow_ready_owners: bool = False,
        protected_context_id: str | None = None,
        bypass_owner_context_ids: frozenset[str] = frozenset(),
        host_available_bytes: int | None = None,
        device_available_bytes: int | None = None,
    ) -> tuple[PhysicalBundlePreview, ...]:
        context = self.graph.contexts.get(context_id)
        if (
            context is None
            or context.epoch != context_epoch
            or not self.page_index.has_context(context_id)
            or self.page_index.context_epoch(context_id) != context_epoch
        ):
            return ()
        if command_kind in {
            CommandKind.OFFLOAD_CONTEXT,
            CommandKind.SHADOW_CONTEXT,
        }:
            previews = self._offload_previews(
                command_kind,
                context_id,
                context_epoch,
                now_ms=now_ms,
                allow_ready_owners=allow_ready_owners,
                protected_context_id=protected_context_id,
                bypass_owner_context_ids=bypass_owner_context_ids,
                host_available_bytes=host_available_bytes,
            )
        elif command_kind == CommandKind.DROP_CONTEXT:
            previews = self._drop_previews(
                context_id,
                context_epoch,
                now_ms=now_ms,
                allow_ready_owners=allow_ready_owners,
                protected_context_id=protected_context_id,
                bypass_owner_context_ids=bypass_owner_context_ids,
            )
        elif command_kind == CommandKind.PREFETCH_CONTEXT:
            previews = self._prefetch_previews(
                context_id,
                context_epoch,
                now_ms=now_ms,
                device_available_bytes=device_available_bytes,
            )
        else:
            return ()
        return tuple(
            sorted(
                previews,
                key=lambda item: (
                    not item.eligible,
                    -item.bundle.marginal_reclaimable_bytes,
                    item.bundle.closure_bytes,
                    item.bundle.bundle_id,
                ),
            )
        )

    def best_exclusive_shadow_preview_for_context(
        self,
        context_id: str,
        context_epoch: int,
        *,
        now_ms: float,
        host_available_bytes: int | None = None,
        max_copy_bytes: int | None = None,
    ) -> PhysicalBundlePreview | None:
        """Build one batched private D2H shadow candidate.

        A bounded batch may merge multiple disjoint exclusive suffixes or
        select only part of an oversized private tree. Shadow actions retain
        GPU copies, so selected pages need resident GPU ancestors but need not
        include every GPU descendant.
        """

        context = self.graph.contexts.get(context_id)
        if (
            context is None
            or context.epoch != context_epoch
            or not self.page_index.has_context(context_id)
            or self.page_index.context_epoch(context_id) != context_epoch
        ):
            return None
        if max_copy_bytes is not None and max_copy_bytes <= 0:
            return None

        target_owner = {context_id}
        memo: dict[PageHandle, tuple[bool, bool, int]] = {}
        visiting: set[PageHandle] = set()

        def private_subtree(handle: PageHandle) -> tuple[bool, bool, int]:
            cached = memo.get(handle)
            if cached is not None:
                return cached
            if handle in visiting:
                return False, False, 0
            page = self.page_index.pages.get(handle)
            if page is None or page.residency == PhysicalResidency.DEAD:
                return False, False, 0
            if not page.gpu_resident:
                return True, True, 0

            visiting.add(handle)
            private = set(page.owner_contexts) == target_owner
            unblocked = not self._page_blockers(page)
            copy_bytes = (
                page.size_bytes
                if page.residency == PhysicalResidency.GPU_ONLY
                else 0
            )
            for child_handle in page.children:
                child = self.page_index.pages.get(child_handle)
                if child is None or child.residency == PhysicalResidency.DEAD:
                    private = False
                    unblocked = False
                    continue
                if not child.gpu_resident:
                    continue
                child_private, child_unblocked, child_bytes = private_subtree(
                    child_handle
                )
                private = private and child_private
                unblocked = unblocked and child_unblocked
                copy_bytes += child_bytes
            visiting.remove(handle)
            result = private, unblocked, copy_bytes
            memo[handle] = result
            return result

        budget = max_copy_bytes
        if budget is not None and host_available_bytes is not None:
            budget = min(budget, host_available_bytes)
        candidates: list[tuple[int, PageHandle, tuple[PageHandle, ...] | None]] = []
        for page in self.page_index.context_pages(context_id):
            if not page.gpu_resident:
                continue
            private, unblocked, copy_bytes = private_subtree(page.handle)
            if not private or not unblocked or copy_bytes <= 0:
                continue
            if budget is not None and copy_bytes > budget:
                # A shadow keeps the GPU copy, so a private tree can be copied
                # in part without evicting an ancestor above live descendants.
                remaining = budget
                partial: list[PageHandle] = []
                stack = [page.handle]
                while stack:
                    handle = stack.pop()
                    node = self.page_index.pages[handle]
                    if node.residency == PhysicalResidency.GPU_ONLY:
                        if node.size_bytes > remaining:
                            continue
                        remaining -= node.size_bytes
                    partial.append(handle)
                    stack.extend(
                        sorted(
                            (
                                child
                                for child in node.children
                                if self.page_index.pages[child].gpu_resident
                            ),
                            reverse=True,
                        )
                    )
                if remaining == budget:
                    continue
                candidates.append((budget - remaining, page.handle, tuple(partial)))
                continue
            parent_is_candidate = False
            if max_copy_bytes is None and page.parent is not None:
                parent = self.page_index.pages.get(page.parent)
                if parent is not None and parent.gpu_resident:
                    parent_private, parent_unblocked, parent_bytes = private_subtree(
                        parent.handle
                    )
                    parent_is_candidate = (
                        parent_private and parent_unblocked and parent_bytes > 0
                    )
            if not parent_is_candidate:
                candidates.append((copy_bytes, page.handle, None))

        selected: list[PhysicalBundlePreview] = []
        selected_handles: set[PageHandle] = set()
        selected_copy_bytes = 0
        for _, root_handle, partial_handles in sorted(
            candidates,
            key=lambda item: (-item[0], item[1]),
        ):
            if partial_handles is None:
                preview = self.preview_offload_root(
                    CommandKind.SHADOW_CONTEXT,
                    context_id,
                    context_epoch,
                    root_handle,
                    now_ms=now_ms,
                    host_available_bytes=host_available_bytes,
                )
            else:
                preview = self.preview_offload_handles(
                    CommandKind.SHADOW_CONTEXT,
                    context_id,
                    context_epoch,
                    partial_handles,
                    now_ms=now_ms,
                    host_available_bytes=host_available_bytes,
                )
            if (
                preview is not None
                and preview.eligible
                and preview.copy_bytes > 0
                and (
                    max_copy_bytes is None
                    or (
                        preview.copy_bytes + selected_copy_bytes
                        <= max_copy_bytes
                    )
                )
                and not selected_handles.intersection(preview.bundle.handles)
                and preview.bundle.exclusive_action_bytes > 0
                and preview.bundle.cross_context_action_bytes == 0
                and preview.bundle.owner_context_ids == (context_id,)
            ):
                if (
                    host_available_bytes is not None
                    and preview.copy_bytes + selected_copy_bytes
                    > host_available_bytes
                ):
                    continue
                selected.append(preview)
                selected_handles.update(preview.bundle.handles)
                selected_copy_bytes += preview.copy_bytes
                if max_copy_bytes is None or selected_copy_bytes >= max_copy_bytes:
                    break
        if not selected:
            return None
        if len(selected) == 1:
            return selected[0]
        merged_handles = tuple(
            sorted(
                {handle for item in selected for handle in item.bundle.handles}
            )
        )
        return self.preview_offload_handles(
            CommandKind.SHADOW_CONTEXT,
            context_id,
            context_epoch,
            merged_handles,
            now_ms=now_ms,
            host_available_bytes=host_available_bytes,
        )

    def preview_offload_handles(
        self,
        command_kind: CommandKind,
        context_id: str,
        context_epoch: int,
        handles: tuple[PageHandle, ...],
        *,
        now_ms: float,
        allow_ready_owners: bool = False,
        protected_context_id: str | None = None,
        bypass_owner_context_ids: frozenset[str] = frozenset(),
        host_available_bytes: int | None = None,
    ) -> PhysicalBundlePreview | None:
        """Rebuild a D2H preview; shadow permits partial descendant selection."""

        if command_kind not in {
            CommandKind.OFFLOAD_CONTEXT,
            CommandKind.SHADOW_CONTEXT,
        } or not handles:
            return None
        context = self.graph.contexts.get(context_id)
        if context is None or context.epoch != context_epoch:
            return None
        pages: dict[PageHandle, PhysicalPageRecord] = {}
        for handle in handles:
            page = self.page_index.pages.get(handle)
            if page is None:
                return None
            pages[handle] = page

        actions: list[ResolvedPageAction] = []
        blockers: list[TransferBlocker] = []
        blocked_handles: set[PageHandle] = set()
        selected = set(handles)
        for page in sorted(
            pages.values(),
            key=lambda item: (-item.radix_depth, item.handle),
        ):
            if page.residency == PhysicalResidency.DUAL_CLEAN:
                if command_kind == CommandKind.OFFLOAD_CONTEXT:
                    actions.append(
                        ResolvedPageAction(
                            page.handle,
                            PhysicalPageAction.COMMIT_CPU,
                            page.size_bytes,
                        )
                    )
            elif page.residency == PhysicalResidency.GPU_ONLY:
                actions.append(
                    ResolvedPageAction(
                        page.handle,
                        PhysicalPageAction.START_D2H,
                        page.size_bytes,
                    )
                )
            page_blockers = self._page_blockers(page)
            page_blockers += self._owner_blockers(
                page,
                now_ms=now_ms,
                allow_ready_owners=allow_ready_owners,
                protected_context_id=protected_context_id,
                bypass_owner_context_ids=bypass_owner_context_ids,
            )
            blockers.extend(page_blockers)
            if page_blockers:
                blocked_handles.add(page.handle)
                continue
            ancestor = page.parent
            seen = {page.handle}
            while ancestor is not None:
                if ancestor in seen:
                    blockers.append(
                        TransferBlocker(
                            TransferBlockerCode.ANCESTOR_CLOSURE,
                            page.handle,
                            page.size_bytes,
                            "D2H merged closure has an ancestor cycle",
                        )
                    )
                    blocked_handles.add(page.handle)
                    break
                seen.add(ancestor)
                if (
                    command_kind == CommandKind.OFFLOAD_CONTEXT
                    and ancestor not in selected
                ):
                    blockers.append(
                        TransferBlocker(
                            TransferBlockerCode.ANCESTOR_CLOSURE,
                            page.handle,
                            page.size_bytes,
                            "D2H merged closure omits an ancestor",
                        )
                    )
                    blocked_handles.add(page.handle)
                    break
                parent = self.page_index.pages.get(ancestor)
                if parent is None or not parent.gpu_resident:
                    blockers.append(
                        TransferBlocker(
                            TransferBlockerCode.ANCESTOR_CLOSURE,
                            page.handle,
                            page.size_bytes,
                            "D2H merged closure has a non-resident ancestor",
                        )
                    )
                    blocked_handles.add(page.handle)
                    break
                ancestor = parent.parent

        copy_bytes = sum(
            item.size_bytes
            for item in actions
            if item.action == PhysicalPageAction.START_D2H
        )
        if host_available_bytes is not None and copy_bytes > host_available_bytes:
            blockers.append(
                TransferBlocker(
                    TransferBlockerCode.HOST_CAPACITY,
                    handles[0],
                    copy_bytes,
                    "D2H merged bundle exceeds current host availability",
                )
            )
        return self._preview(
            command_kind,
            context_id,
            context_epoch,
            pages,
            tuple(actions),
            self._deduplicate_blockers(blockers),
            blocked_handles,
            now_ms=now_ms,
        )

    def find_intent_preview(
        self,
        command_kind: CommandKind,
        context_id: str,
        context_epoch: int,
        bundle_id: str,
        *,
        now_ms: float,
        allow_ready_owners: bool = False,
        protected_context_id: str | None = None,
        bypass_owner_context_ids: frozenset[str] = frozenset(),
        host_available_bytes: int | None = None,
        device_available_bytes: int | None = None,
    ) -> PhysicalBundlePreview | None:
        return next(
            (
                item
                for item in self.previews_for_context(
                    command_kind,
                    context_id,
                    context_epoch,
                    now_ms=now_ms,
                    allow_ready_owners=allow_ready_owners,
                    protected_context_id=protected_context_id,
                    bypass_owner_context_ids=bypass_owner_context_ids,
                    host_available_bytes=host_available_bytes,
                    device_available_bytes=device_available_bytes,
                )
                if item.bundle.bundle_id == bundle_id
            ),
            None,
        )

    def _offload_previews(
        self,
        command_kind: CommandKind,
        context_id: str,
        context_epoch: int,
        *,
        now_ms: float,
        allow_ready_owners: bool,
        protected_context_id: str | None,
        bypass_owner_context_ids: frozenset[str],
        host_available_bytes: int | None,
    ) -> list[PhysicalBundlePreview]:
        roots = [
            page
            for page in self.page_index.context_pages(context_id)
            if page.gpu_resident
        ]
        previews: list[PhysicalBundlePreview] = []
        seen_closures: set[tuple[PageHandle, ...]] = set()
        for root in sorted(roots, key=lambda page: (-page.radix_depth, page.handle)):
            preview = self.preview_offload_root(
                command_kind,
                context_id,
                context_epoch,
                root.handle,
                now_ms=now_ms,
                allow_ready_owners=allow_ready_owners,
                protected_context_id=protected_context_id,
                bypass_owner_context_ids=bypass_owner_context_ids,
                host_available_bytes=host_available_bytes,
            )
            if preview is None:
                continue
            handles = preview.bundle.handles
            if handles in seen_closures:
                continue
            seen_closures.add(handles)
            previews.append(preview)
        return previews

    def preview_offload_root(
        self,
        command_kind: CommandKind,
        context_id: str,
        context_epoch: int,
        root_handle: PageHandle,
        *,
        now_ms: float,
        allow_ready_owners: bool = False,
        protected_context_id: str | None = None,
        bypass_owner_context_ids: frozenset[str] = frozenset(),
        host_available_bytes: int | None = None,
    ) -> PhysicalBundlePreview | None:
        """Build one closure-complete D2H preview from an indexed root."""

        if command_kind not in {
            CommandKind.OFFLOAD_CONTEXT,
            CommandKind.SHADOW_CONTEXT,
        }:
            return None
        context = self.graph.contexts.get(context_id)
        root = self.page_index.pages.get(root_handle)
        if (
            context is None
            or context.epoch != context_epoch
            or root is None
            or not root.gpu_resident
            or context_id not in root.owner_contexts
        ):
            return None
        closure, closure_blockers = self._gpu_descendant_closure(root)
        if not closure:
            return None
        blockers = list(closure_blockers)
        actions: list[ResolvedPageAction] = []
        blocked_handles: set[PageHandle] = {
            item.page_handle
            for item in blockers
            if item.page_handle is not None
        }
        ancestor_failures: dict[PageHandle, str | None] = {}

        def ancestor_failure(page: PhysicalPageRecord) -> str | None:
            trail = [page.handle]
            seen = {page.handle}
            ancestor = page.parent
            failure: str | None = None
            while ancestor is not None:
                if ancestor in ancestor_failures:
                    failure = ancestor_failures[ancestor]
                    break
                if ancestor in seen:
                    failure = "D2H target has an ancestor cycle"
                    break
                seen.add(ancestor)
                parent = self.page_index.pages.get(ancestor)
                if parent is None or parent.residency == PhysicalResidency.DEAD:
                    failure = "D2H closure has a missing ancestor"
                    break
                if not parent.gpu_resident:
                    failure = "D2H target has a non-resident ancestor"
                    break
                trail.append(parent.handle)
                ancestor = parent.parent
            for handle in trail:
                ancestor_failures[handle] = failure
            return failure

        for page in sorted(
            closure.values(),
            key=lambda item: (-item.radix_depth, item.handle),
        ):
            if page.residency == PhysicalResidency.DUAL_CLEAN:
                if command_kind == CommandKind.OFFLOAD_CONTEXT:
                    actions.append(
                        ResolvedPageAction(
                            page.handle,
                            PhysicalPageAction.COMMIT_CPU,
                            page.size_bytes,
                        )
                    )
            elif page.residency == PhysicalResidency.GPU_ONLY:
                actions.append(
                    ResolvedPageAction(
                        page.handle,
                        PhysicalPageAction.START_D2H,
                        page.size_bytes,
                    )
                )
            page_blockers = self._page_blockers(page)
            page_blockers += self._owner_blockers(
                page,
                now_ms=now_ms,
                allow_ready_owners=allow_ready_owners,
                protected_context_id=protected_context_id,
                bypass_owner_context_ids=bypass_owner_context_ids,
            )
            blockers.extend(page_blockers)
            if page_blockers:
                blocked_handles.add(page.handle)
                continue
            failure = ancestor_failure(page)
            if failure is not None:
                blockers.append(
                    TransferBlocker(
                        TransferBlockerCode.ANCESTOR_CLOSURE,
                        page.handle,
                        page.size_bytes,
                        failure,
                    )
                )
                blocked_handles.add(page.handle)
        copy_bytes = sum(
            item.size_bytes
            for item in actions
            if item.action == PhysicalPageAction.START_D2H
        )
        if host_available_bytes is not None and copy_bytes > host_available_bytes:
            blockers.append(
                TransferBlocker(
                    TransferBlockerCode.HOST_CAPACITY,
                    root.handle,
                    copy_bytes,
                    "D2H bundle exceeds current host availability",
                )
            )
        return self._preview(
            command_kind,
            context_id,
            context_epoch,
            closure,
            tuple(actions),
            self._deduplicate_blockers(blockers),
            blocked_handles,
            now_ms=now_ms,
        )

    def _drop_previews(
        self,
        context_id: str,
        context_epoch: int,
        *,
        now_ms: float,
        allow_ready_owners: bool,
        protected_context_id: str | None,
        bypass_owner_context_ids: frozenset[str],
    ) -> list[PhysicalBundlePreview]:
        roots = [
            page
            for page in self.page_index.context_pages(context_id)
            if page.gpu_resident
        ]
        previews: list[PhysicalBundlePreview] = []
        seen_closures: set[tuple[PageHandle, ...]] = set()
        for root in sorted(roots, key=lambda page: (-page.radix_depth, page.handle)):
            closure, closure_blockers = self._gpu_descendant_closure(root)
            handles = tuple(sorted(closure))
            if not handles or handles in seen_closures:
                continue
            seen_closures.add(handles)
            blockers = list(closure_blockers)
            actions: list[ResolvedPageAction] = []
            blocked_handles: set[PageHandle] = set()
            for page in sorted(
                closure.values(), key=lambda item: (-item.radix_depth, item.handle)
            ):
                actions.append(
                    ResolvedPageAction(
                        page.handle,
                        PhysicalPageAction.DROP,
                        page.size_bytes,
                    )
                )
                page_blockers = self._page_blockers(page)
                page_blockers += self._owner_blockers(
                    page,
                    now_ms=now_ms,
                    allow_ready_owners=allow_ready_owners,
                    protected_context_id=protected_context_id,
                    bypass_owner_context_ids=bypass_owner_context_ids,
                )
                blockers.extend(page_blockers)
                if page_blockers:
                    blocked_handles.add(page.handle)
            blockers_tuple = self._deduplicate_blockers(blockers)
            previews.append(
                self._preview(
                    CommandKind.DROP_CONTEXT,
                    context_id,
                    context_epoch,
                    closure,
                    tuple(actions),
                    blockers_tuple,
                    blocked_handles,
                    now_ms=now_ms,
                )
            )
        return previews

    def _prefetch_previews(
        self,
        context_id: str,
        context_epoch: int,
        *,
        now_ms: float,
        device_available_bytes: int | None,
    ) -> list[PhysicalBundlePreview]:
        targets = [
            page
            for page in self.page_index.context_pages(context_id)
            if page.residency == PhysicalResidency.CPU_ONLY
        ]
        previews: list[PhysicalBundlePreview] = []
        seen_closures: set[tuple[PageHandle, ...]] = set()
        for target in sorted(targets, key=lambda page: (-page.radix_depth, page.handle)):
            closure: dict[PageHandle, PhysicalPageRecord] = {}
            blockers: list[TransferBlocker] = []
            node: PhysicalPageRecord | None = target
            seen: set[PageHandle] = set()
            while node is not None and not node.gpu_resident:
                if node.handle in seen:
                    blockers.append(
                        TransferBlocker(
                            TransferBlockerCode.EXTENT_MUTATED,
                            node.handle,
                            node.size_bytes,
                            "Radix ancestor cycle",
                        )
                    )
                    break
                seen.add(node.handle)
                closure[node.handle] = node
                if node.parent is None:
                    break
                parent = self.page_index.pages.get(node.parent)
                if parent is None or parent.residency == PhysicalResidency.DEAD:
                    blockers.append(
                        TransferBlocker(
                            TransferBlockerCode.ANCESTOR_CLOSURE,
                            node.handle,
                            node.size_bytes,
                            "H2D closure has a missing ancestor",
                        )
                    )
                    break
                node = parent
            if node is not None and node.gpu_resident:
                closure[node.handle] = node
            handles = tuple(sorted(closure))
            if not handles or handles in seen_closures:
                continue
            seen_closures.add(handles)
            actions: list[ResolvedPageAction] = []
            blocked_handles: set[PageHandle] = set()
            for page in sorted(
                closure.values(), key=lambda item: (item.radix_depth, item.handle)
            ):
                if page.gpu_resident:
                    continue
                if page.residency == PhysicalResidency.CPU_ONLY:
                    actions.append(
                        ResolvedPageAction(
                            page.handle,
                            PhysicalPageAction.START_H2D,
                            page.size_bytes,
                        )
                    )
                page_blockers = self._page_blockers(page)
                blockers.extend(page_blockers)
                if page_blockers:
                    blocked_handles.add(page.handle)
                    continue
                if page.residency != PhysicalResidency.CPU_ONLY:
                    blocker = TransferBlocker(
                        TransferBlockerCode.NODE_LOADING,
                        page.handle,
                        page.size_bytes,
                        "H2D closure extent is not CPU_ONLY",
                    )
                    blockers.append(blocker)
                    blocked_handles.add(page.handle)
                    continue
            h2d_bytes = sum(item.size_bytes for item in actions)
            if (
                device_available_bytes is not None
                and h2d_bytes > device_available_bytes
            ):
                blockers.append(
                    TransferBlocker(
                        TransferBlockerCode.DEVICE_CAPACITY,
                        target.handle,
                        h2d_bytes,
                        "H2D bundle exceeds current device availability",
                    )
                )
            blockers_tuple = self._deduplicate_blockers(blockers)
            previews.append(
                self._preview(
                    CommandKind.PREFETCH_CONTEXT,
                    context_id,
                    context_epoch,
                    closure,
                    tuple(actions),
                    blockers_tuple,
                    blocked_handles,
                    now_ms=now_ms,
                )
            )
        return previews

    def _preview(
        self,
        command_kind: CommandKind,
        context_id: str,
        context_epoch: int,
        closure: dict[PageHandle, PhysicalPageRecord],
        actions: tuple[ResolvedPageAction, ...],
        blockers: tuple[TransferBlocker, ...],
        blocked_handles: set[PageHandle],
        *,
        now_ms: float,
    ) -> PhysicalBundlePreview:
        handles = tuple(sorted(closure))
        bundle_id = self._bundle_id(command_kind, handles)
        owner_context_ids = tuple(
            sorted(
                {
                    owner
                    for page in closure.values()
                    for owner in page.owner_contexts
                }
            )
        )
        (
            scope,
            exclusive_action_bytes,
            cross_context_action_bytes,
            foreign_owner_context_ids,
        ) = self._action_scope(context_id, closure, actions)
        lease = self.leases.bundle(bundle_id, owner_context_ids, now_ms=now_ms)
        closure_bytes = sum(item.size_bytes for item in actions)
        reclaimable = (
            closure_bytes
            if command_kind
            in {CommandKind.OFFLOAD_CONTEXT, CommandKind.DROP_CONTEXT}
            and not blockers
            else 0
        )
        residencies = sorted({page.residency.value for page in closure.values()})
        bundle = PhysicalBundle(
            bundle_id=bundle_id,
            handles=handles,
            owner_context_ids=owner_context_ids,
            scope=scope,
            exclusive_action_bytes=exclusive_action_bytes,
            cross_context_action_bytes=cross_context_action_bytes,
            foreign_owner_context_ids=foreign_owner_context_ids,
            physical_unique_bytes=sum(
                page.size_bytes for page in closure.values()
            ),
            gpu_bytes=sum(page.size_bytes for page in closure.values() if page.gpu_resident),
            cpu_bytes=sum(page.size_bytes for page in closure.values() if page.cpu_resident),
            marginal_reclaimable_bytes=reclaimable,
            closure_bytes=closure_bytes,
            locked_bytes=sum(
                closure[handle].size_bytes
                for handle in blocked_handles
                if handle in closure
            ),
            residency=residencies[0] if len(residencies) == 1 else "mixed",
            generation_fingerprint=self._fingerprint(
                command_kind,
                closure,
                lease,
                blocker_scope=(
                    set(closure)
                    if command_kind
                    in {
                        CommandKind.OFFLOAD_CONTEXT,
                        CommandKind.DROP_CONTEXT,
                        CommandKind.SHADOW_CONTEXT,
                    }
                    else {
                        handle
                        for handle, page in closure.items()
                        if not page.gpu_resident
                    }
                ),
            ),
            lease=lease,
        )
        return PhysicalBundlePreview(
            command_kind=command_kind,
            context_id=context_id,
            context_epoch=context_epoch,
            bundle=bundle,
            page_actions=actions,
            blockers=blockers,
            copy_bytes=sum(
                item.size_bytes
                for item in actions
                if item.action
                in {PhysicalPageAction.START_D2H, PhysicalPageAction.START_H2D}
            ),
        )

    @staticmethod
    def _action_scope(
        context_id: str,
        closure: dict[PageHandle, PhysicalPageRecord],
        actions: tuple[ResolvedPageAction, ...],
    ) -> tuple[BundleScope, int, int, tuple[str, ...]]:
        exclusive_bytes = 0
        cross_context_bytes = 0
        foreign_owners: set[str] = set()
        for action in actions:
            page = closure[action.handle]
            page_foreign_owners = set(page.owner_contexts) - {context_id}
            if page_foreign_owners:
                cross_context_bytes += action.size_bytes
                foreign_owners.update(page_foreign_owners)
            else:
                exclusive_bytes += action.size_bytes
        scope = (
            BundleScope.SHARED_SUBTREE
            if foreign_owners
            else BundleScope.EXCLUSIVE_SUFFIX
        )
        return (
            scope,
            exclusive_bytes,
            cross_context_bytes,
            tuple(sorted(foreign_owners)),
        )

    def _gpu_descendant_closure(
        self, root: PhysicalPageRecord
    ) -> tuple[dict[PageHandle, PhysicalPageRecord], list[TransferBlocker]]:
        closure: dict[PageHandle, PhysicalPageRecord] = {}
        blockers: list[TransferBlocker] = []
        stack = [root.handle]
        while stack:
            handle = stack.pop()
            if handle in closure:
                blockers.append(
                    TransferBlocker(
                        TransferBlockerCode.EXTENT_MUTATED,
                        handle,
                        0,
                        "Radix descendant cycle",
                    )
                )
                continue
            page = self.page_index.pages.get(handle)
            if page is None or page.residency == PhysicalResidency.DEAD:
                blockers.append(
                    TransferBlocker(
                        TransferBlockerCode.DESCENDANT_CLOSURE,
                        handle,
                        0,
                        "Radix descendant is missing",
                    )
                )
                continue
            if not page.gpu_resident:
                continue
            closure[handle] = page
            stack.extend(sorted(page.children, reverse=True))
        return closure, blockers

    def _owner_blockers(
        self,
        page: PhysicalPageRecord,
        *,
        now_ms: float,
        allow_ready_owners: bool,
        protected_context_id: str | None,
        bypass_owner_context_ids: frozenset[str],
    ) -> list[TransferBlocker]:
        blockers: list[TransferBlocker] = []
        for context_id in sorted(page.owner_contexts):
            if context_id in bypass_owner_context_ids:
                continue
            lease = self.leases.context(context_id, now_ms=now_ms)
            blocked = (
                context_id == protected_context_id
                or lease.kind == LeaseKind.RUNNING
                or (lease.kind == LeaseKind.READY and not allow_ready_owners)
            )
            if blocked:
                blockers.append(
                    TransferBlocker(
                        TransferBlockerCode.ENGINE_BUSY,
                        page.handle,
                        page.size_bytes,
                        f"owner {context_id} has {lease.kind.value} lease",
                    )
                )
        return blockers

    @staticmethod
    def _page_blockers(page: PhysicalPageRecord) -> list[TransferBlocker]:
        blockers: list[TransferBlocker] = []
        if not page.sealed:
            blockers.append(
                TransferBlocker(
                    TransferBlockerCode.UNSEALED,
                    page.handle,
                    page.size_bytes,
                    "page extent is not sealed",
                )
            )
        if page.engine_lock_ref > 0 or page.active_reader_count > 0:
            blockers.append(
                TransferBlocker(
                    TransferBlockerCode.NODE_LOCKED,
                    page.handle,
                    page.size_bytes,
                    "page has an engine lock or active reader",
                )
            )
        if page.semantic_pin_contexts:
            blockers.append(
                TransferBlocker(
                    TransferBlockerCode.SEMANTIC_PIN,
                    page.handle,
                    page.size_bytes,
                    "page is semantically pinned",
                )
            )
        if not page.transfer_idle:
            blockers.append(
                TransferBlocker(
                    TransferBlockerCode.NODE_LOADING
                    if page.residency == PhysicalResidency.PREFETCHING
                    else TransferBlockerCode.INFLIGHT,
                    page.handle,
                    page.size_bytes,
                    "page has an in-flight transfer",
                )
            )
        return blockers

    @staticmethod
    def _bundle_id(
        command_kind: CommandKind, handles: tuple[PageHandle, ...]
    ) -> str:
        payload = repr(
            (
                command_kind.value,
                tuple(
                    (item.page_id, item.allocation_generation) for item in handles
                ),
            )
        ).encode("utf-8")
        return f"bundle-{blake2b(payload, digest_size=10).hexdigest()}"

    @staticmethod
    def _fingerprint(
        command_kind: CommandKind,
        closure: dict[PageHandle, PhysicalPageRecord],
        lease: BundleLease,
        blocker_scope: set[PageHandle],
    ) -> str:
        state = []
        for handle in sorted(closure):
            page = closure[handle]
            state.append(
                (
                    handle.page_id,
                    handle.allocation_generation,
                    page.size_bytes,
                    page.residency.value,
                    (
                        (page.parent.page_id, page.parent.allocation_generation)
                        if page.parent is not None
                        else None
                    ),
                    tuple(
                        (child.page_id, child.allocation_generation)
                        for child in sorted(page.children)
                    ),
                    tuple(sorted(page.owner_contexts.items())),
                    page.engine_lock_ref if handle in blocker_scope else None,
                    page.active_reader_count if handle in blocker_scope else None,
                    (
                        tuple(sorted(page.semantic_pin_contexts))
                        if handle in blocker_scope
                        else None
                    ),
                    page.sealed if handle in blocker_scope else None,
                    (
                        page.transfer_direction.value
                        if handle in blocker_scope
                        and page.transfer_direction is not None
                        else None
                    ),
                )
            )
        payload = repr(
            (
                command_kind.value,
                tuple(state),
                lease.strongest_kind.value,
                lease.owner_context_ids,
                tuple(lease.conditions),
            )
        ).encode("utf-8")
        return blake2b(payload, digest_size=16).hexdigest()

    @staticmethod
    def _deduplicate_blockers(
        blockers: list[TransferBlocker],
    ) -> tuple[TransferBlocker, ...]:
        unique = {
            (item.code, item.page_handle, item.required_bytes, item.detail): item
            for item in blockers
        }
        return tuple(
            unique[key]
            for key in sorted(
                unique,
                key=lambda item: (
                    item[0].value,
                    item[1] or PageHandle(0, 0),
                    item[2],
                    item[3],
                ),
            )
        )
