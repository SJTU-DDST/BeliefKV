"""Private output forcing through SGLang's custom logit processor interface."""

from __future__ import annotations

import json


PARAMETER_KEY = "beliefkv_causal_replay"


class CapturedOutputLogitsProcessor:
    """Choose recorded tokens using current Req progress, with native stopping."""

    @classmethod
    def to_str(cls) -> str:
        import dill

        return json.dumps({"callable": dill.dumps(cls).hex()})

    def __call__(self, logits, custom_param_list=None):
        import torch

        if (
            logits.ndim != 2 or not logits.is_floating_point()
            or type(custom_param_list) is not list
            or len(custom_param_list) != logits.shape[0]
        ):
            raise ValueError("replay requires one parameter entry per logits row")
        targets, seen = [], set()
        for parameters in custom_param_list:
            replay = parameters.get(PARAMETER_KEY) if type(parameters) is dict else None
            req = parameters.get("__req__") if type(parameters) is dict else None
            request = replay.get("request_id") if type(replay) is dict else None
            if (
                type(request) is not str or not request or request in seen
                or req is None or getattr(req, "rid", None) != request
            ):
                raise ValueError("missing, duplicate or mismatched native replay request")
            seen.add(request)
            tokens, output = replay.get("output_token_ids"), req.output_ids
            if not isinstance(tokens, (list, tuple)) or not 0 <= len(output) < len(tokens):
                raise ValueError("native request failed to stop at its recorded boundary")
            position = len(output)
            if position and output[-1] != tokens[position - 1]:
                raise ValueError("native output diverged from the captured path")
            target = tokens[position]
            if type(target) is not int or not 0 <= target < logits.shape[1]:
                raise ValueError("recorded next token is outside the native vocabulary")
            targets.append(target)
        if targets:
            indices = torch.tensor(targets, dtype=torch.int64, device=logits.device)
            logits.fill_(-float("inf"))
            logits.scatter_(1, indices[:, None], 0.)
        return logits
