import pytest

from caps.review import review_rubric


@pytest.mark.unit
def test_rubric_carries_the_tags_and_the_net_metric():
    text = review_rubric()
    # The five cut-tags are the whole vocabulary...
    for tag in ("delete:", "stdlib:", "native:", "yagni:", "shrink:"):
        assert tag in text
    # ...the net-lines metric is the verdict...
    assert "net:" in text and "Lean already. Ship." in text
    # ...and it stays in its lane: complexity only, not correctness/security.
    assert "out of scope" in text.lower()
    assert "security" in text.lower()
