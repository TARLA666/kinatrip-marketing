#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kinatrip 营销发布提醒脚本 v6

v6 改动:
  - 每日汇总改到 18:00 BJT（晚间发布前统一预览当天内容）
  - 移除「24小时提前提醒」：不再需要
  - 保留「30分钟精准提醒」+「每日汇总」两级提醒体系

v5 改动:
  - 修正所有平台发布时间：小红书→晚间/周末，FB/IG/X/Reddit→按美国时区受众活跃时间
  - 正文完整展示：去掉所有截断，方便直接复制发布
  - 显示全部配图：每个帖子列出所有配图文件名和数量
  - 移除 GitHub 链接：用户不需要

依赖: 仅 Python 标准库
"""

import json
import os
import re
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.header import Header

# ========== 配置 ==========
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CALENDAR_FILE = os.path.join(BASE_DIR, "content_calendar.json")
TRACKER_FILE = os.path.join(BASE_DIR, "reminder_tracker.json")
TZ_BJT = timezone(timedelta(hours=8))

SMTP_PWD = os.environ.get("SMTP_PWD", "")
SMTP_EMAIL = os.environ.get("SMTP_EMAIL", "")
TO_EMAIL = os.environ.get("TO_EMAIL", "")
SMTP_HOST = "smtp.qq.com"
SMTP_PORT = 465

# GitHub 链接已移除（用户不需要，直接复制帖子内容发布）

# 提醒类型配置
REMINDER_CONFIG = {
    "daily_summary": {
        "hour": 18, "minute": 0,
        "window": 30,   # 18:00±30min 即 17:30~18:30
    },
    "30min": {
        "target": 30, "window": 5,  # 30±5min
    },
}

PLATFORM_META = {
    "xiaohongshu": {"emoji": "📕", "label": "小红书", "color": "#FF2442"},
    "facebook":    {"emoji": "📘", "label": "Facebook", "color": "#1877F2"},
    "x-twitter":   {"emoji": "🐦", "label": "X/Twitter", "color": "#1DA1F2"},
    "instagram":   {"emoji": "📸", "label": "Instagram", "color": "#E4405F"},
    "reddit":      {"emoji": "🤖", "label": "Reddit", "color": "#FF4500"},
}
# ==========================


def load_json(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: str, data: dict):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def now_bjt() -> datetime:
    return datetime.now(TZ_BJT)


def collect_all_posts(calendar: dict) -> list:
    """收集所有待发布内容"""
    posts = []
    for week in calendar.get("weeks", []):
        for post in week.get("posts", []):
            if post.get("status") != "待发布":
                continue
            dt_str = f"{post['date']} {post.get('time_bjt', '00:00')}"
            try:
                post_dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M").replace(tzinfo=TZ_BJT)
            except (ValueError, KeyError):
                continue
            posts.append({
                **post,
                "_dt": post_dt,
            })
    posts.sort(key=lambda x: x["_dt"])
    return posts


def read_content(content_file: str) -> str:
    """读取文案文件，返回完整纯文本 (去 Markdown 格式，不截断)"""
    if not content_file:
        return ""
    full_path = os.path.join(BASE_DIR, content_file) if not os.path.isabs(content_file) else content_file
    if not os.path.exists(full_path):
        return ""
    try:
        with open(full_path, "r", encoding="utf-8") as f:
            raw = f.read()
        cleaned = re.sub(r'^#+\s*', '', raw, flags=re.MULTILINE)
        cleaned = re.sub(r'\*\*(.+?)\*\*', r'\1', cleaned)
        cleaned = re.sub(r'\*(.+?)\*', r'\1', cleaned)
        cleaned = re.sub(r'`(.+?)`', r'\1', cleaned)
        cleaned = re.sub(r'^---$', '', cleaned, flags=re.MULTILINE)
        lines = [l.rstrip() for l in cleaned.split("\n")]
        # 返回完整内容，不截断
        return "\n".join(lines).strip()
    except Exception:
        return ""


def get_images(post: dict, calendar: dict) -> list:
    """获取帖子的全部配图列表"""
    image_ref = post.get("image_ref")
    if not image_ref:
        return []
    # 单个 image_ref 可能是逗号分隔的多个文件名
    if isinstance(image_ref, str) and "," in image_ref:
        return [img.strip() for img in image_ref.split(",")]
    return [image_ref]


def find_content_images(content_file: str) -> list:
    """从文案文件中提取配图信息"""
    if not content_file:
        return []
    full_path = os.path.join(BASE_DIR, content_file) if not os.path.isabs(content_file) else content_file
    if not os.path.exists(full_path):
        return []
    try:
        with open(full_path, "r", encoding="utf-8") as f:
            raw = f.read()
        # 尝试匹配 "配图:" 或 "images:" 或 "image:" 行
        img_matches = []
        for line in raw.split("\n"):
            m = re.match(r'^(?:配图|images?)[：:]\s*(.+)', line.strip(), re.IGNORECASE)
            if m:
                img_matches.append(m.group(1).strip())
        return img_matches
    except Exception:
        return []


def build_github_url(file_path: str) -> str:
    # GitHub 链接已移除，不再生成
    return ""


# ============ 每日汇总邮件 ============

def build_daily_summary(posts_today: list, calendar: dict = None) -> tuple:
    """构建今日发布清单邮件（完整正文+全部配图，无GitHub链接）"""
    now = now_bjt()
    date_str = posts_today[0]["date"] if posts_today else now.strftime("%Y-%m-%d")

    platforms = {}
    for p in posts_today:
        plat = p["platform"]
        if plat not in platforms:
            platforms[plat] = []
        platforms[plat].append(p)

    summary_lines = [f"{'='*50}",
                     f"  Kinatrip 今日发布清单 — {date_str}",
                     f"{'='*50}",
                     ""]

    for plat, posts in platforms.items():
        meta = PLATFORM_META.get(plat, {"emoji": "📄", "label": plat})
        summary_lines.append(f"\n{meta['emoji']} {meta['label']} ({len(posts)}条)")
        summary_lines.append("-" * 40)
        for i, p in enumerate(posts, 1):
            summary_lines.append(f"  {i}. [{p['time_bjt']}] {p['title']}")
            summary_lines.append(f"     类型: {p['type']}")
            images = get_images(p, calendar)
            if images:
                summary_lines.append(f"     配图({len(images)}张): {', '.join(images)}")
            else:
                summary_lines.append(f"     配图: 无")
            if p.get("hashtags"):
                summary_lines.append(f"     标签: {p['hashtags']}")
            content = read_content(p.get("content_file", ""))
            if content:
                summary_lines.append(f"     文案:")
                summary_lines.append(f"     {content}")
            summary_lines.append("")

    summary_lines.extend([
        f"{'='*50}",
        f"  共 {len(posts_today)} 条内容，覆盖 {len(platforms)} 个平台",
        f"  发布前30分钟将收到逐条精准提醒",
        f"{'='*50}",
    ])
    text_body = "\n".join(summary_lines)

    subject = f"📋 [Kinatrip 今日发布] {date_str} — {len(posts_today)}条内容"

    html_parts = []
    for plat, posts in platforms.items():
        meta = PLATFORM_META.get(plat, {"emoji": "📄", "label": plat, "color": "#666"})
        html_parts.append(f'<h3 style="color:{meta["color"]};margin:16px 0 8px">{meta["emoji"]} {meta["label"]} ({len(posts)}条)</h3>')
        for p in posts:
            content = read_content(p.get("content_file", ""))
            images = get_images(p, calendar)
            images_html = ""
            if images:
                imgs_list = "".join([f'<span style="display:inline-block;background:#e8f0fe;color:#0D47A1;padding:2px 8px;border-radius:3px;margin:2px;font-size:11px">{img}</span>' for img in images])
                images_html = f'<div style="margin:6px 0"><span style="font-size:12px;color:#888">配图({len(images)}张):</span> {imgs_list}</div>'

            html_parts.append(f'''
            <div style="background:#f8f9fa;border-radius:8px;padding:12px;margin:8px 0;border-left:3px solid {meta['color']}">
              <div style="font-weight:600;font-size:14px">{p['time_bjt']} — {p['title']}</div>
              <div style="font-size:12px;color:#888;margin:4px 0">{p['type']}</div>
              {images_html}
              {f'<div style="font-size:12px;color:#666;margin:4px 0">🏷️ {p["hashtags"]}</div>' if p.get('hashtags') else ''}
              {f'<pre style="background:#f0f0f0;padding:12px;border-radius:4px;font-size:13px;white-space:pre-wrap;line-height:1.6">{content}</pre>' if content else '<p style="color:#999;font-size:12px">文案待补充</p>'}
            </div>''')

    html_body = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:16px;font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#f5f5f5">
<div style="max-width:600px;margin:0 auto;background:#fff;border-radius:12px;padding:20px">
  <h2 style="margin:0 0 4px;color:#0D47A1">📋 Kinatrip 今日发布清单</h2>
  <p style="color:#888;font-size:13px;margin:0 0 16px">{date_str} · 共 {len(posts_today)} 条内容 · 可直接复制发布</p>
  {''.join(html_parts)}
  <hr style="border:none;border-top:1px solid #eee;margin:20px 0">
  <p style="font-size:12px;color:#999">发布前30分钟将收到逐条精准提醒 | {now.strftime('%Y-%m-%d %H:%M')} BJT</p>
</div>
</body></html>"""

    return subject, text_body, html_body


