"""Buy one initial batch; persist delivery before creating any Mochi accounts."""
from core.mail.providers.gmailvip_vn import GmailVipVn
from core.mail.providers.one_trieu_mail import OneTrieuMail
from core.mail.providers.priority import MULTI_SHOP, GMAILVIP, ONE_TRIEU, buy_priority_batch
from core.mail.batch_inventory import save_batch
from core.platforms.mochichat.runners.invite_referral import MochiAccountItem


async def purchase_stock_batch(store, batch, *, provider, keys, purchase_sink, mail_pool=None,
        directory=None, on_wait=None):
    store.batch_update(batch['id'], 'buying', 'Đang mua một lô mail; không gửi lại khi chưa xác nhận giao dịch.')
    options = dict(on_purchase=purchase_sink, job_id=batch['id'], on_wait=on_wait)
    if mail_pool is not None:
        options['purchase_route'] = mail_pool.session
    try:
        if provider == MULTI_SHOP:
            delivered = await buy_priority_batch(batch['product_ids'], keys, batch['count'],
                provider_types={GMAILVIP:GmailVipVn, ONE_TRIEU:OneTrieuMail}, **options)
        else:
            cls = OneTrieuMail if provider == ONE_TRIEU else GmailVipVn
            async with cls(keys.get(provider, ''), **options) as client:
                delivered = await client.buy_gmail_domain_info(batch['product_ids'], amount=batch['count'])
        # This is an internal purchase receipt, not the public account inventory.
        save_batch(batch['id'], [MochiAccountItem(i.username, i.username, i.password) for i in delivered], directory)
        store.save_delivery(batch, delivered)
    except BaseException:
        store.batch_update(batch['id'], 'uncertain', 'Chưa xác nhận đủ giao dịch/lưu lô. Đối soát shop và bản ghi lô trước khi mua lại.')
        raise

