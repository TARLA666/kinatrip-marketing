#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地测试脚本：验证 QQ 邮箱 SMTP 配置是否正确
用法：python test_smtp.py
环境变量：SMTP_PWD / SMTP_EMAIL / TO_EMAIL
"""

import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header
from datetime import datetime, timedelta, timezone

TZ_BJT = timezone(timedelta(hours=8))

SMTP_PWD = os.environ.get("SMTP_PWD", "")
SMTP_EMAIL = os.environ.get("SMTP_EMAIL", "")
TO_EMAIL = os.environ.get("TO_EMAIL", "")

SMTP_HOST = "smtp.qq.com"
SMTP_PORT = 465


def send_test_email():
    if not SMTP_PWD or not SMTP_EMAIL or not TO_EMAIL:
        print("❌ 请先设置环境变量：")
        print(f"   SMTP_PWD   = {'***' if SMTP_PWD else '未设置'}")
        print(f"   SMTP_EMAIL = {SMTP_EMAIL or '未设置'}")
        print(f"   TO_EMAIL   = {TO_EMAIL or '未设置'}")
        print()
        print("设置方式：")
        print('  export SMTP_PWD="你的QQ邮箱授权码"')
        print('  export SMTP_EMAIL="123456789@qq.com"')
        print('  export TO_EMAIL="接收提醒的邮箱"')
        return False

    now = datetime.now(TZ_BJT).strftime("%Y-%m-%d %H:%M BJT")

    subject = "📢 [Kinatrip 测试] SMTP 配置验证邮件"
    text_body = f"""
这是一封测试邮件，用于验证 Kinatrip 营销提醒系统的 SMTP 配置。

发送时间：{now}
发件人：{SMTP_EMAIL}
收件人：{TO_EMAIL}

如果你的 QQ 邮箱授权码和 SMTP 设置正确，你会收到这封邮件 ✅

下一步：将以上三个变量配置到 GitHub Secrets：
  - SMTP_PWD
  - SMTP_EMAIL
  - TO_EMAIL

详见：GitHub 仓库 → Settings → Secrets and variables → Actions → New repository secret
"""

    html_body = f"""
<html>
<body style="font-family:Helvetica,Arial,sans-serif;max-width:600px;padding:20px;color:#333;">
    <div style="background:#0D47A1;color:#fff;padding:20px;border-radius:8px 8px 0 0;">
        <h2 style="margin:0;">📢 Kinatrip 营销提醒 — SMTP 测试</h2>
        <p style="margin:5px 0 0;opacity:0.8;">{now}</p>
    </div>
    <div style="border:1px solid #eee;border-top:none;padding:20px;border-radius:0 0 8px 8px;">
        <p>✅ 如果你看到这封邮件，说明 <strong>SMTP 配置正确</strong>！</p>
        <table style="width:100%;border-collapse:collapse;font-size:14px;margin:15px 0;">
            <tr style="background:#f5f5f5;">
                <td style="padding:8px;border:1px solid #ddd;">发件人</td>
                <td style="padding:8px;border:1px solid #ddd;">{SMTP_EMAIL}</td>
            </tr>
            <tr>
                <td style="padding:8px;border:1px solid #ddd;">收件人</td>
                <td style="padding:8px;border:1px solid #ddd;">{TO_EMAIL}</td>
            </tr>
            <tr style="background:#f5f5f5;">
                <td style="padding:8px;border:1px solid #ddd;">SMTP 服务器</td>
                <td style="padding:8px;border:1px solid #ddd;">{SMTP_HOST}:{SMTP_PORT} (SSL)</td>
            </tr>
        </table>
        <hr style="margin:20px 0;border:none;border-top:1px solid #eee;">
        <p style="font-size:13px;color:#666;">
            🔧 下一步：将以下三个变量配置到 GitHub Secrets：<br>
            1. <code>SMTP_PWD</code> = 你的 QQ 邮箱授权码<br>
            2. <code>SMTP_EMAIL</code> = {SMTP_EMAIL}<br>
            3. <code>TO_EMAIL</code> = {TO_EMAIL}<br><br>
            路径：GitHub 仓库 → <strong>Settings</strong> → <strong>Secrets and variables</strong> → <strong>Actions</strong> → <strong>New repository secret</strong>
        </p>
    </div>
</body>
</html>
"""

    msg = MIMEMultipart("alternative")
    msg["From"] = SMTP_EMAIL
    msg["To"] = TO_EMAIL
    msg["Subject"] = Header(subject, "utf-8")
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        print(f"📡 正在连接 {SMTP_HOST}:{SMTP_PORT} ...")
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
            print(f"📡 正在登录 {SMTP_EMAIL} ...")
            server.login(SMTP_EMAIL, SMTP_PWD)
            print("📡 正在发送测试邮件 ...")
            server.sendmail(SMTP_EMAIL, [TO_EMAIL], msg.as_string())
        print(f"✅ 测试邮件已成功发送至 {TO_EMAIL}！")
        print(f"   请检查收件箱（含垃圾邮件箱）")
        return True
    except Exception as e:
        print(f"❌ 发送失败：{e}")
        print()
        print("常见错误排查：")
        print("  535 = 授权码错误，请重新生成 QQ 邮箱授权码")
        print("  连接超时 = 检查网络，或 QQ 邮箱未开启 SMTP 服务")
        print("  请确认 QQ 邮箱 → 设置 → 账户 → 开启 POP3/SMTP 服务")
        return False


if __name__ == "__main__":
    send_test_email()
