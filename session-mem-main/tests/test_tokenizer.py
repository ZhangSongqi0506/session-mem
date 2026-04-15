from session_mem.utils.tokenizer import TokenEstimator


def test_estimate_known_model():
    est = TokenEstimator(model="gpt-4o")
    assert est.estimate("hello world") > 0


def test_estimate_fallback_encoding():
    """未知模型应 fallback 到 cl100k_base。"""
    est = TokenEstimator(model="unknown-model-xyz")
    assert est.estimate("hello world") > 0
