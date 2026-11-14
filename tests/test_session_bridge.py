from __future__ import annotations

from d2l_cli.browser_auth import BrowserAuthResult, CookieRecord
from d2l_cli.secret import SecretStr
from d2l_cli.session_bridge import build_session_material

BASE_URL = "https://example.brightspace.com/"


def _cookie(name: str, value: str) -> CookieRecord:
    return CookieRecord(
        name=name,
        value=SecretStr(value),
        domain="example.brightspace.com",
        path="/",
        secure=True,
        http_only=True,
        same_site="Lax",
    )


def test_build_session_material_joins_cookies_in_order() -> None:
    result = BrowserAuthResult(
        base_url=BASE_URL,
        cookies=(_cookie("a", "1"), _cookie("b", "2")),
    )
    material = build_session_material(result)
    assert material.cookie_header.reveal() == "a=1; b=2"
    assert material.cookie_names == ("a", "b")


def test_build_session_material_single_cookie() -> None:
    result = BrowserAuthResult(base_url=BASE_URL, cookies=(_cookie("d2lSessionVal", "secret"),))
    material = build_session_material(result)
    assert material.cookie_header.reveal() == "d2lSessionVal=secret"


def test_build_session_material_empty_cookies() -> None:
    result = BrowserAuthResult(base_url=BASE_URL, cookies=())
    material = build_session_material(result)
    assert material.cookie_header.reveal() == ""
    assert material.cookie_names == ()


def test_session_material_repr_never_reveals_value() -> None:
    result = BrowserAuthResult(base_url=BASE_URL, cookies=(_cookie("a", "supersecret"),))
    material = build_session_material(result)
    assert "supersecret" not in repr(material)
    assert "a" in repr(material)  # cookie NAMES are fine to show
