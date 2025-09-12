# Grafana 告警设置导入（联系点/通知策略）— 快速上手

适用脚本：tools/import_alert_settings.py
目标：从本仓库的 YAML 将联系点（Contact Points）与通知策略（Notification Policies）导入 Grafana，并确保“可在 UI 编辑”。

—

## 1. 必备条件
- Python 3.10+，PowerShell（Windows）。
- 依赖：requests、PyYAML（随 requirements.txt 安装）。
- Grafana 账号具备 Unified Alerting 管理权限。

环境变量（至少设置以下几项）：
- GRAFANA_URL：如 https://your-grafana/
- GRAFANA_USER / GRAFANA_PASSWORD：用于 Basic 认证
- VERIFY_ENV：false 表示跳过证书校验（测试可用；生产应开启校验）
- GRAFANA_TIMEOUT：HTTP 超时（秒），默认 60

—

## 2. 一键命令（PowerShell）
切到项目根目录：
```
cd c:\work\project\grafana
```
导入联系点（Contact Points）：
```
$env:VERIFY_ENV="false"; python -u tools\import_alert_settings.py --contact-points alert\setting\supplier-contact-points.yaml
```
导入通知策略（Policies）：
```
$env:VERIFY_ENV="false"; python -u tools\import_alert_settings.py --notification-policies alert\setting\supplier-notification-policies.yaml
```
同时导入两者：
```
$env:VERIFY_ENV="false"; python -u tools\import_alert_settings.py --contact-points alert\setting\supplier-contact-points.yaml --notification-policies alert\setting\supplier-notification-policies.yaml
```

—

## 3. 输入文件结构速记
- 联系点：根键 contactPoints（列表）
  - 每项：name，receivers（数组，每个含 type, settings, 可选 uid, 可选 disableResolveMessage）
- 策略：根键 policies（字典或列表；最终 PUT 需要“策略树对象”本体）

—

## 4. 成功判定与输出
- HTTP 200/201/202 视为成功。
- 联系点示例输出：
  - Updated ContactPoint receiver: name=<n>, type=<t> -> uid=<uid>
  - ContactPoints result: created=X, updated=Y
- 策略示例输出：
  - Updated Notification Policies successfully

—

## 5. 常见问题速查
- 400：请求体结构不符 → 联系点按“receiver 对象”POST/PUT；策略 PUT 直接发送“策略树对象”（不要再包 policies 外壳）。
- 401/403：凭据/权限问题 → 校验 GRAFANA_USER/PASSWORD 与组织上下文。
- 5xx 或重试错误：Grafana 端异常 → 稍后重试或减小批量。
- UI 不可编辑：需带 X-Disable-Provenance=true（脚本已内置）。
- YAML 报 !!value 等标签：脚本内置宽松 Loader，可忽略未知标签。

—

## 6. 备份与回滚
- 备份：
  - GET /api/v1/provisioning/contact-points
  - GET /api/v1/provisioning/policies
- 回滚：用备份 JSON 作为请求体直接覆盖即可。

—

## 7. 实战建议
- 测试环境可设 VERIFY_ENV=false；生产应开启证书校验。
- 初次导入建议先执行联系点，再导入策略；导入后在 UI 检查可见且可编辑。
- 大批量变更建议分批执行，或先 dry-run（后续可扩展）。