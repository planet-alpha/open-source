import os
import sys
import json
import yaml
from typing import Any, Dict, List

# 输入目录: c:\work\project\grafana\alert\instance
# 输出文件: c:\work\project\grafana\out\provisioning_rules.json (file provisioning 格式)
#          c:\work\project\grafana\out\api_rules.json (API 创建规则所需的逐条规则数组)

INSTANCE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'alert', 'instance'))
OUT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'out'))
FILE_PROVISION_PATH = os.path.join(OUT_DIR, 'provisioning_rules.json')
API_RULES_PATH = os.path.join(OUT_DIR, 'api_rules.json')

# 默认文件夹名称与组名处理
DEFAULT_FOLDER = 'Business-Auto'


def ensure_out_dir():
    os.makedirs(OUT_DIR, exist_ok=True)


def load_yaml_files(directory: str) -> List[Dict[str, Any]]:
    docs = []
    for fname in sorted(os.listdir(directory)):
        if not fname.endswith('.yaml') and not fname.endswith('.yml'):
            continue
        path = os.path.join(directory, fname)
        with open(path, 'r', encoding='utf-8') as f:
            try:
                docs.append(yaml.safe_load(f))
            except Exception as e:
                print(f"YAML parse error in {fname}: {e}", file=sys.stderr)
                raise
    return docs


def normalize_groups(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    # 支持两种结构：
    # 1) 完整 provisioning 结构: { apiVersion: 1, groups: [ {name, folder, interval, rules: [...]}, ... ] }
    # 2) 仅 rules 组（某些文件可能只有单个组）
    if not doc:
        return []
    if 'groups' in doc and isinstance(doc['groups'], list):
        return doc['groups']
    # 如果没有 groups，尝试直接读取根级 keys（不太可能）
    return []


def to_file_provisioning(groups: List[Dict[str, Any]]) -> Dict[str, Any]:
    # 按 name/folder 聚合，保持 interval 与 rules
    result_groups: List[Dict[str, Any]] = []
    for g in groups:
        if not g:
            continue
        org_id = g.get('orgId', 1)
        name = g.get('name') or 'default'
        folder = g.get('folder') or DEFAULT_FOLDER
        interval = g.get('interval') or '1m'
        rules = g.get('rules') or []
        result_groups.append({
            'orgId': org_id,
            'name': name,
            'folder': folder,
            'interval': interval,
            'rules': rules,
        })
    return { 'apiVersion': 1, 'groups': result_groups }


def to_api_rules(groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    # 将 provisioning 组展开为 API 可用的逐条规则（POST /api/v1/provisioning/alert-rules）
    api_rules: List[Dict[str, Any]] = []
    for g in groups:
        if not g:
            continue
        folder = g.get('folder') or DEFAULT_FOLDER
        rule_group = g.get('name') or 'default'
        group_interval = g.get('interval') or '60s'
        rules = g.get('rules') or []
        for r in rules:
            api_rules.append({
                'title': r.get('title') or r.get('uid') or 'Unnamed',
                'ruleGroup': rule_group,
                'folder': folder,  # 临时字段，后续脚本会把 folder 名称映射成 folderUID
                'groupInterval': group_interval,  # 保留组评估间隔，导入时用于设置 evaluation interval
                'noDataState': r.get('noDataState', 'OK'),
                'execErrState': r.get('execErrState', 'Error'),
                'for': r.get('for', '0m'),
                'orgId': g.get('orgId', 1),
                'uid': r.get('uid', ''),  # 留空由服务器生成；若存在将尝试保持
                'condition': r.get('condition'),
                'annotations': r.get('annotations', {}),
                'labels': r.get('labels', {}),
                'data': r.get('data', []),
            })
    return api_rules


def main():
    ensure_out_dir()
    docs = load_yaml_files(INSTANCE_DIR)
    all_groups: List[Dict[str, Any]] = []
    for d in docs:
        all_groups.extend(normalize_groups(d))

    # 生成 file provisioning JSON
    file_prov = to_file_provisioning(all_groups)
    with open(FILE_PROVISION_PATH, 'w', encoding='utf-8') as f:
        json.dump(file_prov, f, ensure_ascii=False, indent=2)

    # 生成 API 规则数组 JSON
    api_rules = to_api_rules(all_groups)
    with open(API_RULES_PATH, 'w', encoding='utf-8') as f:
        json.dump(api_rules, f, ensure_ascii=False, indent=2)

    print(f"Wrote provisioning to: {FILE_PROVISION_PATH}")
    print(f"Wrote API rules to: {API_RULES_PATH}")


if __name__ == '__main__':
    main()