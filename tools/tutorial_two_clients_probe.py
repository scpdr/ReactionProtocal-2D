# -*- coding: utf-8 -*-
"""真网络双客户端探针：两个"新手"同时进教程，验证互相看不见、都只看到自己的靶子。

用法：python tools/tutorial_two_clients_probe.py --host 127.0.0.1 --port 8899

注意：必须针对"该 IP 还没完成过教程"的服务器运行（即先删掉 save/tutorial_done.json 再开服），
      否则两个客户端会直接进主对局（这是正确行为，本探针会报失败）。
"""
import argparse
import importlib.util
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def load_lancheck():
    path = os.path.join(ROOT, "tests", "lan_client_check.py")
    spec = importlib.util.spec_from_file_location("lan_client_check", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def names(snap):
    return sorted(p.get("n", "") for p in (snap or {}).get("ps", []) if isinstance(p, dict))


def status(host, port):
    import urllib.request
    return json.loads(urllib.request.urlopen(
        "http://%s:%d/status.json" % (host, port), timeout=6).read().decode("utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8899)
    a = ap.parse_args()
    lc = load_lancheck()
    salt, src = lc.auth_salt(os.path.join(ROOT, "public", "game.js"))
    print("签名盐来源:", src)

    c1 = lc.WsClient(a.host, a.port)
    c1.connect()
    c2 = lc.WsClient(a.host, a.port)
    c2.connect()
    r1 = lc.wait_join(c1, "新手甲", None, salt, skip_tutorial=False)
    r2 = lc.wait_join(c2, "新手乙", None, salt, skip_tutorial=False)
    print("join#1:", str(r1)[:150])
    print("join#2:", str(r2)[:150])
    time.sleep(1.5)

    ok, bad = [], []

    def check(name, cond, want, got):
        (ok if cond else bad).append(name)
        print(("[PASS] " if cond else "[FAIL] ") + "%s 期望 %s / 实测 %s" % (name, want, got))

    check("客户端1 进入教程局", bool(c1.tut_start), "有 tut_start",
          str(bool(c1.tut_start)))
    check("客户端2 进入教程局", bool(c2.tut_start), "有 tut_start",
          str(bool(c2.tut_start)))
    m1 = ((c1.tut_start or {}).get("map") or {}).get("id")
    m2 = ((c2.tut_start or {}).get("map") or {}).get("id")
    check("两边都是教程小地图", m1 == "tutorial" and m2 == "tutorial", "tutorial/tutorial",
          "%s/%s" % (m1, m2))

    n1, n2 = names(c1.latest), names(c2.latest)
    print("客户端1 看到:", n1)
    print("客户端2 看到:", n2)
    check("客户端1 看不到 新手乙", "新手乙" not in n1, "不含 新手乙", str(n1))
    check("客户端2 看不到 新手甲", "新手甲" not in n2, "不含 新手甲", str(n2))
    check("各自 3 个靶子",
          sum(1 for x in n1 if "靶子" in x) == 3 and sum(1 for x in n2 if "靶子" in x) == 3,
          "3/3", "%d/%d" % (sum(1 for x in n1 if "靶子" in x),
                            sum(1 for x in n2 if "靶子" in x)))
    check("每人 4 个实体（自己+3靶子）", len(n1) == 4 and len(n2) == 4, "4/4",
          "%d/%d" % (len(n1), len(n2)))

    st = status(a.host, a.port)
    check("服务端有 2 个教程小局", st.get("tutorial_sessions") == 2, "2",
          str(st.get("tutorial_sessions")))
    check("主对局不含这两位新手",
          all(p.get("n") not in ("新手甲", "新手乙") for p in st.get("players", [])),
          "不含", str([p.get("n") for p in st.get("players", [])]))

    # 一人通关：进主对局；另一人还在自己的教程局
    c1.send_text({"t": "tut_done"})
    time.sleep(2.0)
    st2 = status(a.host, a.port)
    check("服务端教程小局剩 1 个", st2.get("tutorial_sessions") == 1, "1",
          str(st2.get("tutorial_sessions")))
    check("通关者已进入主对局",
          any(p.get("n") == "新手甲" for p in st2.get("players", [])), "含 新手甲",
          str([p.get("n") for p in st2.get("players", [])]))
    check("通关者收到了切图消息", (c1.map_msg or {}).get("map") is not None,
          "有 map", str(bool(c1.map_msg)))
    n2b = names(c2.latest)
    check("另一人仍在教程里（看不到通关者）", "新手甲" not in n2b, "不含 新手甲", str(n2b))
    check("另一人看到的仍是教程地图", "靶子" in "".join(n2b), "含靶子", str(n2b))

    c1.close()
    c2.close()
    print("\n结果: %d 通过 / %d 失败" % (len(ok), len(bad)))
    if bad:
        print("失败: " + ", ".join(bad))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
