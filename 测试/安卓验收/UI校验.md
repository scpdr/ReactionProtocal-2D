# 手机端 UI 校验报告（Reaction Protocol）

- 生成时间：2026-10-18 17:56:19
- 设备：HUAWEI FOA-AL00（Android 12，物理 1084×2412，横屏 CSS 视口约 768×362）
- 包名/Activity：`com.reactionprotocol.app/.MainActivity`，启动参数 `--es server 127.0.0.1:8765`
- 校验脚本：`tests/android_ui_check.py`（CDP 真机）+ `tests/mobile_layout_check.js`（Node 多尺寸）
- 耗时：7.8s

## 结论

- 通过 **33** 项，失败 **0** 项，提示 0 项，跳过 0 项 → **全部通过**

## 环境

| 项目 | 期望 | 实测 | 结论 |
|---|---|---|---|
| adb 设备在线 | 9TM9K23404002482 | 在线 | ✅ 通过 |
| 服务器 /status.json 可达 | 127.0.0.1:8765 返回 JSON | 玩家 3（AI 2） | ✅ 通过 |
| WebView devtools 可达 | page target ≥1 | 1 个（socket 复用现有 forward tcp:9222） | ✅ 通过 |

## 布局报告

| 项目 | 期望 | 实测 | 结论 |
|---|---|---|---|
| window.__mobileLayoutReport() 可读 | 返回对象 | OK（10 个字段） | ✅ 通过 |
| vw 与真机 CSS 视口一致(±2) | innerWidth=768 | report.vw=768 | ✅ 通过 |
| vh 与真机 CSS 视口一致(±2) | innerHeight=362 | report.vh=362 | ✅ 通过 |
| 报告含 VW/VH 且与 vw/vh 一致(±2) | VW/VH ≈ 768×362 | VW=768 VH=362 | ✅ 通过 |
| 报告含 DPR 且与 devicePixelRatio 一致 | DPR=3 | report.DPR=3 | ✅ 通过 |
| 横屏（VW>VH） | VW>VH | 768>362 | ✅ 通过 |
| 真机像素比自洽 | 1084/362≈2.99 | devicePixelRatio=3 | ✅ 通过 |
| ↳ 说明 | | 设备物理 1084×2412，横屏 CSS 视口约 768x362（环境事实） | |
| vw/vh 接近环境事实 768×362 | ±4px | vw=768 vh=362 | ✅ 通过 |
| overlaps 为空 | [] | [] | ✅ 通过 |
| outOfSafe（越安全区元素）为空 | [] | [] | ✅ 通过 |
| 关键元素齐全 | ring reload/smoke/skill + menu board/chat/admin + 6 chips + fire | ring=['reload', 'smoke', 'skill'] menu=['admin', 'board', 'chat'] chips=6 fire=True | ✅ 通过 |

## 点击序列

| 项目 | 期望 | 实测 | 结论 |
|---|---|---|---|
| 复用已加入的会话（不重新 join） | joined=true | joined=True（isHost=True），已有对局，跳过 joinBtn 点击 | ✅ 通过 |
| ↳ 说明 | | 共享设备安全模式：已加入时不再点击开始界面（start 遮罩已隐藏，点其坐标会误触发到游戏画面） | |
| 本客户端为主机（admin 菜单可用） | isHost=true | isHost=True | ✅ 通过 |
| ↳ 说明 | | 非主机时 admin 行按 SKIP 处理 | |
| 点击 board 中心 → showBoard 翻转 | showBoard: False→True | showBoard=True（坐标 628.0,20.5） | ✅ 通过 |
| board 的 __mobileLastHit | menu:board | lastHit='menu:board' | ✅ 通过 |
| board 复位（再点一次关闭） | showBoard=False | showBoard=False | ✅ 通过 |
| 点击 chat 中心 → chatInput.className 含 show | 含 'show' | 'show'（坐标 682.0,20.5） | ✅ 通过 |
| chat 的 __mobileLastHit | menu:chat | lastHit='menu:chat' | ✅ 通过 |
| chat 复位（隐藏输入框，避免吞掉后续点击） | className 不含 show | '' | ✅ 通过 |
| 点击 admin 中心 → showAdmin 翻转 | showAdmin: False→True | showAdmin=True（坐标 736.0,20.5） | ✅ 通过 |
| admin 的 __mobileLastHit | menu:admin | lastHit='menu:admin' | ✅ 通过 |
| admin 复位（点面板“关闭”热区） | showAdmin=False | showAdmin=False（坐标 303.0,24.0） | ✅ 通过 |
| 点击 reload 中心 → __mobileLastHit | ring:reload | lastHit='ring:reload'（坐标 639.0,311.0） | ✅ 通过 |
| 点击 smoke 中心 → __mobileLastHit | ring:smoke | lastHit='ring:smoke'（坐标 639.0,251.0） | ✅ 通过 |
| smoke 复位（再按一次取消落点模式） | smokeAim=false | smokeAim=False | ✅ 通过 |
| ↳ 说明 | | 落点模式下会吞掉后续点击，必须先退出 | |
| 点击 skill 中心 → __mobileLastHit | ring:skill | lastHit='ring:skill'（坐标 639.0,191.0） | ✅ 通过 |
| 全程无误触开火 | 任何菜单/功能键点击 lastHit 均非 'fire:' | 6 次点击 lastHit：board→'menu:board'; chat→'menu:chat'; admin→'menu:admin'; reload→'ring:reload'; smoke→'ring:smoke'; skill→'ring:skill' | ✅ 通过 |
| 点击序列坐标（每次现取布局，非硬编码） | 坐标来自 report.layout | board(628.0,20.5)@768x362; chat(682.0,20.5)@768x362; admin(736.0,20.5)@768x362; reload(639.0,311.0)@768x362; smoke(639.0,251.0)@768x362; skill(639.0,191.0)@768x362 | ✅ 通过 |

