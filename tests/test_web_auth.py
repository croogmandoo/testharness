def _key():
    return b"\x00" * 32

def test_make_and_load_session_token_roundtrip():
    from web.auth import make_session_token, load_session_token
    token = make_session_token("user-123", _key(), session_hours=8)
    user_id = load_session_token(token, _key(), session_hours=8)
    assert user_id == "user-123"

def test_load_session_token_wrong_key():
    from web.auth import make_session_token, load_session_token
    token = make_session_token("user-123", _key(), session_hours=8)
    assert load_session_token(token, b"\xff" * 32, session_hours=8) is None

def test_load_session_token_tampered():
    from web.auth import load_session_token
    assert load_session_token("tampered.garbage", _key(), session_hours=8) is None

def test_load_session_token_empty():
    from web.auth import load_session_token
    assert load_session_token("", _key(), session_hours=8) is None
