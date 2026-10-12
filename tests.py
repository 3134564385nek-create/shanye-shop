"""使用独立数据库进行接口集成测试。"""
import json
import os
import shutil
import uuid
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.cookiejar import CookieJar
from http.server import ThreadingHTTPServer
from pathlib import Path
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPCookieProcessor
import database
from server import Handler
from analytics import dashboard, recommend


class Client:
    def __init__(self, base):
        self.base, self.csrf = base, ''
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()))

    def call(self, path, data=None, headers=None):
        h = {'Content-Type': 'application/json', 'X-CSRF-Token': self.csrf}
        h.update(headers or {})
        request = Request(self.base+'/api/'+path, headers=h,
                          data=json.dumps(data).encode() if data is not None else None)
        try:
            response = self.opener.open(request)
        except HTTPError as exc:
            response = exc
        value = json.loads(response.read())
        if path == 'me':
            self.csrf = value.get('csrf', '')
        return response.code, value

    def login(self, name='nikita', password='TrailKit2026!'):
        code, _ = self.call('login', {'username': name, 'password': password})
        self.call('me')
        return code


class TrailKitTests(unittest.TestCase):
    def setUp(self):
        self.test_root = Path(__file__).resolve().parent / 'test_runs'
        self.temp = self.test_root / str(uuid.uuid4())
        self.temp.mkdir(parents=True)
        database.DB_PATH = self.temp/'test.db'
        database.initialize(demo=False)
        self.server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.customer, self.sales, self.admin = [Client(self.base) for _ in range(3)]

    def tearDown(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()
        assert self.temp.resolve().parent == self.test_root.resolve()
        shutil.rmtree(self.temp)

    def order(self, client=None, pid=1, quantity=1):
        client = client or self.customer
        self.assertEqual(client.call('cart',{'id':pid,'quantity':quantity})[0],200)
        code, value = client.call('checkout',{})
        self.assertEqual(code,200)
        return value['id']

    def test_public_search_and_auth_boundary(self):
        self.assertEqual(len(self.customer.call('products')[1]),8)
        self.assertEqual(len(self.customer.call('products?q=%E5%B8%90%E7%AF%B7')[1]),1)
        self.assertEqual(self.customer.call('dashboard')[0],401)
        self.assertEqual(self.customer.call('cart',{'id':1,'quantity':1})[0],401)

    def test_registration_validation_and_duplicate(self):
        payload={'username':'newuser','password':'StrongPass2026!','email':'new@example.test','region':'深圳','role':'Admin'}
        self.assertEqual(self.customer.call('register',payload)[0],200)
        self.assertEqual(self.customer.call('register',payload)[0],409)
        self.assertEqual(self.customer.login('newuser','StrongPass2026!'),200)
        self.assertEqual(self.customer.call('me')[1]['user']['role'],'Customer')

    def test_bad_password_and_injection(self):
        self.assertEqual(self.customer.login("' OR 1=1 --",'wrong'),401)
        self.assertEqual(self.customer.login('nikita','wrong'),401)
        with database.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events WHERE kind='failed_login'").fetchone()[0],2)

    def test_checkout_pay_idempotency_and_mail(self):
        self.customer.login()
        oid=self.order(quantity=2)
        self.assertEqual(self.customer.call('pay',{'id':oid})[0],200)
        self.assertTrue(self.customer.call('pay',{'id':oid})[1]['already_paid'])
        self.assertEqual(self.customer.call('products')[1][0]['stock'],22)
        self.assertEqual(len(self.customer.call('outbox')[1]),1)
        self.assertEqual(self.customer.call('orders')[1][0]['total'],139800)

    def test_stock_race_only_one_payment_succeeds(self):
        self.customer.login()
        with database.connect() as db:
            db.execute('UPDATE products SET stock=1 WHERE id=1')
        orders=[self.order(),self.order()]
        # 独立请求客户端共享同一个有效会话。
        workers=[]
        for _ in orders:
            client=Client(self.base)
            client.login()
            workers.append(client)
        with ThreadPoolExecutor(2) as pool:
            codes=list(pool.map(lambda pair:pair[0].call('pay',{'id':pair[1]})[0],zip(workers,orders)))
        self.assertEqual(sorted(codes),[200,409])
        self.assertEqual(self.customer.call('products')[1][0]['stock'],0)

    def test_failed_payment_rolls_back_all_items(self):
        self.customer.login()
        self.customer.call('cart',{'id':1,'quantity':1})
        self.customer.call('cart',{'id':2,'quantity':1})
        oid=self.customer.call('checkout',{})[1]['id']
        with database.connect() as db:
            db.execute('UPDATE products SET stock=0 WHERE id=2')
        self.assertEqual(self.customer.call('pay',{'id':oid})[0],409)
        self.assertEqual(self.customer.call('products')[1][0]['stock'],24)
        self.assertEqual(self.customer.call('orders')[1][0]['status'],'pending')

    def test_other_customer_cannot_pay_order(self):
        self.customer.login()
        oid=self.order()
        other=Client(self.base)
        other.call('register',{'username':'second','password':'Second2026!','email':'s@example.test','region':'杭州'})
        other.login('second','Second2026!')
        self.assertEqual(other.call('pay',{'id':oid})[0],404)
        self.assertEqual(other.call('orders')[1],[])
        self.assertEqual(other.call('outbox')[1],[])

    def test_role_permissions_and_catalog(self):
        self.customer.login();self.sales.login('sales')
        self.assertEqual(self.customer.call('category',{'name':'测试'})[0],403)
        self.assertEqual(self.sales.call('staff')[0],403)
        self.assertEqual(self.sales.call('category',{'name':'应急用品'})[0],200)
        cats=self.sales.call('categories')[1]
        self.assertEqual(self.sales.call('category',{'id':cats[-1]['id'],'delete':True})[0],200)
        self.assertEqual(self.sales.call('category',{'id':1,'delete':True})[0],409)
        p=self.sales.call('products')[1][0];p.update(price=68800,stock=25)
        self.assertEqual(self.sales.call('product',p)[0],200)
        self.assertEqual(self.sales.call('products')[1][0]['price'],68800)

    def test_staff_reset_and_disable_revoke_sessions(self):
        self.admin.login('admin');self.sales.login('sales')
        self.assertEqual(self.admin.call('staff',{'action':'reset','id':2,'password':'Changed2026!'})[0],200)
        self.assertEqual(self.sales.call('dashboard')[0],401)
        self.sales.csrf=''
        self.assertEqual(self.sales.login('sales','Changed2026!'),200)
        self.assertEqual(self.admin.call('staff',{'action':'disable','id':2})[0],200)
        self.assertEqual(self.sales.call('dashboard')[0],401)

    def test_csrf_and_cross_origin(self):
        self.customer.login()
        self.assertEqual(self.customer.call('checkout',{}, {'X-CSRF-Token':'wrong'})[0],403)
        self.assertEqual(self.customer.call('checkout',{}, {'Origin':'https://other.invalid'})[0],403)

    def test_cancelled_order_and_quantity_validation(self):
        self.customer.login()
        for quantity in (-1,100,1.5):
            self.assertEqual(self.customer.call('cart',{'id':1,'quantity':quantity})[0],400)
        oid=self.order()
        self.assertEqual(self.customer.call('cancel',{'id':oid})[0],200)
        self.assertEqual(self.customer.call('pay',{'id':oid})[0],409)

    def test_view_event_and_cold_start_recommendation(self):
        self.customer.login()
        self.assertEqual(self.customer.call('view',{'id':1,'seconds':42})[0],200)
        self.assertEqual(self.customer.call('view',{'id':1,'seconds':9999})[0],400)
        rec=self.customer.call('recommend?product=1')[1]
        self.assertEqual(rec['mode'],'热门回退')
        self.assertNotIn(1,[p['id'] for p in rec['products']])
        with database.connect() as db:
            event=db.execute("SELECT * FROM events WHERE kind='view'").fetchone()
            self.assertEqual((event['category'],event['seconds']),('露营住宿',42))

    def test_cf_uses_similar_user_and_dashboard_filters(self):
        with database.connect() as db:
            database.seed_history(db)
            a=dashboard(db)
            self.assertGreater(a['orders'],0)
            self.assertEqual(dashboard(db,real_only=True)['orders'],0)
            db.execute("INSERT INTO events(user_id,kind,product_id,category,seconds,detail,ip,created) "
                       "VALUES(1,'view',1,'露营住宿',10,'test','127.0.0.1',?)",(database.now(),))
            rec=recommend(db,1)
            self.assertEqual(rec['mode'],'用户协同过滤')
            self.assertNotEqual(rec['products'][0]['id'],1)
            self.assertGreaterEqual(a['mae'],0)
            self.assertLessEqual(len(dashboard(db,'month')['trend']),3)

    def test_forecast_no_future_leakage_and_anomaly_threshold(self):
        today=datetime.now(timezone.utc).date()
        with database.connect() as db:
            for day in range(45,0,-1):
                stamp=str(today-timedelta(days=day))+'T10:00:00Z'
                db.execute("INSERT INTO orders(user_id,status,total,created,paid_at) VALUES(1,'paid',10000,?,?)",
                           (stamp,stamp))
            stamp=str(today)+'T10:00:00Z'
            db.execute("INSERT INTO orders(user_id,status,total,created,paid_at) VALUES(1,'paid',40100,?,?)",
                       (stamp,stamp))
            a=dashboard(db)
            self.assertEqual((a['forecast'],a['mae'],a['threshold']),(100.0,0.0,100.0))
            self.assertTrue(a['anomaly'])


if __name__=='__main__':
    unittest.main(verbosity=2)
