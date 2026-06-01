#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kinatrip 营销发布提醒脚本
功能：读取 content_calendar.json → 检查今日/明日待发布内容 → SMTP 发送 QQ 邮箱提醒
依赖：仅 Python 标准库（smtplib / json / datetime / os / email）
"""

import json
import os
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header

# ========== 配置 ==========
CALENDAR_FILE = "content_calendar.json"
TRACKER_FILE = "reminder_tracker.json"
TZ_BJT = timezone(timedelta(hours=8))  # 北京时间 GMT+8

# 从环境变量读取（GitHub Actions Secrets）
SMTP_PWD = os.environ.get("SMTP_PWD", "")
SMTP_EMAIL = os.environ.get("SMTP_EMAIL", "")
TO_EMAIL = os.environ.get("TO_EMAIL", "")

# QQ 邮箱 SMTP 配置
SMTP_HOST = "smtp.qq.com"
SMTP_PORT = 465  # SSL
# ==========================


def load_json(path: str) -> dict:
    """安全读取 JSON 文件"""
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: str, data: dict):
    """保存 JSON 文件"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_today_and_tomorrow():
    """返回 (今天日期字符串, 明天日期字符串) 北京时间"""
    now = datetime.now(TZ_BJT)
    today = now.strftime("%Y-%m-%d")
    tomorrow = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    return today, tomorrow


def collect_posts(calendar: dict, target_dates: list) -> list:
    """
    从日历中收集指定日期的所有待发布内容
    返回列表：每个元素为 {date, time_bjt, platform, title, id, type}
    """
    posts = []
    for week in calendar.get("weeks", []):
        for post in week.get("posts", []):
            if post.get("date") in target_dates and post.get("status") == "待发布":
                posts.append({
                    "date": post.get("date"),
                    "time_bjt": post.get("time_bjt", "00:00"),
                    "platform": post.get("platform", ""),
                    "title": post.get("title", ""),
                    "id": post.get("id", ""),
                    "type": post.get("type", ""),
                    "hashtags": post.get("hashtags", ""),
                })
    # 按日期 + 时间排序
    posts.sort(key=lambda x: (x["date"], x["time_bjt"]))
    return posts


def filter_unsent(posts: list, tracker: dict) -> list:
    """过滤掉已发送过提醒的内容"""
    sent = tracker.get("sent", {})
    return [p for p in posts if p["id"] not in sent]


def build_email_body(posts: list, today: str, tomorrow: str) -> str:
    """
    构建邮件正文（纯文本 + 简单表格）
    分【今日】和【明日】两个区块
    """
    lines = []
    lines.append("=" * 50)
    lines.append("  Kinatrip 营销发布提醒")
    lines.append("  生成时间：" + datetime.now(TZ_BJT).strftime("%Y-%m-%d %H:%M BJT"))
    lines.append("=" * 50)
    lines.append("")

    # 按日期分组
    today_posts = [p for p in posts if p["date"] == today]
    tomorrow_posts = [p for p in posts if p["date"] == tomorrow]

    def format_posts(group: list, label: str, date_str: str):
        """格式化一组内容"""
        block = []
        block.append(f"【{label} {date_str} 待发布】共 {len(group)} 条")
        block.append("-" * 40)
        if not group:
            block.append("（无待发布内容）")
        else:
            for p in group:
                platform_cn = {
                    "xiaohongshu": "小红书",
                    "facebook": "Facebook",
                    "x-twitter": "X/Twitter",
                    "instagram": "Instagram",
                    "reddit": "Reddit",
                }.get(p["platform"], p["platform"])
                block.append(f"  ⏰ {p['time_bjt']}  {platform_cn}")
                block.append(f"     📝 {p['title']}")
                block.append(f"     🏷️  类型：{p['type']}  |  ID：{p['id']}")
                if p.get("hashtags"):
                    block.append(f"     🏷️  标签：{p['hashtags']}")
                block.append("")
        block.append("")
        return block

    lines.extend(format_posts(today_posts, "今日", today))
    lines.extend(format_posts(tomorrow_posts, "明日", tomorrow))

    lines.append("=" * 50)
    lines.append("  🔗 内容日历：content_calendar.json")
    lines.append("  📂 草稿目录：drafts/")
    lines.append("=" * 50)

    return "\n".join(lines)


