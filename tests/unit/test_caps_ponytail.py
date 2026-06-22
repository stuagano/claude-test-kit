import pytest

from caps.ponytail import ponytail_instructions


@pytest.mark.unit
def test_posture_carries_the_ladder_and_the_safety_floor():
    text = ponytail_instructions()
    # The whole point is the YAGNI-first ladder...
    assert "ladder" in text.lower()
    assert "stdlib" in text.lower() or "standard library" in text.lower()
    # ...and that it never tells the agent to drop validation/security.
    assert "validation" in text.lower()
    assert "security" in text.lower()
