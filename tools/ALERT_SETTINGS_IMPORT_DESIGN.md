# Grafana 统一告警设置导入脚本（联系点与通知策略）设计文档

本文档聚焦 tools/import_alert_settings.py，面向以“可在 UI 编辑”的方式导入联系点（Contact Points）与通知策略（Notification Policies）的脚本实现、原理与运维实践。

---

## 目录
- 1. 脚本设计思路与解释
  - 1.1 总体架构设计
  - 1.2 核心功能模块说明
  - 1.3 关键算法逻辑阐述
  - 1.4 数据流与处理流程
- 2. 使用说明
  - 2.1 环境要求与依赖项
  - 2.2 安装部署步骤
  - 2.3 配置参数说明
  - 2.4 执行方法与命令格式
  - 2.5 预期输出示例
- 3. 注意事项
  - 3.1 系统资源要求
  - 3.2 权限与安全考量
  - 3.3 性能优化建议
  - 3.4 兼容性说明
- 4. 异常处理与意外终止情况
  - 4.1 常见错误代码及解决方案
  - 4.2 日志分析与问题定位
  - 4.3 自动恢复机制
  - 4.4 灾难恢复方案
  - 4.5 资源释放与清理流程
- 附：实际运行示例

---

## 1. 脚本设计思路与解释

### 1.1 总体架构设计
- 目标：
  - 从 YAML 配置独立导入联系点与通知策略，导入后在 Grafana UI 中可编辑。
  - 不改变现有目录结构，保持文件路径不变。
- 设计原则：
  - 对齐 Grafana Unified Alerting Provisioning HTTP API 的对象模型与幂等语义。
  - 明确“文件格式”与“HTTP API 请求体格式”的差异。
  - 宽松 YAML 解析，容忍如 `!!value` 等自定义标签。
  - 使用 `X-Disable-Provenance: true` 以创建 UI 可编辑资源。
- 组件构成：
  - 入口与参数解析：`--contact-points`、`--notification-policies`。
  - 会话与 URL：复用现有 `get_auth_session` 与 `_url`，统一认证与 TLS 验证逻辑。
  - YAML 适配层：自定义宽松 Loader，忽略未知标签。
  - 导入器：
    - Contact Points：按接收器（receiver）粒度 POST/PUT。
    - Notification Policies：直接 PUT 策略树对象。

### 1.2 核心功能模块说明
- 会话与 URL 构造：
  - 继承规则导入脚本的会话配置，统一超时、TLS 验证、基础 URL。
- YAML 加载模块：
  - 自定义 Permissive Loader：忽略未知标签（如 `!!value`），按基础类型构造，避免解析中断。
- Contact Points 导入：
  - 输入键：`contactPoints`（列表），包含 `name` 与 `receivers`（每项含 `type`,`settings`,`disableResolveMessage`, 可选 `uid`）。
  - 现状对齐：先 GET 全量列表，建立 `by_uid` 与 `by_key (name,type)` 索引。
  - 幂等导入：命中目标则 PUT 更新，否则 POST 创建；将 200/201/202 视为成功。
- Notification Policies 导入：
  - 输入键：`policies`（字典或列表）。
  - 体格式：PUT `/api/v1/provisioning/policies` 接受的是“策略树对象”，若为列表取第一项，若为字典直接使用。

### 1.3 关键算法逻辑阐述
- 联系点匹配策略：
  1) 优先用 `receiver.uid` 匹配；
  2) 否则用 `(name, type)` 匹配；
  3) 命中 -> PUT `/contact-points/{uid}`；未命中 -> POST `/contact-points`。
- 请求头策略：
  - 所有写操作均带 `X-Disable-Provenance: true` 与 `Content-Type: application/json`。
- 成功语义：
  - 通用接受 200/201/202。
- YAML 宽松解析：
  - 对未知标签节点分别按 Scalar/Sequence/Mapping 基础构造，维持数据结构正确性。

### 1.4 数据流与处理流程
```
[读取 YAML] -> [宽松解析] -> [校验键名结构]
   ├─ 联系点：GET /contact-points -> 建索引(by_uid, by_key)
   │    └─ 遍历 receiver：匹配(uid 或 name+type) -> PUT 或 POST -> 记录结果
   └─ 策略：抽取 policies -> 取根策略对象 -> PUT /policies

[统计/打印结果] -> [在 UI 核验可见性与可编辑性]
```

---

## 2. 使用说明

### 2.1 环境要求与依赖项
- 运行环境：Windows PowerShell，Python 3.10+。
- 依赖：
  - `requests==2.32.3`
  - `PyYAML==6.0.2`
- Grafana：启用 Unified Alerting，具备 API 访问管理权限的账号。

### 2.2 安装部署步骤
1) 切换到项目根目录：`c:\work\project\grafana`
2) 安装依赖：`pip install -r requirements.txt`
3) 确认网络连通与凭据正确。

### 2.3 配置参数说明
- 环境变量：
  - `GRAFANA_URL`：Grafana 基础 URL（例如 `https://<grafana-host>/`）
  - `GRAFANA_USER`：用户名
  - `GRAFANA_PASSWORD`：密码
  - `VERIFY_ENV`：若设为 `false`，禁用 TLS 验证（测试时可用；生产应开启验证）
  - `GRAFANA_TIMEOUT`：HTTP 超时（秒），默认 60
- 命令行参数：
  - `--contact-points`：联系点 YAML 文件路径
  - `--notification-policies`：通知策略 YAML 文件路径
