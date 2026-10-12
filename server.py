"""山野装备商城应用服务。"""
import argparse
import json
import mimetypes
import re
import secrets
import sqlite3
import sys
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from database import ROOT, connect, initialize, now, password_hash, password_ok, rows
from analytics import dashboard, recommend


class Problem(Exception):
    def __init__(self, message, status=400):
        self.message, self.status = message, status


def require(condition, message, status=400):
    if not condition:
        raise Problem(message, status)


def integer(value, low, high):
    require(type(value) is int and low <= value <= high, f'请输入 {low} 到 {high} 的整数')
    return value


def text(value, low=1, high=120):
    require(isinstance(value, str) and low <= len(value.strip()) <= high, '文字长度不符合要求')
    return value.strip()


class Handler(BaseHTTPRequestHandler):
    server_version = 'TrailKit/1.0'

    def log_message(self, fmt, *args):
        pass

    def reply(self, data, status=200, cookie=None):
        body = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        if cookie:
            self.send_header('Set-Cookie', cookie)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith('/api/'):
            self.api('GET')
            return
        allowed = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css'}
        file = allowed.get(urlparse(self.path).path)
        if not file:
            self.reply({'error': '页面不存在'}, 404)
            return
        body = (ROOT / 'static' / file).read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', (mimetypes.guess_type(file)[0] or 'text/plain') + '; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' data:; "
                         "style-src 'self' 'unsafe-inline'; script-src 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.api('POST')

    def api(self, method):
        db = connect()
        self.cookie = None
        self.user = None
        self.csrf = ''
        self.session_token = ''
        try:
            parsed = urlparse(self.path)
            path, query = parsed.path, parse_qs(parsed.query)
            data = {}
            cookie = SimpleCookie(self.headers.get('Cookie', ''))
            token = cookie['session'].value if 'session' in cookie else ''
            session = db.execute('SELECT s.csrf,u.* FROM sessions s JOIN users u ON u.id=s.user_id '
                                 'WHERE s.token=? AND s.expires>? AND u.active=1', (token, int(time.time()))).fetchone()
            if session:
                self.user, self.csrf, self.session_token = dict(session), session['csrf'], token
            if method == 'POST':
                origin = self.headers.get('Origin')
                if origin:
                    require(urlparse(origin).netloc == self.headers.get('Host'), '禁止跨站请求', 403)
                require(self.headers.get('Content-Type', '').split(';')[0] == 'application/json', '需要 JSON', 415)
                length = int(self.headers.get('Content-Length', '0'))
                require(0 < length <= 65536, '请求长度无效', 413)
                data = json.loads(self.rfile.read(length))
                require(isinstance(data, dict), '请求必须是对象')
                if session:
                    require(secrets.compare_digest(self.csrf, self.headers.get('X-CSRF-Token', '')), 'CSRF 校验失败', 403)
                db.execute('BEGIN IMMEDIATE')
            result = self.route(db, method, path, query, data)
            db.commit()
            self.reply(result, cookie=self.cookie)
        except Problem as exc:
            db.rollback()
            self.reply({'error': exc.message}, exc.status)
        except sqlite3.IntegrityError:
            db.rollback()
            self.reply({'error': '名称已存在，或数据仍被其他记录引用'}, 409)
        except (ValueError, TypeError, KeyError):
            db.rollback()
            self.reply({'error': '参数格式错误'}, 400)
        except Exception:
            db.rollback()
            import traceback
            traceback.print_exc()
            self.reply({'error': '服务器错误，请查看终端日志'}, 500)
        finally:
            db.close()

    def role(self, *roles):
        require(self.user is not None, '请先登录', 401)
        require(self.user['role'] in roles, '没有此操作权限', 403)

    def event(self, db, kind, detail, product=None, seconds=0):
        db.execute('INSERT INTO events(user_id,kind,product_id,category,seconds,detail,ip,created) '
                   'VALUES(?,?,?,?,?,?,?,?)', (self.user['id'] if self.user else None, kind,
                   product['id'] if product else None, product['category'] if product else None,
                   seconds, detail, self.client_address[0], now()))

    def route(self, db, method, path, q, d):
        uid = self.user['id'] if self.user else None
        if method == 'GET':
            if path == '/api/health':
                return {'status': 'ok', 'app': 'TrailKit'}
            if path == '/api/me':
                return {'user': {k: self.user[k] for k in ('id','username','role','region')} if uid else None,
                        'csrf': self.csrf}
            if path == '/api/categories':
                return rows(db, 'SELECT * FROM categories')
            if path == '/api/products':
                term = q.get('q', [''])[0][:100]
                category = q.get('category', [''])[0]
                return rows(db, 'SELECT p.*,c.name category FROM products p JOIN categories c ON c.id=p.category_id '
                            "WHERE p.active=1 AND p.name LIKE ? AND (?='' OR c.id=?)",
                            ('%' + term + '%', category, category))
            if path == '/api/recommend':
                return recommend(db, uid, int(q.get('product', ['0'])[0]))
            if path == '/api/cart':
                self.role('Customer')
                return rows(db, 'SELECT p.id,p.name,p.price,p.stock,c.quantity FROM cart c '
                            'JOIN products p ON p.id=c.product_id WHERE c.user_id=?', (uid,))
            if path == '/api/orders':
                self.role('Customer','Sales','Admin')
                orders = rows(db, 'SELECT o.*,u.username FROM orders o JOIN users u ON u.id=o.user_id ' +
                            ('WHERE user_id=? ' if self.user['role'] == 'Customer' else '') +
                            'ORDER BY o.id DESC LIMIT 100', (uid,) if self.user['role']=='Customer' else ())
                for order in orders:
                    order['items'] = rows(db, 'SELECT * FROM items WHERE order_id=?', (order['id'],))
                return orders
            if path == '/api/outbox':
                self.role('Customer')
                return rows(db, 'SELECT m.* FROM outbox m JOIN orders o ON o.id=m.order_id WHERE o.user_id=?', (uid,))
            if path == '/api/dashboard':
                self.role('Sales','Admin')
                period = q.get('period', ['day'])[0]
                require(period in ('day','week','month'), '时间粒度无效')
                return dashboard(db, period, q.get('scope', ['all'])[0] == 'real')
            if path == '/api/logs':
                self.role('Sales','Admin')
                return rows(db, 'SELECT e.*,u.username FROM events e LEFT JOIN users u ON u.id=e.user_id '
                            'ORDER BY e.id DESC LIMIT 150')
            if path == '/api/staff':
                self.role('Admin')
                return rows(db, "SELECT id,username,email,active FROM users WHERE role='Sales'")
            raise Problem('接口不存在', 404)

        if path == '/api/register':
            name = text(d.get('username'), 3, 24)
            require(re.fullmatch(r'[A-Za-z0-9_]+', name), '用户名只支持字母、数字、下划线')
            password = text(d.get('password'), 10, 100)
            email = text(d.get('email'), 5, 100)
            require(re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email), '邮箱格式无效')
            db.execute('INSERT INTO users(username,password,email,region,role) VALUES(?,?,?,?,?)',
                       (name, password_hash(password), email, text(d.get('region'), 1, 30), 'Customer'))
            return {'ok': True}
        if path == '/api/login':
            # 登录限流记录持久化保存，仅使用直接连接地址。
            recent = db.execute("SELECT COUNT(*) FROM events WHERE kind='failed_login' AND ip=? "
                                "AND created>strftime('%Y-%m-%dT%H:%M:%SZ','now','-5 minutes')",
                                (self.client_address[0],)).fetchone()[0]
            require(recent < 20, '登录失败过多，请五分钟后重试', 429)
            user = db.execute('SELECT * FROM users WHERE username=? AND active=1', (d.get('username'),)).fetchone()
            if not user or not password_ok(str(d.get('password', '')), user['password']):
                self.event(db, 'failed_login', '登录失败')
                db.commit()
                raise Problem('账号或密码错误', 401)
            self.user = dict(user)
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
            db.execute('DELETE FROM sessions WHERE expires<=?', (int(time.time()),))
            db.execute('INSERT INTO sessions VALUES(?,?,?,?)', (token, user['id'], csrf, int(time.time()) + 28800))
            self.cookie = f'session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=28800'
            self.event(db, 'login', '登录成功')
            return {'ok': True}
        if path == '/api/logout':
            db.execute('DELETE FROM sessions WHERE token=?', (self.session_token,))
            self.cookie = 'session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0'
            return {'ok': True}
        if path == '/api/view':
            p = db.execute('SELECT p.id,c.name category FROM products p JOIN categories c '
                           'ON c.id=p.category_id WHERE p.id=? AND p.active=1', (d.get('id'),)).fetchone()
            require(p is not None, '商品不存在', 404)
            seconds = integer(d.get('seconds'), 0, 1800)
            self.event(db, 'view', '商品详情停留', p, seconds)
            return {'ok': True}
        if path == '/api/cart':
            self.role('Customer')
            pid, qty = integer(d.get('id'), 1, 1000000), integer(d.get('quantity'), 0, 99)
            p = db.execute('SELECT * FROM products WHERE id=? AND active=1', (pid,)).fetchone()
            require(p is not None, '商品不存在', 404)
            require(qty <= p['stock'], '库存不足', 409)
            if qty == 0:
                db.execute('DELETE FROM cart WHERE user_id=? AND product_id=?', (uid, pid))
            else:
                db.execute('INSERT INTO cart VALUES(?,?,?) ON CONFLICT(user_id,product_id) '
                           'DO UPDATE SET quantity=excluded.quantity', (uid, pid, qty))
            return {'ok': True}
        if path == '/api/checkout':
            self.role('Customer')
            cart = rows(db, 'SELECT p.*,c.quantity,k.name category FROM cart c JOIN products p '
                        'ON p.id=c.product_id JOIN categories k ON k.id=p.category_id WHERE c.user_id=?', (uid,))
            require(cart, '购物车为空')
            require(all(p['active'] and p['stock']>=p['quantity'] for p in cart), '存在缺货或下架商品', 409)
            total = sum(p['price'] * p['quantity'] for p in cart)
            oid = db.execute("INSERT INTO orders(user_id,status,total,created) VALUES(?,'pending',?,?)",
                             (uid,total,now())).lastrowid
            for p in cart:
                db.execute('INSERT INTO items VALUES(?,?,?,?,?,?)',
                           (oid,p['id'],p['category'],p['name'],p['price'],p['quantity']))
            db.execute('DELETE FROM cart WHERE user_id=?', (uid,))
            return {'id': oid, 'total': total}
        if path in ('/api/pay','/api/cancel'):
            self.role('Customer')
            order = db.execute('SELECT * FROM orders WHERE id=? AND user_id=?', (d.get('id'),uid)).fetchone()
            require(order is not None, '订单不存在', 404)
            if path == '/api/cancel':
                require(order['status'] == 'pending', '只有待支付订单可取消', 409)
                db.execute("UPDATE orders SET status='cancelled' WHERE id=?", (order['id'],))
                return {'ok': True}
            if order['status']=='paid':
                return {'ok': True, 'already_paid': True}
            require(order['status']=='pending', '订单不可支付', 409)
            for item in rows(db, 'SELECT * FROM items WHERE order_id=?', (order['id'],)):
                count = db.execute('UPDATE products SET stock=stock-? WHERE id=? AND stock>=? AND active=1',
                                   (item['quantity'],item['product_id'],item['quantity'])).rowcount
                require(count == 1, '库存不足或商品下架，支付未完成', 409)
            db.execute("UPDATE orders SET status='paid',paid_at=? WHERE id=?", (now(),order['id']))
            body = f"山野装备订单 #{order['id']} 已确认。金额 ¥{order['total']/100:.2f}。"
            db.execute('INSERT INTO outbox(order_id,recipient,subject,body) VALUES(?,?,?,?)',
                       (order['id'],self.user['email'],f"山野装备订单确认 #{order['id']}",body))
            self.event(db,'purchase',f"确认订单 {order['id']}")
            return {'ok': True, 'email_status': 'local'}
        if path == '/api/category':
            self.role('Sales','Admin')
            if d.get('delete'):
                db.execute('DELETE FROM categories WHERE id=?', (d.get('id'),))
                detail = f"删除类别 {d.get('id')}"
            else:
                name = text(d.get('name'),1,30)
                db.execute('INSERT INTO categories(name) VALUES(?)', (name,))
                detail = '新增类别 ' + name
            self.event(db,'operation',detail)
            return {'ok': True}
        if path == '/api/product':
            self.role('Sales','Admin')
            values = (text(d.get('name'),1,80),integer(d.get('category_id'),1,1000000),
                      integer(d.get('price'),1,10000000),integer(d.get('stock'),0,100000),
                      text(d.get('description'),1,500),text(d.get('icon','pack'),1,20))
            if d.get('id'):
                count = db.execute('UPDATE products SET name=?,category_id=?,price=?,stock=?,description=?,icon=? '
                                   'WHERE id=?', (*values,d['id'])).rowcount
                require(count == 1,'商品不存在',404)
            else:
                db.execute('INSERT INTO products(name,category_id,price,stock,description,icon) VALUES(?,?,?,?,?,?)',values)
            self.event(db,'operation','维护商品 ' + values[0])
            return {'ok': True}
        if path == '/api/staff':
            self.role('Admin')
            action = d.get('action')
            if action == 'create':
                name = text(d.get('username'),3,24)
                require(re.fullmatch(r'[A-Za-z0-9_]+',name),'用户名格式无效')
                db.execute('INSERT INTO users(username,password,email,region,role) VALUES(?,?,?,?,?)',
                           (name,password_hash(text(d.get('password'),10,100)),name+'@example.test','广州','Sales'))
            elif action in ('disable','reset'):
                staff = db.execute("SELECT * FROM users WHERE id=? AND role='Sales' AND active=1", (d.get('id'),)).fetchone()
                require(staff is not None,'销售账号不存在',404)
                if action == 'disable':
                    db.execute('UPDATE users SET active=0 WHERE id=?',(staff['id'],))
                else:
                    db.execute('UPDATE users SET password=? WHERE id=?',
                               (password_hash(text(d.get('password'),10,100)),staff['id']))
                db.execute('DELETE FROM sessions WHERE user_id=?',(staff['id'],))
            else:
                raise Problem('未知账号操作')
            self.event(db,'operation','销售账号操作：' + {'create':'创建','reset':'重置密码','disable':'停用'}[action])
            return {'ok': True}
        raise Problem('接口不存在',404)


def run():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8088)
    parser.add_argument('--no-demo', action='store_true')
    args = parser.parse_args()
    initialize(demo=not args.no_demo)
    server = ThreadingHTTPServer((args.host,args.port),Handler)
    print(f'山野装备已启动： http://{args.host}:{args.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    run()
