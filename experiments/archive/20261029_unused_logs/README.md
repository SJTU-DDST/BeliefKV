# Superseded Experiment Logs, 2026-10-29

Verified 151 log files from 14 old Qwen3.5 runs.
Complete tar byte comparisons passed before raw copies were removed.
Plans, summaries, workflow events, request token bundles and workspaces remain.

Receipt: `/home/longhao/experiment/BeliefKV/experiments/reports/unused_archive_20261029/receipt.json`
Independent verification: `/home/longhao/experiment/BeliefKV/experiments/reports/unused_archive_20261029/final_review.json`

| Run | Source GB | Archive GB | SHA256 |
| --- | ---: | ---: | --- |
| qwen35_native_predictive_replenished_108plus48_20261014_v56 | 10.757079 | 0.315582 | `bc8e48021cbf3c8dc226c1d6aceda36f8878783184e2ced72ff5f4eb058ee181` |
| qwen35_native_predictive_replenished_108plus48_20261014_v57 | 9.783665 | 0.277268 | `c22110a1926db5c32a265a51905c7eba1864372b0b30c06ca1215f6518167232` |
| qwen35_native_predictive_replenished_108plus48_20261014_v59 | 10.277163 | 0.290127 | `1efcc07a3815c77d7bdca7e4267f250e86eea737f6c0c2f05a3f97914e007e58` |
| qwen35_native_predictive_replenished_108plus48_20261015_v61 | 8.324714 | 0.228957 | `f7aa254e8e16c8957c2ab9898fd6ad3a7da6810192ff93d1ca38bf6e8140c3da` |
| qwen35_native_predictive_replenished_108plus48_20261015_v62 | 9.216995 | 0.252091 | `a7d3d1d75c6dd01f7e144b8ef56b9183cc418234843bd6c5382224bfd05d4b45` |
| qwen35_native_predictive_replenished_108plus48_20261015_v63_retry1 | 9.285517 | 0.268391 | `82572f034bedfb03d1524ae7f689820d758a1de4736a0c6eaaf618663be3ba2b` |
| qwen35_native_predictive_replenished_108plus48_20261015_v68_partial_restore | 9.285980 | 0.256642 | `8d2d8994efb2c9ac99cea71de46da14300ffdee28360ce36de073f8cac20532d` |
| qwen35_native_predictive_replenished_108plus48_20261015_v70_terminal_root | 8.392782 | 0.236479 | `b723191eea1614d3761677cf39c7b7201eeeb6b29dd4698a76b6aafeefaeef67` |
| qwen35_native_predictive_replenished_108plus48_20261015_v71_aged_head | 2.402001 | 0.080838 | `7f912fe24555119f65f2b154932ebf4798bd6a6647942c148b303ab36a6fac88` |
| qwen35_native_predictive_online_poisson_156root_20261016_v73 | 17.250890 | 0.437003 | `26e05e2e281dac96a173ffaa4b417fb15e634f32038bfc752d39435b758ca123` |
| qwen35_native_predictive_online_poisson_156root_20261016_v75_batched_writeback | 16.438874 | 0.405980 | `c05773bc3bc72056d92e522d112e2efcd3f596ded3f2144a55c6abedfb4bba68` |
| qwen35_native_reactive_online_poisson_156root_20261016_v76_no_prefetch | 9.263194 | 0.259490 | `10ce4bbe2a88dc05f13609e1be0ba80bcf850bcedc8f619f97f6e60bea067bdd` |
| qwen35_native_close_gc_online_poisson_156root_20261016_v77 | 7.811465 | 0.186945 | `d66668802e0353d81907fb1ad986b01cdeb40a2ef5a8ad6187b2486aff3c96d3` |
| qwen35_native_reactive_online_poisson_156root_20261016_v78b_native_order | 8.230257 | 0.209500 | `f1c696cf45830864f47ca56b69b4bec242b87fdb46c3ab751c8c1322c2bcd603` |

## Restore

Run the command for the needed experiment before rerunning its old raw-log audit.

### qwen35_native_predictive_replenished_108plus48_20261014_v56

```bash
tar --extract '--use-compress-program=zstd -T1' --file /home/longhao/experiment/BeliefKV/experiments/archive/20261029_unused_logs/qwen35_native_predictive_replenished_108plus48_20261014_v56.tar.zst --directory /home/longhao/experiment/BeliefKV/experiments/raw
```

### qwen35_native_predictive_replenished_108plus48_20261014_v57

```bash
tar --extract '--use-compress-program=zstd -T1' --file /home/longhao/experiment/BeliefKV/experiments/archive/20261029_unused_logs/qwen35_native_predictive_replenished_108plus48_20261014_v57.tar.zst --directory /home/longhao/experiment/BeliefKV/experiments/raw
```

