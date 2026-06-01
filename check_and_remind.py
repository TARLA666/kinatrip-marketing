#!/usr/bin/env python3
"""
Kinatrip 发布前提醒 — GitHub Actions 版
======================================
功能：每小时运行一次，检查排期表中是否有 30 分钟后发布的条目，通过 QQ 邮箱 SMTP 发送提醒。
文件存储：直接用 GitHub 仓库（schedule.md + reminder_tracker.json）
追踪器更新：通过 git commit & push 写回仓库

环境变量（在 GitHub Secrets 配置）：
  SMTP_PWD     — QQ 邮箱 SMTP 授权码
  SMTP_EMAIL   — 发件邮箱（默认 ytityi@foxmail.com）
  TO_EMAIL     — 收件邮箱（默认 ytityi@foxmail.com）
  GITHUB_TOKEN — 自动提供，无需手动配置
"""

import os
import re
import json
import smtplib
import email.utils
import logging
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText

# 日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("kinatrip_reminder")

# ========== 配置（从环境变量读取） ==========

SMTP_EMAIL = os.environ.get("SMTP_EMAIL", "ytityi@foxmail.com")
SMTP_PWD = os.environ.get("SMTP_PWD", "")
TO_EMAIL = os.environ.get("TO_EMAIL", "ytityi@foxmail.com")
SMTP_SERVER = "smtp.qq.com"
SMTP_PORT = 465

# 仓库内文件路径（GitHub Actions 中工作目录就是仓库根目录）
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SCHEDULE_PATH = os.path.join(SCRIPT_DIR, "drafts/week-2026-06-08/schedule.md")
TRACKER_PATH = os.path.join(SCRIPT_DIR, "reminder_tracker.json")

CURRENT_YEAR = 2026


# ========== 排期表解析 ==========

def parse_schedule(content):
    """解析排期表 Markdown 内容"""
    lines = content.split("\n")
    entries = []

    for line in lines:
        line = line.strip()
        if not line.startswith("|") or "---" in line or "日期" in line:
            continue

        if line.startswith("|"):
            line = line[1:]
        if line.endswith("|"):
            line = line[:-1]

        cols = [c.strip() for c in line.split("|")]

        date_col = cols[0] if cols else ""
        date_match = re.search(
            r"\*{0,2}(周一|周二|周三|周四|周五|周六|周日)\s+(\d+/\d+)\*{0,2}", date_col
        )
        if not date_match:
            continue

        _, date_str = date_match.groups()
        month, day = map(int, date_str.split("/"))

        try:
            if len(cols) >= 9:
                # 普通平台（9列）：日期 | EST | BJT | 平台 | 类型 | 标题 | 配图 | 内容 | 状态
                time_est_str = cols[1]
                platform = cols[3]
                content_type = cols[4]
                title = cols[5]
                image_method = cols[6]

                if ":" not in time_est_str or time_est_str == "—":
                    continue

                hour_est, minute_est = map(int, time_est_str.split(":"))
                publish_est = datetime(CURRENT_YEAR, month, day, hour_est, minute_est)

            elif len(cols) >= 8:
                # 小红书（8列）：日期 | BJT | 平台 | 类型 | 标题 | 配图 | 内容 | 状态
                time_bjt_str = cols[1]
                platform = cols[2]
                content_type = cols[3]
                title = cols[4]
                image_method = cols[5]

                if ":" not in time_bjt_str:
                    continue

                hour_bjt, minute_bjt = map(int, time_bjt_str.split(":"))
                publish_bjt = datetime(CURRENT_YEAR, month, day, hour_bjt, minute_bjt)
                publish_est = publish_bjt - timedelta(hours=12)

            else:
                continue

        except (ValueError, IndexError) as e:
            logger.warning(f"解析行失败: {e}, 行: {line[:60]}")
            continue

        entries.append({
            "platform": platform.strip(),
            "title": title.strip().strip('"'),
            "content_type": content_type.strip(),
            "image_method": image_method.strip(),
            "publish_est": publish_est,
            "day": date_str,
        })

    return entries


# ========== 匹配提醒条目 ==========

