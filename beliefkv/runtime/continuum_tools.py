"""Current-model output adaptation for Continuum's extensible tool handler."""

import json


class ContinuumTools:
    """Identify only completed, parsed Qwen3-Coder calls; never future metadata."""

    def __init__(self):
        from sglang.srt.function_call.qwen3_coder_detector import Qwen3CoderDetector

        self.detector = Qwen3CoderDetector()

    def identify(self, request: object) -> str | None:
        tokenizer = getattr(request, "tokenizer", None)
        output = getattr(request, "output_ids", ())
        if (
            not getattr(request, "finished", lambda: False)()
            or tokenizer is None or not output
        ):
            return None
        # Special-token delimiters are necessary for the model's native parser.
        text = tokenizer.decode(output, skip_special_tokens=False)
        parsed = self.detector.detect_and_parse(text, tools=None)
        if len(parsed.calls) != 1:
            return None
        call = parsed.calls[0]
        if not call.name:
            return None
        name = call.name.rsplit(".", 1)[-1]
        if name in ("shell", "bash", "execute", "run_shell"):
            parameters = json.loads(call.parameters)
            command = parameters.get("command")
            if type(command) is not str or not command.strip():
                return None
            # Match the public artifact's first-word command identity.
            return "shell:" + command.strip().split()[0]
        return "tool:" + call.name
