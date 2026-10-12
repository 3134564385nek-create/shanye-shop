"""用户协同过滤、关联购买、趋势预测与异常分析。"""
import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from database import rows


def recommend(db, uid, product_id=0):
    products = rows(db, 'SELECT p.*,c.name category FROM products p '
                       'JOIN categories c ON c.id=p.category_id WHERE p.active=1 AND p.stock>0')
    vectors = defaultdict(Counter)
    for r in db.execute("SELECT user_id,product_id,COUNT(*) n FROM events WHERE kind='view' "
                        'AND user_id IS NOT NULL GROUP BY user_id,product_id'):
        vectors[r['user_id']][r['product_id']] += min(r['n'], 5)
    for r in db.execute("SELECT o.user_id,i.product_id,SUM(i.quantity) n FROM orders o "
                        "JOIN items i ON i.order_id=o.id WHERE o.status='paid' GROUP BY o.user_id,i.product_id"):
        vectors[r['user_id']][r['product_id']] += 3 * r['n']
    scores, mode = Counter(), '热门回退'
    if product_id:
        mode = '浏览关联购买'
        viewers = {r[0] for r in db.execute("SELECT DISTINCT user_id FROM events WHERE kind='view' "
                                           'AND product_id=? AND user_id IS NOT NULL', (product_id,))}
        for r in db.execute("SELECT o.user_id,i.product_id,i.quantity FROM orders o "
                            "JOIN items i ON i.order_id=o.id WHERE o.status='paid'"):
            if r['user_id'] in viewers and r['product_id'] != product_id:
                scores[r['product_id']] += r['quantity']
    elif uid and vectors[uid]:
        base = vectors[uid]
        norm = math.sqrt(sum(v * v for v in base.values()))
        neighbours = []
        for other, vec in vectors.items():
            if other == uid:
                continue
            dot = sum(v * vec.get(p, 0) for p, v in base.items())
            sim = dot / (norm * math.sqrt(sum(v * v for v in vec.values()))) if vec else 0
            if sim:
                neighbours.append((sim, vec))
        for sim, vec in sorted(neighbours, key=lambda x: x[0], reverse=True)[:5]:
            for pid, weight in vec.items():
                if pid not in base:
                    scores[pid] += sim * weight
        mode = '用户协同过滤' if scores else '热门回退'
    popular = Counter()
    for vec in vectors.values():
        popular.update(vec)
    candidates = [p for p in products if p['id'] != product_id]
    candidates.sort(key=lambda p: (scores[p['id']], popular[p['id']], -p['id']), reverse=True)
    return {'mode': mode if scores else '热门回退', 'products': candidates[:4]}


def dashboard(db, period='day', real_only=False):
    paid = rows(db, "SELECT * FROM orders WHERE status='paid'" + (' AND demo=0' if real_only else ''))
    daily, grouped = Counter(), Counter()
    for order in paid:
        date = datetime.fromisoformat(order['paid_at'].replace('Z', '+00:00')).date()
        key = str(date) if period == 'day' else (date.strftime('%Y-%m') if period == 'month'
                  else str(date - timedelta(days=date.weekday())))
        grouped[key] += order['total']
        daily[str(date)] += order['total']
    today = datetime.now(timezone.utc).date()
    # 按协调世界时统计前四十五个完整日，无记录日期补零。
    series = [daily[str(today - timedelta(days=d))] / 100 for d in range(45, 0, -1)]
    forecast = statistics.mean(series[-7:])
    mae = statistics.mean(abs(series[i] - statistics.mean(series[i-7:i]))
                          for i in range(7, len(series)))
    baseline = series[-14:]
    threshold = statistics.mean(baseline) + 3 * statistics.pstdev(baseline)
    current = daily[str(today)] / 100
    inventory = rows(db, 'SELECT p.id,p.name,p.stock,p.active,c.name category FROM products p '
                         'JOIN categories c ON c.id=p.category_id ORDER BY p.stock')
    condition = ' AND o.demo=0' if real_only else ''
    ranking = rows(db, 'SELECT i.name,i.category,SUM(i.quantity) quantity,'
                   'SUM(i.price*i.quantity)/100.0 revenue FROM items i JOIN orders o ON o.id=i.order_id '
                   "WHERE o.status='paid'" + condition + ' GROUP BY i.product_id ORDER BY quantity DESC')
    categories = rows(db, 'SELECT i.category,SUM(i.price*i.quantity)/100.0 revenue FROM items i '
                      "JOIN orders o ON o.id=i.order_id WHERE o.status='paid'" + condition + ' GROUP BY i.category')
    profiles = []
    for user in rows(db, "SELECT id,username,region,active FROM users WHERE role='Customer'"):
        spend = sum(o['total'] for o in paid if o['user_id'] == user['id']) / 100
        pref = db.execute("SELECT category,SUM(seconds) score FROM events WHERE user_id=? AND kind='view' "
                          'GROUP BY category ORDER BY score DESC LIMIT 1', (user['id'],)).fetchone()
        profiles.append({**user, 'spend': spend, 'power': '高' if spend >= 3000 else '中' if spend >= 1000 else '低',
                         'preference': pref[0] if pref else '暂无行为'})
    return {'revenue': sum(o['total'] for o in paid) / 100, 'orders': len(paid),
            'scope': '当前订单' if real_only else '全部记录',
            'trend': [{'date': k, 'value': v / 100} for k, v in sorted(grouped.items())],
            'forecast': round(forecast, 2), 'mae': round(mae, 2), 'threshold': round(threshold, 2),
            'today': current, 'anomaly': current > max(threshold, 1),
            'ranking': ranking, 'categories': categories, 'inventory': inventory,
            'profiles': profiles, 'statuses': rows(db, 'SELECT status,COUNT(*) n FROM orders' +
                        (' WHERE demo=0' if real_only else '') + ' GROUP BY status')}