# ============ 30分钟精准提醒 ============

def build_30min_reminder(post: dict, minutes_left: int, calendar: dict = None) -> tuple:
    """单条精准提醒邮件（完整文案+全部配图，可直接复制发布）"""
    meta = PLATFORM_META.get(post["platform"], {"emoji": "📄", "label": post["platform"]})
    now = now_bjt()
    publish_time = f"{post['date']} {post['time_bjt']}"

    content = read_content(post.get("content_file", ""))
    images = get_images(post, calendar)
    images_str = ", ".join(images) if images else "无"

    text_body = f"""{'='*50}
  ⏰ Kinatrip 发布提醒 — 还有 {minutes_left} 分钟
{'='*50}

📅 发布时间: {publish_time} BJT
📱 平台: {meta['emoji']} {meta['label']}
📝 标题: {post['title']}
🏷️ 类型: {post['type']}
{'🏷️ 标签: ' + post['hashtags'] if post.get('hashtags') else ''}
🖼️ 配图({len(images)}张): {images_str}

{'─'*50}
  📄 文案（可直接复制发布）:
{'─'*50}
{content}

{'─'*50}
□ 打开 {meta['label']} 并登录
□ 复制上方文案（含标题、正文、标签）
□ 上传配图: {images_str}
□ 检查格式 → 发布
{'─'*50}
"""

    subject = f"⏰ [发布提醒] {minutes_left}分钟后 → {meta['label']}: {post['title'][:30]}"

    # 配图HTML
    images_html = ""
    if images:
        imgs_list = "".join([f'<span style="display:inline-block;background:#e8f0fe;color:#0D47A1;padding:2px 8px;border-radius:3px;margin:2px;font-size:12px">📎 {img}</span>' for img in images])
        images_html = f'<div style="margin:6px 0"><span style="font-size:12px;color:#888">🖼️ 配图({len(images)}张):</span> {imgs_list}</div>'

    html_body = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:16px;font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#f5f5f5">
