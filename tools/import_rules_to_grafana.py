import os
import sys
import json
import time
import getpass
from typing import Dict, Any, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from urllib.parse import quote

# 读取由 convert_yaml_to_grafana_json.py 生成的 API 规则文件
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
API_RULES_PATH = os.path.join(BASE_DIR, 'out', 'api_rules.json')

# 默认目标Grafana
DEFAULT_BASE_URL = os.environ.get('GRAFANA_URL', 'https://monitor-test.planet-alpha.net/')
DEFAULT_USER = os.environ.get('GRAFANA_USER', 'admin')
DEFAULT_PASSWORD = os.environ.get('GRAFANA_PASSWORD', 'pa@2025')
API_TOKEN = os.environ.get('GRAFANA_API_TOKEN')
VERIFY_ENV = os.environ.get('GRAFANA_VERIFY', 'true').lower()
CA_CERT_PATH = os.environ.get('GRAFANA_CA_CERT')
REQ_TIMEOUT = float(os.environ.get('GRAFANA_TIMEOUT', '60'))

# 采用经典 Folder API 获取/创建文件夹 + 获取UID
FOLDERS_API = 'api/folders'
PROVISION_ALERT_RULES_API = 'api/v1/provisioning/alert-rules'
PUT_RULE_GROUP_API = 'api/v1/provisioning/folder/{folderUid}/rule-groups/{group}'
GET_RULE_GROUP_API = 'api/v1/provisioning/folder/{folderUid}/rule-groups/{group}'
PUT_ALERT_RULE_API = 'api/v1/provisioning/alert-rules/{uid}'


def _url(base: str, path: str) -> str:
    return base.rstrip('/') + '/' + path.lstrip('/')


def get_auth_session(base_url: str, user: Optional[str], password: Optional[str]) -> requests.Session:
    sess = requests.Session()
    sess.headers.update({'Content-Type': 'application/json'})

    # TLS verify handling
    if CA_CERT_PATH and os.path.exists(CA_CERT_PATH):
        sess.verify = CA_CERT_PATH
    elif VERIFY_ENV in ('false', '0', 'no', 'off'):
        sess.verify = False
        try:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        except Exception:
            pass

    # Auth: API token preferred, else basic auth
    if API_TOKEN:
        sess.headers.update({'Authorization': f'Bearer {API_TOKEN}'})
    elif user and password:
        sess.auth = (user, password)

    # Retries for idempotent methods and POST
    retries = Retry(total=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504], allowed_methods=["HEAD","GET","PUT","DELETE","OPTIONS","TRACE","POST"]) 
    adapter = HTTPAdapter(max_retries=retries)
    sess.mount('http://', adapter)
    sess.mount('https://', adapter)
    return sess


def ensure_folder(session: requests.Session, base_url: str, folder_title: str) -> str:
    # 先查询是否存在
    r = session.get(_url(base_url, FOLDERS_API), timeout=REQ_TIMEOUT)
    r.raise_for_status()
    for f in r.json():
        if f.get('title') == folder_title:
            return f.get('uid')
    # 不存在则创建
    r = session.post(_url(base_url, FOLDERS_API), data=json.dumps({'title': folder_title}), timeout=REQ_TIMEOUT)
    r.raise_for_status()
    return r.json().get('uid')


def get_rule_group(session: requests.Session, base_url: str, folder_uid: str, group: str) -> Optional[Dict[str, Any]]:
    group_path = quote(group, safe='') if group else ''
    url = _url(base_url, GET_RULE_GROUP_API.format(folderUid=folder_uid, group=group_path))
    r = session.get(url, timeout=REQ_TIMEOUT)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def _parse_duration_seconds(s: str) -> Optional[int]:
    if not s:
        return None
    try:
        s = str(s).strip().lower()
        if s.endswith('ms'):
            # not supported by API seconds, round up to 1s
            return max(1, int(round(float(s[:-2]) / 1000.0)))
        if s.endswith('s'):
            return int(float(s[:-1]))
        if s.endswith('m'):
            return int(float(s[:-1]) * 60)
        if s.endswith('h'):
            return int(float(s[:-1]) * 3600)
        # plain number
        return int(float(s))
    except Exception:
        return None


def _normalize_interval(interval: Optional[str], default_seconds: int = 60, scheduler_step: int = 10) -> (str, int):
    seconds = _parse_duration_seconds(interval) if interval else None
    if not seconds or seconds <= 0:
        seconds = default_seconds
    # align to scheduler step (round up)
    if scheduler_step > 0:
        q, r = divmod(int(seconds), scheduler_step)
        if r != 0:
            seconds = (q + 1) * scheduler_step
        else:
            seconds = q * scheduler_step
        if seconds <= 0:
            seconds = scheduler_step
    return f"{int(seconds)}s", int(seconds)


