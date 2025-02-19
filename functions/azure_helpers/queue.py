from azure.storage.queue import (
    QueueClient,
    BinaryBase64EncodePolicy,
)


class QueueManager:
    """Creates a wrapper around QueueClient"""

    def __init__(self, storage_account, credential, queue_name):
        """
        Initialize a wrapper around QueueClient.

        Args:
            storage_account (str): The storage account name.
            queue_name (str): The queue to write to
        """
        self._storage_account = storage_account
        self._queue_name = queue_name

        self._client = QueueClient(
            account_url=f"https://{self._storage_account}.queue.core.windows.net",
            queue_name=self._queue_name,
            credential=credential,
            message_encode_policy=BinaryBase64EncodePolicy(),
        )

    def send(self, msg: str, **kwargs) -> None:
        self._client.send_message(msg.encode(encoding="utf-8"), **kwargs)