<div style="max-width:600px;margin:0 auto;background:#fff;border-radius:12px;padding:20px">
  <h2 style="margin:0;color:#0D47A1">⏰ 发布提醒 — 还有 {minutes_left} 分钟</h2>
  <p style="color:#888;font-size:13px;margin:4px 0 16px">{meta['emoji']} {meta['label']} · {publish_time} BJT · 可直接复制发布</p>
  <div style="background:#f8f9fa;border-radius:8px;padding:12px;margin:8px 0">
    <div style="font-weight:600;font-size:15px">{post['title']}</div>
    <div style="font-size:12px;color:#888;margin:4px 0">{post['type']}{' · ' + post['hashtags'] if post.get('hashtags') else ''}</div>
    {images_html}
  </div>
  {f'<pre style="background:#f0f0f0;padding:12px;border-radius:6px;font-size:13px;white-space:pre-wrap;line-height:1.6">{content}</pre>' if content else '<p style="color:#999">文案待补充</p>'}
  <div style="background:#fffbe6;border:1px solid #ffe58f;border-radius:8px;padding:10px;margin:12px 0">
    <p style="margin:0;font-size:13px">📋 打开{meta['label']} → 复制文案 → 上传{len(images)}张配图 → 发布</p>
  </div>
</div>
</body></html>"""

    return subject, text_body, html_body


# ============ 发送逻辑 ============

def send_email(subject: str, text_body: str, html_body: str = "", to_email: str = None) -> bool:
    to = to_email or TO_EMAIL
    msg = MIMEMultipart("alternative")
    msg["From"] = SMTP_EMAIL
    msg["To"] = to
    msg["Subject"] = Header(subject, "utf-8")
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    if html_body:
        msg.attach(MIMEText(html_body, "html", "utf-8"))
    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
            server.login(SMTP_EMAIL, SMTP_PWD)
            server.sendmail(SMTP_EMAIL, [to], msg.as_string())
        print(f"  [OK] {subject[:60]}...")
        return True
    except Exception as e:
        print(f"  [ERR] 发送失败: {e}")
        return False


def mark_sent(tracker: dict, post_id: str, remind_type: str):
    if "sent" not in tracker:
        tracker["sent"] = {}
    if post_id not in tracker["sent"]:
        tracker["sent"][post_id] = {}
    tracker["sent"][post_id][remind_type] = now_bjt().strftime("%Y-%m-%dT%H:%M:%S")
    tracker["meta"] = tracker.get("meta", {})
    tracker["meta"]["last_updated"] = now_bjt().strftime("%Y-%m-%dT%H:%M:%S")


def is_sent(tracker: dict, post_id: str, remind_type: str) -> bool:
    return remind_type in tracker.get("sent", {}).get(post_id, {})


# ============ 主逻辑 ============

def main():
    print("=" * 50)
    print("  Kinatrip 发布提醒 v6")
    print("  (每日汇总 18:00 + 30min精准)")
    print("=" * 50)

    if not all([SMTP_PWD, SMTP_EMAIL, TO_EMAIL]):
        print("[ERROR] 缺少 SMTP 环境变量")
        return

    calendar = load_json(CALENDAR_FILE)
    if not calendar:
        print(f"[ERROR] 日历文件为空: {CALENDAR_FILE}")
        return

    all_posts = collect_all_posts(calendar)
    now = now_bjt()
    print(f"  当前时间: {now.strftime('%Y-%m-%d %H:%M')} BJT")
    print(f"  待发布总数: {len(all_posts)}")

    tracker = load_json(TRACKER_FILE)
    sent_any = False

    # ---- 1. 每日汇总 (18:00±30min) ----
    cfg = REMINDER_CONFIG["daily_summary"]
    target = now.replace(hour=cfg["hour"], minute=cfg["minute"], second=0, microsecond=0)
    delta = abs((now - target).total_seconds()) / 60
    if delta <= cfg["window"]:
        today_str = now.strftime("%Y-%m-%d")
        posts_today = [p for p in all_posts if p["date"] == today_str]
        if posts_today:
            date_key = f"daily_{today_str}"
            if not is_sent(tracker, date_key, "daily_summary"):
                print(f"\n[每日汇总] {today_str} — {len(posts_today)}条")
                subj, text, html = build_daily_summary(posts_today, calendar)
                if send_email(subj, text, html):
                    mark_sent(tracker, date_key, "daily_summary")
                    sent_any = True

    # ---- 2. 30分钟精准提醒 ----
    cfg_30 = REMINDER_CONFIG["30min"]
    for post in all_posts:
        delta = (post["_dt"] - now).total_seconds() / 60
        if cfg_30["target"] - cfg_30["window"] <= delta <= cfg_30["target"] + cfg_30["window"]:
            if not is_sent(tracker, post["id"], "30min"):
                minutes_left = int(delta)
                print(f"  [30min] {post['platform']}: {post['title'][:30]} ({minutes_left}min)")
                subj, text, html = build_30min_reminder(post, minutes_left, calendar)
                if send_email(subj, text, html):
                    mark_sent(tracker, post["id"], "30min")
                    sent_any = True

    # ---- 完成 ----
    if sent_any:
        save_json(TRACKER_FILE, tracker)
        print(f"\n[OK] tracker 已更新")

    print("=" * 50)
    print("  完成")


if __name__ == "__main__":
    main()
