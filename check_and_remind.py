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
    """构建美观的响应式 HTML 格式邮件正文（品牌色 #0D47A1）"""
    today_posts = [p for p in posts if p["date"] == today]
    tomorrow_posts = [p for p in posts if p["date"] == tomorrow]

    def platform_badge(platform: str) -> str:
        """带 emoji + 彩色标签的平台徽章"""
        info = {
            "xiaohongshu": {"color": "#FF2442", "name": "📕 小红书"},
            "facebook":    {"color": "#1877F2", "name": "📘 Facebook"},
            "x-twitter":   {"color": "#14171A", "name": "🐦 X/Twitter"},
            "instagram":   {"color": "#E4405F", "name": "📸 Instagram"},
            "reddit":      {"color": "#FF4500", "name": "🤖 Reddit"},
        }.get(platform, {"color": "#666666", "name": platform})
        return (
            f'<span style="display:inline-block;background:{info["color"]};'
            f'color:#fff;padding:3px 10px;border-radius:12px;'
            f'font-size:12px;font-weight:600;white-space:nowrap;">'
            f'{info["name"]}</span>'
        )

    def type_badge(post_type: str) -> str:
        """内容类型标签"""
        type_colors = {
            "痛点共鸣": "#E91E63",
            "攻略种草": "#00BCD4",
            "功能展示": "#4CAF50",
            "Story": "#FF9800",
            "Scene Story": "#9C27B0",
            "Pain Point": "#F44336",
            "Quick Tip": "#2196F3",
            "Interactive": "#FF5722",
            "Guide": "#009688",
            "Tool Review": "#795548",
            "Feature Spotlight": "#3F51B5",
            "Tip": "#607D8B",
        }
        c = type_colors.get(post_type, "#999")
        return (
            f'<span style="display:inline-block;background:{c};'
            f'color:#fff;padding:1px 7px;border-radius:8px;'
            f'font-size:11px;white-space:nowrap;">{post_type}</span>'
        )

    def render_section(group: list, label: str, date_str: str) -> str:
        """渲染一个日期分组的卡面"""
        if not group:
            return (
                f'<div style="text-align:center;padding:30px 0;color:#bbb;'
                f'font-size:14px;">🎉 暂无待发布内容</div>'
            )
        rows_html = ""
        for i, p in enumerate(group):
            bg = "#f8f9fa" if i % 2 == 0 else "#ffffff"
            rows_html += f"""
            <tr style="background:{bg};">
                <td style="padding:10px 8px;border-bottom:1px solid #eee;
                           font-size:13px;font-weight:600;color:#444;
                           white-space:nowrap;width:50px;">
                    🕐 {p['time_bjt']}
                </td>
                <td style="padding:10px 8px;border-bottom:1px solid #eee;width:105px;">
                    {platform_badge(p['platform'])}
                </td>
                <td style="padding:10px 8px;border-bottom:1px solid #eee;
                           font-size:13px;color:#333;line-height:1.4;">
                    {p['title']}
                    <div style="margin-top:4px;">{type_badge(p['type'])}</div>
                </td>
                <td style="padding:10px 8px;border-bottom:1px solid #eee;
                           font-size:11px;color:#888;text-align:center;width:45px;">
                    #{p['id']}
                </td>
            </tr>"""
        return f"""
        <table style="width:100%;border-collapse:collapse;font-size:14px;
                       border-radius:8px;overflow:hidden;
                       box-shadow:0 1px 3px rgba(0,0,0,0.08);">
            <thead>
                <tr style="background:linear-gradient(135deg,#0D47A1,#1565C0);color:#fff;">
                    <th style="padding:10px 8px;text-align:left;font-size:13px;">时间</th>
                    <th style="padding:10px 8px;text-align:left;font-size:13px;">平台</th>
                    <th style="padding:10px 8px;text-align:left;font-size:13px;">标题</th>
                    <th style="padding:10px 8px;text-align:center;font-size:13px;">ID</th>
                </tr>
            </thead>
            <tbody>{rows_html}</tbody>
        </table>"""

    # 今日/明日 统计卡片
    def stat_card(count: int, label: str, color: str) -> str:
        return f"""
        <div style="flex:1;text-align:center;background:{color};color:#fff;
                    border-radius:10px;padding:12px 8px;
                    box-shadow:0 2px 4px rgba(0,0,0,0.1);">
            <div style="font-size:28px;font-weight:700;">{count}</div>
            <div style="font-size:12px;opacity:0.9;">{label}</div>
        </div>"""

    stats_html = f"""
    <div style="display:flex;gap:12px;margin-bottom:20px;">
        {stat_card(len(today_posts), f"今日 {today}", "#0D47A1")}
        {stat_card(len(tomorrow_posts), f"明日 {tomorrow}", "#1565C0")}
        {stat_card(len(posts), "合计", "#1976D2")}
    </div>"""

    now_str = datetime.now(TZ_BJT).strftime("%Y年%m月%d日 %H:%M")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0;padding:0;background:#f0f2f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">

<!-- 外层容器 -->
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f0f2f5;">
<tr>
<td align="center" style="padding:20px 10px;">

<!-- 主卡片 -->
<table width="100%" style="max-width:640px;border-collapse:collapse;">

    <!-- 品牌头部 -->
    <tr>
        <td style="background:linear-gradient(135deg,#0D47A1,#1565C0,#1976D2);
                   padding:28px 24px;border-radius:16px 16px 0 0;
                   text-align:center;">
            <div style="font-size:32px;margin-bottom:6px;">🌏</div>
            <h1 style="color:#fff;margin:0;font-size:22px;font-weight:700;
                       letter-spacing:1px;">
                Kinatrip 发布提醒</h1>
            <p style="color:rgba(255,255,255,0.85);margin:6px 0 0;
                      font-size:13px;">
                {now_str} BJT  ·  GMT+8</p>
        </td>
    </tr>

    <!-- 统计卡片 -->
    <tr>
        <td style="background:#fff;padding:20px 24px 0 24px;">
            {stats_html}
        </td>
    </tr>

    <!-- 内容区域 -->
    <tr>
        <td style="background:#fff;padding:0 24px 24px 24px;
                   border-radius:0 0 16px 16px;">

            <!-- 今日 -->
            <h2 style="color:#0D47A1;font-size:16px;margin:0 0 12px 0;
                       padding-bottom:8px;border-bottom:2px solid #0D47A1;">
                📅 今日 {today}</h2>
            {render_section(today_posts, "今日", today)}

            <!-- 明日 -->
            <h2 style="color:#1565C0;font-size:16px;margin:24px 0 12px 0;
                       padding-bottom:8px;border-bottom:2px solid #1565C0;">
                📅 明日 {tomorrow}</h2>
            {render_section(tomorrow_posts, "明日", tomorrow)}

        </td>
    </tr>

    <!-- 底部信息 -->
    <tr>
        <td style="background:#fff;border-top:1px solid #eee;
                   padding:16px 24px;border-radius:0 0 16px 16px;
                   font-size:12px;color:#999;text-align:center;">
            <p style="margin:0 0 4px;">
                🔗 内容日历：content_calendar.json  ·
                📂 草稿：drafts/ ·
                🔄 由 GitHub Actions 自动发送 · 无需回复
            </p>
            <p style="margin:0;font-size:11px;">
                Kinatrip — 跨境旅游翻译APP · 拍照翻译 + 快捷沟通
                <br>在预设时间截图发送对应平台的文案
            </p>
        </td>
    </tr>

</table>

</td>
</tr>
</table>

</body>
</html>"""
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