- 输入文件结构：
  - 联系点：根键 `contactPoints`（列表），每项含 `name` 与 `receivers`（receiver 含 `type`,`settings`, 可选 `uid`,`disableResolveMessage`）。
  - 策略：根键 `policies`（字典或列表）。

### 2.4 执行方法与命令格式
- 导入联系点：
```powershell
cd c:\work\project\grafana
$env:VERIFY_ENV="false"; python -u tools\import_alert_settings.py --contact-points alert\setting\supplier-contact-points.yaml
```
- 导入通知策略：
```powershell
cd c:\work\project\grafana
$env:VERIFY_ENV="false"; python -u tools\import_alert_settings.py --notification-policies alert\setting\supplier-notification-policies.yaml
```
- 同时导入两者：
```powershell
cd c:\work\project\grafana
$env:VERIFY_ENV="false"; python -u tools\import_alert_settings.py --contact-points alert\setting\supplier-contact-points.yaml --notification-policies alert\setting\supplier-notification-policies.yaml
```

### 2.5 预期输出示例
- 联系点：
```
Updated ContactPoint receiver: name=supplier-conn-drop, type=webhook -> uid=supplier-conn-drop-feishu
...
ContactPoints result: created=0, updated=6
```
- 通知策略：
```
Updated Notification Policies successfully
```

---

## 3. 注意事项

### 3.1 系统资源要求
- 导入过程以网络与 I/O 为主，CPU/内存占用较低。
- 大批量导入时建议分批执行（如 < 500 receivers/批）。

### 3.2 权限与安全考量
- 使用具备告警管理权限的账号。
- 凭据通过环境变量注入，不应写入仓库。
- 生产环境建议开启 TLS 验证（`VERIFY_ENV` 非 `false`，并正确配置 CA 证书）。

### 3.3 性能优化建议
- 已通过一次性 GET 列表+索引避免逐条远程查询。
- 如需更快导入，可引入受控并发（5~10），注意速率限制与后端负载。
- 幂等策略基于 `uid` 与 `(name,type)`，减少重复 POST。

### 3.4 兼容性说明
- 兼容 YAML 未知标签（如 `!!value`），不会因标签报错而中止。
- 兼容 Grafana 对 202 Accepted 的成功语义。
- 通过 `X-Disable-Provenance: true` 确保 UI 可编辑来源。

---

## 4. 异常处理与意外终止情况

### 4.1 常见错误代码及解决方案
- 400 Bad Request：请求体结构不符；检查必填字段与体格式（策略体不要再包裹 `{policies: ...}`）。
- 401/403：认证失败或权限不足；核对凭据与角色权限。
- 404 Not Found：API 路径或资源不存在；核对 Grafana 版本与基础 URL。
- 409 Conflict：资源/UID 冲突；优先尝试 PUT 更新或调整命名/UID。
- 412/415/422：请求头或体不符合预期；使用 `Content-Type: application/json` 并检查对象结构。
- 429 Too Many Requests：速率限制；降低并发、增加间隔。
- 5xx：服务端错误；稍后重试，或减小批量规模。

### 4.2 日志分析与问题定位
- 输出粒度：逐条 receiver 创建/更新结果（含 name/type/uid）、汇总统计、策略 PUT 成功/错误响应体。
- 诊断建议：若 4xx/5xx，优先查看响应 JSON；必要时对导入前后调用 GET 接口做差异核验。

### 4.3 自动恢复机制（建议）
- 幂等重试：对网络/5xx 错误进行有限次指数退避重试。
- 状态校验：导入后 GET 校对关键资源是否与预期一致。
- 差异执行：预留 `--inspect/--dry-run` 以降低误操作风险（可后续增强）。

### 4.4 灾难恢复方案
- 备份：
  - Contact Points：GET `/api/v1/provisioning/contact-points` 导出 JSON。
  - Notification Policies：GET `/api/v1/provisioning/policies` 导出 JSON。
- 回滚：使用备份 JSON 作为请求体直接覆盖恢复。
- 快照命名：建议以时间戳命名，便于定位目标版本。

### 4.5 资源释放与清理流程
- 脚本为短进程，无额外资源占用；会话自动释放。
- 建议将备份/导出文件集中管理并定期清理。

---

## 附：实际运行示例

### A. 联系点导入
```powershell
cd c:\work\project\grafana
$env:VERIFY_ENV="false"; python -u tools\import_alert_settings.py --contact-points alert\setting\supplier-contact-points.yaml
```
输出（节选）：
```
Updated ContactPoint receiver: name=supplier-conn-drop, type=webhook -> uid=supplier-conn-drop-feishu
Updated ContactPoint receiver: name=supplier-deliver, type=webhook -> uid=supplier-deliver-feishu
Updated ContactPoint receiver: name=supplier-latency, type=webhook -> uid=supplier-latency-feishu
Updated ContactPoint receiver: name=supplier-mo-tps, type=webhook -> uid=supplier-mo-tps-feishu
Updated ContactPoint receiver: name=supplier-submitresp-drop, type=webhook -> uid=supplier-submitresp-drop-feishu
Updated ContactPoint receiver: name=supplier-tps-surge, type=webhook -> uid=supplier-tps-surge-feishu
ContactPoints result: created=0, updated=6
```

### B. 通知策略导入
```powershell
cd c:\work\project\grafana
$env:VERIFY_ENV="false"; python -u tools\import_alert_settings.py --notification-policies alert\setting\supplier-notification-policies.yaml
```
输出：
```
Updated Notification Policies successfully
```