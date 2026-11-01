# Home Storage Cleanup, 2026-11-01

Inactive task workspaces retain every original Git pack/index path and payload.
Verified duplicate immutable objects share read-only inodes with canonical archive objects.
Original per-copy metadata and file modes are recorded in the frozen inventory.
Native git verify-pack and every retained workspace HEAD read passed independent review.

Deduplicated redundant pack/index pairs: 598.
Verified object allocation reclaimed: 102.038 GB.
Archived old service logs: 40.
Log allocation reclaimed: 2.739 GB.

## Evidence

Selection and original metadata: `/home/longhao/experiment/BeliefKV/experiments/reports/home_storage_cleanup_20261101/inventory.json`
Execution receipt: `/home/longhao/experiment/BeliefKV/experiments/reports/home_storage_cleanup_20261101/receipt.json`
Independent verification: `/home/longhao/experiment/BeliefKV/experiments/reports/home_storage_cleanup_20261101/final_review.json`

## Shared Git Objects

| Git object pack | Preserved copies | Reclaimed GB |
| --- | ---: | ---: |
| `pack-2c0dd49741723972374afeff759d388c4a26ccc5.pack` | 125 | 7.111 |
| `pack-3974a816ef87e7cf6ece39d496a567ee50898b69.pack` | 10 | 0.510 |
| `pack-5a724d36f986f066a7076026cf9b86e930766929.pack` | 54 | 2.395 |
| `pack-82b7e07112179ca056b7b28565b827306af8135d.pack` | 130 | 5.950 |
| `pack-eb6ab04d27187e12fe899ca7dde198fe727b834f.pack` | 284 | 86.073 |

## Restore Archived Logs

### 20260920_v58_pair

Archive SHA256: `842c97aebaa4400cc10b4268abdaccb2d1096a59977e2f768e00833ac79d8d71`

```bash
tar --extract '--use-compress-program=zstd -T1' --file /home/longhao/experiment/BeliefKV/experiments/archive/20261101_home_storage/old_service_logs_1.tar.zst --directory /home/longhao/experiment/BeliefKV
```

### 20260921_v59_predictive

Archive SHA256: `473d6ac8f415a797c8bbcb2165d8991d7d32abae79243dbc2f955d716d2b542c`

```bash
tar --extract '--use-compress-program=zstd -T1' --file /home/longhao/experiment/BeliefKV/experiments/archive/20261101_home_storage/old_service_logs_2.tar.zst --directory /home/longhao/experiment/BeliefKV
```

### 20260921_v60_predictive_retry1

Archive SHA256: `b711672a0a0bebcc93598ed406f9fc2fc667be0876a412ed2c0aecf06d2f5e81`

```bash
tar --extract '--use-compress-program=zstd -T1' --file /home/longhao/experiment/BeliefKV/experiments/archive/20261101_home_storage/old_service_logs_3.tar.zst --directory /home/longhao/experiment/BeliefKV
```

### p6_predictive_wait_shadow_v58

Archive SHA256: `89c8e09628a5f0749a0f80822c0f2afcf64b49b9adeb994f8190df66ada62e22`

```bash
tar --extract '--use-compress-program=zstd -T1' --file /home/longhao/experiment/BeliefKV/experiments/archive/20261101_home_storage/old_service_logs_4.tar.zst --directory /home/longhao/experiment/BeliefKV
```
