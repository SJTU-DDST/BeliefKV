# Historical Worktree Retirement

Full frozen checkouts duplicate Git-tracked historical reports and figures.
Retirement preserves their commits rather than compressing another complete
copy of those same tracked files.

Completed: 342 worktrees retired, 201.765 GB source allocation, 0.450 GB
archived extra data, and approximately 201.316 GB reclaimed. Both independent
reviews passed, including a complete checkout reconstruction and extraction
of actual ignored files and symbolic links into a fresh checkout.

`final_review.json` and `linked_final_review.json` record independently checked
original paths, commits, trees and exact restoration commands. The detailed
inventories, progress receipts, administrative interruption checkpoints and
worker records remain locally in this directory. Their SHA256 values are
included in the review and receipt chain.

Ignored files and original Git administration are stored under
`experiments/archive/20261101_retired_worktrees/` and
`experiments/archive/20261101_retired_linked_worktrees/`. Each archive root has
an index with per-worktree restoration commands. Recreate a checkout using its
pinned `refs/archive/20261101-worktrees/` reference, then extract its ignored
files. Retain the original administration archive as evidence; Git creates
fresh administration during restoration.

The selection excludes recent, referenced, active, mounted, locked, dirty,
branch-attached and custom-configured worktrees. Symbolic links are archived
as links; their external SGLang targets are retained. Every selected directory
passes complete tar comparison, native Git safety checks and fresh reference
and process checks before ordinary, non-forced `git worktree remove`.

Execution uses four disjoint low-priority CPU batches, each with single-thread
compression. The faster GNU find enumeration is checked against the original
file population. A racing Docker container removal caused one bounded mount
snapshot retry; its original failure remains in the receipt chain. This
maintenance starts no GPU work and changes no experiment policy.
