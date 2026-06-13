import requests
import time
import json
import os
import sys

API = "https://api.csqaq.com"
HDR = lambda token: {"ApiToken": token, "User-Agent": "Mozilla/5.0", "Content-Type": "application/json"}

CHART_CONFIGS = []
for pid, prefix in [[1, "buff"], [2, "yyyp"]]:
    for period in [365, 30, 7, 90, 180]:
        for key in ["sell_price", "sell_num", "buy_num", "buy_price"]:
            CHART_CONFIGS.append({"data_key": f"{prefix}_{key}_{period}d", "key": key, "platform": pid, "period": period})

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


def collect_item(text, token, goods_id=None):
    print(f"采集: {text}, token={token[:8]}...")

    print(f"[1/3] bind...")
    bind_r = bind_with_retry(token)
    print(f"  bind: code={bind_r.get('code')}")
    if bind_r.get("code") != 200:
        print(f"  bind失败: {bind_r}")
        return {"name": text, "chart_ok": 0, "chart_fail": 40, "error": "bind_failed"}

    bind_ip_raw = bind_r.get("data", "")
    bind_ip = bind_ip_raw.split("\uff1a")[-1].strip() if "\uff1a" in bind_ip_raw else bind_ip_raw.strip()
    print(f"  绑定IP: {bind_ip}")
    time.sleep(3)

    name = text

    if not goods_id:
        print(f"[2/3] search...")
        search_r = requests.get(f"{API}/api/v1/search/suggest?text={text}", headers=HDR(token), timeout=30).json()
        if search_r.get("code") != 200:
            print(f"  search失败: {search_r}")
            return {"name": text, "chart_ok": 0, "chart_fail": 40, "error": "search_failed"}

        search_data = search_r.get("data", [])
        if not search_data:
            return {"name": text, "chart_ok": 0, "chart_fail": 40, "error": "not_found"}

        normal = [it for it in search_data if "StatTrak" not in it.get("value", "")]
        selected = normal[0] if normal else search_data[0]
        goods_id = selected["id"]
        name = selected["value"]
        print(f"  找到: {name} (id={goods_id})")
        time.sleep(1.1)
    else:
        print(f"[2/3] 跳过search, 直接使用goods_id={goods_id}")

    print(f"[3/3] detail...")
    detail_data = None
    try:
        detail_r = requests.get(f"{API}/api/v1/info/good?id={goods_id}", headers=HDR(token), timeout=30).json()
        if detail_r.get("code") == 200:
            detail_data = detail_r.get("data")
            if detail_data and detail_data.get("market_hash_name"):
                name = detail_data["market_hash_name"]
    except Exception:
        pass
    time.sleep(1.1)

    print(f"[3/3] chart (40个)...")
    chart_data = {}
    chart_ok = 0
    chart_fail = 0

    for i, cfg in enumerate(CHART_CONFIGS):
        try:
            r = requests.post(f"{API}/api/v1/info/chart", headers=HDR(token),
                              json={"good_id": str(goods_id), "key": cfg["key"], "platform": cfg["platform"], "period": cfg["period"], "style": "all_style"}, timeout=30).json()
            if r.get("code") == 200:
                chart_data[cfg["data_key"]] = r.get("data")
                chart_ok += 1
            else:
                chart_data[cfg["data_key"]] = None
                chart_fail += 1
        except Exception:
            chart_data[cfg["data_key"]] = None
            chart_fail += 1

        if (i + 1) % 10 == 0:
            print(f"  [{i+1}/40] {chart_ok}OK/{chart_fail}FAIL")
        if i < len(CHART_CONFIGS) - 1:
            time.sleep(1.1)

    print(f"  结果: {chart_ok}OK/{chart_fail}FAIL ({chart_ok/40*100:.0f}%)")

    return {
        "name": name,
        "goods_id": goods_id,
        "bind_ip": bind_ip,
        "detail": detail_data,
        "chart": chart_data,
        "chart_ok": chart_ok,
        "chart_fail": chart_fail,
    }


