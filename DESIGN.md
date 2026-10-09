# travel-agent · 设计文档

> 一个**脱离 DSH 独立运行**的旅游 agent。
> 目标：需求澄清 → 三源比价（酒店/火车/民宿）→ 总成本排序 → 行程单 → 关注项盯价提醒。
>
> 设计日期：2026-09-30 ｜ 所有数据源均为**本项目实测验证过**的，不是文档抄来的。

---

## 零、实现说明（2026-10-09 落地时修订）

本文档原按 **Node.js** 设计，实际实现改为 **Python 3.13**，并在落地时改了两处数据源决策：

| 项 | 原设计 | 实际实现 | 原因 |
|---|---|---|---|
| 语言 | Node.js ≥ 18 | **Python 3.13，零第三方依赖** | 项目目录已是 PyCharm + venv 的 Python 工程；HTTP 用标准库即可 |
| 火车源 | 魔搭托管 12306 MCP | **12306 原生接口**（`sources/rail12306.py`） | 托管实例 URL 于 2026-10-09 过期（HTTP 410），原生接口免密钥、一直可用，且**带真实票价** |
| 飞猪 | 自实现 HMAC 签名 | **调官方 CLI bundle 子进程**（`tools/flyai-cli/`） | 签名要 `signSecret` + AES-256-GCM 设备指纹，bundle 里才有；子进程最省事、可回退 |
| 出图 | 复用 `tools/render-chart.mjs` | 同左（M3 阶段接入） | 中文 / ★ / ¥ 渲染正常，且不需要审批 |

接口的一手实测细节全部搬到 [接口速查.md](接口速查.md)，本文只留设计与取舍。
目录结构以 [README.md](README.md) 为准（`sources/` 多了一个 `rail12306.py`，`core|plan|report|watch` 按本文分法）。

**已知约束**：飞猪 bundle 内置的是**体验凭证**，价格打码为 `¥1xx`；要真实价格必须拿到正式 API Key。
在拿到之前，民宿只用于「发现候选」，价格以 RollingGo 为准。

---

## 一、为什么独立、以及独立意味着什么

DSH 里的 MCP 好用，但依赖宿主。独立程序要自己承担三件事：

1. **自己调 MCP 端点**（三个源全都是 HTTP JSON-RPC，不用 MCP SDK，直接 POST 即可）
2. **自己处理鉴权**（RollingGo 是 Bearer；12306 免密钥；**飞猪要 HMAC-SHA256 签名**）
3. **自己做后置过滤**（这是核心价值，见第四节）

**技术选型：Node.js ≥ 18**（无构建步骤、自带 fetch）
理由：飞猪官方 CLI 是 Node；工作区已有的 `tools/render-chart.mjs`、`tools/fetch.mjs` 也是 Node，可直接复用。

---

## 二、目录结构

```
travel-agent/
├─ README.md
├─ DESIGN.md                 ← 本文档
├─ package.json              （type: module，零运行时依赖）
├─ config.json               （gitignore；密钥放这里）
├─ config.example.json
├─ src/
│  ├─ sources/               ── 数据源适配层（每个源一个文件，互不依赖）
│  │   ├─ rollinggo.js       酒店：B2B 批发价 + 官方预订链接
│  │   ├─ rail.js            火车：12306 实时余票/票价
│  │   └─ flyai.js           民宿/度假村：飞猪官方（含签名）
│  ├─ core/                  ── 与渠道无关的纯逻辑（可单测）
│  │   ├─ normalize.js       三源 → 统一 Candidate 模型
│  │   ├─ postfilter.js      本地后置过滤（修上游过滤失效）
│  │   ├─ cost.js            总成本 = 房费×晚数 + 两人往返交通
│  │   └─ rank.js            排序 + 自动生成"亮点/短板"
│  ├─ plan/
│  │   ├─ intake.js          需求澄清（问答式；可被 LLM 或表单驱动）
│  │   └─ itinerary.js       逐日行程生成（模板 / LLM 增强二选一）
│  ├─ report/
│  │   ├─ markdown.js        行程单 .md
│  │   └─ chart.js           调 ../tools/render-chart.mjs 出 PNG
│  ├─ watch/
│  │   ├─ monitor.js         盯价轮询（间隔、阈值、去重、历史快照）
│  │   └─ notify.js          提醒渠道（桌面通知 / 文件 / 可选 webhook）
│  └─ cli.js                 命令入口
└─ state/                    （gitignore）盯价快照、去重标记
```

