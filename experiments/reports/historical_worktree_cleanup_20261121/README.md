# September/October Worktree Retirement

This maintenance batch supplements `storage_cleanup_20261121`. It includes the
separate SGLang worktree registry and distinguishes historical narrative
references from current executable, configuration, process and input dependencies.

The initial selection contains 261 worktrees: 12 BeliefKV copies and 249 SGLang
copies, occupying 53,990,653,952 allocated bytes including their Git administration.
There are 83 protected old worktrees. Their exact reasons remain in `inventory.json`.

All 261 selected copies have been retired and verified. Full retained archives use
16,573,149,184 allocated bytes; estimated net recovery is 37,417,504,768 bytes.
Nine restoration rehearsals and one original-path restore recipe passed. All 83
protected old directories remain present.

`retire.py` inventories and archives complete source directories, including frozen
engine patches, untracked files, ignored files and symbolic links. It also archives
the original Git administration, index and reflog. It compares each complete tar
against its source before removal, repeats process/input/content checks, and
rehearses restoration for each repository/state category. Application checkouts
with local modifications remain in place.

`preserve_index.py` fixes an additional recovery requirement before execution:
the original index tree, staged objects and resolve-undo objects must remain
reachable after Git garbage collection. It uses a temporary copy of each index;
the original index and original branches remain unchanged. HEAD and HEAD reflog
commits are separately pinned by the full-archive operator.

`continue_verified.py` validates a cached path resolver against the original
protection set and file count. `parallel_retirement.py` finishes disjoint portions
of the unchanged selection in three batches, preserving both administrative
interruption checkpoints. Each archive still uses one zstd thread.

Removal follows verified full archival. The operator removes only the selected
expanded copy and unregisters that specific absent path using ordinary
`git worktree remove`. It does not force Git removal, reset user changes or prune
unrelated registrations.

The detailed inventory, receipts and object pins remain local and untracked:

- `inventory.json`: immutable selection, source signatures and protected paths.
- `index_object_pins.json`: durable references for staged/undo objects.
- `receipt.json`, `continuation_receipt.json`: original maintenance checkpoints.
- `parallel_plan.json`, `parallel_receipts/`: disjoint final batches and bindings.
- `complete_receipt.json`: assembled per-copy hashes, comparisons and restore recipes.
- `review.json`: compact archive/history verification.
- `index_review.json`: compact staged/undo object verification.
- `original_path_restoration_review.json`: actual recovery of a retired dirty
  engine at its original source and Git-admin paths, followed by verified removal.
- `final_storage_review.json`: before/after disk observations and evidence hashes.

The [archive recovery index](../../archive/20261121_historical_worktrees/README.md)
contains a complete recipe for each removed worktree. Recipes restore both the
expanded source and its original Git administration; use them only when both
original destinations are absent.

All maintenance runs at nice 19 with idle I/O and single-threaded zstd compression.
The active GPU campaign, its inputs and outputs remain outside this selection.
