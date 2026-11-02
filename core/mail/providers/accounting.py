"""Read shop-reported balances inside the shared purchase gate."""
import hashlib
import uuid

from core.finance import MailAccountingError, MailPurchaseReceipt, profile_balance_units


class PurchaseAccounting:
    def __init__(self, provider, api_key, product_id, quantity, callback, job_id):
        self.provider = provider.strip().lower()
        self.account_hash = hashlib.sha256(api_key.encode()).hexdigest()
        self.product_id, self.quantity = product_id, quantity
        self.callback, self.job_id = callback, job_id
        self.purchase_id = uuid.uuid4().hex
        self.order_id = ''

    def report(self, data, before, after):
        intent = bool(data and data.get('_accounting_intent'))
        if intent:
            self.purchase_id, self.order_id = uuid.uuid4().hex, ''
        elif data:
            for key in ('trans_id', 'order_id', 'transaction_id'):
                candidate = data.get(key)
                if isinstance(candidate, (str, int)) and not isinstance(candidate, bool):
                    self.order_id = str(candidate).strip()[:200]
                    break
        status = data.get('status') if data else None
        cost = None
        state = 'intent' if intent else 'confirmed' if status == 'success' else 'uncertain'
        if (not intent and before is not None and after is not None and before >= after
                and (status == 'success' or before > after)):
            cost = before - after
        if data and status and status != 'success' and before == after and not intent:
            from core.mail.providers.purchase_policy import has_account_payload
            if not has_account_payload(data.get('data')):
                cost, state = 0, 'rejected'
        receipt = MailPurchaseReceipt(self.purchase_id, self.job_id, self.provider,
            self.account_hash, self.product_id, self.quantity, cost, self.order_id,
            'shop_rejected' if state == 'rejected' else 'balance_difference', state,
            balance_before_units=before, balance_after_units=after)
        try:
            self.callback(receipt)
        except Exception as exc:
            raise MailAccountingError('Không lưu được biên nhận mua mail; dừng để đối soát, không mua tiếp.') from exc


async def read_balance_units(session, api_url, api_key):
    response = await session.get(api_url + '/profile.php', params={'api_key': api_key}, timeout=10.0)
    return profile_balance_units(response.json())