## 截图

| 项目 | 期望 | 实测 | 结论 |
|---|---|---|---|
| 真机截图已保存 | 测试/安卓验收/ui_layout.png 存在且 >10KB | 测试\安卓验收\ui_layout.png（161370 字节） | ✅ 通过 |

## Node 多尺寸

| 项目 | 期望 | 实测 | 结论 |
|---|---|---|---|
| 9 尺寸布局校验全部通过 | 退出码 0 | 退出码 0；== minGap 汇总表 == | ✅ 通过 |
| ↳ 说明 | | 尺寸                    最小间距        最小间距键                         最紧元素对                             overlaps   中心命中       空白L/R       结论<br>640x300               6           menu:board\|menu:chat          ring:reload↔ring:smoke 6.0px      0          13/13      1869/1720   PASS<br>640x300+sniper/nuke   6           menu:board\|menu:chat          ring:reload↔ring:smoke 6.0px      0          15/15      1804/1597   PASS<br>667x375               6           menu:board\|menu:chat          ring:reload↔ring:smoke 6.0px      0          13/13      1340/1218   PASS<br>667x375+sniper/nuke   6           menu:board\|menu:chat          ring:reload↔ring:smoke 6.0px      0          15/15      1309/1110   PASS<br>736x414               6           menu:board\|menu:chat          ring:reload↔ring:smoke 6.0px      0          13/13      1342/1291   PASS<br>736x414+sniper/nuke   6           menu:board\|menu:chat          ring:reload↔ring:smoke 6.0px      0          15/15      1304/1217   PASS<br>768x362               6           menu:board\|menu:chat          ring:reload↔ring:smoke 6.0px      0          13/13      1498/1447   PASS<br>768x362+sniper/nuke   6           menu:board\|menu:chat          ring:reload↔ring:smoke 6.0px      0          15/15      1439/1345   PASS<br>800x360               6           menu:board\|menu:chat          ring:reload↔ring:smoke 6.0px      0          13/13      1578/1502   PASS<br>800x360+sniper/nuke   6           menu:board\|menu:chat          ring:reload↔ring:smoke 6.0px      0          15/15      1505/1408   PASS<br>844x390               6           menu:board\|menu:chat          ring:reload↔ring:smoke 6.0px      0          13/13      1919/1816   PASS<br>844x390+sniper/nuke   6           menu:board\|menu:chat          ring:reload↔ring:smoke 6.0px      0          15/15      1836/1706   PASS<br>915x412               6           menu:board\|menu:chat          ring:reload↔ring:smoke 6.0px      0          13/13      1797/1742   PASS<br>915x412+sniper/nuke   6           menu:board\|menu:chat          ring:reload↔ring:smo | |

## 备注

- 设备: FOA-AL00 / Android 12 / Physical size: 1084x2412
- 按 --no-reverse 跳过 adb reverse（假设 Lead 已设好）
- page url: file:///android_asset/mobile.html?server=127.0.0.1%3A8765&v=1.0
- 布局: vw=768 vh=362 safe={'l': 0, 'r': 0, 't': 0, 'b': 0} fire={'cx': 717, 'cy': 311, 'r': 43}

## 复现命令（独立可复现）

前提：Node ≥ 18；Python 3.10+（仅标准库）；`adb` 在 PATH；`public/mobile_layout.js` 已落盘；
Android 设备经 USB 连接且 `adb devices` 可见；PC 端 `server.py` 已启动。
两个脚本都能在任意工作目录下运行（内部按 `__file__` 解析仓库根目录）。

### 1) Node 多尺寸布局校验（不需要设备）

```powershell
cd lan-shooter-v2
node tests/mobile_layout_check.js             # 期望 18/18 PASS，退出码 0
node tests/mobile_layout_check.js --verbose   # 追加完整 minGap JSON
node tests/mobile_layout_check.js --sizes 768x362,640x300   # 只跑指定尺寸
```

### 2) 真机 UI 校验（需要设备 + 服务器）

```powershell
cd lan-shooter-v2
# 终端 A：启动服务器（已在运行可跳过）；另开一个窗口
$env:PYTHONIOENCODING='utf-8'; python server.py 8765 --bots 2 --no-browser
# 终端 B：模式一 独立跑（自带 adb reverse / forward / 启动 app）
python tests/android_ui_check.py --serial 9TM9K23404002482 --restart
# 终端 B：模式二 共享设备安全模式（复用他人已启动+已加入的会话，不 force-stop / 不 am start / 不 adb reverse）
python tests/android_ui_check.py --serial 9TM9K23404002482 --no-launch --reuse-forward --no-reverse
```

退出码：`0` 全部通过；`1` 存在断言/契约失败；`2` 真机或 CDP 环境不可用（无设备 / 无 WebView devtools / 服务器未监听）。

### 3) 本次实际执行的逐步命令

```powershell
adb devices
adb -s 9TM9K23404002482 shell wm size
python server.py 8765 --bots 2 --no-browser
python tools/cdp_eval.py "JSON.stringify(window.__mobileLayoutReport())"
adb -s 9TM9K23404002482 shell screencap -p /sdcard/ui_layout.png
adb -s 9TM9K23404002482 pull /sdcard/ui_layout.png $env:TEMP\ui_layout.png
node tests/mobile_layout_check.js
```

### 4) 本次产物

- 本报告：`测试/安卓验收/UI校验.md`
- 真机截图：`测试/安卓验收/ui_layout.png`

