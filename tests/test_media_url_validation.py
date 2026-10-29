from __future__ import annotations

import socket

import pytest

from unigrok_public import server
from unigrok_public.tools import media


def test_media_url_validation_is_resolver_independent_and_normalizes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_resolution(*args: object, **kwargs: object) -> object:
        pytest.fail("media URL validation must not resolve DNS")

    monkeypatch.setattr(socket, "getaddrinfo", unexpected_resolution)

    assert (
        media.validated_media_url("  HTTPS://ExAmPle.COM.:443/a%20b.png?sig=A%2FB  ", "image_url")
        == "https://example.com/a%20b.png?sig=A%2FB"
    )
    assert (
        media.validated_media_url("https://BÜCHER.de", "image_url") == "https://xn--bcher-kva.de/"
    )
    assert (
        media.validated_media_url("https://CDN.Example.COM:8443/media", "image_url")
        == "https://cdn.example.com:8443/media"
    )


@pytest.mark.parametrize(
    "value",
    [
        "",
        "http://example.com/image.png",
        "https:///image.png",
        "https://user@example.com/image.png",
        "https://user:password@example.com/image.png",
        "https://@example.com/image.png",
        "https://exa%6dple.com/image.png",
        "https://example.com:/image.png",
        "https://example.com:0/image.png",
        "https://example.com:invalid/image.png",
        "https://example.com:65536/image.png",
        "https://example.com\\@127.0.0.1/image.png",
        "https://example.com/image.png#fragment",
        "https://example..com/image.png",
        "https://-example.com/image.png",
        "https://example-.com/image.png",
        "https://example_com/image.png",
        "https://xn--abc.com/image.png",
        "https://example.xn--abc/image.png",
        "https://example.com/image\n.png",
        "https://example.com/" + "a" * media.MAX_MEDIA_URL_CHARS,
    ],
)
def test_media_url_rejects_credentials_and_ambiguous_syntax(value: str) -> None:
    with pytest.raises(ValueError, match="public https URL"):
        media.validated_media_url(value, "image_url")


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "127.1",
        "2130706433",
        "017700000001",
        "0x7f000001",
        "0177.0.0.1",
        "0x7f.0x0.0x0.0x1",
        "127。0。0。1",
        "8.8.8.8",
        "[::1]",
        "[::ffff:127.0.0.1]",
        "[2001:4860:4860::8888]",
        "%31%32%37.0.0.1",
    ],
)
def test_media_url_rejects_ip_literals_and_legacy_numeric_forms(host: str) -> None:
    with pytest.raises(ValueError, match="public https URL"):
        media.validated_media_url(f"https://{host}/image.png", "image_url")


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "assets.localhost",
        "printer.local",
        "metadata.google.internal",
        "router.lan",
        "service.corp",
        "portal.intranet",
        "host.private",
        "host.home.arpa",
        "name.test",
        "name.invalid",
        "hidden.onion",
    ],
)
def test_media_url_rejects_obvious_non_public_names(host: str) -> None:
    with pytest.raises(ValueError, match="public https URL"):
        media.validated_media_url(f"https://{host}/image.png", "image_url")


def test_media_url_optional_domain_allowlist_is_label_bounded() -> None:
    assert (
        media.validated_media_url(
            "https://Images.CDN.Example.com./a.png",
            "image_url",
            allowed_domains=["CDN.EXAMPLE.COM."],
        )
        == "https://images.cdn.example.com/a.png"
    )
    assert (
        media.validated_media_url(
            "https://example.com/a.png",
            "image_url",
            allowed_domains="example.com",
        )
        == "https://example.com/a.png"
    )

    for value in (
        "https://evil-example.com/a.png",
        "https://example.com.evil.org/a.png",
    ):
        with pytest.raises(ValueError, match="not in allowed_domains"):
            media.validated_media_url(
                value,
                "image_url",
                allowed_domains=["example.com"],
            )

    with pytest.raises(ValueError, match="not in allowed_domains"):
        media.validated_media_url("https://example.com/a.png", "image_url", allowed_domains=[])
    with pytest.raises(ValueError, match="plain public domain names"):
        media.validated_media_url(
            "https://example.com/a.png",
            "image_url",
            allowed_domains=["https://example.com"],
        )


def test_media_url_list_normalizes_and_applies_one_allowlist() -> None:
    assert media.validated_media_urls(
        ["https://A.Example.com", "https://b.example.com:443/two"],
        "image_urls",
        2,
        allowed_domains=["example.com"],
    ) == ["https://a.example.com/", "https://b.example.com/two"]


def test_server_media_allowlist_is_fail_closed_and_applies_to_wrappers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UNIGROK_MEDIA_ALLOWED_DOMAINS", "example.com,")
    with pytest.raises(RuntimeError, match="malformed entry"):
        server._configured_media_allowed_domains()

    monkeypatch.setenv("UNIGROK_MEDIA_ALLOWED_DOMAINS", "https://example.com")
    with pytest.raises(RuntimeError, match="plain public domain names"):
        server._configured_media_allowed_domains()

    monkeypatch.setattr(server, "MEDIA_ALLOWED_DOMAINS", ("example.com",))
    assert server._validated_media_url("https://cdn.example.com/a", "image_url") == (
        "https://cdn.example.com/a"
    )
    with pytest.raises(ValueError, match="not in allowed_domains"):
        server._validated_media_url("https://attacker.org/a", "image_url")
