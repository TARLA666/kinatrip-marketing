#!/usr/bin/env python3
"""
Kinatrip 发布前提醒 — 腾讯云函数 SCF 版
========================================
功能：每 30 分钟运行一次，检查排期表中是否有 30 分钟后发布的条目，通过 QQ 邮箱 SMTP 发送提醒。

触发方式：定时触发器 (0,30 * * * *)
依赖：cos-python-sdk-v5 (通过 requirements.txt 部署)

环境变量（在 SCF 控制台配置）：
  COS_SECRET_ID      — 腾讯云 API 密钥 ID
  COS_SECRET_KEY      — 腾讯云 API 密钥 Key
  COS_BUCKET          — COS 存储桶名称（默认 kinatrip-reminders）
  COS_REGION          — COS 存储桶所属地域（默认 ap-guangzhou）
  SMTP_PWD            — QQ 邮箱 SMTP 授权码
  SMTP_EMAIL          — 发件邮箱（默认 ytityi@foxmail.com）
  TO_EMAIL            — 收件邮箱（默认 ytityi@foxmail.com）
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

COS_SECRET_ID = os.environ.get("COS_SECRET_ID", "")
COS_SECRET_KEY = os.environ.get("COS_SECRET_KEY", "")
COS_BUCKET = os.environ.get("COS_BUCKET", "kinatrip-reminders")
COS_REGION = os.environ.get("COS_REGION", "ap-guangzhou")
SCHEDULE_FILE = os.environ.get("SCHEDULE_FILE", "schedule.md")
TRACKER_FILE = os.environ.get("TRACKER_FILE", "reminder_tracker.json")

SMTP_EMAIL = os.environ.get("SMTP_EMAIL", "ytityi@foxmail.com")
SMTP_PWD = os.environ.get("SMTP_PWD", "")
TO_EMAIL = os.environ.get("TO_EMAIL", "ytityi@foxmail.com")
SMTP_SERVER = "smtp.qq.com"
SMTP_PORT = 465  # SSL

# 当前基准年份（排期表通常不写年份）
CURRENT_YEAR = 2026


# ========== COS 文件存取 ==========

def _get_cos_client():
    """延迟导入 COS SDK，避免本地测试时缺少依赖报错"""
    from qcloud_cos import CosConfig, CosS3Client

    config = CosConfig(
        Region=COS_REGION,
        SecretId=COS_SECRET_ID,
        SecretKey=COS_SECRET_KEY,
    )
    return CosS3Client(config)


def download_text_from_cos(key):
    """从 COS 下载文本文件内容"""
    try:
        client = _get_cos_client()
        response = client.get_object(
            Bucket=COS_BUCKET,
            Key=key,
        )
        content = response["Body"].getvalue().decode("utf-8")
        logger.info(f"成功从 COS 下载: {key} ({len(content)} 字节)")
        return content
    except Exception as e:
        logger.error(f"下载 {key} 失败: {e}")
        return None


def upload_text_to_cos(key, text):
    """将文本内容上传到 COS"""
    try:
        client = _get_cos_client()
        client.put_object(
            Bucket=COS_BUCKET,
            Key=key,
            Body=text.encode("utf-8"),
        )
        logger.info(f"成功上传到 COS: {key} ({len(text)} 字节)")
        return True
    except Exception as e:
        logger.error(f"上传 {key} 失败: {e}")
        return False


# ========== 排期表解析 ==========

def parse_schedule(content):
    """
    解析排期表 Markdown 内容
    返回条目列表，每条包含：platform, title, publish_est, publish_bjt, content_type, image_method, day
    
    时间规则：
    - 非小红书：EST 列权威（EST = UTC-4）
    - 小红书：BJT 列权威，EST = BJT - 12小时
    """
    lines = content.split("\n")
    entries = []

    for line in lines:
        line = line.strip()
        if not line.startswith("|") or "---" in line or "日期" in line:
            continue

        # 移除首尾 |
        if line.startswith("|"):
            line = line[1:]
        if line.endswith("|"):
            line = line[:-1]

        cols = [c.strip() for c in line.split("|")]

        # 识别日期列（如 "周一 6/8" 或 "**周一 6/8**"）
        date_col = cols[0] if cols else ""
        date_match = re.search(
            r"\*{0,2}(周一|周二|周三|周四|周五|周六|周日)\s+(\d+/\d+)\*{0,2}", date_col
        )
        if not date_match:
            continue

        _, date_str = date_match.groups()
        month, day = map(int, date_str.split("/"))

        try:
            if len(cols) == 9:
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

            elif len(cols) == 8:
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
                # BJT = EST + 12小时 => EST = BJT - 12
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
    """
    找出需要在当前时间发送提醒的条目
    
    规则：
    1. 提醒时间 = 发布时间(EST) - 30分钟
    2. 计算提醒时间的 UTC 值（EST = UTC-4）
    3. 匹配当前 UTC 时间 ± tolerance_minutes
    
    返回需要发送提醒的条目列表
    """
    now_utc = datetime.now(timezone.utc)
    due_entries = []

    for entry in entries:
        publish_est = entry["publish_est"]
        # 提醒时间 = EST 发布时间 - 30分钟
        reminder_est = publish_est - timedelta(minutes=30)
        # 转换为 UTC（EST = UTC-4, EDT = UTC-4, 固定使用 -4）
        reminder_utc = reminder_est.replace(tzinfo=timezone.utc) - timedelta(hours=4)

        # 检查时间窗口
        diff = abs((now_utc - reminder_utc).total_seconds())
        if diff > tolerance_minutes * 60:
            continue

        # 计算 tracker key
        platform_clean = entry["platform"]
        track_key = f"{entry['day']}-{publish_est.strftime('%H%M')}-{platform_clean}"

        # 检查是否已发送
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

    # BJT 显示逻辑
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
  Kinatrip 自动发布提醒 · 由腾讯云函数 (SCF) 驱动
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


# ========== 提醒追踪器 ==========

def load_tracker():
    """从 COS 加载提醒追踪器"""
    content = download_text_from_cos(TRACKER_FILE)
    if content:
        try:
            return json.loads(content)
        except json.JSONDecodeError as e:
            logger.warning(f"解析 tracker 失败，重建: {e}")
    
    return {"reminders_sent": {}, "last_check": None, "note": "key: 日期-时间(EST)-平台"}


def save_tracker(tracker):
    """保存提醒追踪器到 COS"""
    content = json.dumps(tracker, ensure_ascii=False, indent=2)
    return upload_text_to_cos(TRACKER_FILE, content)


# ========== 主逻辑 ==========

def check_and_send_reminders():
    """主流程：下载 → 解析 → 匹配 → 发送 → 更新追踪"""
    now_utc = datetime.now(timezone.utc)
    logger.info(f"=== Kinatrip 提醒检查开始 [UTC: {now_utc.strftime('%Y-%m-%d %H:%M')}] ===")

    # 1. 下载排期表
    schedule_content = download_text_from_cos(SCHEDULE_FILE)
    if not schedule_content:
        logger.warning(f"排期表 {SCHEDULE_FILE} 未找到，跳过本次检查")
        return

    # 2. 解析排期表
    entries = parse_schedule(schedule_content)
    logger.info(f"解析到 {len(entries)} 条发布计划")

    if not entries:
        logger.info("没有有效的发布计划，跳过")
        return

    # 3. 加载追踪器
    tracker = load_tracker()

    # 4. 查找需要提醒的条目
    due_entries = find_due_reminders(entries, tracker)
    logger.info(f"找到 {len(due_entries)} 个需要提醒的条目")

    if not due_entries:
        tracker["last_check"] = now_utc.isoformat()
        save_tracker(tracker)
        logger.info("没有到期提醒")
        return

    # 5. 逐个发送提醒
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

    # 6. 更新追踪器
    tracker["last_check"] = now_utc.isoformat()
    save_tracker(tracker)

    logger.info(f"=== 完成: 成功发送 {sent_count}/{len(due_entries)} 条提醒 ===")


# ========== SCF 入口 ==========

def main_handler(event, context):
    """腾讯云函数入口"""
    logger.info("SCF 函数触发: event=%s", json.dumps(event, ensure_ascii=False))
    try:
        check_and_send_reminders()
        return {"code": 0, "message": "success"}
    except Exception as e:
        logger.exception("SCF 执行异常")
        return {"code": 1, "message": str(e)}


# ========== 本地测试入口 ==========

if __name__ == "__main__":
    # 本地测试时需设置环境变量
    print("=" * 60)
    print("Kinatrip 提醒 SCF — 本地测试模式")
    print("=" * 60)
    print(f"COS_BUCKET: {COS_BUCKET}")
    print(f"SMTP_EMAIL: {SMTP_EMAIL}")
    print(f"TO_EMAIL: {TO_EMAIL}")
    print(f"SMTP_PWD: {'已配置' if SMTP_PWD else '未配置!'}")
    print()

    check_and_send_reminders()
