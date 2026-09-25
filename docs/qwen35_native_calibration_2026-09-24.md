# Qwen3.5 native-reactive calibration collection (2026-09-24)

- Previous source/runtime changes were committed as `6688a56` and `6912567`.
- Refit train collection: frozen 128-root train plan, 64+64 roots at 0/60 s,
  48 running slots, H200, BF16, 180 GB NUMA-local Host pool with FULL/Mamba
  70:30, `mem_fraction_static=0.94`, 2048 graph steps, 8192 completion tokens.
  Dedicated output: `experiments/raw/qwen35_native_reactive_refit_20260924_v1`.
- Calibration collection: **all 66 tasks** in the frozen
  `swebench_verified_split_v1.json` calibration projects
  `astropy/astropy` (22) and `sphinx-doc/sphinx` (44), with original
  base commits and SWE-bench instance images. The fixed workload and plan
  are in `configs/migration/qwen35_native_reactive_calibration_66root_*_2026-09-24.json`.
  Selection does not depend on outcomes or previous model predictions.
  Two 33-root waves at 0/60 s, 48 server running slots; all other service
  settings match train. This is a lower-pressure calibration condition than
  the 128-root train run; report pressure-sensitive coverage and errors
  separately instead of claiming matched high-pressure generalization.
- Calibration only starts after the new train run has exported valid
  split-isolated telemetry and fitted an uncalibrated checkpoint. Its
  datasets remain in a separate output directory; neither the training
  CLI nor the model hyperparameter search may consume them. Test projects
  remain sealed.
- Collection and dataset export alone do **not** establish a calibrated
  predictive-action artifact. Before promotion, validate actual PCIe
  labels, JOIN timing and interval coverage, build action targets and a
  passing native calibration coverage report, and check fitted and held-out
  runtime-environment digests agree. Insufficient target coverage must be
  reported rather than presented as a successful calibration.

## September 25 result and recovery

The 128-root refit collection finished with 127 completed workflows; all 128
traces passed the raw trace gate. The frozen training export was locally
eligible (123 JOIN labels, 32,813 eligible decode-demand requests), but its
PCIe label count was zero: the native evidence gate overrode measured transfer
eligibility with `False`. A separate, non-destructive export from the same
raw telemetry now preserves completed native transfer-stream intervals only
when the telemetry writer is healthy. The collection shell also terminated
after a successful export, printing an inconsistent `export=0` rejection;
its script was edited while the process was running, so that shell exit
cannot establish a dataset defect. It did not produce a fitted checkpoint.
The recovery sequence is: re-export
to `dataset_native_transfer_v2`, check train provenance and PCIe labels,
fit the model on that dataset alone, run the frozen 66-root calibration
collection, then calibrate available prediction heads only if the held-out
labels pass. No GPU re-collection is needed for the train split.

The optional `--native-heads-only` calibration output is explicitly **not**
action-calibrated or online-eligible. Action thresholds and predictive
transfer service calibration still require separately proven action/service
targets and a native coverage audit; absence of these targets must not be
converted into a full model promotion.
