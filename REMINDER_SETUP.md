# Kinatrip 营销发布提醒系统

## 系统概述

```
GitHub Actions 定时触发
       ↓
check_and_remind.py（读取内容日历）
       ↓
判断今日/明日待发布内容（去重）
       ↓
通过 QQ 邮箱 SMTP 发送提醒邮件
       ↓
更新 reminder_tracker.json（防止重复提醒）
```

---

## 文件清单

| 文件 | 作用 |
|------|------|
| `content_calendar.json` | 结构化内容日历（日期/平台/标题/内容ID） |
| `check_and_remind.py` | 核心脚本：读日历 → 判断待发布 → SMTP 发信 |
| `test_smtp.py` | 本地测试脚本：验证 SMTP 配置是否正确 |
| `reminder_tracker.json` | 去重记录：已提醒的内容 ID + 时间戳 |
| `requirements.txt` | Python 依赖（本方案仅用标准库，无需安装） |
| `.github/workflows/kinatrip-reminder.yml` | GitHub Actions 工作流定义 |

---

## 一、配置 QQ 邮箱 SMTP

### 1. 获取 QQ 邮箱授权码

1. 登录 QQ 邮箱 → **设置** → **账户**
2. 找到 **POP3/IMAP/SMTP/Exchange/CardDAV/CalDAV服务**
3. 开启 **IMAP/SMTP服务**（如未开启）
4. 点击 **生成授权码**，用短信验证后获取 16 位授权码

> ⚠️ 授权码只显示一次，妥善保存！

### 2. 验证 SMTP 配置（本地测试）

```bash
# 设置环境变量（Linux/macOS）
export SMTP_PWD="你的QQ邮箱授权码"
export SMTP_EMAIL="123456789@qq.com"
export TO_EMAIL="接收提醒的邮箱"

# 运行测试
python test_smtp.py
```

Windows PowerShell：
```powershell
$env:SMTP_PWD="你的QQ邮箱授权码"
$env:SMTP_EMAIL="123456789@qq.com"
$env:TO_EMAIL="接收提醒的邮箱"
python test_smtp.py
```

---

## 二、配置 GitHub Secrets

进入你的 GitHub 仓库：

**Settings → Secrets and variables → Actions → New repository secret**

添加以下 3 个 Secrets：

| Secret 名称 | 说明 | 示例 |
|-------------|------|------|
| `SMTP_PWD` | QQ 邮箱授权码（16位） | `abcdEFGH1234wxyz` |
| `SMTP_EMAIL` | QQ 邮箱地址（发件人） | `123456789@qq.com` |
| `TO_EMAIL` | 接收提醒的邮箱地址 | `your@email.com` |

---

## 三、GitHub Actions 触发规则

Workflow 文件：`.github/workflows/kinatrip-reminder.yml`

**Cron 调度（UTC 时间）：**
```
0 22-23,0-14 * * *
```

对应北京时间（BJT = UTC+8）：
| UTC | BJT | 说明 |
|-----|-----|------|
| 22:00 | 06:00 | 早高峰开始 |
| 23:00 | 07:00 | |
| 00:00 | 08:00 | |
| 01:00 | 09:00 | Facebook/X/Reddit 活跃时段 |
| 02:00 | 10:00 | X 活跃时段 |
| 03:00 | 11:00 | |
| 04:00 | 12:00 | Reddit 发布时段 |
| 05:00 | 13:00 | |
| 06:00 | 14:00 | |
| 07:00 | 15:00 | Facebook 下午时段 |
| 08:00 | 16:00 | |
| 09:00 | 17:00 | |
| 10:00 | 18:00 | X 晚高峰 |
| 11:00 | 19:00 | |
| 12:00 | 20:00 | 小红书/Instagram 晚高峰 |
| 13:00 | 21:00 | X/Facebook 晚间时段 |
| 14:00 | 22:00 | 晚间结束 |

**手动触发**：仓库 → Actions → Kinatrip 发布提醒 → Run workflow

---

## 四、邮件内容说明

邮件包含两个部分：

### 【今日待发布】
- 今天各平台待发布内容列表
- 每条显示：发布时间 | 平台徽章 | 标题 | 类型

### 【明日预告】
- 明天各平台待发布内容列表
- 格式同上

### 去重机制
- `reminder_tracker.json` 记录已发送提醒的内容 ID
- 每个内容 ID 只会发送一次提醒
- 防止 GitHub Actions 重跑导致重复发信

---

## 五、内容日历维护

### 新增一周排期

编辑 `content_calendar.json`，在 `weeks` 数组中添加新对象：

```json
{
  "week_start": "2026-06-15",
  "week_end": "2026-06-21",
  "theme": "欧洲旅行周 — 巴黎 → 罗马 → 柏林",
  "posts": [
    {
      "id": "08-mon-xiaohongshu",
      "date": "2026-06-15",
      "time_bjt": "07:30",
      "platform": "xiaohongshu",
      "type": "痛点共鸣",
      "title": "巴黎菜单看不懂？拍照翻译秒懂",
      "content_file": "drafts/week-2026-06-15/final/xiaohongshu/08-mon.md",
      "image_ref": "paris_menu_1.jpg",
      "hashtags": "#跨境旅游 #巴黎旅游 #Kinatrip",
      "status": "待发布"
    }
  ]
}
```

### 标记已发布

将对应内容的 `"status": "待发布"` 改为 `"已发布"`，脚本会自动跳过。

---

## 六、推送与激活

```bash
git add content_calendar.json check_and_remind.py test_smtp.py \
        reminder_tracker.json requirements.txt \
        .github/workflows/kinatrip-reminder.yml \
        REMINDER_SETUP.md

git commit -m "feat: 添加 GitHub Actions + SMTP 邮件提醒系统"
git push origin main
```

推送后：
1. 进入 GitHub 仓库 → **Actions** 选项卡
2. 看到 **Kinatrip 发布提醒** 工作流
3. 首次运行可能需要手动触发一次（Run workflow）验证配置

---

## 七、故障排查

| 现象 | 原因 | 解决方案 |
|------|------|----------|
| `535 Login Fail` | 授权码错误 | 重新生成 QQ 邮箱授权码，更新 Secret |
| `Connection refused` | 网络限制 | 检查服务器能否访问 `smtp.qq.com:465` |
| 未收到邮件 | 被当作垃圾邮件 | 检查垃圾邮件箱，将发件人加入白名单 |
| 重复收到提醒 | tracker 未更新 | 检查 `reminder_tracker.json` 是否被正确提交 |
| `requirements.txt` 安装失败 | 无第三方依赖 | 本方案不需要安装，忽略该步骤 |

---

*系统版本：v1.0 | 2026-06-01*