### qwen35_native_predictive_replenished_108plus48_20261014_v59

```bash
tar --extract '--use-compress-program=zstd -T1' --file /home/longhao/experiment/BeliefKV/experiments/archive/20261029_unused_logs/qwen35_native_predictive_replenished_108plus48_20261014_v59.tar.zst --directory /home/longhao/experiment/BeliefKV/experiments/raw
```

### qwen35_native_predictive_replenished_108plus48_20261015_v61

```bash
tar --extract '--use-compress-program=zstd -T1' --file /home/longhao/experiment/BeliefKV/experiments/archive/20261029_unused_logs/qwen35_native_predictive_replenished_108plus48_20261015_v61.tar.zst --directory /home/longhao/experiment/BeliefKV/experiments/raw
```

### qwen35_native_predictive_replenished_108plus48_20261015_v62

```bash
tar --extract '--use-compress-program=zstd -T1' --file /home/longhao/experiment/BeliefKV/experiments/archive/20261029_unused_logs/qwen35_native_predictive_replenished_108plus48_20261015_v62.tar.zst --directory /home/longhao/experiment/BeliefKV/experiments/raw
```

### qwen35_native_predictive_replenished_108plus48_20261015_v63_retry1

```bash
tar --extract '--use-compress-program=zstd -T1' --file /home/longhao/experiment/BeliefKV/experiments/archive/20261029_unused_logs/qwen35_native_predictive_replenished_108plus48_20261015_v63_retry1.tar.zst --directory /home/longhao/experiment/BeliefKV/experiments/raw
```

### qwen35_native_predictive_replenished_108plus48_20261015_v68_partial_restore

```bash
tar --extract '--use-compress-program=zstd -T1' --file /home/longhao/experiment/BeliefKV/experiments/archive/20261029_unused_logs/qwen35_native_predictive_replenished_108plus48_20261015_v68_partial_restore.tar.zst --directory /home/longhao/experiment/BeliefKV/experiments/raw
```

### qwen35_native_predictive_replenished_108plus48_20261015_v70_terminal_root

```bash
tar --extract '--use-compress-program=zstd -T1' --file /home/longhao/experiment/BeliefKV/experiments/archive/20261029_unused_logs/qwen35_native_predictive_replenished_108plus48_20261015_v70_terminal_root.tar.zst --directory /home/longhao/experiment/BeliefKV/experiments/raw
```

### qwen35_native_predictive_replenished_108plus48_20261015_v71_aged_head

```bash
tar --extract '--use-compress-program=zstd -T1' --file /home/longhao/experiment/BeliefKV/experiments/archive/20261029_unused_logs/qwen35_native_predictive_replenished_108plus48_20261015_v71_aged_head.tar.zst --directory /home/longhao/experiment/BeliefKV/experiments/raw
```

### qwen35_native_predictive_online_poisson_156root_20261016_v73

```bash
tar --extract '--use-compress-program=zstd -T1' --file /home/longhao/experiment/BeliefKV/experiments/archive/20261029_unused_logs/qwen35_native_predictive_online_poisson_156root_20261016_v73.tar.zst --directory /home/longhao/experiment/BeliefKV/experiments/raw
```

### qwen35_native_predictive_online_poisson_156root_20261016_v75_batched_writeback

```bash
tar --extract '--use-compress-program=zstd -T1' --file /home/longhao/experiment/BeliefKV/experiments/archive/20261029_unused_logs/qwen35_native_predictive_online_poisson_156root_20261016_v75_batched_writeback.tar.zst --directory /home/longhao/experiment/BeliefKV/experiments/raw
```

### qwen35_native_reactive_online_poisson_156root_20261016_v76_no_prefetch

```bash
tar --extract '--use-compress-program=zstd -T1' --file /home/longhao/experiment/BeliefKV/experiments/archive/20261029_unused_logs/qwen35_native_reactive_online_poisson_156root_20261016_v76_no_prefetch.tar.zst --directory /home/longhao/experiment/BeliefKV/experiments/raw
```

### qwen35_native_close_gc_online_poisson_156root_20261016_v77

```bash
tar --extract '--use-compress-program=zstd -T1' --file /home/longhao/experiment/BeliefKV/experiments/archive/20261029_unused_logs/qwen35_native_close_gc_online_poisson_156root_20261016_v77.tar.zst --directory /home/longhao/experiment/BeliefKV/experiments/raw
```

### qwen35_native_reactive_online_poisson_156root_20261016_v78b_native_order

```bash
tar --extract '--use-compress-program=zstd -T1' --file /home/longhao/experiment/BeliefKV/experiments/archive/20261029_unused_logs/qwen35_native_reactive_online_poisson_156root_20261016_v78b_native_order.tar.zst --directory /home/longhao/experiment/BeliefKV/experiments/raw
```
