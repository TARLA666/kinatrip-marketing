#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kinatrip 营销发布精准提醒脚本 (v2)
功能：每30分钟扫描 content_calendar.json → 对距发布还有30分钟(±5min)的帖子
      逐一发送精准提醒邮件 → 通过 tracker 防重
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

# 精准提醒阈值（分钟）
REMINDER_MINUTES = 30       # 目标提前时间
REMINDER_WINDOW = 5         # 允许的误差窗口（±分钟）

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


def get_now_bjt() -> datetime:
    """返回当前北京时间"""
    return datetime.now(TZ_BJT)


def collect_upcoming_posts(calendar: dict, hours_ahead: int = 48) -> list:
    """
    从日历中收集未来 N 小时内的所有待发布内容
    返回列表：每个元素为 {date, time_bjt, platform, title, id, type, hashtags, content_file, image_ref}
    """
    now = get_now_bjt()
    cutoff = now + timedelta(hours=hours_ahead)

    posts = []
    for week in calendar.get("weeks", []):
        for post in week.get("posts", []):
            if post.get("status") != "待发布":
                continue

            post_datetime_str = f"{post['date']} {post.get('time_bjt', '00:00')}"
            try:
                post_dt = datetime.strptime(post_datetime_str, "%Y-%m-%d %H:%M").replace(tzinfo=TZ_BJT)
            except (ValueError, KeyError):
                continue

            # 只关注未来即将发布的内容（不超过 cutoff）
            if now <= post_dt <= cutoff:
                posts.append({
                    "date": post.get("date"),
                    "time_bjt": post.get("time_bjt", "00:00"),
                    "platform": post.get("platform", ""),
                    "title": post.get("title", ""),
                    "id": post.get("id", ""),
                    "type": post.get("type", ""),
                    "hashtags": post.get("hashtags", ""),
                    "content_file": post.get("content_file", ""),
                    "image_ref": post.get("image_ref", ""),
                })

    # 按发布时间排序
    posts.sort(key=lambda x: (x["date"], x["time_bjt"]))
    return posts


def find_posts_to_remind(posts: list) -> list:
    """
    筛选出距发布还有 30±5 分钟的帖子
    返回：[(post, minutes_until_publish), ...]
    """
    now = get_now_bjt()
    result = []

    for post in posts:
        post_datetime_str = f"{post['date']} {post['time_bjt']}"
        try:
            post_dt = datetime.strptime(post_datetime_str, "%Y-%m-%d %H:%M").replace(tzinfo=TZ_BJT)
        except ValueError:
            continue

        delta_minutes = (post_dt - now).total_seconds() / 60

        # 检查是否在 [30-5, 30+5] = [25, 35] 分钟窗口内
        if REMINDER_MINUTES - REMINDER_WINDOW <= delta_minutes <= REMINDER_MINUTES + REMINDER_WINDOW:
            result.append((post, int(delta_minutes)))

    return result


def filter_unsent(posts_with_minutes: list, tracker: dict) -> list:
    """
    过滤掉已发送过提醒的内容
    tracker 结构：{"sent": {"<post_id>": {"30min": "<timestamp>", "24h": "<timestamp>"}}}
    """
    sent = tracker.get("sent", {})
    result = []
    for post, minutes in posts_with_minutes:
        post_sent = sent.get(post["id"], {})
        if "30min" not in post_sent:  # 尚未发送30分钟提醒
            result.append((post, minutes))
    return result


def platform_info(platform: str) -> dict:
    """返回平台显示信息"""
    return {
        "xiaohongshu": {"emoji": "📕", "name": "小红书", "color": "#FF2442"},
        "facebook":    {"emoji": "📘", "name": "Facebook", "color": "#1877F2"},
        "x-twitter":   {"emoji": "🐦", "name": "X/Twitter", "color": "#14171A"},
        "instagram":   {"emoji": "📸", "name": "Instagram", "color": "#E4405F"},
        "reddit":      {"emoji": "🤖", "name": "Reddit", "color": "#FF4500"},
    }.get(platform, {"emoji": "📄", "name": platform, "color": "#666666"})


def type_style(post_type: str) -> str:
    """返回内容类型的 CSS 颜色"""
    return {
        "痛点共鸣": "#E91E63", "攻略种草": "#00BCD4", "功能展示": "#4CAF50",
        "Story": "#FF9800", "Scene Story": "#9C27B0", "Pain Point": "#F44336",
        "Quick Tip": "#2196F3", "Interactive": "#FF5722", "Guide": "#009688",
        "Tool Review": "#795548", "Feature Spotlight": "#3F51B5", "Tip": "#607D8B",
    }.get(post_type, "#999999")


