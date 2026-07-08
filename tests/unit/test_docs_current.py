"""Capability check for `docs-current`: docs don't drift from reality."""

import pytest

pytestmark = pytest.mark.unit

from ctk import find_stale_docs, format_findings


def test_no_stale_docs():
    errors = [f for f in find_stale_docs() if f.severity == "error"]
    assert errors == [], "\n" + format_findings(errors)