**分层原则**：`sources`（会变、会坏）→ `core`（纯函数、可测）→ `plan/report/watch`（业务）。
换数据源只动 `sources/`；换界面只动 `cli.js`（或以后加 `web/`）。

---

## 三、数据源规格（本项目实测，非文档）

### 3.1 RollingGo 酒店
- `POST https://mcp.rollinggo.cn/mcp`
- 头：`Authorization: Bearer mcp_xxx`、`Content-Type: application/json`、`Accept: application/json, text/event-stream`
- 工具：`searchHotels` / `getHotelDetail` / `getHotelSearchTags`
- ⚠️ **价格口径**：`price.lowestPrice` 是**每晚均价**，不是总价（`price.message` 里写着"N晚总价：X（约Y/晚）"，Y 才是这个字段）

### 3.2 12306 火车
- `POST https://mcp.api-inference.modelscope.net/<实例id>/mcp`（魔搭托管，免密钥）
- 工具：`get-tickets`（余票）、`get-interline-tickets`（中转）、`get-train-route-stations`、`get-stations-code-*`
- ⚠️ **站名必须写全**：`南浔` 查不到，要写 `湖州南浔`

### 3.3 飞猪 民宿/度假村
- `POST https://flyai.open.fliggy.com/mcp`，工具 `fliggy_ai_search`
- 头（缺一不可）：
  - `Authorization: Bearer sk-xxx`
  - `x-flyai-sign-ver: 7`、`x-flyai-sign-alg: hmac-sha256`、`x-flyai-ts`、`x-flyai-nonce`、`x-flyai-sign`
  - `x-ff-ctx`（gzip 后的设备指纹 JSON，用 `sha256(signSecret)` 做 AES-256-GCM 加密后 base64）
  - `x-ttid: ai2c(sk.clawhub)`、`User-Agent: flyai-cli/1.0.6`
- **签名串**：`method\npathname\ntimestamp_ms\nnonce\nsha256(body)\nsha256(auth)`
  → HMAC-SHA256(signSecret)，urlsafe-base64 去掉 padding
- 另有官方 CLI（`@fly-ai/flyai-cli`）可用，本项目的 `sources/flyai.js` 直接实现协议，不依赖它

---

## 四、核心：后置过滤（本项目最重要的一段逻辑）

**三个源的过滤参数全都不可靠**，实测记录：

| 发生的坑 | 现象 |
|---|---|
| RollingGo `requiredTags` 失效 | 要求"度假酒店+户外泳池"双硬约束，返回 0★ 汽车旅馆 |
| RollingGo 地点串台 | 查"南通"返回**天津**香格里拉 |
| RollingGo `tags` 字段造假 | 泰州某酒店标"户外泳池"，设施清单和简介都没有泳池 |
| 飞猪 `--key-words` 失效 | 查"慧心谷"返回电竞酒店 |
| 飞猪 `--hotel-types` 失效 | 指定只要民宿，返回一堆"精选酒店" |
| 飞猪 `--dest-name` 模糊 | 查"莫干山"命中杭州**莫干山路**；查"临安"返回拱墅 |
| 12306 白天班次 | 国庆期间 10/2、10/3 白天全部无票，只有凌晨/夜车 |

因此 `core/postfilter.js` 必须实现：

