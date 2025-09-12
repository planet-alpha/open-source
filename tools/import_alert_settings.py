import os
import sys
import json
import argparse
from typing import Dict, Any, List, Optional

import yaml
import requests

# 自定义宽松Loader，忽略未知YAML标签（例如 !!value 等），按其基础节点类型构造
class _PermissiveLoader(yaml.SafeLoader):
    pass

def _unknown_tag_constructor(loader: yaml.SafeLoader, tag_suffix: str, node: yaml.Node):
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node)
    return None

# 捕获所有以 'tag:yaml.org,2002:' 开头的全局标签，以及本地 '!' 标签
_PermissiveLoader.add_multi_constructor('tag:yaml.org,2002:', _unknown_tag_constructor)
_PermissiveLoader.add_multi_constructor('!', _unknown_tag_constructor)

# 复用已有的鉴权会话与URL构造
try:
    from tools.import_rules_to_grafana import get_auth_session, _url
except Exception:
    # 兼容从工具目录直接执行
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
    from tools.import_rules_to_grafana import get_auth_session, _url  # type: ignore

# 端点常量（Unified Alerting Provisioning HTTP API）
CONTACT_POINTS_API = 'api/v1/provisioning/contact-points'
CONTACT_POINT_BY_UID_API = 'api/v1/provisioning/contact-points/{uid}'
NOTIFICATION_POLICIES_API = 'api/v1/provisioning/policies'

# 环境变量（与规则导入脚本保持一致）
DEFAULT_BASE_URL = os.environ.get('GRAFANA_URL', 'https://monitor-test.planet-alpha.net/')
DEFAULT_USER = os.environ.get('GRAFANA_USER', 'admin')
DEFAULT_PASSWORD = os.environ.get('GRAFANA_PASSWORD', '')
REQ_TIMEOUT = float(os.environ.get('GRAFANA_TIMEOUT', '60'))


def _load_yaml(path: str) -> Dict[str, Any]:
    with open(path, 'r', encoding='utf-8') as f:
        return yaml.load(f, Loader=_PermissiveLoader) or {}


def _get_existing_contact_points(session: requests.Session, base_url: str) -> List[Dict[str, Any]]:
    url = _url(base_url, CONTACT_POINTS_API)
    r = session.get(url, timeout=REQ_TIMEOUT)
    r.raise_for_status()
    return r.json() or []


def import_contact_points(session: requests.Session, base_url: str, yaml_path: str) -> None:
    data = _load_yaml(yaml_path)
    cps: List[Dict[str, Any]] = data.get('contactPoints', []) or []
    if not cps:
        print(f"No contactPoints found in: {yaml_path}")
        return

    # 预取现有列表，建立 uid->obj 与 (name,type)->obj
    existing = _get_existing_contact_points(session, base_url)
    by_uid: Dict[str, Dict[str, Any]] = {cp.get('uid'): cp for cp in existing if cp.get('uid')}
    by_key: Dict[tuple, Dict[str, Any]] = {(cp.get('name'), cp.get('type')): cp for cp in existing if cp.get('name') and cp.get('type')}

    created, updated = 0, 0
    headers = {
        'X-Disable-Provenance': 'true',
        'Content-Type': 'application/json'
    }

    for cp in cps:
        name = cp.get('name')
        receivers: List[Dict[str, Any]] = cp.get('receivers', []) or []
        if not name or not receivers:
            continue

        for rc in receivers:
            body = {
                'name': name,
                'type': rc.get('type'),
                'settings': rc.get('settings', {}) or {},
                'disableResolveMessage': bool(rc.get('disableResolveMessage', False))
            }
            if rc.get('uid'):
                body['uid'] = rc['uid']

            target_uid: Optional[str] = None
            if rc.get('uid') and rc['uid'] in by_uid:
                target_uid = rc['uid']
            elif (name, rc.get('type')) in by_key and by_key[(name, rc.get('type'))].get('uid'):
                target_uid = by_key[(name, rc.get('type'))]['uid']

            if target_uid:
                # 更新指定接收器
                url = _url(base_url, CONTACT_POINT_BY_UID_API.format(uid=target_uid))
                resp = session.put(url, headers=headers, data=json.dumps(body), timeout=REQ_TIMEOUT)
                if resp.status_code in (200, 201, 202):
                    updated += 1
                    j = resp.json() if resp.headers.get('Content-Type', '').startswith('application/json') else {}
                    print(f"Updated ContactPoint receiver: name={name}, type={body['type']} -> uid={j.get('uid', target_uid)}")
                else:
                    try:
                        err = resp.json()
                    except Exception:
                        err = resp.text
                    print(f"Error updating ContactPoint receiver name={name}, type={body['type']}: {resp.status_code} {err}")
            else:
                # 创建新的接收器，按 name 聚合到同一 Contact Point 组
                url = _url(base_url, CONTACT_POINTS_API)
                resp = session.post(url, headers=headers, data=json.dumps(body), timeout=REQ_TIMEOUT)
                if resp.status_code in (200, 201, 202):
                    created += 1
                    j = resp.json() if resp.headers.get('Content-Type', '').startswith('application/json') else {}
                    print(f"Created ContactPoint receiver: name={name}, type={body['type']} -> uid={j.get('uid')}")
                else:
                    try:
                        err = resp.json()
                    except Exception:
                        err = resp.text
                    print(f"Error creating ContactPoint receiver name={name}, type={body['type']}: {resp.status_code} {err}")

    print(f"ContactPoints result: created={created}, updated={updated}")