def find_due_reminders(entries, tracker, tolerance_minutes=3):
    """找出需要在当前时间发送提醒的条目"""
    now_utc = datetime.now(timezone.utc)
    due_entries = []

    for entry in entries:
        publish_est = entry["publish_est"]
        reminder_est = publish_est - timedelta(minutes=30)
        reminder_utc = reminder_est.replace(tzinfo=timezone.utc) - timedelta(hours=4)

        diff = abs((now_utc - reminder_utc).total_seconds())
        if diff > tolerance_minutes * 60:
            continue

        platform_clean = entry["platform"]
        track_key = f"{entry['day']}-{publish_est.strftime('%H%M')}-{platform_clean}"

        sent = tracker.get("reminders_sent", {}).get(track_key)
        if sent:
            logger.info(f"跳过已发送的提醒: {track_key}")
            continue

        entry["track_key"] = track_key
        due_entries.append(entry)

    return due_entries


# ========== SMTP 邮件发送 ==========

def build_email_body(entry):
    """构建 HTML 邮件正文"""
    publish_est = entry["publish_est"]
    platform_clean = entry["platform"]
    title_clean = entry["title"]
    content_type_clean = entry["content_type"]
    image_method_clean = entry["image_method"]

    if "小红书" in platform_clean:
        bjt_display = publish_est.strftime("%H:%M")
    else:
        bjt_display = (publish_est + timedelta(hours=12)).strftime("%H:%M")

    est_display = publish_est.strftime("%H:%M")

    return f"""<html>
<body style="font-family: -apple-system, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333;">
<div style="max-width: 600px; margin: 0 auto; padding: 20px;">
<div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); border-radius: 12px; padding: 30px; text-align: center; margin-bottom: 24px;">
  <h1 style="color: #fff; margin: 0; font-size: 24px;">⏰ Kinatrip 发布提醒</h1>
  <p style="color: rgba(255,255,255,0.9); margin: 8px 0 0; font-size: 15px;">请在 30 分钟内完成发布</p>
</div>

<table style="width: 100%; border-collapse: collapse; margin-bottom: 24px;">
  <tr><td style="padding: 10px 16px; background: #f8f9fa; border-bottom: 1px solid #eee;"><strong>平台</strong></td><td style="padding: 10px 16px;">{platform_clean}</td></tr>
  <tr><td style="padding: 10px 16px; background: #f8f9fa; border-bottom: 1px solid #eee;"><strong>内容类型</strong></td><td style="padding: 10px 16px;">{content_type_clean}</td></tr>
  <tr><td style="padding: 10px 16px; background: #f8f9fa; border-bottom: 1px solid #eee;"><strong>标题</strong></td><td style="padding: 10px 16px;">{title_clean}</td></tr>
  <tr><td style="padding: 10px 16px; background: #f8f9fa; border-bottom: 1px solid #eee;"><strong>发布时间 (EST)</strong></td><td style="padding: 10px 16px;">{est_display}</td></tr>
  <tr><td style="padding: 10px 16px; background: #f8f9fa; border-bottom: 1px solid #eee;"><strong>发布时间 (BJT)</strong></td><td style="padding: 10px 16px;">{bjt_display}</td></tr>
  <tr><td style="padding: 10px 16px; background: #f8f9fa; border-bottom: 1px solid #eee;"><strong>配图方式</strong></td><td style="padding: 10px 16px;">{image_method_clean}</td></tr>
</table>

<div style="background: #fff3cd; border: 1px solid #ffeeba; border-radius: 8px; padding: 16px; color: #856404; font-size: 14px;">
  <strong>💡 发布提示：</strong><br>
  • 配图在 <code>drafts/week-*/final/</code> 目录<br>
  • 完整文案见 <code>schedule.md</code> 对应行<br>
  • Reddit 需保持真实用户语气，避免硬广
</div>

<p style="margin-top: 20px; color: #999; font-size: 12px; text-align: center;">
  Kinatrip 自动发布提醒 · 由 GitHub Actions 驱动
</p>
</div>
</body>
</html>"""


