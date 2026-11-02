"""Validate a complete operator mail-key pair without echoing credentials."""
from core.mail.providers.priority import GMAILVIP, ONE_TRIEU, SHOP_NAMES


def validated_user_mail_keys(keys):
    if not isinstance(keys, dict):
        raise ValueError('Cần nhập đủ API key riêng của GmailVip.vn và 1TrieuMail.com.')
    result = {}
    for shop in (GMAILVIP, ONE_TRIEU):
        value = keys.get(shop)
        if not isinstance(value, str) or not value.strip():
            raise ValueError('Cần nhập đủ 2 API key riêng: GmailVip.vn và 1TrieuMail.com. Dùng /mailapi KEY_GMAILVIP KEY_1TRIEUMAIL.')
        value = value.strip()
        if len(value) > 2048 or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value) or '://' in value:
            raise ValueError(f'API key {SHOP_NAMES[shop]} không hợp lệ. Chỉ nhập key, không nhập link hoặc khoảng trắng.')
        result[shop] = value
    return result
