import os
from types import SimpleNamespace

import pytest

from beliefkv.runtime.chat_token_cache import ChatPromptTokenCache


class BoundaryTokenizer:
    def __init__(self, *, unsafe=False):
        self.calls = []
        self.backend_tokenizer = SimpleNamespace(
            post_processor=None,
            get_added_tokens_decoder=lambda: {999: SimpleNamespace(
                content="<|im_start|>", special=True, normalized=unsafe,
                single_word=False, lstrip=False, rstrip=False,
            )},
        )

    def convert_tokens_to_ids(self, text):
        return 999

    def encode(self, text, **kwargs):
        self.calls.append((text, kwargs))
        tokens = []
        for index, block in enumerate(text.split("<|im_start|>")):
            if index:
                tokens.append(999)
            tokens.extend(block.encode())
        return tokens


def test_growing_histories_reuse_static_blocks_and_preserve_exact_ids():
    tokenizer = BoundaryTokenizer()
    cache = ChatPromptTokenCache(tokenizer)
    first = "<|im_start|>system\nsource<|im_start|>user\nquestion"
    second = first + "<|im_start|>tool\nnew evidence"
    assert cache.encode(first) == tokenizer.encode(first)
    tokenizer.calls.clear()
    assert cache.encode(second) == BoundaryTokenizer().encode(second)
    assert [text for text, _ in tokenizer.calls] == ["tool\nnew evidence"]
    assert cache.hits == 2
    actual = cache.encode(second)
    actual[0] = -1
    assert cache.encode(second)[0] == 999


@pytest.mark.parametrize("text", ["", "<|im_start|>", "é", "<|im_start|><|im_start|>"])
def test_empty_unicode_and_adjacent_boundaries(text):
    tokenizer = BoundaryTokenizer()
    assert ChatPromptTokenCache(tokenizer).encode(text) == tokenizer.encode(text)


def test_memory_and_entry_budgets_evict_without_changing_tokens():
    tokenizer = BoundaryTokenizer()
    cache = ChatPromptTokenCache(tokenizer, max_bytes=1500, max_entries=2)
    for size in (10, 100, 2000, 20, 30):
        text = "<|im_start|>" + "x" * size
        assert cache.encode(text) == tokenizer.encode(text)
        assert cache.cached_bytes <= 1500
        assert len(cache._cache) <= 2


@pytest.mark.parametrize("processor_kind", ("none", "byte_level", "template", "sequence"))
def test_processor_validation_does_not_export_the_tokenizer_vocabulary(processor_kind):
    from tokenizers.processors import ByteLevel, Sequence, TemplateProcessing

    tokenizer = BoundaryTokenizer()
    processors = {
        "none": None,
        "byte_level": ByteLevel(),
        "template": TemplateProcessing(single="[BOS] $A", special_tokens=[("[BOS]", 1000)]),
        "sequence": Sequence([ByteLevel()]),
    }
    tokenizer.backend_tokenizer.post_processor = processors[processor_kind]

    def export_vocabulary():
        raise AssertionError("serialized the whole tokenizer for a processor check")

    tokenizer.backend_tokenizer.to_str = export_vocabulary
    cache = ChatPromptTokenCache(tokenizer)
    text = "<|im_start|>system\nsource"
    assert cache.encode(text) == tokenizer.encode(text)
    assert (cache.separator_id is not None) == (processor_kind in ("none", "byte_level"))


@pytest.mark.parametrize("unsafe,kwargs", [
    (True, {}), (False, {"truncation": True, "max_length": 10}),
])
def test_unsupported_tokenizers_and_encoding_options_keep_native_path(unsafe, kwargs):
    tokenizer = BoundaryTokenizer(unsafe=unsafe)
    cache = ChatPromptTokenCache(tokenizer)
    tokenizer.calls.clear()
    text = "<|im_start|>system\nsource"
    assert cache.encode(text, **kwargs) == tokenizer.encode(text, **kwargs)
    assert tokenizer.calls[0] == (text, kwargs)
    assert cache.bypasses == 1 and not cache._cache


def test_real_qwen_template_and_mutating_history_match_native_tokenization():
    path = os.environ.get("BELIEFKV_CONTEXT_TOKENIZER_TEST")
    if not path:
        pytest.skip("requires explicit local serving tokenizer")
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(path, local_files_only=True)
    cache = ChatPromptTokenCache(tokenizer)
    assert cache.separator_id is not None
    messages = [
        {"role": "system", "content": "Source evidence: e\u0301 中文\n" * 4000},
        {"role": "user", "content": "Inspect <|im_start|> in the source."},
    ]
    for thinking in (True, False):
        for step in range(3):
            rendered = tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True,
                enable_thinking=thinking,
            )
            assert cache.encode(rendered) == tokenizer.encode(rendered)
            assert cache.encode(rendered, add_special_tokens=False) == tokenizer.encode(
                rendered, add_special_tokens=False,
            )
            messages.extend([
                {"role": "assistant", "content": f"Observed evidence {step}."},
                {"role": "user", "content": "Check a distinct consequence."},
            ])
    assert cache.hits > cache.misses
