"""Private output forcing through SGLang's custom logit processor interface."""

from __future__ import annotations

import json


PARAMETER_KEY = "beliefkv_causal_replay"
POSITION_KEY = "_beliefkv_causal_output_positions"
SAMPLING_ENV = "BELIEFKV_CAUSAL_REPLAY_SAMPLING"


def _device_indices(values, device):
    import torch

    return torch.tensor(
        values, dtype=torch.int64, pin_memory=device.type == "cuda",
    ).to(device, non_blocking=True)


class CapturedOutputLogitsProcessor:
    """Choose recorded tokens using current Req progress, with native stopping."""

    @classmethod
    def to_str(cls) -> str:
        import dill

        return json.dumps({"callable": dill.dumps(cls).hex()})

    @staticmethod
    def targets(logits, custom_param_list, positions=None):
        if (
            logits.ndim != 2 or not logits.is_floating_point()
            or type(custom_param_list) is not list
            or len(custom_param_list) != logits.shape[0]
            or positions is not None and (
                type(positions) is not tuple or len(positions) != logits.shape[0]
            )
        ):
            raise ValueError("replay requires one parameter entry per logits row")
        targets, active_rows, seen = [], [], set()
        for row, parameters in enumerate(custom_param_list):
            replay = parameters.get(PARAMETER_KEY) if type(parameters) is dict else None
            req = parameters.get("__req__") if type(parameters) is dict else None
            request = replay.get("request_id") if type(replay) is dict else None
            if (
                type(request) is not str or not request or request in seen
                or req is None or getattr(req, "rid", None) != request
            ):
                raise ValueError("missing, duplicate or mismatched native replay request")
            seen.add(request)
            # Overlap can sample once more after the previous result ended Req.
            # Native discards that result; leave its original logits untouched.
            if getattr(req, "finished", lambda: False)():
                continue
            tokens, output = replay.get("output_token_ids"), req.output_ids
            if not isinstance(tokens, (list, tuple)) or not tokens:
                raise ValueError("native request failed to stop at its recorded boundary")
            position = len(output) if positions is None else positions[row]
            if (
                type(position) is not int or not len(output) <= position <= len(output) + 1
                or position > len(tokens)
                or positions is None and position == len(tokens)
            ):
                raise ValueError("native request failed to stop at its recorded boundary")
            if output and output[-1] != tokens[len(output) - 1]:
                raise ValueError("native output diverged from the captured path")
            # Native overlap can launch one forward past a pending terminal
            # result. Its sample is discarded; preserve that row's real logits.
            if position == len(tokens):
                continue
            target = tokens[position]
            if type(target) is not int or not 0 <= target < logits.shape[1]:
                raise ValueError("recorded next token is outside the native vocabulary")
            targets.append(target)
            active_rows.append(row)
        return targets, active_rows

    def __call__(self, logits, custom_param_list=None, *, positions=None):
        import torch

        targets, active_rows = self.targets(logits, custom_param_list, positions)
        if targets:
            indices = (
                torch.tensor(targets, dtype=torch.int64, device=logits.device)
                if positions is None else _device_indices(targets, logits.device)
            )
            if len(targets) == logits.shape[0]:
                logits.fill_(-float("inf"))
                logits.scatter_(1, indices[:, None], 0.)
            else:
                rows = (
                    torch.tensor(active_rows, dtype=torch.int64, device=logits.device)
                    if positions is None else _device_indices(active_rows, logits.device)
                )
                selected = logits.index_select(0, rows)
                selected.fill_(-float("inf"))
                selected.scatter_(1, indices[:, None], 0.)
                logits.index_copy_(0, rows, selected)
        return logits


