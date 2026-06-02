#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kinatrip 营销发布精准提醒脚本 (v3)
功能：每30分钟扫描 content_calendar.json → 对距发布还有30分钟(±5min)的帖子
      逐一发送精准提醒邮件（嵌入文案内容）→ 通过 tracker 防重
依赖：仅 Python 标准库（smtplib / json / datetime / os / email）
优化点 v2→v3：
  - 邮件直接嵌入文案正文（读取 content_file，支持 .md/.txt）
  - 配图信息含完整路径 + GitHub 链接
  - 平台特定发布提示（小红书话题、X字符限制、Reddit排版等）
  - 优化 HTML 视觉设计（品牌色 #0D47A1、卡片阴影、代码块样式）
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

# GitHub 仓库信息（用于生成文件链接）
GITHUB_REPO = "TARLA666/kinatrip-marketing"
GITHUB_BRANCH = "master"
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
        "xiaohongshu": {"emoji": "📕", "name": "小红书", "color": "#FF2442", "short": "xhs"},
        "facebook":    {"emoji": "📘", "name": "Facebook", "color": "#1877F2", "short": "fb"},
        "x-twitter":   {"emoji": "🐦", "name": "X / Twitter", "color": "#14171A", "short": "x"},
        "instagram":   {"emoji": "📸", "name": "Instagram", "color": "#E4405F", "short": "ig"},
        "reddit":      {"emoji": "🤖", "name": "Reddit", "color": "#FF4500", "short": "rd"},
    }.get(platform, {"emoji": "📄", "name": platform, "color": "#666666", "short": "unk"})


def type_style(post_type: str) -> str:
    """返回内容类型的 CSS 颜色"""
    return {
        "痛点共鸣": "#E91E63", "攻略种草": "#00BCD4", "功能展示": "#4CAF50",
        "Story": "#FF9800", "Scene Story": "#9C27B0", "Pain Point": "#F44336",
        "Quick Tip": "#2196F3", "Interactive": "#FF5722", "Guide": "#009688",
        "Tool Review": "#795548", "Feature Spotlight": "#3F51B5", "Tip": "#607D8B",
    }.get(post_type, "#999999")


def read_content_file(content_file: str) -> str:
    """
    读取 content_file 内容，返回纯文本
    支持 .md / .txt 文件，自动去除 Markdown 格式符号
    """
    if not content_file:
        return ""
    full_path = os.path.join(BASE_DIR, content_file) if not os.path.isabs(content_file) else content_file
    if not os.path.exists(full_path):
        return f"[文件不存在: {content_file}]"
    try:
        with open(full_path, "r", encoding="utf-8") as f:
            raw = f.read()
        # 去除 Markdown 标题符号（# ）和加粗（**），保留正文
        cleaned = re.sub(r'^#+\s*', '', raw, flags=re.MULTILINE)  # 去除标题#
        cleaned = re.sub(r'\*\*(.+?)\*\*', r'\1', cleaned)         # 去除加粗**
        cleaned = re.sub(r'\*(.+?)\*', r'\1', cleaned)             # 去除斜体*
        cleaned = re.sub(r'`(.+?)`', r'\1', cleaned)               # 去除行内代码`
        return cleaned.strip()
    except Exception as e:
        return f"[读取失败: {e}]"


def build_github_link(file_path: str) -> str:
    """生成 GitHub 文件链接"""
    if not file_path:
        return ""
    # 去除开头的 ./ 或 /
    file_path = re.sub(r'^\.?/', '', file_path)
    return f"https://github.com/{GITHUB_REPO}/blob/{GITHUB_BRANCH}/{file_path}"


