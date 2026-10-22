"""Provider clients must use verified system trust without changing injected clients."""

import ssl
from unittest.mock import patch

from finwhiz.providers import Providers


def test_default_provider_client_uses_system_certificate_trust(settings):
    with patch("finwhiz.providers.truststore.SSLContext") as context_factory, patch(
        "finwhiz.providers.httpx.Client"
    ) as client_factory:
        provider = Providers(settings, settings.provider())
        context_factory.assert_called_once_with(ssl.PROTOCOL_TLS_CLIENT)
        assert client_factory.call_args.kwargs["verify"] is context_factory.return_value
        assert provider.client is client_factory.return_value
        provider.close()


def test_injected_provider_client_is_preserved(settings):
    with patch("finwhiz.providers.truststore.SSLContext") as context_factory, patch(
        "finwhiz.providers.httpx.Client"
    ) as client_factory:
        injected = object()
        provider = Providers(settings, settings.provider(), client=injected)
        assert provider.client is injected
        context_factory.assert_not_called()
        client_factory.assert_not_called()
