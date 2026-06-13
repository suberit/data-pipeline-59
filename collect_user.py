import requests
import time
import json
import os
import sys

API = "https://api.csqaq.com"
HDR = lambda token: {"ApiToken": token, "User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}

TOKENS = [t.strip() for t in os.environ.get("CSQAQ_TOKENS", "").split(",") if t.strip()]


def bind_with_retry(token, max_retries=3):
    """bind_local_ip，429频率限制时等待30s重试"""
    for attempt in range(max_retries + 1):
        try:
            r = requests.post(f"{API}/api/v1/sys/bind_local_ip", headers=HDR(token), timeout=30).json()
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            if attempt < max_retries:
                print(f"  bind 网络错误({type(e).__name__}), 10s后重试 ({attempt+1}/{max_retries})...")
                time.sleep(10)
                continue
            print(f"  bind 网络错误({type(e).__name__}), 已达最大重试次数")
            return {"code": -1, "msg": str(e)}
        except Exception as e:
            return {"code": -1, "msg": str(e)}
        code = r.get("code")
        if code == 200:
            return r
        if code == 429:
            if attempt < max_retries:
                print(f"  bind 429频率限制, 30s后重试 ({attempt+1}/{max_retries})...")
                time.sleep(30)
                continue
            print(f"  bind 429频率限制, 已达最大重试次数")
            return r
        return r
    return r


def collect_user_trade(user_id, token, skip_bind=False):
    """采集单个用户的交易记录"""
    print(f"采集用户交易: user_id={user_id}, token={token[:8]}...")

    # 1. bind（批量模式下可能已绑定，跳过）
    if skip_bind:
        print("[1/3] bind: 已绑定，跳过")
    else:
        print("[1/3] bind...")
        bind_r = bind_with_retry(token)
        print(f"  bind: code={bind_r.get('code')}")
        if bind_r.get("code") != 200:
            return {"user_id": user_id, "error": "bind_failed", "detail": bind_r.get("msg", "")}
        time.sleep(5)

    # 2. get_task_info - 获取用户监控任务信息
    print("[2/3] get_task_info...")
    try:
        info_r = requests.post(f"{API}/api/v1/task/get_task_info",
                               headers=HDR(token), json={"task_id": str(user_id)}, timeout=30).json()
    except requests.exceptions.RequestException as e:
        return {"user_id": user_id, "error": "get_task_info_failed", "detail": f"网络错误: {type(e).__name__}"}
    except Exception as e:
        return {"user_id": user_id, "error": "get_task_info_failed", "detail": f"{type(e).__name__}: {e}"}
    if info_r.get("code") != 200:
        return {"user_id": user_id, "error": "get_task_info_failed", "detail": info_r.get("msg", "")}

    task_info = info_r.get("data", {})
    # API 返回用户信息在 data.info 列表中，提取第一个元素
    if isinstance(task_info, dict) and "info" in task_info:
        info_list = task_info.get("info", [])
        if isinstance(info_list, list) and info_list:
            task_info = info_list[0]
    time.sleep(1.1)

    # 3. get_task_business - 获取交易记录（分页）
    print("[3/3] get_task_business...")
    all_trades = []
    page = 1
    total_pages = 1
    MAX_PAGES = 10  # 50/页 × 10页 = 500 条上限，与 web 端一致，防止大用户超时

    while page <= total_pages and page <= MAX_PAGES:
        try:
            trade_r = requests.post(f"{API}/api/v1/task/get_task_business",
                                    headers=HDR(token),
                                    json={"task_id": str(user_id), "page_index": page,
                                          "page_size": 50, "type": "ALL"}, timeout=30).json()
        except requests.exceptions.RequestException as e:
            if page == 1:
                return {"user_id": user_id, "error": "get_task_business_failed",
                        "detail": f"网络错误: {type(e).__name__}"}
            break
        except Exception as e:
            if page == 1:
                return {"user_id": user_id, "error": "get_task_business_failed",
                        "detail": f"{type(e).__name__}: {e}"}
            break
        if trade_r.get("code") != 200:
            if page == 1:
                return {"user_id": user_id, "error": "get_task_business_failed",
                        "detail": trade_r.get("msg", "")}
            break

        trade_data = trade_r.get("data", {})
        # API 返回交易数据在 trades 字段，不是 data 字段
        items = trade_data.get("trades", [])
        all_trades.extend(items)

        total_count = trade_data.get("total", 0)
        total_pages = max(1, (total_count + 49) // 50)

        # 软封锁检测：API 返回 total > 0 但 trades 为空
        if total_count > 0 and len(items) == 0 and page == 1:
            print(f"  [软封锁] total={total_count} 但 trades 为空，同一 Token 同 IP 重复采集被限流")
            return {"user_id": user_id, "error": "api_soft_block",
                    "detail": f"total={total_count} but trades empty, possible rate limit for token+IP",
                    "task_info": task_info}

        print(f"  page {page}/{total_pages}, got {len(items)} trades")
        page += 1
        if page <= total_pages and page <= MAX_PAGES:
            time.sleep(1.1)
        if page > MAX_PAGES:
            print(f"  [max_pages={MAX_PAGES}] 已达上限，停止翻页")

    return {
        "user_id": user_id,
        "task_info": task_info,
        "trades": all_trades,
        "trade_count": len(all_trades),
        "error": None,
    }


def collect_user_inventory(user_id, token, skip_bind=False):
    """采集单个用户的库存快照"""
    print(f"采集用户库存: user_id={user_id}, token={token[:8]}...")

    # 1. bind（批量模式下可能已绑定，跳过）
    if skip_bind:
        print("[1/2] bind: 已绑定，跳过")
    else:
        print("[1/2] bind...")
        bind_r = bind_with_retry(token)
        print(f"  bind: code={bind_r.get('code')}")
        if bind_r.get("code") != 200:
            return {"user_id": user_id, "error": "bind_failed", "detail": bind_r.get("msg", "")}
        time.sleep(5)

    # 2. get_task_info - 包含库存信息
    print("[2/2] get_task_info...")
    try:
        info_r = requests.post(f"{API}/api/v1/task/get_task_info",
                               headers=HDR(token), json={"task_id": str(user_id)}, timeout=30).json()
    except requests.exceptions.RequestException as e:
        return {"user_id": user_id, "error": "get_task_info_failed", "detail": f"网络错误: {type(e).__name__}"}
    except Exception as e:
        return {"user_id": user_id, "error": "get_task_info_failed", "detail": f"{type(e).__name__}: {e}"}
    if info_r.get("code") != 200:
        return {"user_id": user_id, "error": "get_task_info_failed", "detail": info_r.get("msg", "")}

    task_info = info_r.get("data", {})
    # API 返回用户信息在 data.info 列表中，提取第一个元素
    if isinstance(task_info, dict) and "info" in task_info:
        info_list = task_info.get("info", [])
        if isinstance(info_list, list) and info_list:
            task_info = info_list[0]

    return {
        "user_id": user_id,
        "task_info": task_info,
        "error": None,
    }


def collect_batch(user_list, action):
    """批量采集多个用户，同一 Token 只 bind 一次"""
    if not TOKENS:
        return {str(u.get("user_id", "")): {"user_id": str(u.get("user_id", "")),
                "error": "bind_failed", "detail": "无可用 Token"} for u in user_list}

    # 收集需要的 Token 索引（去重）
    token_indices = sorted(set(u.get("token_index", 0) for u in user_list))
    bound_tokens = {}  # token_index -> token_value，已绑定的 Token

    # 逐个 bind 每个需要的 Token
    print(f"\n=== 批量绑定 {len(token_indices)} 个 Token ===")
    for idx in token_indices:
        token = TOKENS[idx % len(TOKENS)]
        print(f"  bind Token[{idx}] ({token[:8]}...)")
        bind_r = bind_with_retry(token)
        if bind_r.get("code") == 200:
            bound_tokens[idx] = token
            print(f"  bind Token[{idx}]: 成功")
            time.sleep(5)
        else:
            print(f"  bind Token[{idx}]: 失败 code={bind_r.get('code')}")

    # 串行采集每个用户
    print(f"\n=== 批量采集 {len(user_list)} 个用户 ({action}) ===")
    results = {}
    for i, user in enumerate(user_list):
        uid = str(user.get("user_id", ""))
        token_index = user.get("token_index", 0)
        token = bound_tokens.get(token_index)

        if not token:
            results[uid] = {"user_id": uid, "error": "bind_failed",
                            "detail": f"Token[{token_index}] 绑定失败，跳过采集"}
            print(f"  [{i+1}/{len(user_list)}] user_id={uid}: Token[{token_index}] 未绑定，跳过")
            continue

        skip_bind = token_index in bound_tokens
        print(f"\n  [{i+1}/{len(user_list)}] user_id={uid}, Token[{token_index}]")

        if action == "trade":
            result = collect_user_trade(uid, token, skip_bind=skip_bind)
        elif action == "inventory":
            result = collect_user_inventory(uid, token, skip_bind=skip_bind)
        else:
            result = {"user_id": uid, "error": f"未知 action: {action}"}

        results[uid] = result

        # 用户之间间隔 2 秒
        if i < len(user_list) - 1:
            time.sleep(2)

    return results


def main():
    user_id = ""
    action = "trade"
    token_index = 0
    user_list_json = ""

    for i, arg in enumerate(sys.argv):
        if arg == "--user-id" and i + 1 < len(sys.argv):
            user_id = sys.argv[i + 1]
        if arg == "--action" and i + 1 < len(sys.argv):
            action = sys.argv[i + 1]
        if arg == "--token-index" and i + 1 < len(sys.argv):
            token_index = int(sys.argv[i + 1])
        if arg == "--user-list" and i + 1 < len(sys.argv):
            user_list_json = sys.argv[i + 1]

    if not TOKENS:
        print("错误: 未设置CSQAQ_TOKENS环境变量")
        sys.exit(1)

    # 批量模式
    if user_list_json:
        try:
            user_list = json.loads(user_list_json)
        except json.JSONDecodeError as e:
            print(f"错误: --user-list JSON 解析失败: {e}")
            sys.exit(1)

        print(f"批量采集: {len(user_list)} 个用户, action={action}")
        results = collect_batch(user_list, action)

        with open("result.json", "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"\n保存: result.json")

        success = sum(1 for r in results.values() if not r.get("error"))
        failed = sum(1 for r in results.values() if r.get("error"))
        print(f"结果: {success} 成功, {failed} 失败")
        return

    # 单用户模式（向后兼容）
    if not user_id:
        print("错误: 缺少 --user-id 或 --user-list 参数")
        sys.exit(1)

    token = TOKENS[token_index % len(TOKENS)]
    print(f"用户采集: user_id={user_id}, action={action}, token_index={token_index}")

    if action == "trade":
        result = collect_user_trade(user_id, token)
    elif action == "inventory":
        result = collect_user_inventory(user_id, token)
    else:
        print(f"错误: 未知 action={action}, 支持 trade/inventory")
        sys.exit(1)

    with open("result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"保存: result.json")


if __name__ == "__main__":
    main()