def platform_tips(platform: str) -> list:
    """返回平台特定发布提示"""
    return {
        "xiaohongshu": [
            "文案末尾话题标签格式：#话题1 #话题2（空格分隔）",
            "封面图建议 3:4 比例（1080×1440px）",
            "发布后观察前2小时互动数据",
        ],
        "facebook": [
            "建议设置为「公开」可见",
            "可 @Kinatrip 官方账号增加曝光",
            "图片描述可加 alt 文本提升无障碍体验",
        ],
        "x-twitter": [
            "注意字符限制：280 字符以内（含话题标签）",
            "话题标签格式：#Kinatrip #TravelHack（驼峰式）",
            "建议附图 1-4 张，增加曝光率",
        ],
        "instagram": [
            "Story 注意第一帧吸引力，建议加文字引导",
            "话题标签放评论区或末尾，建议 8-15 个",
            "可添加 Location 标签增加本地曝光",
        ],
        "reddit": [
            "标题要吸引人但不标题党（Reddit 用户反感标题党）",
            "建议选择对应 Subreddit（如 r/travel, r/JapanTravel）",
            "发布后积极回复评论，提高帖子权重",
        ],
    }.get(platform, ["按平台要求发布内容"])


def build_single_email(post: dict, minutes_left: int) -> tuple:
    """
    为单条内容构建精准提醒邮件（纯文本 + HTML）
    返回：(subject, text_body, html_body)
    优化 v3：嵌入文案正文、平台特定提示、配图 GitHub 链接
    """
    p_info = platform_info(post["platform"])
    now = get_now_bjt()

    # 时间计算
    publish_time = f"{post['date']} {post['time_bjt']}"
    time_display = f"{post['time_bjt']} BJT"

    # 读取文案内容
    content_text = read_content_file(post.get("content_file", ""))
    content_preview = content_text[:800] + ("..." if len(content_text) > 800 else "")

    # 配图 GitHub 链接
    image_github_link = ""
    if post.get("image_ref"):
        # image_ref 可能是文件名，需要找到实际路径
        # 简单处理：如果 content_file 存在，尝试在同目录找图片
        if post.get("content_file"):
            base_dir = os.path.dirname(post["content_file"])
            image_path = os.path.join(base_dir, post["image_ref"])
            image_github_link = build_github_link(image_path)
        else:
            image_github_link = build_github_link(f"drafts/{post['image_ref']}")

    # 文案文件 GitHub 链接
    content_github_link = build_github_link(post.get("content_file", ""))

    # 平台提示
    tips = platform_tips(post["platform"])

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
        f"🏷️ 类型：{post['type']}",
        f"🆔 ID：{post['id']}",
        "",
    ]
    if post.get("hashtags"):
        text_lines.append(f"🏷️ 标签：{post['hashtags']}")
    if post.get("content_file"):
        text_lines.append(f"📂 内容文件：{post['content_file']}")
        text_lines.append(f"🔗 GitHub链接：{content_github_link}")
    if post.get("image_ref"):
        text_lines.append(f"🖼️ 配图：{post['image_ref']}")
        if image_github_link:
            text_lines.append(f"🔗 配图链接：{image_github_link}")
    text_lines.extend([
        "",
        "-" * 50,
        "  📄 文案内容预览：",
        "-" * 50,
        content_preview,
        "-" * 50,
        "",
        "📌 平台特定提示：",
    ])
    for i, tip in enumerate(tips, 1):
        text_lines.append(f"  {i}. {tip}")
    text_lines.extend([
        "",
        "=" * 50,
        "  📋 操作清单：",
        "     □ 打开对应平台并登录",
        "     □ 复制上方文案内容",
        "     □ 上传配图（见上方链接）",
        "     □ 检查标签/话题格式",
        "     □ 发布后更新 content_calendar.json 状态为「已发布」",
        "=" * 50,
    ])
    text_body = "\n".join(text_lines)

    # ===== HTML 版本 =====
    type_color = type_style(post["type"])
    tips_html = "\n".join(f"<li>{tip}</li>" for tip in tips)

    # 文案内容 HTML（代码块样式）
    content_html = ""
    if content_preview:
        # 转义 HTML 特殊字符
        escaped = content_preview.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        content_html = f"""
    <div class="section">
      <div class="section-title">📄 文案内容 <span class="platform-badge" style="background:#f0f0f0;color:#666;">可复制</span></div>
      <div class="content-block">{escaped}</div>
      {f'<div class="file-link"><a href="{content_github_link}" target="_blank">🔗 在 GitHub 查看完整文件</a></div>' if content_github_link else ''}
    </div>"""

    # 配图信息 HTML
    image_html = ""
    if post.get("image_ref"):
        img_link_html = f'<a href="{image_github_link}" target="_blank">🔗 GitHub 查看</a>' if image_github_link else ''
        image_html = f"""
    <div class="section">
      <div class="section-title">🖼️ 配图</div>
      <div class="info-row">
        <div class="info-label">文件名</div>
        <div class="info-value">{post['image_ref']}</div>
      </div>
      {f'<div class="info-row"><div class="info-label">链接</div><div class="info-value">{img_link_html}</div></div>' if img_link_html else ''}
    </div>"""

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  body {{ margin:0; padding:16px; background:#f0f2f5; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; }}
  .container {{ max-width:640px; margin:0 auto; background:#fff; border-radius:16px; overflow:hidden; box-shadow:0 4px 24px rgba(0,0,0,0.10); }}
  .header {{ background:linear-gradient(135deg,#0D47A1,#1565C0); color:#fff; padding:24px 24px 20px; text-align:center; }}
  .header-emoji {{ font-size:40px; margin-bottom:8px; }}
  .header-title {{ font-size:22px; font-weight:700; margin-bottom:4px; letter-spacing:0.5px; }}
  .header-sub {{ font-size:13px; opacity:0.85; }}
  .countdown {{ background:linear-gradient(135deg,#E3F2FD,#BBDEFB); padding:24px; text-align:center; border-bottom:1px solid #e0e0e0; }}
  .countdown-number {{ font-size:56px; font-weight:800; color:#0D47A1; line-height:1; letter-spacing:-2px; }}
  .countdown-label {{ font-size:14px; color:#1565C0; margin-top:6px; font-weight:500; }}
  .content {{ padding:0 0 20px; }}
  .info-card {{ margin:16px 20px 0; background:#f8f9fa; border-radius:12px; padding:16px 18px; border:1px solid #e8eaed; }}
  .info-row {{ display:flex; align-items:center; padding:8px 0; border-bottom:1px solid #eee; }}
  .info-row:last-child {{ border-bottom:none; }}
  .info-label {{ width:72px; font-size:12px; color:#999; flex-shrink:0; font-weight:500; }}
  .info-value {{ font-size:14px; color:#333; flex:1; line-height:1.5; }}
  .platform-badge {{ display:inline-block; background:{p_info['color']}; color:#fff; padding:4px 12px; border-radius:12px; font-size:13px; font-weight:600; margin-right:6px; }}
  .type-badge {{ display:inline-block; background:{type_color}; color:#fff; padding:3px 10px; border-radius:8px; font-size:11px; font-weight:500; }}
  .publish-time {{ background:#fff3cd; border:1px solid #ffecb5; border-radius:8px; margin:16px 20px 0; padding:12px 16px; font-size:14px; color:#856404; text-align:center; font-weight:500; }}
  .section {{ margin:16px 20px 0; }}
  .section-title {{ font-size:14px; font-weight:600; color:#0D47A1; margin-bottom:10px; display:flex; align-items:center; gap:6px; }}
  .content-block {{ background:#1e1e1e; color:#d4d4d4; padding:16px; border-radius:8px; font-family:'Courier New',monospace; font-size:12px; line-height:1.6; white-space:pre-wrap; word-break:break-all; max-height:260px; overflow-y:auto; }}
  .file-link {{ margin-top:8px; font-size:12px; }}
  .file-link a {{ color:#0D47A1; text-decoration:none; }}
  .file-link a:hover {{ text-decoration:underline; }}
  .tips-card {{ margin:16px 20px 0; background:#E3F2FD; border:1px solid #90CAF9; border-radius:12px; padding:14px 18px; }}
  .tips-title {{ font-size:13px; font-weight:600; color:#1565C0; margin-bottom:8px; }}
  .tips-card ol {{ margin:0; padding-left:20px; color:#333; font-size:13px; line-height:1.8; }}
  .checklist {{ margin:16px 20px 0; background:#f1f8e9; border:1px solid #aed581; border-radius:12px; padding:14px 18px; }}
  .checklist-title {{ font-size:13px; font-weight:600; color:#33691e; margin-bottom:8px; }}
  .checklist ul {{ margin:0; padding-left:20px; color:#333; font-size:13px; line-height:1.8; list-style:none; padding-left:0; }}
  .checklist li {{ padding:2px 0; }}
  .checklist li:before {{ content:""; }}
  .footer {{ text-align:center; padding:16px; font-size:11px; color:#999; border-top:1px solid #eee; margin-top:16px; }}
  .tag-list {{ font-size:12px; color:#666; line-height:1.6; word-break:break-all; }}
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

    <div class="info-card">
      <div class="info-row">
        <div class="info-label">平台</div>
        <div class="info-value"><span class="platform-badge">{p_info['emoji']} {p_info['name']}</span></div>
      </div>
      <div class="info-row">
        <div class="info-label">标题</div>
        <div class="info-value">{post['title']} <span class="type-badge">{post['type']}</span></div>
      </div>
      <div class="info-row">
        <div class="info-label">ID</div>
        <div class="info-value">#{post['id']}</div>
      </div>"""

    if post.get("hashtags"):
        html += f"""
      <div class="info-row">
        <div class="info-label">标签</div>
        <div class="info-value"><div class="tag-list">{post['hashtags']}</div></div>
      </div>"""

    if post.get("content_file"):
        html += f"""
      <div class="info-row">
        <div class="info-label">文案文件</div>
        <div class="info-value" style="font-size:12px;word-break:break-all;">{post['content_file']}</div>
      </div>"""

    html += """
    </div>""" + content_html + image_html

    html += f"""
    <div class="tips-card">
      <div class="tips-title">📌 平台特定提示（{p_info['name']}）</div>
      <ol>
        {tips_html}
      </ol>
    </div>

    <div class="checklist">
      <div class="checklist-title">📋 发布前检查清单</div>
      <ul>
        <li>□ 打开 {p_info['name']} 并登录</li>
        <li>□ 复制上方文案内容（点击代码块可全选）</li>
        <li>□ 上传配图（格式/尺寸符合要求）</li>
        <li>□ 检查标签/话题格式</li>
        <li>□ 预览确认无误 → 发布</li>
        <li>□ 发布后更新 content_calendar.json 状态为「已发布」</li>
      </ul>
    </div>
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
        print(f"  [OK] 邮件发送成功 → {subject}")
        return True
    except Exception as e:
        print(f"  [ERROR] 邮件发送失败：{e}")
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
    print("  Kinatrip 精准发布提醒脚本 v3")
    print("  （嵌入文案内容 + 平台特定提示 + GitHub链接）")
    print("=" * 60)

    # 1. 检查环境变量
    if not SMTP_PWD or not SMTP_EMAIL or not TO_EMAIL:
        print("[ERROR] 缺少环境变量：")
        print(f"   SMTP_PWD   = {'已设置' if SMTP_PWD else '未设置'}")
        print(f"   SMTP_EMAIL = {'已设置' if SMTP_EMAIL else '未设置'}")
        print(f"   TO_EMAIL   = {'已设置' if TO_EMAIL else '未设置'}")
        print("   请通过 GitHub Secrets 设置以上变量")
        return

    # 2. 读取内容日历
    if not os.path.exists(CALENDAR_FILE):
        print(f"[ERROR] 日历文件不存在：{CALENDAR_FILE}")
        return
    calendar = load_json(CALENDAR_FILE)
    print("[OK] 已读取内容日历")

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
        print(f"   [MAIL] 发送提醒：{p_info['name']} - {post['title'][:30]}（{minutes_left}分钟）")

        subject, text_body, html_body = build_single_email(post, minutes_left)
        ok = send_email(subject, text_body, html_body, TO_EMAIL)

        if ok:
            update_tracker(tracker, post, "30min")
            sent_count += 1

    # 7. 保存 tracker
    if sent_count > 0:
        save_json(TRACKER_FILE, tracker)
        print(f"[OK] 已更新 tracker：{TRACKER_FILE}（{sent_count} 条）")

    print("=" * 60)
    print(f"  完成 — 本次发送 {sent_count} 条精准提醒")


if __name__ == "__main__":
    main()
