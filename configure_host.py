"""首次创建数据库并设置账号口令。"""
from getpass import getpass
import sys
from database import DB_PATH, connect, initialize


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    if DB_PATH.exists():
        with connect() as db:
            exists = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'").fetchone()
            if exists and db.execute('SELECT COUNT(*) FROM users').fetchone()[0]:
                print('数据库已存在，保留现有数据和口令。')
                return
    password = getpass('设置初始口令（至少十位）：')
    if len(password) < 10:
        raise SystemExit('口令长度不足，请重新运行。')
    if password != getpass('再次输入口令：'):
        raise SystemExit('两次输入不一致，请重新运行。')
    initialize(demo=True, initial_password=password)
    print('初始化完成。账号：nikita、sales、admin。')


if __name__ == '__main__':
    main()
