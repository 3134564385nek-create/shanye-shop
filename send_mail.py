"""按需执行邮件发送，订单提交时仅保存邮件记录。"""
import os
import smtplib
import ssl
import sys
from email.message import EmailMessage
from database import connect, rows


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    host=os.environ['SMTP_HOST']
    username=os.environ['SMTP_USER']
    password=os.environ['SMTP_PASSWORD']
    sender=os.environ.get('SMTP_FROM',username)
    with connect() as db, smtplib.SMTP(host,int(os.environ.get('SMTP_PORT','587')),timeout=20) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        smtp.login(username,password)
        for row in rows(db,"SELECT * FROM outbox WHERE status='local'"):
            message=EmailMessage()
            message['From'],message['To'],message['Subject']=sender,row['recipient'],row['subject']
            message['Message-ID']=f"<trailkit-order-{row['order_id']}@{sender.split('@')[-1]}>"
            message.set_content(row['body'])
            smtp.send_message(message)
            db.execute("UPDATE outbox SET status='sent' WHERE id=?",(row['id'],))
            db.commit()
    print('发送处理已完成，请查看发件箱状态。')


if __name__=='__main__':
    main()
