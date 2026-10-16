# Generation Acceleration Primary Sources

Fetched on 2026-10-16. `SHA256SUMS` binds unmodified arXiv HTML sources.
These papers constrain candidate novelty; their GPU results are not our results.

- AgentSpec, arXiv:2608.24004v1, sections 3.2-3.4:
  `https://arxiv.org/html/2608.24004v1`.
  Covers a shared n-gram corpus and batch-budgeted agent speculation, including
  prompt and tool-boundary reuse signals.
- ReplaySSM, arXiv:2608.20961v1, abstract and sections 3-4:
  `https://arxiv.org/html/2608.20961v1`.
  Covers local compact state replay for Mamba-2/GDN and speculative rollback.
  The paper's kernel measurements do not establish workflow JCT improvement.

The local frozen engine already has NGRAM corpus, Mamba verify-commit and
ReplaySSM entry points. BeliefKV admission explicitly rejects speculation.
No checks were removed and no speculative GPU experiment was registered.
