# 茶庄园品控溯源系统

制茶师、品控员和庄园主协作管理茶园地块、制茶工序与溯源链条，确保每一批次茶叶从鲜叶到成品全程可追溯。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/base.py     共享服务骨架
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8000/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5173/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8000`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 茶园地块 | `tea_garden` | 地块 | 海拔高度、种植面积、茶树品种 |
| 鲜叶采摘 | `picking` | 采摘批次 | 采摘日期、采摘人员、芽叶标准 |
| 萎凋工序 | `withering` | 萎凋记录 | 摊晾厚度、翻动次数、失水率 |
| 杀青工序 | `fixing` | 杀青记录 | 锅温设定、杀青时长、投叶量 |
| 揉捻工序 | `rolling` | 揉捻记录 | 压力档位、揉捻时长、成条率 |
| 发酵工序 | `fermentation` | 发酵记录 | 环境温度、环境湿度、堆高尺寸 |
| 干燥工序 | `drying` | 干燥记录 | 起始温度、终止温度、干燥时长 |
| 精制分级 | `grading` | 分级结果 | 筛号规格、碎末比例、等级判定 |
| 拼配配方 | `blending` | 拼配批次 | 原料比例、拼配日期、口感评分 |
| 包装规格 | `packing` | 包装批 | 包装形式、净含量、标签文案 |
| 仓储养护 | `storage` | 养护记录 | 入库温度、入库湿度、陈化周期 |
| 检测报告 | `testing` | 检测报告 | 农残项目、检测机构、报告编号 |
| 认证资质 | `certification` | 资质证书 | 证书类型、发证机构、有效期至 |
| 溯源码段 | `trace_code` | 码段 | 起始码号、结束码号、激活日期 |
| 发运交接 | `shipment` | 发运单 | 收货方、物流单号、发运日期 |
| 样品留样 | `sample` | 留样记录 | 留样编号、取样节点、样品形态 |
| 不合格处置 | `defect` | 缺陷记录 | 缺陷类型、发现节点、处置方式 |
| 内审巡查 | `audit` | 巡查记录 | 巡查日期、巡查要点、整改期限 |

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 含水率补测与精制领取一致性

干燥与分级之间用「补测条」对齐结果，避免同一盘料出现两个互相矛盾的含水率。

- **补测条**：每次补测是本炉本盘的一次真实实测，登记在 `drying` 服务，
  只引用「当时所接的干燥确认记录」（`干燥记录ID`）。它不占用干燥记录的
  `上游工序 / 上游记录ID` 字段，与上一个工序引用相互独立。
- **按盘隔离**：补测条按「炉次 + 盘号」归属。补录某盘只作废**本盘**上一条
  有效读数；同炉其它盘的有效读数与领取单原样保留。同一盘两次实测都可查回，
  新旧读数通过 `替代了 / 被替代为` 串起来。
- **本盘一次有效**：精制领取只能用**本盘**当前唯一有效补测条；引用同炉其它盘
  或已作废的补测条会被拒绝。
- **已签收不重复也不撤销**：入秤签收过的领取单是既成事实。之后干燥再补报，
  既不会多领一次实物，也不会撤掉或改写这张单的实际含水结果。
- **未正式办理可重核**：分级已领出但界面响应未回、等待入秤确认时，若干燥工补报
  前次含水率偏高，该领取单转为「需重核」，并记下重核原因。分级台可查看当时
  接的是哪次干燥确认、本盘历次实测，并选择「继续按原交接」或「按新读数重核」。
  也允许在处理前重新提交，但只会读到同一张单，不会重新领料。
- **可查回**：每张领取单保留实际使用的补测条、含水结果、领取次数、重试原因与
  更换读数记录（`GET /api/grading-pickups/{id}`）。
- **刷新后重提**：关闭并刷新分级页后，按分级记录上的实际来源（炉次/盘号）重提
  确认；分级确认时会把实际使用的补测条与含水率固化到分级结果上。分级已确认后，
  新读数不再作用到该分级。

相关接口（独立于 18 模块通用资源路由）：

```text
POST /api/moisture-retests                          干燥工作台提交一次补测
GET  /api/moisture-retests?furnace=&pan=            查本盘历次实测
GET  /api/moisture-retests/pan-context?furnace=&pan= 重新进分级台时的本盘上下文
POST /api/grading-pickups                           领出本盘 / 重提确认（幂等、不重复领料）
GET  /api/grading-pickups/{id}                      查回实际含水结果与重试原因
POST /api/grading-pickups/{id}/sign                 入秤签收
POST /api/grading-pickups/{id}/resolve              需重核时：继续按原交接 / 按新读数重核
```

前端在干燥与分级工作台的每行提供「补测/领取」入口，展开
`frontend/src/views/MoistureConsistencyPanel.vue` 面板。


领域：应用开发
子方向：web前端/全栈开发
