# Grafana 告警规则导入与组间隔更新 — 快速上手

适用脚本：tools/import_rules_to_grafana.py
目标：将 alert/*.yaml 转换并导入为 Grafana 告警规则，随后统一更新各规则组的 interval（对齐调度步长）。

—

## 1. 必备条件
- Python 3.8+，PowerShell（Windows）或任意 Shell。
- 依赖：requests（随 requirements.txt 安装）。
- Grafana 9+，开启 Unified Alerting Provisioning API，具备相应权限。

关键环境变量：
- GRAFANA_URL：https://your-grafana/
- GRAFANA_API_TOKEN（优先）或 GRAFANA_USER/GRAFANA_PASSWORD
- GRAFANA_VERIFY：true/false（生产建议 true）
- GRAFANA_CA_CERT：自定义 CA 证书（可选）
- GRAFANA_TIMEOUT：请求超时（秒），默认 60

—

## 2. 标准流程
1) 安装依赖：
```
pip install -r requirements.txt
```
2) 生成输入 JSON：
```
python -u tools\convert_yaml_to_grafana_json.py
# 产物：out\api_rules.json
```
3) 执行导入：
```
python -u tools\import_rules_to_grafana.py
```

—

## 3. 规则与组间隔逻辑
- 以 (folder, ruleGroup) 分桶导入；规则存在且含 uid → PUT 更新；否则 POST 创建。
- 组间隔（groupInterval）会被规范化为“整数秒”，并向上对齐调度步长（默认 10s），避免 400。
- 仅在组内 rules 可见后，再统一 PUT 更新组 interval，避免空组 PUT 失败。

—

## 4. 常见输出与成功判定
- Imported/Updated: <规则标题> -> uid=<uid>
- Warning: update_rule_group_interval 组名 failed after retries: 400 {...}
- 返回码：0 成功，非 0 失败。

—

## 5. 常见问题速查
- 400：interval=0 或非整除 → 调整 groupInterval 或依赖脚本自动对齐。
- 401/403：认证/权限不足 → 校验 Token 或账号权限。
- 404：目标组/文件夹未准备好 → 稍后重试，或确认 convert/导入顺序。
- 409：规则已存在冲突 → 应包含 uid 以执行 PUT 更新。
- 429/5xx：重试后仍失败 → 检查限流与网络。

—

## 6. 实战建议
- 优先使用 API Token；尽量开启证书校验（GRAFANA_VERIFY=true）。
- 同名组在不同 Folder 会分别导入；注意源 YAML 的 folder 与 ruleGroup 一致性。
- 初次导入建议备份目标环境的现有规则与组配置，以便回滚。