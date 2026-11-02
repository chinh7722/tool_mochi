"""Server token encryption uses a private key file, independent of Windows DPAPI."""
import os
from cryptography.fernet import Fernet
from telegram_store import TelegramStore


class ServerStore(TelegramStore):
    def __init__(self, path):
        super().__init__(path)
        key_path = self.path.parent / "token.key"
        try:
            descriptor = os.open(key_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(Fernet.generate_key())
        self.cipher = Fernet(key_path.read_bytes())

    def save_bot(self, token, auto_start):
        if token:
            from telegram_protocol import TelegramClient
            TelegramClient(token)
            self.set_config(token=self.cipher.encrypt(token.encode()).decode(), auto_start=bool(auto_start))
        else:
            self.set_config(auto_start=bool(auto_start))

    def token(self):
        encrypted = self.config("token", "")
        return self.cipher.decrypt(encrypted.encode()).decode() if encrypted else ""
