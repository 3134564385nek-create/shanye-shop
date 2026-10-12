PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL,
 password TEXT NOT NULL, email TEXT NOT NULL, region TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('Customer','Sales','Admin')),
 active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS sessions (
 token TEXT PRIMARY KEY, user_id INTEGER REFERENCES users(id),
 csrf TEXT NOT NULL, expires INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS categories (
 id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL);
CREATE TABLE IF NOT EXISTS products (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL,
 category_id INTEGER NOT NULL REFERENCES categories(id),
 price INTEGER NOT NULL CHECK(price > 0),
 stock INTEGER NOT NULL CHECK(stock >= 0), description TEXT NOT NULL,
 icon TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS cart (
 user_id INTEGER REFERENCES users(id), product_id INTEGER REFERENCES products(id),
 quantity INTEGER NOT NULL CHECK(quantity BETWEEN 1 AND 99),
 PRIMARY KEY(user_id, product_id));
CREATE TABLE IF NOT EXISTS orders (
 id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id),
 status TEXT NOT NULL CHECK(status IN ('pending','paid','cancelled')),
 total INTEGER NOT NULL, created TEXT NOT NULL, paid_at TEXT,
 demo INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS items (
 order_id INTEGER REFERENCES orders(id), product_id INTEGER REFERENCES products(id),
 category TEXT NOT NULL, name TEXT NOT NULL, price INTEGER NOT NULL,
 quantity INTEGER NOT NULL, PRIMARY KEY(order_id, product_id));
CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id),
 kind TEXT NOT NULL, product_id INTEGER REFERENCES products(id),
 category TEXT, seconds INTEGER NOT NULL DEFAULT 0, detail TEXT NOT NULL,
 ip TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS outbox (
 id INTEGER PRIMARY KEY, order_id INTEGER UNIQUE REFERENCES orders(id),
 recipient TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'local');
CREATE INDEX IF NOT EXISTS idx_events_user_product ON events(user_id, product_id);
CREATE INDEX IF NOT EXISTS idx_events_created ON events(created);
CREATE INDEX IF NOT EXISTS idx_orders_paid ON orders(status, paid_at);
CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id, created);
