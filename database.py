"""数据库存储与可重复生成的历史数据。"""
import hashlib
import hmac
import os
import random
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get('TRAILKIT_DB', str(ROOT / 'data' / 'trailkit.db')))


def now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    value = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 260000)
    return salt + ':' + value.hex()


def password_ok(password, stored):
    return hmac.compare_digest(password_hash(password, stored.split(':')[0]), stored)


class Connection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def connect():
    db = sqlite3.connect(DB_PATH, timeout=10, factory=Connection)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db


def rows(db, sql, params=()):
    return [dict(row) for row in db.execute(sql, params)]


def initialize(demo=True, initial_password='TrailKit2026!'):
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        db.execute('PRAGMA journal_mode=DELETE')
        db.executescript((ROOT / 'schema.sql').read_text(encoding='utf-8'))
        if db.execute('SELECT COUNT(*) FROM users').fetchone()[0]:
            return
        for name, role, region in [('nikita', 'Customer', '广州'),
                                   ('sales', 'Sales', '广州'),
                                   ('admin', 'Admin', '广州')]:
            db.execute('INSERT INTO users(username,password,email,region,role) VALUES(?,?,?,?,?)',
                       (name, password_hash(initial_password), name + '@example.test', region, role))
        db.executemany('INSERT INTO categories(name) VALUES(?)',
                       [(x,) for x in ['露营住宿', '徒步装备', '野外炊具', '照明工具']])
        products = [
            ('云岭双人帐篷', 1, 69900, 24, '双层通风结构，适合周末露营。重量 2.3 千克。', 'tent'),
            ('林间轻量睡袋', 1, 23900, 32, '舒适温标 摄氏 10 度，可压缩收纳，重量 900 克。', 'sleep'),
            ('远行 35 升 背包', 2, 32900, 18, '透气背负与防雨罩，适合一日徒步。', 'pack'),
            ('山径折叠登山杖', 2, 15900, 40, '铝合金三节结构，一对装。', 'pole'),
            ('营地便携炉具', 3, 18900, 12, '折叠支架与独立收纳盒，不含燃料。', 'stove'),
            ('溪谷钛杯 600 毫升', 3, 12900, 50, '折叠把手，可直接加热。重量 80 克。', 'cup'),
            ('星野营地灯', 4, 16900, 7, '三档亮度与暖光模式，通用接口充电。', 'lamp'),
            ('晨光头灯', 4, 9900, 36, '轻量头带，支持红光，适合夜间行走。', 'light')]
        db.executemany('INSERT INTO products(name,category_id,price,stock,description,icon) '
                       'VALUES(?,?,?,?,?,?)', products)
        if demo:
            seed_history(db)


def seed_history(db):
    rng = random.Random(183)
    fixture_password = password_hash(secrets.token_urlsafe(24))
    for i in range(8):
        db.execute('INSERT INTO users(username,password,email,region,role,active) '
                   'VALUES(?,?,?,?,?,0)', (f'fixture_{i}', fixture_password,
                   f'fixture{i}@example.test', ['广州', '深圳', '杭州'][i % 3], 'Customer'))
    ps = rows(db, 'SELECT p.*, c.name category FROM products p JOIN categories c ON c.id=p.category_id')
    today = datetime.now(timezone.utc).date()
    for days in range(45, 0, -1):
        for k in range(rng.randint(2, 5)):
            uid = 4 + rng.randrange(8)
            selected = rng.sample(ps, rng.randint(1, 3))
            stamp = f'{today - timedelta(days=days)}T10:{k:02}:00Z'
            total = sum(p['price'] for p in selected)
            oid = db.execute('INSERT INTO orders(user_id,status,total,created,paid_at,demo) '
                             "VALUES(?,'paid',?,?,?,1)", (uid, total, stamp, stamp)).lastrowid
            for p in selected:
                db.execute('INSERT INTO items VALUES(?,?,?,?,?,1)',
                           (oid, p['id'], p['category'], p['name'], p['price']))
                db.execute('INSERT INTO events(user_id,kind,product_id,category,seconds,detail,ip,created) '
                           "VALUES(?,'view',?,?,?,'历史浏览','127.0.0.1',?)",
                           (uid, p['id'], p['category'], rng.randint(8, 80), stamp))