def import_notification_policies(session: requests.Session, base_url: str, yaml_path: str) -> None:
    data = _load_yaml(yaml_path)
    raw = data.get('policies')

    # HTTP API 与文件导入格式不同：PUT /policies 需要的是“策略树对象”本身，而非 {policies: [...]} 包裹
    # 若为列表，取第一项作为根策略；若为字典，直接使用
    if isinstance(raw, list):
        policy = (raw[0] if raw else None)
    elif isinstance(raw, dict):
        policy = raw
    else:
        policy = None

    if not policy:
        print(f"No policies object found in: {yaml_path}")
        return

    headers = {
        'X-Disable-Provenance': 'true',
        'Content-Type': 'application/json'
    }

    url = _url(base_url, NOTIFICATION_POLICIES_API)
    resp = session.put(url, headers=headers, data=json.dumps(policy), timeout=REQ_TIMEOUT)
    if resp.status_code in (200, 201, 202):
        print("Updated Notification Policies successfully")
    else:
        try:
            err = resp.json()
        except Exception:
            err = resp.text
        print(f"Error updating Notification Policies: {resp.status_code} {err}")


def main():
    parser = argparse.ArgumentParser(description='Import Grafana Unified Alerting settings (contact points / notification policies).')
    parser.add_argument('--contact-points', dest='cp_path', help='Path to contact-points YAML to import')
    parser.add_argument('--notification-policies', dest='np_path', help='Path to notification-policies YAML to import')
    args = parser.parse_args()

    if not args.cp_path and not args.np_path:
        print('Nothing to do. Specify --contact-points and/or --notification-policies')
        sys.exit(0)

    base_url = os.environ.get('GRAFANA_URL', DEFAULT_BASE_URL)
    user = os.environ.get('GRAFANA_USER', DEFAULT_USER)
    password = os.environ.get('GRAFANA_PASSWORD', DEFAULT_PASSWORD)

    sess = get_auth_session(base_url, user, password)

    if args.cp_path:
        path = os.path.abspath(args.cp_path)
        if not os.path.exists(path):
            print(f"Contact-points file not found: {path}", file=sys.stderr)
        else:
            import_contact_points(sess, base_url, path)

    if args.np_path:
        path = os.path.abspath(args.np_path)
        if not os.path.exists(path):
            print(f"Notification-policies file not found: {path}", file=sys.stderr)
        else:
            import_notification_policies(sess, base_url, path)


if __name__ == '__main__':
    main()