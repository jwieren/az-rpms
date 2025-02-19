import logging

from azure.identity import (
    DefaultAzureCredential,
    ManagedIdentityCredential,
    CredentialUnavailableError,
)


class CachedCredential:
    """Caches an azure access token"""

    def __init__(self, client_id=None):
        self._cached_credential = None
        self._client_id = client_id

    def get(self) -> DefaultAzureCredential | ManagedIdentityCredential:
        if not self._cached_credential:
            try:
                credential = ManagedIdentityCredential(client_id=self._client_id)
                # Test that ManagedIdentity is available...
                credential.get_token("https://management.azure.com/.default")
                self._cached_credential = credential
            except CredentialUnavailableError:
                logging.info("Falling back to DefaultAzureCredential()")
                self._cached_credential = DefaultAzureCredential()

        return self._cached_credential
