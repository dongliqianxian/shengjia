---
name: winols-noread-remover
description: >
  WinOLS / EVC「NOREAD 再导入禁令」去除技能。当用户读取/导入 ECU、TCU bin 文件时，
  WinOLS 报出「This file cannot be processed, since it was marked by another customer
  with a re-import ban (NOREAD, encrypted / hidden)」「This project was imported despite
  NoREAD and may therefore only be programmed/exported」「MSG1872」「MSG2383」
  「再导入禁令」「re-import ban」「只能 program/export」「无法编辑/看不了 map」
  「NR 文件」「NoRead 文件」「去除 NOREAD」「解锁文件」等情况时，必须立即调用本技能。
  即使用户只说「这个文件打不开图」「WinOLS 说只能导出」「改装文件导入报错」，也要调用。
  覆盖两种形态：明文页脚水印，以及现代 WinOLS 5.x 的隐写式（encrypted/hidden）水印
  —— 后者需要同版本原厂 bin 做差分。本技能只做水印检测与去除，不输出标定方案。
---

# WinOLS / EVC NOREAD 再导入禁令去除

## 角色

熟悉 WinOLS / EVC 工程文件结构与 ECU bin 分区布局的标定工程师。目标单一：让一个被打了
NOREAD 的文件能在 WinOLS 里重新作为原始文件 import、正常看图与编辑，**且不破坏任何活动
MAP 数据、不影响 ECU 自身 checksum**。

## NOREAD 是什么（先想清楚再动手）

NOREAD **不是** ECU 的读取保护，也不是 ECU 固件的一部分。它是 **上游调校者 / EVC 插件
主动写进 bin 的"再导入禁令"水印**——目的是让拿到文件的人只能 program/export 刷写，
不能再 import 进 WinOLS 反编辑/扒程序。两种典型报错：
> MSG2383 · This file cannot be edited, since it was marked by another customer with a
> re-import ban (NOREAD). But you can still import and program it.
> MSG1872 · This file cannot be processed, since it was marked by another customer with a
> re-import ban (NOREAD, encrypted / hidden).

关键事实：
- **老式（A 类）**写在标定分区尾部的**空闲填充区**（原生填充常见 `0xC3`/`0xFF`/`0x00`），
  明文 ASCII：`NOREAD`、`EVC.EVC.`、`Tag:xxx` 加一段签名块。落在空闲区、不属于被校验
  数据 → 去掉不影响 ECU checksum。去除 = 还原成该区的原生填充字节。
- **新式（B 类，WinOLS 5.x 报 `encrypted / hidden` 的就是它）**没有任何明文，payload
  被**隐写进真实标定数据里**。去掉它**会动到被校验的数据 → checksum 必须重算**。
- 两类都**不是 ECU 的读保护**，跟车、跟刷写工具无关，只跟 WinOLS 的 import 检查有关。

## 三类 NOREAD（决定用哪条路）

| 类型 | 特征 | 处理 |
|------|------|------|
| **A. 明文 / 旧版** | bin 里能直接搜到 `NOREAD`/`EVC.EVC` 字符串，且位于空闲填充区 | `scripts/noread_remove.py` 自动去除 |
| **B. 隐藏 / 加密（现代 WinOLS 5.x 主流）** | 明文搜不到；报错括号里写 `encrypted / hidden`。payload 被**隐写进真实标定数据的一个字节车道** | `scripts/noread_hidden.py --ori` 差分去除 |
| **C. 其它未知形态** | 上面两条都命中不了 | 停手，需 EVC 原版插件 / 找上游要干净 mod |

### B 类（隐写水印）的工作原理 —— 必须理解才敢动手

WinOLS 导出时勾选 re-import ban，它做的事是：

1. 在 flash 里挑一段**平滑/单调的标定数据**当"载体"——最爱挑**线性化表 / 传感器
   转换曲线 / 轴**这种逐格缓慢递增的 uint8 斜坡；
2. 只改这段数据里 **uint16 对齐的某一个字节车道**（小端 ECU 改低字节 = LSB 车道，
   大端改高字节），把 `原值 + 一个小的有符号偏移` 写回去，用偏移量携带比特；
3. 偏移量刻意**伪装成周围的数值水平**，所以：
   - 物理值只动千分之几 → **刷了车照跑，测不出来**；
   - 明文里啥也搜不到 → HxD 搜 `NOREAD` 一无所获；
   - 但按字节看，**原本单调的曲线被打成锯齿**。

这就是它的破绽。判据三件套（三条同时成立才算数）：

| 判据 | 水印 | 正常调校改动 |
|------|------|--------------|
| **车道纯度**：改动是否 100% 落在同一奇偶偏移 | 是 | 否（调校两条车道都动） |
| **粗糙度比**：MOD 二阶差分 / ORI 二阶差分 | ≥ 4×，实测常见 20~40× | ≈ 1×（调校保持曲线形状） |
| **原厂该区是否本来就平滑** | 是（`rough_ORI` < 3） | 无所谓 |

只用"车道纯度"一条会误判：uint16 表做小幅缩放时也可能只动一条车道，
但它**不会破坏平滑度**。必须叠加粗糙度比。

## 标准工作流

### 第 0 步：判型
在 bin 里搜 `NOREAD` / `EVC.EVC` 明文。搜得到 → A 类，走 `noread_remove.py`。
搜不到 + 报错写着 `encrypted / hidden` → B 类，往下走。