def build_email_html(posts: list, today: str, tomorrow: str) -> str:
    """构建 HTML 格式邮件正文（可选，作为 multipart 的 alternative）"""
    today_posts = [p for p in posts if p["date"] == today]
    tomorrow_posts = [p for p in posts if p["date"] == tomorrow]

    def platform_badge(platform: str) -> str:
        color = {
            "xiaohongshu": "#FF2442",
            "facebook": "#1877F2",
            "x-twitter": "#14171A",
            "instagram": "#E4405F",
            "reddit": "#FF4500",
        }.get(platform, "#666666")
        name = {
            "xiaohongshu": "小红书",
            "facebook": "Facebook",
            "x-twitter": "X",
            "instagram": "Instagram",
            "reddit": "Reddit",
        }.get(platform, platform)
        return f'<span style="background:{color};color:#fff;padding:2px 8px;border-radius:4px;font-size:12px;">{name}</span>'

    def render_table(group: list) -> str:
        if not group:
            return '<p style="color:#999;">（无待发布内容）</p>'
        rows = ""
        for p in group:
            rows += f"""
            <tr>
                <td style="padding:8px;border-bottom:1px solid #eee;">{p['time_bjt']}</td>
                <td style="padding:8px;border-bottom:1px solid #eee;">{platform_badge(p['platform'])}</td>
                <td style="padding:8px;border-bottom:1px solid #eee;">{p['title']}</td>
                <td style="padding:8px;border-bottom:1px solid #eee;color:#666;font-size:12px;">{p['type']}</td>
            </tr>
            """
        return f"""
        <table style="width:100%;border-collapse:collapse;font-size:14px;">
            <thead>
                <tr style="background:#f5f5f5;">
                    <th style="padding:8px;text-align:left;">时间</th>
                    <th style="padding:8px;text-align:left;">平台</th>
                    <th style="padding:8px;text-align:left;">标题</th>
                    <th style="padding:8px;text-align:left;">类型</th>
                </tr>
            </thead>
            <tbody>{rows}</tbody>
        </table>
        """

    html = f"""
    <html>
    <body style="font-family:Helvetica,Arial,sans-serif;max-width:600px;margin:0 auto;padding:20px;color:#333;">
        <div style="background:#0D47A1;color:#fff;padding:20px;border-radius:8px 8px 0 0;">
            <h2 style="margin:0;">📢 Kinatrip 营销发布提醒</h2>
            <p style="margin:5px 0 0;opacity:0.8;">{datetime.now(TZ_BJT).strftime("%Y年%m月%d日 %H:%M")} BJT</p>
        </div>
        <div style="border:1px solid #eee;border-top:none;padding:20px;border-radius:0 0 8px 8px;">
            <h3 style="color:#0D47A1;margin-top:0;">【今日 {today}】共 {len(today_posts)} 条</h3>
            {render_table(today_posts)}
            <h3 style="color:#0D47A1;margin-top:30px;">【明日 {tomorrow}】共 {len(tomorrow_posts)} 条</h3>
            {render_table(tomorrow_posts)}
            <hr style="margin:30px 0;border:none;border-top:1px solid #eee;">
            <p style="font-size:12px;color:#999;">
                🔗 内容日历：content_calendar.json<br>
                📂 草稿目录：drafts/<br>
                由 GitHub Actions 自动发送
            </p>
        </div>
    </body>
    </html>
    """
    return html


def send_email(subject: str, text_body: str, html_body: str, to_email: str):
    """通过 QQ 邮箱 SMTP 发送邮件（SSL）"""
    msg = MIMEMultipart("alternative")
    msg["From"] = SMTP_EMAIL
    msg["To"] = to_email
    msg["Subject"] = Header(subject, "utf-8")

    # 纯文本版本
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    # HTML 版本
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
            server.login(SMTP_EMAIL, SMTP_PWD)
            server.sendmail(SMTP_EMAIL, [to_email], msg.as_string())
        print(f"✅ 邮件发送成功 → {to_email}")
        return True
    except Exception as e:
        print(f"❌ 邮件发送失败：{e}")
        return False


def update_tracker(tracker: dict, posts: list):
    """将已提醒的内容 ID 写入 tracker"""
    if "sent" not in tracker:
        tracker["sent"] = {}
    now_str = datetime.now(TZ_BJT).strftime("%Y-%m-%dT%H:%M:%S")
    for p in posts:
        tracker["sent"][p["id"]] = now_str


def main():
    print("=" * 50)
    print("  Kinatrip 发布提醒脚本")
    print("=" * 50)

    # 1. 检查环境变量
    if not SMTP_PWD or not SMTP_EMAIL or not TO_EMAIL:
        print("❌ 缺少环境变量：")
        print(f"   SMTP_PWD   = {'已设置' if SMTP_PWD else '未设置'}")
        print(f"   SMTP_EMAIL = {'已设置' if SMTP_EMAIL else '未设置'}")
        print(f"   TO_EMAIL   = {'已设置' if TO_EMAIL else '未设置'}")
        print("   请通过 GitHub Secrets 设置以上变量")
        return

    # 2. 读取内容日历
    if not os.path.exists(CALENDAR_FILE):
        print(f"❌ 日历文件不存在：{CALENDAR_FILE}")
        return
    calendar = load_json(CALENDAR_FILE)
    print(f"✅ 已读取内容日历")

    # 3. 确定目标日期（今天 + 明天）
    today, tomorrow = get_today_and_tomorrow()
    print(f"   今日：{today}  |  明日：{tomorrow}")

    # 4. 收集待发布内容
    posts = collect_posts(calendar, [today, tomorrow])
    print(f"   共找到 {len(posts)} 条待发布内容（含今日+明日）")

    if not posts:
        print("   无待发布内容，无需发送提醒")
        return

    # 5. 过滤已发送
    tracker = load_json(TRACKER_FILE)
    unsent = filter_unsent(posts, tracker)
    print(f"   过滤后待提醒：{len(unsent)} 条（已去重）")

    if not unsent:
        print("   所有内容均已发送过提醒，跳过")
        return

    # 6. 构建邮件
    subject = f"📢 [Kinatrip 发布提醒] {today} 待发布 ({len(unsent)}条)"
    text_body = build_email_body(unsent, today, tomorrow)
    html_body = build_email_html(unsent, today, tomorrow)

    print(f"   邮件主题：{subject}")
    print(f"   收件人：{TO_EMAIL}")

    # 7. 发送邮件
    ok = send_email(subject, text_body, html_body, TO_EMAIL)

    # 8. 更新 tracker
    if ok:
        update_tracker(tracker, unsent)
        save_json(TRACKER_FILE, tracker)
        print(f"✅ 已更新 tracker：{TRACKER_FILE}")

    print("=" * 50)
    print("  完成")


if __name__ == "__main__":
    main()