def sample_with_positions(original, runner, logits_output, forward_batch):
    """Bind immutable CPU forward progress while the native sampler is called."""
    info = forward_batch.sampling_info
    parameters = info.custom_params
    if not parameters or not any(
        type(row) is dict and PARAMETER_KEY in row for row in parameters
    ):
        return original(runner, logits_output, forward_batch)
    lengths = forward_batch.seq_lens_cpu
    if (
        lengths is None or lengths.device.type != "cpu" or lengths.ndim != 1
        or len(lengths) != len(parameters) or hasattr(info, POSITION_KEY)
    ):
        raise ValueError("replay requires one unambiguous CPU length per forward row")
    positions = []
    for length, row in zip(lengths.tolist(), parameters, strict=True):
        if type(row) is dict and PARAMETER_KEY in row:
            req = row.get("__req__")
            if req is None:
                raise ValueError("replay forward row lacks its native request")
            position = length - len(req.origin_input_ids)
            if position < len(req.output_ids):
                # A retracted request can rebuild chunks before its retained
                # output. Native discards those intermediate prefill samples.
                if not forward_batch.forward_mode.is_extend():
                    raise ValueError("decode forward precedes its committed output")
                position = len(req.output_ids)
            positions.append(position)
        else:
            positions.append(None)
    setattr(info, POSITION_KEY, tuple(positions))
    try:
        result = original(runner, logits_output, forward_batch)
        selected = [
            index for index, row in enumerate(parameters)
            if type(row) is dict and type(row.get(PARAMETER_KEY)) is dict
            and row[PARAMETER_KEY].get("force_after_sample") is True
        ]
        if selected:
            import torch

            logits = logits_output.next_token_logits
            if (
                result.ndim != 1 or result.shape[0] != len(parameters)
                or result.dtype not in (torch.int32, torch.int64)
                or result.device != logits.device
                or getattr(forward_batch, "return_logprob", False)
            ):
                raise ValueError("post-sample replay requires native token-only sampling")
            # Keep native softmax/filter/sampling work on real model logits.
            # Replace only returned IDs, before the asynchronous CPU result copy.
            rows = [parameters[index] for index in selected]
            targets, active = CapturedOutputLogitsProcessor.targets(
                logits[:len(rows)],
                rows, tuple(positions[index] for index in selected),
            )
            if targets:
                values = _device_indices(targets, result.device).to(dtype=result.dtype)
                if len(targets) == len(parameters):
                    result.copy_(values)
                else:
                    indices = _device_indices([selected[index] for index in active], result.device)
                    result.index_copy_(0, indices, values)
            for row in rows:
                row["__req__"]._beliefkv_native_sampler_preserved = True
        return result
    finally:
        delattr(info, POSITION_KEY)


def filter_position_batch(original, info, keep_indices, keep_indices_device):
    """Filter a proven all-replay batch without reading a device mask scalar."""
    processors, parameters = info.custom_logit_processor, info.custom_params
    if (
        type(processors) is not dict or len(processors) != 1
        or type(parameters) is not list
    ):
        return original(info, keep_indices, keep_indices_device)
    processor_key, (processor, mask) = next(iter(processors.items()))
    if (
        type(processor) is not CapturedOutputLogitsProcessor
        or mask.ndim != 1 or len(parameters) != mask.shape[0]
    ):
        return original(info, keep_indices, keep_indices_device)
    for row in parameters:
        replay = row.get(PARAMETER_KEY) if type(row) is dict else None
        req = row.get("__req__") if type(row) is dict else None
        flag = getattr(req, "custom_logit_processor", None)
        if (
            type(replay) is not dict or type(replay.get("request_id")) is not str
            or not replay["request_id"] or getattr(req, "rid", None) != replay["request_id"]
            or type(flag) is not str or hash(flag) != processor_key
        ):
            return original(info, keep_indices, keep_indices_device)
    # Native construction and merge derive this mask from these same Req flags.
    # All rows belong to the one qualified processor, including after reordering.
    filtered = [parameters[index] for index in keep_indices]
    if filtered:
        info.custom_logit_processor = {
            processor_key: (processor, mask[keep_indices_device]),
        }
        info.custom_params = filtered
    else:
        info.custom_logit_processor = None
        info.custom_params = None
        info.has_custom_logit_processor = False


def apply_position_logits(original, logits, sampling_info, num_tokens_in_batch=1):
    """Use CPU row ownership instead of querying a device mask for replay."""
    positions = getattr(sampling_info, POSITION_KEY, None)
    if positions is None:
        return original(logits, sampling_info, num_tokens_in_batch)
    parameters = sampling_info.custom_params
    processors = tuple(sampling_info.custom_logit_processor.values())
    if (
        num_tokens_in_batch != 1 or len(processors) != 1
        or type(processors[0][0]) is not CapturedOutputLogitsProcessor
        or len(parameters) != logits.shape[0] or len(positions) != logits.shape[0]
    ):
        raise ValueError("position replay requires the qualified non-speculative processor")
    selected = [index for index, position in enumerate(positions) if position is not None]
    processor = processors[0][0]
    for index in selected:
        req = parameters[index]["__req__"]
        if not getattr(req, "_beliefkv_causal_position_sampled", False):
            req._beliefkv_causal_position_sampled = True
    selected = [
        index for index in selected
        if parameters[index][PARAMETER_KEY].get("force_after_sample") is not True
    ]
    if not selected:
        return
    if len(selected) == logits.shape[0]:
        processor(logits, parameters, positions=positions)
    else:
        rows = _device_indices(selected, logits.device)
        values = logits.index_select(0, rows)
        processor(
            values, [parameters[index] for index in selected],
            positions=tuple(positions[index] for index in selected),
        )
        logits.index_copy_(0, rows, values)