def main():
    items = []
    text = ""
    goods_id = None
    token_index = 0

    for i, arg in enumerate(sys.argv):
        if arg == "--items-json" and i + 1 < len(sys.argv):
            try:
                parsed = json.loads(sys.argv[i + 1])
                if isinstance(parsed, list):
                    items = [{"name": str(it.get("name", "")), "goods_id": str(it.get("goods_id") or "")}
                             for it in parsed if it.get("name")]
            except (json.JSONDecodeError, TypeError) as e:
                print(f"  [ERROR] --items-json 解析失败: {e}")
                sys.exit(1)
        if arg == "--text" and i + 1 < len(sys.argv):
            text = sys.argv[i + 1]
        if arg == "--goods-id" and i + 1 < len(sys.argv):
            goods_id = sys.argv[i + 1]
        if arg == "--token-index" and i + 1 < len(sys.argv):
            token_index = int(sys.argv[i + 1])

    if not items:
        if not text:
            print("错误: 缺少 --items-json 或 --text 参数")
            sys.exit(1)
        items = [{"name": text, "goods_id": goods_id or ""}]

    if not TOKENS:
        print("错误: 未设置CSQAQ_TOKENS环境变量")
        sys.exit(1)

    token = TOKENS[token_index % len(TOKENS)]
    print(f"CSQAQ采集: K={len(items)}, token={token[:8]}...")

    if len(items) == 1:
        result = collect_item(items[0]["name"], token, goods_id=items[0]["goods_id"] or None)
        output = [result]
    else:
        output = collect_batch(items, token)

    with open("result.json", "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"保存: result.json (K={len(output)})")


def collect_batch(items, token):
    """批量采集：共享一次 bind，每个 item 独立 search/detail/chart

    限流策略：
    - bind 成功后 sleep 5s（CSQAQ bind 频率限制）
    - item 之间 sleep 1.1s（避免触发 1 req/s/IP 限流）
    - chart 之间 sleep 1.2s
    """
    print(f"批量采集 {len(items)} 个 item, token={token[:8]}...")

    print("[batch] bind...")
    bind_r = bind_with_retry(token)
    if bind_r.get("code") != 200:
        print(f"  bind 失败: {bind_r.get('code')}")
        return [
            {"name": it["name"], "goods_id": it.get("goods_id") or "",
             "chart_ok": 0, "chart_fail": 40, "error": "bind_failed"}
            for it in items
        ]

    bind_ip_raw = bind_r.get("data", "")
    bind_ip = bind_ip_raw.split("\uff1a")[-1].strip() if "\uff1a" in bind_ip_raw else bind_ip_raw.strip()
    print(f"  绑定IP: {bind_ip}")
    time.sleep(3)

    results = []
    for idx, it in enumerate(items):
        text = it["name"]
        gid = it.get("goods_id") or None
        print(f"\n[{idx + 1}/{len(items)}] {text}")
        result = _collect_one_in_batch(text, gid, token)
        result["bind_ip"] = bind_ip
        results.append(result)
        if idx < len(items) - 1:
            time.sleep(1.1)

    total_ok = sum(r.get("chart_ok", 0) for r in results)
    total_fail = sum(r.get("chart_fail", 0) for r in results)
    print(f"\n  batch 汇总: {len(results)} item, chart {total_ok}OK/{total_fail}FAIL")
    return results


def _collect_one_in_batch(text, goods_id, token):
    """batch 内单个 item 的 search/detail/chart（共享 bind 已在外层完成）"""
    name = text

    if not goods_id:
        print(f"  search...")
        try:
            search_r = requests.get(f"{API}/api/v1/search/suggest?text={text}",
                                    headers=HDR(token), timeout=30).json()
        except Exception as e:
            return {"name": text, "chart_ok": 0, "chart_fail": 40, "error": f"search_err: {type(e).__name__}"}
        if search_r.get("code") != 200:
            return {"name": text, "chart_ok": 0, "chart_fail": 40, "error": "search_failed"}
        sdata = search_r.get("data", [])
        if not sdata:
            return {"name": text, "chart_ok": 0, "chart_fail": 40, "error": "not_found"}
        normal = [it for it in sdata if "StatTrak" not in it.get("value", "")]
        selected = normal[0] if normal else sdata[0]
        goods_id = selected["id"]
        name = selected["value"]
        print(f"  -> {name} (id={goods_id})")
        time.sleep(1.1)
    else:
        print(f"  跳过search, goods_id={goods_id}")

    print(f"  detail...")
    detail_data = None
    try:
        detail_r = requests.get(f"{API}/api/v1/info/good?id={goods_id}",
                                headers=HDR(token), timeout=30).json()
        if detail_r.get("code") == 200:
            detail_data = detail_r.get("data")
            if detail_data and detail_data.get("market_hash_name"):
                name = detail_data["market_hash_name"]
    except Exception:
        pass
    time.sleep(1.1)

    print(f"  chart (40)...")
    chart_data = {}
    chart_ok = 0
    chart_fail = 0
    for i, cfg in enumerate(CHART_CONFIGS):
        try:
            r = requests.post(f"{API}/api/v1/info/chart", headers=HDR(token),
                              json={"good_id": str(goods_id), "key": cfg["key"],
                                    "platform": cfg["platform"], "period": cfg["period"],
                                    "style": "all_style"}, timeout=30).json()
            if r.get("code") == 200:
                chart_data[cfg["data_key"]] = r.get("data")
                chart_ok += 1
            else:
                chart_data[cfg["data_key"]] = None
                chart_fail += 1
        except Exception:
            chart_data[cfg["data_key"]] = None
            chart_fail += 1
        if i < len(CHART_CONFIGS) - 1:
            time.sleep(1.1)

    return {
        "name": name,
        "goods_id": goods_id,
        "detail": detail_data,
        "chart": chart_data,
        "chart_ok": chart_ok,
        "chart_fail": chart_fail,
    }


if __name__ == "__main__":
    main()