1. **地点校验**：候选的 `address` 必须包含目标行政区关键字，否则丢弃（修"串台"）
2. **类型校验（民宿）**：`name` 命中 `民宿|客栈|小院|乡居|美宿|人家|山居|院子` 才算民宿
3. **设施校验（泳池/温泉）**：**只在 `hotelAmenities` 和简介正文里找**，**绝不信 `tags`**
4. **价格校验**：自己按"每晚价 ≤ 预算"卡，不依赖上游 `maxPricePerNight`
5. **站名校验**：查询前先跑一次站点解析，失败了自动补全城市名重试

---

## 五、总成本模型

```
总成本(2人) = 每晚房价 × 晚数 + 两人往返交通
```

- 交通用 12306 实查价（二等座优先；无票时标注"仅无座/需改期"）
- 市内交通、餐食**不计入排序**，但出行程单时给估算
- **排序按总成本**，不按星级（星级不是线性质量）

> 这条模型是这轮筛选的胜负手：云南房价 ¥282/晚看着便宜，但机票让总成本翻倍；
> 江浙 ¥374/晚反而总价最低。**只比房价必然选错。**

---

## 六、盯价与提醒

```
node src/cli.js watch add --candidate <id> --below 350
node src/cli.js watch run            # 跑一轮，写快照、判断阈值、发提醒
node src/cli.js watch list
```

- 快照存 `state/watch.json`：`{id, 阈值, 历史[{ts, price}], 上次提醒时间}`
- **去重**：同一候选同一天只提醒一次（参考工作区已有教训：幂等标记写文件）
- **提醒渠道**（按可靠性排序）：
  1. **写文件**（最稳，永远做）：`state/alerts/YYYY-MM-DD.md`
  2. **Windows 桌面通知**：PowerShell + `Windows.UI.Notifications`
  3. **Webhook**（可选）：微信/钉钉/飞书机器人
- ⚠️ **QQ 群发不出主动消息**（工作区 `群背景.md` 实测 `delivery-failed`）→ 盯价提醒**不能走 QQ**，别设计成那样

---

## 七、CLI 命令设计

```bash
# 全流程：澄清 → 比价 → 行程单
node src/cli.js plan --from 上海 --days 7 --nights 6 --people 2 \
                     --budget 400 --transport-cap 3000 --prefer 安静,温泉 \
                     --date 2026-10-03 --out ./out/

# 只比酒店
node src/cli.js hotels --dest 扬州 --check-in 2026-10-03 --nights 6 --max 450

# 只查车
node src/cli.js train --from 上海 --to 扬州 --date 2026-10-03

# 只查民宿
node src/cli.js homestay --dest 德清 --check-in 2026-10-03 --nights 6

# 盯价
node src/cli.js watch add --candidate <id> --below 350
node src/cli.js watch run
```

---

## 八、里程碑

| 阶段 | 内容 | 验收 |
|---|---|---|
| **M1 数据层** | 三个 `sources/*.js` 各跑通一次真实查询 | 三份原始 JSON 落盘 |
| **M2 核心层** | normalize + postfilter + cost + rank | 用今晚已知坑做回归（泳池造假、地点串台、民宿冒充） |
| **M3 输出层** | markdown 行程单 + chart 出图 | 生成一份可交付行程单 |
| **M4 盯价** | monitor + notify + 幂等 | 改一次测试价，能收到且只收到一次提醒 |
| **M5 全流程** | `plan` 一条命令跑完 | 用它复现"扬州方案" |

---

## 九、待定（需你拍板）

1. **智能程度**：行程安排用**模板**（零依赖、确定性）还是**LLM 生成**（需 DeepSeek API key）？
2. **界面**：先 CLI，还是直接上 Web？（`core` 与渠道无关，后加 Web 不影响前面）
3. **提醒渠道**：桌面通知 + 文件够不够，要不要再加 webhook？

## 十、明确不做（避免范围膨胀）

- ❌ 不做下单/支付（三个源里只有飞猪支持下单，且需 OAuth 用户授权，风险高）
- ❌ 不做爬虫（携程/小红书无开放接口，抓取有合规与失效风险）
- ❌ 不依赖 DSH 运行时（可独立跑；但允许复用工作区的 `tools/render-chart.mjs`）