def update_rule_group_interval(session: requests.Session, base_url: str, folder_uid: str, group: str, interval: str) -> None:
    last_status = None
    last_detail = None
    for attempt in range(3):
        existing = get_rule_group(session, base_url, folder_uid, group)
        if not existing:
            return
        rules_payload = existing.get('rules', [])
        if not rules_payload:
            # 等待规则在组内可见
            time.sleep(1)
            continue

        headers = {'X-Disable-Provenance': 'true'}
        group_path = quote(group, safe='') if group else ''
        url = _url(base_url, PUT_RULE_GROUP_API.format(folderUid=folder_uid, group=group_path))

        norm_interval_str, norm_seconds = _normalize_interval(interval)

        # 使用 interval 数字秒，符合HTTP API预期
        body = {
            'interval': norm_seconds,
            'rules': rules_payload
        }
        resp = session.put(url, headers=headers, data=json.dumps(body), timeout=REQ_TIMEOUT)
        last_status = resp.status_code
        try:
            last_detail = resp.json()
        except Exception:
            last_detail = resp.text
        if resp.status_code in (200, 201):
            return

        # 对于 400 错误，等待后重试（可能是规则尚未完全可见或调度步长对齐问题）
        if resp.status_code == 400:
            time.sleep(1)
            continue
        else:
            # 其它错误不重试
            break

    print(f"Warning: update_rule_group_interval {group} failed after retries: {last_status} {last_detail}")


def ensure_rule_group(session: requests.Session, base_url: str, folder_uid: str, group: str, interval: Optional[str] = None):
    # 兼容旧调用：不再在组为空时PUT，避免 400。改为在规则创建后再更新 interval。
    if interval:
        update_rule_group_interval(session, base_url, folder_uid, group, interval)


def import_rules(session: requests.Session, base_url: str, rules: List[Dict[str, Any]]):
    # 按 folder 与 ruleGroup 分桶，先创建组（设置 interval）再导入单条规则
    # interval 只能从 provisioning 文件推断；这里无法精准获知，所以使用 60s 默认
    from collections import defaultdict
    buckets = defaultdict(list)
    for r in rules:
        buckets[(r.get('folder'), r.get('ruleGroup'))].append(r)

    folder_uid_cache: Dict[str, str] = {}

    for (folder_title, group_name), group_rules in buckets.items():
        folder_uid = folder_uid_cache.get(folder_title)
        if not folder_uid:
            folder_uid = ensure_folder(session, base_url, folder_title)
            folder_uid_cache[folder_title] = folder_uid
        # 不再预创建空组，直接导入规则，随后再统一设置组 interval
        for rule in group_rules:
            body = dict(rule)
            body.pop('folder', None)
            # 使用正确的字段名 folderUID
            body['folderUID'] = folder_uid
            # 规则组 interval 优先使用规则里携带的 groupInterval
            group_interval = body.pop('groupInterval', None)
            # 确保字段名大小写与API一致
            body['orgId'] = body.get('orgId', 1)
            # POST 创建
            url = _url(base_url, PROVISION_ALERT_RULES_API)
            resp = session.post(url, data=json.dumps(body), timeout=60)
            if resp.status_code not in (200, 201):
                if resp.status_code == 409 and body.get('uid'):
                    # 冲突：规则已存在，尝试改为更新
                    put_url = _url(base_url, PUT_ALERT_RULE_API.format(uid=body['uid']))
                    put_resp = session.put(put_url, data=json.dumps(body), timeout=60)
                    if put_resp.status_code not in (200, 201):
                        try:
                            err = put_resp.json()
                        except Exception:
                            err = put_resp.text
                        print(f"Error updating rule '{rule.get('title')}' uid={body['uid']}: {put_resp.status_code} {err}")
                    else:
                        j = put_resp.json()
                        print(f"Updated: {rule.get('title')} -> uid={j.get('uid')}")
                else:
                    try:
                        err = resp.json()
                    except Exception:
                        err = resp.text
                    print(f"Error importing rule '{rule.get('title')}': {resp.status_code} {err}")
            else:
                j = resp.json()
                print(f"Imported: {rule.get('title')} -> uid={j.get('uid')}")

        # 组内规则创建/更新完成后，再设置组的 interval（若有）
        # 从第一条规则中获取 groupInterval
        desired_interval = None
        for r in group_rules:
            gi = r.get('groupInterval')
            if gi:
                desired_interval = gi
                break
        if desired_interval:
            update_rule_group_interval(session, base_url, folder_uid, group_name, desired_interval)


def main():
    base_url = os.environ.get('GRAFANA_URL', DEFAULT_BASE_URL)
    user = os.environ.get('GRAFANA_USER', DEFAULT_USER)
    password = os.environ.get('GRAFANA_PASSWORD', DEFAULT_PASSWORD)

    if not os.path.exists(API_RULES_PATH):
        print(f"Rules JSON not found: {API_RULES_PATH}", file=sys.stderr)
        sys.exit(1)

    with open(API_RULES_PATH, 'r', encoding='utf-8') as f:
        rules = json.load(f)

    sess = get_auth_session(base_url, user, password)

    try:
        import_rules(sess, base_url, rules)
    except requests.RequestException as e:
        print(f"HTTP error: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == '__main__':
    main()