### 第 1 步：搞到同软件版本的**干净原厂 bin**（B 类的成败关键）
差分模式是唯一**结论可证**的路：有原厂 bin，才能把"水印字节"和"调校字节"
一刀切开，做到**只还原水印、一个调校字节都不碰**。
原厂 bin 来源：车上 OBD/bench 读原车、同零件号原厂库、客户备份。
零件号在文件名/bin 内 ASCII 里（丰田 DENSO 形如 `89663-XXXXX`）。

### 第 2 步：扫描（不写文件）
```
python scripts/noread_hidden.py --ori 原厂.bin --mod 带NOREAD.bin --scan
```
逐簇打印 `车道纯度 / rough_ORI / rough_MOD / 比值 / 判定`。
**人工复核**：被判为水印的簇，`比值` 应该是两位数以上，且 `rough_ORI` 接近 0。
若一堆簇都被判成水印 → 阈值不合适或 ORI 版本不对，停手，别 --fix。

### 第 3 步：去除
```
python scripts/noread_hidden.py --ori 原厂.bin --mod 带NOREAD.bin
```
自动备份 `*.bak`，输出 `*_NOREAD-removed.bin`。脚本会自检：
大小不变 / 相对 MOD 只改了水印那几十个字节 / 相对 ORI 仍完整保留全部调校 /
载体区粗糙度回到原厂水平。

### 第 4 步：自证已清除（很重要，能当交付凭证）
```
python scripts/noread_hidden.py --mod 带NOREAD.bin        --blind --scan
python scripts/noread_hidden.py --mod 原厂.bin            --blind --scan
python scripts/noread_hidden.py --mod 输出_NOREAD-removed.bin --blind --scan
```
盲扫用「单调载体 + 单车道锯齿 + 注入值伪装成周围水平」三条指纹找候选。
**看的是三者的差集**：带 NOREAD 的文件应该比原厂多出若干候选，
去除后应该**回落到与原厂完全一样的候选集**。回落了 = 干净了。

### 第 5 步：回 WinOLS
把输出文件**作为全新原始文件 import**（不是 open 旧工程）。
能进去看图、能编辑 = 成功。

### 第 6 步：校验和（别忘）
去水印改的是**真实标定数据字节**（虽然只有千分之几），
**ECU 校验和必然失效**。刷写前必须过一遍：
WinOLS checksum 模块 / ECM Titanium Auto-Fix / 刷写工具的自动校验和。
只在 WinOLS 里看图不刷车，可以不管。

### 拿不到原厂 bin 时（降级方案，说实话：不好用）
```
python scripts/noread_hidden.py --mod 带NOREAD.bin --blind --scan
```
**实测局限**：盲扫在真实案例里给出 3 个候选，其中 2 个是真水印，1 个是
原厂文件里本来就有的正常数据（对照扫原厂 bin 就能看出来）。
没有原厂做对照，**分不清哪个是水印**。
`--blind --blind-fix` 会用同车道邻值线性插值重建，那是**推测性修复、会改真实
标定数据**，只有在完全拿不到原厂 bin 且能接受风险时才用，且必须人工核对曲线。

## 手工兜底（HxD，明文页脚）
1. HxD 打开 bin，Search → Text-string 搜 `NOREAD`。
2. 确认它周围是一段连续填充（如 `0xC3`）——证明在空闲区。
3. 从 `NOREAD` 起，把整段水印页脚（含 `EVC.EVC`、`Tag:`、其后的签名/哈希块，
   直到进入大段终端 padding 之前）全部填成 **周围那段原生填充字节**（不是凭空填 0）。
4. 保存 → 在 WinOLS 里 re-import。

## 常见误区（必须主动提示用户）
- ❌ 搜不到 `NOREAD` 明文就下结论"这文件没被标记" —— 现代 WinOLS 5.x 用的就是
  隐写式，明文永远搜不到。要走 B 类差分流程。
- ❌ 只凭"改动全在偶数偏移"就判成水印 —— uint16 小幅缩放也会这样。
  必须叠加"粗糙度暴涨"这一条。
- ❌ 没有原厂 bin 就用 `--blind-fix` 硬修 —— 那是猜，会动真实标定。
- ❌ 以为去水印不影响校验和 —— 影响，因为水印就写在标定数据里。
- ❌（A 类）把页脚填成 `0x00`/`0xFF`，而周围原生填充是 `0xC3` —— 会留下不一致痕迹，
  正确做法是**匹配周围填充字节**。
- ❌（A 类）标记嵌在活动数据里还硬删 —— 会破坏 MAP，必须停手。
- ❌ 删完忘了在 WinOLS 重新 import + checksum 验证。

## 边界（本技能不做的事）
- 不做任何标定 / Stage 方案（交给 `stage1-calibration-matrix`）。
- 不做 MAP 拆解（交给 ecu-map-analysis）。
- 不解密码保护 / 加密整文件、不破狗、不动 ECU 自身读保护。
- 不解水印 payload 的内容（不去还原"是谁标的"），只做去除。
- B 类没有同版本原厂 bin 时，不承诺能可靠去除 —— 盲扫只给候选。

## 合规提示
NOREAD 是上游调校者放置的防盗水印。去除前确认你对该文件有合法处置权
（如自有客户车辆的 bench 读取、或自己拥有的文件）。
