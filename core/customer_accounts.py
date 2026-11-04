"""Customer wallets and orders, committed with the existing job ledger."""
from datetime import datetime, timezone
from decimal import Decimal
import re
import uuid

from core.finance import MAX_MONEY_UNITS
from core.job_schedule import schedule_storage

MIN_DEPOSIT_VND = 10_000
MAX_DEPOSIT_VND = 100_000


def chat_id(value):
    if type(value) is not int or not 1 <= value <= 2**63 - 1:
        raise ValueError('Chat ID khách hàng không hợp lệ.')
    return value


def stamp():
    return datetime.now(timezone.utc).isoformat()


class CustomerAccountsMixin:
    def _init_customer_accounts(self):
        with self._connection(write=True) as db:
            db.execute('''CREATE TABLE IF NOT EXISTS customers (
                chat_id INTEGER PRIMARY KEY CHECK(chat_id>0), name TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
            if 'deleted_at' not in {r['name'] for r in db.execute('PRAGMA table_info(customers)')}:
                db.execute('ALTER TABLE customers ADD COLUMN deleted_at TEXT')
            db.execute('''CREATE TABLE IF NOT EXISTS customer_adjustments (
                entry_id TEXT PRIMARY KEY, chat_id INTEGER NOT NULL REFERENCES customers(chat_id),
                amount_units INTEGER NOT NULL, before_units INTEGER NOT NULL CHECK(before_units>=0),
                after_units INTEGER NOT NULL CHECK(after_units>=0), reason TEXT NOT NULL,
                created_at TEXT NOT NULL)''')
            db.execute('CREATE INDEX IF NOT EXISTS customer_adjustments_chat ON customer_adjustments(chat_id)')
            db.execute('''CREATE TABLE IF NOT EXISTS customer_events (
                entry_id TEXT PRIMARY KEY, chat_id INTEGER NOT NULL REFERENCES customers(chat_id),
                kind TEXT NOT NULL, description TEXT NOT NULL, created_at TEXT NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS customer_orders (
                order_id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE REFERENCES jobs(job_id),
                chat_id INTEGER NOT NULL REFERENCES customers(chat_id),
                reserved_units INTEGER NOT NULL CHECK(reserved_units>0),
                charged_units INTEGER CHECK(charged_units>=0),
                created_at TEXT NOT NULL, settled_at TEXT)''')
            db.execute('''CREATE TABLE IF NOT EXISTS customer_wallet (
                entry_id TEXT PRIMARY KEY, chat_id INTEGER NOT NULL REFERENCES customers(chat_id),
                kind TEXT NOT NULL CHECK(kind IN ('deposit','reserve','refund')),
                amount_units INTEGER NOT NULL, reference TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL, created_at TEXT NOT NULL)''')
            db.execute('CREATE INDEX IF NOT EXISTS customer_wallet_chat ON customer_wallet(chat_id)')
            db.execute('''CREATE TABLE IF NOT EXISTS sepay_transactions (
                transaction_id TEXT PRIMARY KEY, bank_account_id TEXT NOT NULL,
                reference_number TEXT NOT NULL, amount_units INTEGER NOT NULL,
                content TEXT NOT NULL, transaction_date TEXT NOT NULL,
                chat_id INTEGER, state TEXT NOT NULL, reason TEXT NOT NULL,
                created_at TEXT NOT NULL)''')
            db.execute('''CREATE UNIQUE INDEX IF NOT EXISTS sepay_reference
                ON sepay_transactions(bank_account_id, reference_number) WHERE reference_number!='' ''')

    @staticmethod
    def _require_customer_id(db,customer_id):
        actor=db.execute('SELECT active FROM users WHERE chat_id=?',(customer_id,)).fetchone()
        if actor is not None and actor[0]:
            raise ValueError('ID đã được cấp quyền admin/partner, không thuộc khách hàng.')

    @staticmethod
    def _customer_balance(db,customer_id):
        # Python integers keep exact totals even when lifetime ledger turnover
        # exceeds SQLite's integer range while the available balance stays valid.
        return sum(row[0] for row in db.execute('''SELECT amount_units FROM customer_wallet WHERE chat_id=?
            UNION ALL SELECT amount_units FROM customer_adjustments WHERE chat_id=?''',(customer_id,customer_id)))

    def sync_customer_profiles(self):
        """Keep revoked IDs discoverable without restoring explicitly deleted profiles."""
        with self._connection(write=True) as db:
            now=stamp()
            db.execute('''INSERT OR IGNORE INTO customers(chat_id,name,created_at,updated_at)
                SELECT chat_id,name,?,? FROM users WHERE chat_id>0 AND active=0''',(now,now))

    def ensure_customer(self, customer_id, name='', *, restore=False):
        customer_id = chat_id(customer_id)
        name = str(name).strip()[:120] or f'Khách {customer_id}'
        with self._connection(write=True) as db:
            self._require_customer_id(db,customer_id)
            old=db.execute('SELECT deleted_at FROM customers WHERE chat_id=?',(customer_id,)).fetchone()
            if old is not None and old['deleted_at']:
                if not restore:
                    raise ValueError('Hồ sơ khách hàng đã bị xóa. Liên hệ quản trị viên để khôi phục.')
                db.execute('UPDATE customers SET deleted_at=NULL WHERE chat_id=?',(customer_id,))
                db.execute('INSERT INTO customer_events VALUES (?,?,?,?,?)',
                    (uuid.uuid4().hex,customer_id,'customer_restored','Khôi phục hồ sơ khách hàng trong tool',stamp()))
            db.execute('''INSERT INTO customers(chat_id,name,created_at,updated_at) VALUES (?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET updated_at=excluded.updated_at''',
                (customer_id, name, stamp(), stamp()))
        return self.get_customer(customer_id)

    def delete_customer(self,customer_id):
        customer_id=chat_id(customer_id)
        with self._connection(write=True) as db:
            self._require_customer_id(db,customer_id)
            row=db.execute('SELECT deleted_at FROM customers WHERE chat_id=?',(customer_id,)).fetchone()
            if row is None or row['deleted_at']:
                return False
            if db.execute('SELECT 1 FROM customer_orders WHERE chat_id=? AND settled_at IS NULL',(customer_id,)).fetchone():
                raise ValueError('Khách còn đơn chưa chốt tiền. Chờ đơn kết thúc hoặc đối soát trước khi xóa.')
            now=stamp()
            db.execute('UPDATE customers SET deleted_at=?,updated_at=? WHERE chat_id=?',(now,now,customer_id))
            db.execute('INSERT INTO customer_events VALUES (?,?,?,?,?)',
                (uuid.uuid4().hex,customer_id,'customer_deleted','Xóa hồ sơ khỏi danh sách khách hàng trong tool',now))
            return True

    def set_customer_balance(self,customer_id,target_units,reason,*,expected_units):
        customer_id=chat_id(customer_id)
        if type(target_units) is not int or not 0<=target_units<=MAX_MONEY_UNITS:
            raise ValueError('Số dư mới phải không âm và trong giới hạn lưu trữ.')
        if type(expected_units) is not int or not 0<=expected_units<=MAX_MONEY_UNITS:
            raise ValueError('Số dư đối chiếu không hợp lệ.')
        if not isinstance(reason,str) or not 1<=len(reason.strip())<=300:
            raise ValueError('Nhập lý do chỉnh số dư, tối đa 300 ký tự.')
        with self._connection(write=True) as db:
            self._require_customer_id(db,customer_id)
            row=db.execute('SELECT deleted_at FROM customers WHERE chat_id=?',(customer_id,)).fetchone()
            if row is None or row['deleted_at']:
                raise ValueError('Khách hàng không tồn tại hoặc đã bị xóa.')
            before=self._customer_balance(db,customer_id)
            if before!=expected_units:
                raise ValueError('Số dư đã thay đổi do giao dịch mới. Đóng và mở lại cửa sổ để đối chiếu trước khi sửa.')
            if before==target_units:
                return False
            db.execute('INSERT INTO customer_adjustments VALUES (?,?,?,?,?,?,?)',
                (uuid.uuid4().hex,customer_id,target_units-before,before,target_units,
                    f'{reason.strip()} (Số dư: {Decimal(before)/100} → {Decimal(target_units)/100} VND)',stamp()))
            return True

    def get_customer(self, customer_id):
        customer_id = chat_id(customer_id)
        with self._connection() as db:
            row = db.execute('''SELECT c.*,
                (SELECT COUNT(*) FROM customer_orders o WHERE o.chat_id=c.chat_id) AS total_orders,
                (SELECT COUNT(*) FROM customer_orders o JOIN jobs j ON j.job_id=o.job_id
                    WHERE o.chat_id=c.chat_id AND j.status='completed') AS completed_orders,
                (SELECT COUNT(*) FROM customer_orders o JOIN jobs j ON j.job_id=o.job_id
                    WHERE o.chat_id=c.chat_id AND j.status='failed') AS failed_orders
                FROM customers c WHERE c.chat_id=?''', (customer_id,)).fetchone()
            if row is None:
                return None
            result=dict(row)
            result['balance_units']=self._customer_balance(db,customer_id)
            return result

    def list_customers(self):
        with self._connection() as db:
            ids = [row[0] for row in db.execute('''SELECT c.chat_id FROM customers c
                LEFT JOIN users u ON u.chat_id=c.chat_id WHERE COALESCE(u.active,0)=0 AND c.deleted_at IS NULL
                ORDER BY c.created_at DESC,c.chat_id''')]
        return tuple(self.get_customer(value) for value in ids)

    def customer_history(self, customer_id):
        customer_id = chat_id(customer_id)
        with self._connection() as db:
            return tuple(dict(row) for row in db.execute('''SELECT * FROM customer_wallet WHERE chat_id=?
                UNION ALL SELECT entry_id,chat_id,'adjustment',amount_units,'adjustment:'||entry_id,
                    'Điều chỉnh số dư trong tool: '||reason,created_at
                    FROM customer_adjustments WHERE chat_id=?
                UNION ALL SELECT entry_id,chat_id,kind,0,'customer:'||entry_id,description,created_at
                    FROM customer_events WHERE chat_id=? ORDER BY created_at DESC,entry_id DESC''',
                    (customer_id,customer_id,customer_id)))

    def customer_orders(self, customer_id):
        customer_id = chat_id(customer_id)
        with self._connection() as db:
            return tuple(dict(row) for row in db.execute('''SELECT o.*, j.code, j.requested_beans,
                j.target_count, j.status, j.dispatch_state, j.scheduled_at, j.success_count, j.sale_rate_units FROM customer_orders o
                JOIN jobs j ON j.job_id=o.job_id WHERE o.chat_id=? ORDER BY o.created_at DESC''', (customer_id,)))

    @staticmethod
    def _wallet_entry(db, customer_id, kind, units, reference, description):
        db.execute('INSERT INTO customer_wallet VALUES (?, ?, ?, ?, ?, ?, ?)',
            (uuid.uuid4().hex, customer_id, kind, units, reference, description, stamp()))

    def record_customer_order(self, job_id, customer_id, code, points, target_count,
            fingerprint, update_id, rate_units, *, scheduled_at=None, schedule_data=None):
        scheduled_at, schedule_data = schedule_storage(scheduled_at, schedule_data)
        from core.pricing import price_snapshot
        quoted=price_snapshot(schedule_data)
        customer_id = chat_id(customer_id)
        if (type(points) is not int or not 1<=points<=1_000_000 or not isinstance(code,str)
                or re.fullmatch(r'[A-Za-z0-9_-]{1,64}',code) is None):
            raise ValueError('Mã mời hoặc số bean của đơn hàng không hợp lệ.')
        if (type(rate_units) is not int or rate_units <= 0 or type(target_count) is not int
                or target_count <= 0 or target_count != (points + 99) // 100
                or (quoted is None and rate_units > MAX_MONEY_UNITS // target_count)):
            raise ValueError('Chưa cấu hình đơn giá hợp lệ cho đơn khách hàng.')
        if not isinstance(job_id, str) or not job_id or len(job_id)>120:
            raise ValueError('Mã job không hợp lệ.')
        if (type(update_id) is not int or not 0<=update_id<=2**63-1 or not isinstance(fingerprint,str)
                or not 1<=len(fingerprint)<=128):
            raise ValueError('Yêu cầu đặt đơn không hợp lệ.')
        reserved = quoted if quoted is not None else rate_units * target_count
        if reserved<=0 or reserved>MAX_MONEY_UNITS:
            raise ValueError('Tổng giá đơn khách không hợp lệ.')
        with self._connection(write=True) as db:
            previous = db.execute('SELECT job_id FROM jobs WHERE (bot_fingerprint=? AND update_id=?) OR job_id=?',
                (fingerprint, update_id, job_id)).fetchone()
            if previous:
                return False
            profile = db.execute('SELECT name,deleted_at FROM customers WHERE chat_id=?', (customer_id,)).fetchone()
            if profile is None or profile['deleted_at']:
                raise ValueError('Chưa có khách hàng. Gửi /start cho bot trước.')
            operator = db.execute('SELECT active FROM users WHERE chat_id=?', (customer_id,)).fetchone()
            if operator and operator[0]:
                raise ValueError('ID hiện đã được cấp quyền; cần dùng luồng người dùng được cấp quyền.')
            balance = self._customer_balance(db,customer_id)
            if balance < reserved:
                raise ValueError(f'Số dư không đủ. Cần {Decimal(reserved)/100:,.0f} VND, '
                    f'khả dụng {Decimal(balance)/100:,.0f} VND. Dùng /nap để nạp tiền.')
            now = stamp()
            db.execute('''INSERT OR IGNORE INTO users VALUES (?, ?, 'partner', 0, ?, ?)''',
                (customer_id, profile[0], now, now))
            db.execute('''INSERT INTO jobs(job_id,chat_id,code,requested_beans,target_count,
                bot_fingerprint,update_id,created_at,updated_at,sale_rate_units,finance_tracked,
                scheduled_at,schedule_data,dispatch_state)
                VALUES (?,?,?,?,?,?,?,?,?,?,1,?,?,?)''',
                (job_id,customer_id,code,points,target_count,fingerprint,update_id,now,now,rate_units,
                 scheduled_at,schedule_data,'scheduled' if scheduled_at else 'running'))
            order_id = 'DH' + uuid.uuid4().hex.upper()
            db.execute('INSERT INTO customer_orders VALUES (?,?,?,?,NULL,?,NULL)',
                (order_id,job_id,customer_id,reserved,now))
            self._wallet_entry(db,customer_id,'reserve',-reserved,'reserve:'+order_id,
                f'Giữ tiền đơn {order_id}, {target_count * 100} bean')
            return True

    def _settle_customer_order(self, db, job_id):
        order = db.execute('''SELECT o.*, j.success_count, j.target_count, j.sale_rate_units,j.schedule_data
            FROM customer_orders o JOIN jobs j ON j.job_id=o.job_id
            WHERE o.job_id=? AND o.settled_at IS NULL''', (job_id,)).fetchone()
        if order is None:
            return
        # A failed/cancelled order still pays for confirmed successful beans.
        from core.pricing import earned_units
        charge = earned_units(min(order['success_count'],order['target_count']),order['target_count'],order['sale_rate_units'],order['schedule_data'])
        refund = order['reserved_units'] - charge
        if refund:
            self._wallet_entry(db,order['chat_id'],'refund',refund,'refund:'+order['order_id'],
                f'Hoàn phần chưa thành công của đơn {order["order_id"]}')
        db.execute('UPDATE customer_orders SET charged_units=?,settled_at=? WHERE order_id=?',
            (charge,stamp(),order['order_id']))

    def get_customer_order_for_job(self, job_id):
        with self._connection() as db:
            row = db.execute('SELECT * FROM customer_orders WHERE job_id=?', (job_id,)).fetchone()
            return dict(row) if row else None

    def sepay_cursor(self, bank_id):
        with self._connection() as db:
            row = db.execute('SELECT value FROM metadata WHERE key=?', ('sepay_cursor:'+bank_id,)).fetchone()
            return row[0] if row else ''

    def set_sepay_cursor(self, bank_id, transaction_id):
        with self._connection(write=True) as db:
            db.execute('INSERT INTO metadata VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                ('sepay_cursor:'+bank_id,transaction_id))

    def process_sepay_transaction(self, data, bank_id, account_number, start_at):
        """Only call with data fetched from authenticated SePay API, never chat text."""
        transaction_id = str(uuid.UUID(str(data.get('id',''))))
        amount = data.get('amount_in')
        if type(amount) is not int or not 0 <= amount <= MAX_MONEY_UNITS // 100:
            raise ValueError('SePay trả số tiền không hợp lệ; chưa cập nhật con trỏ.')
        content = str(data.get('transaction_content') or '').strip()
        ref = str(data.get('reference_number') or '').strip()
        tx_date = datetime.fromisoformat(str(data.get('transaction_date') or ''))
        if tx_date.tzinfo is None:
            from datetime import timedelta
            tx_date = tx_date.replace(tzinfo=timezone(timedelta(hours=7)))
        start = datetime.fromisoformat(start_at)
        if start.tzinfo is None:
            raise ValueError('Mốc bắt đầu nhận tiền thiếu múi giờ.')
        candidate = int(content) if re.fullmatch(r'[1-9][0-9]{0,18}',content) else None
        if candidate is not None and candidate > 2**63-1:
            candidate = None
        reason = ''
        if data.get('bank_account_id') != bank_id or str(data.get('account_number','')) != account_number:
            reason = 'Sai tài khoản nhận đã cấu hình'
        elif data.get('transfer_type') != 'in' or data.get('amount_out') != 0 or amount <= 0:
            reason = 'Không phải giao dịch tiền vào'
        elif tx_date < start:
            reason = 'Giao dịch trước mốc bắt đầu nhận tiền'
        elif not MIN_DEPOSIT_VND <= amount <= MAX_DEPOSIT_VND:
            reason = 'Ngoài giới hạn 10.000–100.000 VND'
        elif candidate is None:
            reason = 'Nội dung phải đúng một Chat ID'
        with self._connection(write=True) as db:
            duplicate = db.execute('''SELECT transaction_id FROM sepay_transactions WHERE transaction_id=?
                OR (bank_account_id=? AND reference_number=? AND reference_number!='')''',
                (transaction_id,bank_id,ref)).fetchone()
            if duplicate:
                return False
            profile = db.execute('SELECT deleted_at FROM customers WHERE chat_id=?',(candidate,)).fetchone() if candidate else None
            operator=db.execute('SELECT active FROM users WHERE chat_id=?',(candidate,)).fetchone() if candidate else None
            if not reason and operator is not None and operator[0]:
                reason='ID đã được cấp quyền admin/partner'
            if not reason and profile is None:
                reason = 'Chat ID chưa đăng ký với bot'
            if not reason and profile['deleted_at']:
                reason='Hồ sơ khách hàng đã bị xóa'
            if not reason:
                balance = self._customer_balance(db,candidate)
                if balance > MAX_MONEY_UNITS - amount*100:
                    reason = 'Số dư vượt giới hạn lưu trữ'
            state = 'review' if reason else 'credited'
            db.execute('INSERT INTO sepay_transactions VALUES (?,?,?,?,?,?,?,?,?,?)',
                (transaction_id,bank_id,ref,amount*100,content,tx_date.isoformat(),candidate,state,reason,stamp()))
            if not reason:
                self._wallet_entry(db,candidate,'deposit',amount*100,'sepay:'+transaction_id,
                    f'Nạp SePay {ref or transaction_id}')
            return not bool(reason)

    def list_sepay_transactions(self):
        with self._connection() as db:
            return tuple(dict(row) for row in db.execute('SELECT * FROM sepay_transactions ORDER BY created_at DESC LIMIT 1000'))

    def reconcile_sepay_credit(self,transaction_id,customer_id):
        """An operator may match a verified, in-range incoming payment to a wallet."""
        customer_id=chat_id(customer_id)
        with self._connection(write=True) as db:
            row=db.execute('SELECT * FROM sepay_transactions WHERE transaction_id=?',(transaction_id,)).fetchone()
            if (row is None or row['state']!='review' or row['reason'] not in
                    ('Nội dung phải đúng một Chat ID','Chat ID chưa đăng ký với bot','Hồ sơ khách hàng đã bị xóa')
                    or not MIN_DEPOSIT_VND*100<=row['amount_units']<=MAX_DEPOSIT_VND*100):
                raise ValueError('Chỉ đối soát giao dịch tiền vào hợp lệ đang chờ khớp Chat ID, trong giới hạn nạp.')
            self._require_customer_id(db,customer_id)
            if not db.execute('SELECT 1 FROM customers WHERE chat_id=? AND deleted_at IS NULL',(customer_id,)).fetchone():
                raise ValueError('Thêm khách hàng hoặc yêu cầu khách gửi /start trước khi đối soát.')
            balance=self._customer_balance(db,customer_id)
            if balance>MAX_MONEY_UNITS-row['amount_units']:
                raise ValueError('Số dư vượt giới hạn lưu trữ.')
            self._wallet_entry(db,customer_id,'deposit',row['amount_units'],'sepay:'+transaction_id,
                f'Đối soát SePay thủ công: {row["reference_number"] or transaction_id}')
            db.execute("UPDATE sepay_transactions SET state='credited',chat_id=?,reason='Đối soát thủ công trong tool' WHERE transaction_id=?",
                (customer_id,transaction_id))