def send_email(subject, html_body):
    """通过 QQ 邮箱 SMTP 发送 HTML 邮件"""
    if not SMTP_PWD:
        logger.error("SMTP_PWD 未配置，跳过邮件发送")
        return False

    msg = MIMEText(html_body, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = email.utils.formataddr(("Kinatrip 提醒助手", SMTP_EMAIL))
    msg["To"] = TO_EMAIL
    msg["Date"] = email.utils.formatdate(localtime=True)

    try:
        server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, timeout=30)
        server.login(SMTP_EMAIL, SMTP_PWD)
        server.sendmail(SMTP_EMAIL, [TO_EMAIL], msg.as_string())
        server.quit()
        logger.info(f"邮件发送成功: {subject}")
        return True
    except Exception as e:
        logger.error(f"邮件发送失败: {e}")
        return False


# ========== 追踪器 ==========

def load_tracker():
    """加载提醒追踪器"""
    try:
        with open(TRACKER_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logger.info(f"追踪器文件不存在，创建新文件: {TRACKER_PATH}")
        return {"reminders_sent": {}, "last_check": None, "note": "key: 日期-时间(EST)-平台"}
    except json.JSONDecodeError as e:
        logger.warning(f"解析 tracker 失败，重建: {e}")
        return {"reminders_sent": {}, "last_check": None, "note": "key: 日期-时间(EST)-平台"}


def save_tracker(tracker):
    """保存追踪器并 git push"""
    with open(TRACKER_PATH, "w", encoding="utf-8") as f:
        json.dump(tracker, f, ensure_ascii=False, indent=2)

    # git commit & push
    try:
        import subprocess
        subprocess.run(["git", "config", "user.email", SMTP_EMAIL], check=True)
        subprocess.run(["git", "config", "user.name", "Kinatrip Reminder Bot"], check=True)
        subprocess.run(["git", "add", "reminder_tracker.json"], check=True)
        subprocess.run(["git", "commit", "-m", "Update reminder tracker [bot]"], check=True)
        subprocess.run(["git", "push"], check=True)
        logger.info("追踪器已更新并推送回仓库")
    except Exception as e:
        logger.warning(f"git push 失败（邮件已发送): {e}")


# ========== 主逻辑 ==========

def check_and_send_reminders():
    """主流程：读取 → 解析 → 匹配 → 发送 → 更新追踪"""
    now_utc = datetime.now(timezone.utc)
    logger.info(f"=== Kinatrip 提醒检查开始 [UTC: {now_utc.strftime('%Y-%m-%d %H:%M')}] ===")

    # 读取排期表
    try:
        with open(SCHEDULE_PATH, "r", encoding="utf-8") as f:
            schedule_content = f.read()
    except FileNotFoundError:

        logger.warning(f"排期表未找到: {SCHEDULE_PATH}，跳过本次检查")
        return

    # 解析排期表
    entries = parse_schedule(schedule_content)
    logger.info(f"解析到 {len(entries)} 条发布计划")

    if not entries:
        logger.info("没有有效的发布计划，跳过")
        return

    # 加载追踪器
    tracker = load_tracker()

    # 查找需要提醒的条目
    due_entries = find_due_reminders(entries, tracker)
    logger.info(f"找到 {len(due_entries)} 个需要提醒的条目")

    if not due_entries:
        tracker["last_check"] = now_utc.isoformat()
        save_tracker(tracker)
        logger.info("没有到期提醒")
        return

    # 逐个发送提醒
    sent_count = 0
    for entry in due_entries:
        subject = f"⏰ [Kinatrip发布提醒] {entry['platform']} - {entry['title']}"
        html_body = build_email_body(entry)

        success = send_email(subject, html_body)
        if success:
            tracker["reminders_sent"][entry["track_key"]] = now_utc.isoformat()
            sent_count += 1
            logger.info(f"✓ 已发送提醒: {entry['track_key']}")
        else:
            logger.error(f"✗ 发送失败: {entry['track_key']}")

    # 更新追踪器
    tracker["last_check"] = now_utc.isoformat()
    save_tracker(tracker)

    logger.info(f"=== 完成: 成功发送 {sent_count}/{len(due_entries)} 条提醒 ===")


if __name__ == "__main__":
    print("=" * 60)
    print("Kinatrip 提醒 — GitHub Actions 版")
    print("=" * 60)
    print(f"SMTP_EMAIL: {SMTP_EMAIL}")
    print(f"TO_EMAIL: {TO_EMAIL}")
    print(f"SMTP_PWD: {'已配置' if SMTP_PWD else '未配置!'}")
    print(f"Schedule: {SCHEDULE_PATH}")
    print()
    check_and_send_reminders()
