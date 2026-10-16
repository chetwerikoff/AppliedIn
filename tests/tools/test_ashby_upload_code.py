"""Literal refusal code must not be inferred from vendor prose or reason."""
import json

import pytest

from tools.browser_skill import Unavailable, _result


@pytest.mark.parametrize("fields, expected", [
    ({"code": "unsupported"}, "unsupported"),
    ({"data": {"code": "unsupported"}}, "unsupported"),
    ({"code": "unsupported", "data": {"code": "unsupported"}}, "unsupported"),
    ({"code": "unsupported", "data": {"code": "timeout"}}, None),
    ({"reason": "unsupported"}, None),
    ({"code": None}, None),
    ({"code": 12}, None),
    ({"code": "UPPER"}, None),
    ({"code": "unsupported\n"}, None),
    ({"code": "a" * 41}, None),
    ({"code": ""}, None),
    ({"code": "unsupported", "data": {"code": False}}, None),
    ({"code": False, "data": {"code": "unsupported"}}, None),
    ({"code": "unsupported", "data": ["code", "timeout"]}, "unsupported"),
    ({"data": "unsupported"}, None),
    ({"code": "a" * 40}, "a" * 40),
])
def test_literal_code_transport_preserves_other_exception_fields(fields, expected):
    payload = {"ok": False, "hint": "Allow access to file URLs", **fields}
    if isinstance(payload.get("data"), dict):
        payload["data"]["effect_state"] = "none"
    with pytest.raises(Unavailable) as caught:
        _result(json.dumps(payload), 1)
    exc = caught.value
    assert exc.code == expected
    reason = payload.get("code") or payload.get("reason") or "command_failed"
    assert str(exc) == f"BrowserSkill operation unavailable: {reason}"
    assert exc.effect_state == ("none" if isinstance(payload.get("data"), dict) else "")
    assert exc.file_access_required is True