def build_single_email(post: dict, minutes_left: int) -> tuple:
    """
    为单条内容构建精准提醒邮件（纯文本 + HTML）
    返回：(subject, text_body, html_body)
    """
    p_info = platform_info(post["platform"])
    now = get_now_bjt()

    # 时间计算
    publish_time = f"{post['date']} {post['time_bjt']}"
    time_display = f"{post['time_bjt']} BJT"

    # 邮件主题
    subject = f"⏰ [Kinatrip 发布提醒] {minutes_left}分钟后 → {p_info['name']}：{post['title'][:25]}"

    # ===== 纯文本版本 =====
    text_lines = [
        "=" * 50,
        f"  Kinatrip 精准发布提醒 — 距发布还有 {minutes_left} 分钟",
        "=" * 50,
        "",
        f"📅 发布时间：{publish_time} BJT（北京时间）",
        f"⏰ 当前时间：{now.strftime('%Y-%m-%d %H:%M')} BJT",
        f"📱 平台：{p_info['name']}  {p_info['emoji']}",
        f"📝 标题：{post['title']}",
        f"🏷️  类型：{post['type']}",
        f"🆔 ID：{post['id']}",
        "",
    ]
    if post.get("hashtags"):
        text_lines.append(f"🏷️  标签：{post['hashtags']}")
    if post.get("content_file"):
        text_lines.append(f"📂 内容文件：{post['content_file']}")
    if post.get("image_ref"):
        text_lines.append(f"🖼️  配图：{post['image_ref']}")
    text_lines.extend([
        "",
        "=" * 50,
        "  📌 操作提示：",
        "     1. 打开对应平台（小红书/Facebook等）",
        "     2. 从 drafts/ 目录找到对应内容文件",
        "     3. 复制文案 + 上传配图 → 发布",
        "     4. 发布后更新 content_calendar.json 中 status 为「已发布」",
        "=" * 50,
    ])
    text_body = "\n".join(text_lines)

    # ===== HTML 版本 =====
    type_color = type_style(post["type"])
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  body {{ margin:0; padding:0; background:#f0f2f5; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; }}
  .container {{ max-width:600px; margin:20px auto; }}
  .header {{ background:linear-gradient(135deg,#0D47A1,#1565C0); color:#fff; padding:24px; text-align:center; border-radius:16px 16px 0 0; }}
  .header-emoji {{ font-size:36px; margin-bottom:8px; }}
  .header-title {{ font-size:20px; font-weight:700; margin-bottom:4px; }}
  .header-sub {{ font-size:13px; opacity:0.85; }}
  .countdown {{ background:#fff; padding:20px; text-align:center; border-bottom:1px solid #eee; }}
  .countdown-number {{ font-size:48px; font-weight:700; color:#0D47A1; line-height:1; }}
  .countdown-label {{ font-size:13px; color:#666; margin-top:4px; }}
  .content {{ background:#fff; padding:20px 24px; }}
  .info-row {{ display:flex; align-items:center; padding:10px 0; border-bottom:1px solid #f0f0f0; }}
  .info-label {{ width:80px; font-size:12px; color:#999; flex-shrink:0; }}
  .info-value {{ font-size:14px; color:#333; flex:1; }}
  .badge-platform {{ display:inline-block; background:{p_info['color']}; color:#fff; padding:4px 12px; border-radius:12px; font-size:13px; font-weight:600; }}
  .badge-type {{ display:inline-block; background:{type_color}; color:#fff; padding:2px 8px; border-radius:8px; font-size:11px; margin-left:8px; }}
  .publish-time {{ background:#fff3cd; border:1px solid #ffecb5; padding:12px 16px; border-radius:8px; margin:16px 0; text-align:center; font-size:14px; color:#856404; }}
  .tips {{ background:#fff; padding:16px 24px 20px; border-radius:0 0 16px 16px; font-size:12px; color:#666; }}
  .tips-title {{ font-weight:600; color:#0D47A1; margin-bottom:8px; font-size:13px; }}
  .tips ol {{ margin:0; padding-left:20px; }}
  .tips li {{ margin-bottom:4px; }}
  .footer {{ text-align:center; padding:12px; font-size:11px; color:#999; }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <div class="header-emoji">⏰</div>
    <div class="header-title">Kinatrip 精准发布提醒</div>
    <div class="header-sub">{now.strftime('%Y年%m月%d日 %H:%M')} BJT · 距发布 {minutes_left} 分钟</div>
  </div>

  <div class="countdown">
    <div class="countdown-number">{minutes_left}</div>
    <div class="countdown-label">分钟后发布</div>
  </div>

  <div class="content">
    <div class="publish-time">
      📅 发布时间：<strong>{publish_time} BJT</strong> &nbsp; ⏱️ {time_display}
    </div>

    <div class="info-row">
      <div class="info-label">平台</div>
      <div class="info-value"><span class="badge-platform">{p_info['emoji']} {p_info['name']}</span></div>
    </div>
    <div class="info-row">
      <div class="info-label">标题</div>
      <div class="info-value">{post['title']} <span class="badge-type">{post['type']}</span></div>
    </div>
    <div class="info-row">
      <div class="info-label">ID</div>
      <div class="info-value">#{post['id']}</div>
    </div>"""

    if post.get("hashtags"):
        html += f"""
    <div class="info-row">
      <div class="info-label">标签</div>
      <div class="info-value" style="font-size:12px;color:#888;">{post['hashtags']}</div>
    </div>"""

    if post.get("content_file"):
        html += f"""
    <div class="info-row">
      <div class="info-label">内容文件</div>
      <div class="info-value" style="font-size:12px;color:#555;">{post['content_file']}</div>
    </div>"""

    if post.get("image_ref"):
        html += f"""
    <div class="info-row">
      <div class="info-label">配图</div>
      <div class="info-value" style="font-size:12px;color:#555;">{post['image_ref']}</div>
    </div>"""

    html += f"""
  </div>

  <div class="tips">
    <div class="tips-title">📌 操作提示</div>
    <ol>
      <li>打开对应平台（{p_info['name']}）</li>
      <li>从 <code>drafts/</code> 目录找到对应内容文件</li>
      <li>复制文案 + 上传配图 → 发布</li>
      <li>发布后更新 <code>content_calendar.json</code> 中 status 为「已发布」</li>
    </ol>
  </div>

  <div class="footer">
    🔗 内容日历：content_calendar.json · 📂 草稿：drafts/<br>
    由 GitHub Actions 自动发送 · {now.strftime('%Y-%m-%d %H:%M')} BJT · 无需回复
  </div>
</div>
</body>
</html>"""
    html_body = html

    return subject, text_body, html_body


def send_email(subject: str, text_body: str, html_body: str, to_email: str) -> bool:
    """通过 QQ 邮箱 SMTP 发送邮件（SSL）"""
    msg = MIMEMultipart("alternative")
    msg["From"] = SMTP_EMAIL
    msg["To"] = to_email
    msg["Subject"] = Header(subject, "utf-8")

    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
            server.login(SMTP_EMAIL, SMTP_PWD)
            server.sendmail(SMTP_EMAIL, [to_email], msg.as_string())
        print(f"  ✅ 邮件发送成功 → {subject}")
        return True
    except Exception as e:
        print(f"  ❌ 邮件发送失败：{e}")
        return False


def update_tracker(tracker: dict, post: dict, remind_type: str = "30min"):
    """将已提醒的内容 ID 写入 tracker（支持多类型提醒）"""
    if "sent" not in tracker:
        tracker["sent"] = {}
    if post["id"] not in tracker["sent"]:
        tracker["sent"][post["id"]] = {}
    now_str = get_now_bjt().strftime("%Y-%m-%dT%H:%M:%S")
    tracker["sent"][post["id"]][remind_type] = now_str
    tracker["meta"] = tracker.get("meta", {})
    tracker["meta"]["last_updated"] = now_str


def main():
    print("=" * 60)
    print("  Kinatrip 精准发布提醒脚本 v2")
    print("  （每30分钟扫描，距发布30±5分钟时逐条提醒）")
    print("=" * 60)

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

    # 3. 收集未来48小时内的待发布内容
    upcoming = collect_upcoming_posts(calendar, hours_ahead=48)
    print(f"   未来48小时内待发布内容：{len(upcoming)} 条")

    # 4. 找出距发布还有30±5分钟的帖子
    to_remind = find_posts_to_remind(upcoming)
    print(f"   距发布30±5分钟的帖子：{len(to_remind)} 条")

    if not to_remind:
        print("   当前无需发送提醒（暂无帖子在30分钟窗口内）")
        return

    # 5. 过滤已发送
    tracker = load_json(TRACKER_FILE)
    unsent = filter_unsent(to_remind, tracker)
    print(f"   过滤后待提醒：{len(unsent)} 条（已去重）")

    if not unsent:
        print("   所有内容均已发送过提醒，跳过")
        return

    # 6. 逐条发送精准提醒
    sent_count = 0
    for post, minutes_left in unsent:
        p_info = platform_info(post["platform"])
        print(f"   📧 发送提醒：{p_info['name']} - {post['title'][:30]}（{minutes_left}分钟）")

        subject, text_body, html_body = build_single_email(post, minutes_left)
        ok = send_email(subject, text_body, html_body, TO_EMAIL)

        if ok:
            update_tracker(tracker, post, "30min")
            sent_count += 1

    # 7. 保存 tracker
    if sent_count > 0:
        save_json(TRACKER_FILE, tracker)
        print(f"✅ 已更新 tracker：{TRACKER_FILE}（{sent_count} 条）")

    print("=" * 60)
    print(f"  完成 — 本次发送 {sent_count} 条精准提醒")


if __name__ == "__main__":
    main()
