# Cascade Forge 叶型几何数据库 v1

> v1 数据、ID 与 API 保持冻结兼容。v2 开发合同见本文末尾“Cascade Forge v2”，界面统一遵循 [design-system.md](design-system.md)。

## 1. 开发目标与边界

Cascade Forge 是面向轴流压气机和涵道风扇的可追溯叶型几何数据库工具。v1 将几何真值、叶栅上下文和未来气动工况严格分层：

$$\text{GeometrySpec}\rightarrow\text{AirfoilGeometry}\rightarrow\text{GeometryFeatures}\rightarrow\text{ValidationReport}$$

首版交付二维叶型生成、NASA 锚点导入、20,000 个有效叶型数据库、三维叶片曲面与 GeomTurbo 3.7 导出，以及本地 Web UI。CFD、网格、损失/偏差模型、AVDR 修正、AE/VAE、STEP 和闭合 STL 不在 v1 范围内。

## 2. 来源与设计依据

- Liu, Geng & Zhang, *Aerospace Science and Technology* 172 (2026) 111664：用于指导“设计需求、几何参数、坐标、物理特征”分层以及数据库到通流设计的接口边界。
- `D:\Project\Research\blade_datas`：只读旧项目。仅迁移 NASA120859 的 48 个原始截面和元数据；随机 B 样条增广输出不作为真值。
- 坐标约定：二维规范坐标中，前缘为 $(0,0)$，尾缘中点为 $(1,0)$，$y>0$ 指向吸力面。金属角相对轴向定义，安装角由弦线闭合条件求得。

## 3. 数学模型

### 3.1 中弧线

入口、出口金属角和 $1/4c$、$3/4c$ 的角度偏置构造自然三次角度样条 $\beta(x)$。求解安装角 $\gamma$ 使：

$$\int_0^1\tan[\beta(x)-\gamma]\,\mathrm dx=0$$

随后积分得到弦长坐标系内的中弧线。该构造保持目标入口/出口金属角，同时保证尾缘中点落在弦线上。

### 3.2 厚度

前段在 $q=\sqrt{x}$ 中构造约束多项式，首项系数 $\sqrt{2r_{LE}}$ 显式控制前缘半径；在最大厚度点满足目标 $t_{\max}/c$、$x_t/c$ 和零一阶导数。后段采用五次 Hermite 多项式，与前段在最大厚度点保持二阶连续，并显式满足尾缘厚度和尾缘半楔角。

### 3.3 ID 与追溯

`Geometry_ID` 是规范化 `GeometrySpec`、生成器版本和坐标约定的 SHA-256 截断值。弦长、稠度、工况、Re、Ma 和 AVDR 不参与二维几何 ID。

## 4. 工程结构

```text
UDF/
├── configs/                  # 版本化数据集配置
├── data/raw/nasa120859/      # 精选迁移的原始锚点
├── src/cascade_forge/        # Python 包
├── tests/                    # 单元、属性、集成与 API 测试
├── datasets/                 # 生成数据库（Git 忽略）
└── artifacts/                # 三维与导出制品（Git 忽略）
```

## 5. 接口

Python 公共入口：`generate_profile`、`validate_profile`、`extract_features`、`ingest_legacy_anchors`、`generate_dataset`、`build_blade`、`export_geomturbo`。

CLI：`cascade-forge ingest-anchors|generate|audit|export|build-blade|serve`。

本地 API 使用 `/api/v1`；Web 服务只绑定本机地址，不包含认证或外网部署能力。

## 6. 构建与运行

```powershell
cd UDF
uv sync --extra dev
uv run pytest
uv run ruff check src tests
uv build
uv run cascade-forge ingest-anchors --source data/raw/nasa120859 --dataset datasets/geometry-v1.0.0
uv run cascade-forge generate --config configs/geometry-v1.0.0.yaml --output datasets/geometry-v1.0.0
uv run cascade-forge audit --dataset datasets/geometry-v1.0.0
uv run cascade-forge serve --dataset datasets/geometry-v1.0.0
```

浏览器访问 `http://127.0.0.1:8000`。服务默认仅监听回环地址，HTMX 与 Plotly 均由本地包提供，不依赖 CDN。

## 7. 验收准则

- 中弧线与前缘闭合误差不超过 $10^{-10}c$。
- 目标金属角误差不超过 $0.05^\circ$；最大厚度误差不超过 $5\times10^{-4}c$。
- 20,000 个生成叶型 ID 唯一、可复现并通过 v1 几何规则；48 个 NASA 锚点全部可追溯。
- R1-e 的可疑尾缘半径保留原值并产生审计告警，不静默修正。
- GeomTurbo 写出与解析往返坐标误差不超过 $10^{-8}$ m。
- Web 预览、查询、比较、批次状态和三维导出在离线条件下通过测试。

## 8. 风险与限制

- v1 参数域比 Liu 2026 更宽，Sobol 提案存在被约束拒绝的正常情况。
- NASA 截面中的元数据和坐标来自历史人工整理；锚点身份不等于无条件几何真值。
- 三维输出是按 section schedule 构造的数值曲面，不是制造级 B-Rep/CAD。
- 当前验证是几何验证，不代表叶型具备良好气动性能。

## 9. 验证记录

### 2026-08-29：二维内核与锚点迁移

- 项目本地环境：Python 3.13.12、NumPy 2.5.2、SciPy 1.18.1、DuckDB 1.5.5、PyArrow 21.0.0。
- 1024 个固定种子 Sobol 提案中，605 个通过 v1 规则，有效率 59.1%；主要拒绝原因是极端组合下最大厚度未满足 $5\times10^{-4}c$ 误差门槛。
- NASA120859 共 48 个截面全部迁移并写入来源哈希：45 个 `valid`、R1-e 为 `warning`（可疑 `rte/c=0.036477`）、R2-d 与 S1-f 因原始几何质量规则为 `rejected`。拒绝状态不删除锚点，原始数据保持不变。
- `pytest` 与 Ruff 在数据库模块提交前均通过。

### 三维叶片与 GeomTurbo

- `SectionSchedule` 显式记录半径、弦长、几何 ID、安装角、积叠偏移、lean 与 sweep；所有截面必须按展向和半径严格递增。
- 三维变换采用 $(X,Y,Z)=(r\cos\theta,r\sin\theta,z)$，支持 CW/CCW 方向，并对中间截面做固定点数坐标插值。
- NASA R1 的 12 个锚点可由精选元数据构造 14 截面叶片并写出 GeomTurbo；写出后回读的坐标误差门槛为 $10^{-8}$ m。

### CLI、API 与 Web

- CLI 已实现 `ingest-anchors`、`generate`、`audit`、`export`、`build-blade` 和 `serve`。
- `/api/v1` 提供叶型列表/详情/预览、数据集批次任务、任务状态和三维叶片任务；任务由单写者线程执行，状态持久化在 DuckDB。
- Web 使用 FastAPI、Jinja2、HTMX 2.0.4 和离线 Plotly，提供参数预览、数据库浏览、双叶型比较和三维 schedule 预览；运行时不访问 CDN。

### 2026-08-29：`geometry-v1.0.0` 正式交付审计

- 固定配置：seed `20260829`、每侧 201 点、批次 512；Sobol 提案持续到恰好获得 20,000 个有效生成叶型。
- 数据集位置：`datasets/geometry-v1.0.0/`；共 20,048 个唯一几何 ID（20,000 个生成叶型 + 48 个 NASA 锚点），另保留 13,602 个拒绝提案的参数与原因。总计 70 个文件，约 168.9 MB。
- 生成叶型状态：19,603 个 `valid`、397 个 `warning`、0 个 `rejected`；warning 来自最大厚度位置的有限点离散误差，不改变几何有效性。
- 锚点状态：45 个 `valid`、R1-e 为 `warning`、R2-d 与 S1-f 为 `rejected`；所有 48 个锚点均保留，不静默修正原始异常。
- 全量 SQL 审计：20,048 个 ID 全部唯一；所有生成叶型均为每侧 201 点；不可变 Parquet 分区 SHA-256 全部匹配 manifest。
- 数学验收：最大弦线闭合误差 `0.0c`，最大入口/出口金属角离散误差 `0.0044901°`，最大厚度误差 `0.0004996755c`，均满足冻结门槛。
- 可复现性：按几何 ID 确定性抽取 128 个生成叶型，由 `spec_json` 重建，ID 与全部坐标逐点误差均为 0。
- 三维验收：NASA R1 的 12 个锚点重建为 14 截面、28 叶片的 GeomTurbo 3.7 制品；写出回读最大坐标误差 `4.999×10^-14 m`。
- 自动化验收：`pytest` 13 项通过；Ruff 通过；Python sdist/wheel 构建通过；CLI `audit`、`export`、`build-blade` smoke test 通过。FastAPI/HTMX API 端到端测试包含预览、查询、后台批次状态与离线页面资源。

## 10. 数据契约与恢复策略

- `tables/geometries/part-*.parquet` 与 `tables/rejected/part-*.parquet` 是不可变真值；`dataset_manifest.json` 记录其 SHA-256。
- `catalog.duckdb` 是可重建的查询索引与任务状态存储，因此不作为不可变哈希制品。DuckDB 视图以 `union_by_name` 合并锚点和生成分区的 nullable schema。
- 后台任务采用进程内单工作线程；同一 catalog 的所有 `DatasetStore` 实例共享可重入锁，避免 Windows 文件占用竞争。
- 再次执行相同 seed/config 会跳过已有 `Geometry_ID`；中断后可从已落盘 Parquet 分区继续，目标计数不会超出。
- 大型数据库与三维制品由 `.gitignore` 排除；源码、配置、测试、精选锚点和来源 manifest 纳入 Git。

## 11. 已知限制与后续版本

- `warning` 不是气动性能评级；v1 没有 CFD、损失、偏差、失速裕度或可制造性验证。
- 当前叶栅喉道是几何代理检查，不替代周期性网格或流动求解。
- AVDR 将采用独立修正库，不进入二维 `Geometry_ID`；更窄或更宽的参数域应创建新的版本化 YAML 和数据集，不覆盖 v1。
- GeomTurbo 输出为本项目支持的 SECTIONAL 3.7 子集；跨软件导入仍应在目标工具中做一次独立验收。

## 12. Cascade Forge v2.1：轴向坐标与金属角合同

### 内核与坐标

v2.1 使用独立推导的统一三次开放夹持 B-spline/G2 内核：中弧线按解析法线方向加厚，双侧叶面由约束最小二乘求解，LE/TE 半径表示鼻点密切圆半径。二维正式坐标始终保存于弦线局部坐标系，显示与三维构造时再施加安装角。

内部与画布采用 NASA 轴流叶栅约定：`x` 为下游轴向，`y` 为正切向，安装角 `γ` 是弦线相对正轴向的有符号角。进口/出口金属角是中弧线端点切线角，不是流动角。端点局部角为：

$$\alpha_{LE}=\beta_{LE,a}-\gamma,\qquad \alpha_{TE}=\beta_{TE,a}-\gamma$$

界面可在轴向 `βa` 与 ANSYS 的切向互补表示 `βt=90°−βa` 间切换。后端一律规范化为有符号轴向角；参考系切换不发起几何计算。安装角改变时，两端轴向金属角同步增加相同增量，使局部端点角、局部坐标、Intent ID 和 Geometry ID 不变，预览坐标仅作刚体旋转。

定义依据：

- [NASA 轴流叶栅定义](https://ntrs.nasa.gov/api/citations/19700002405/downloads/19700002405.pdf)
- [NASA Wind-US TURBOSPEC](https://www.grc.nasa.gov/www/winddocs/beta/user/keywords/turbospec.html)
- [ANSYS BladeGen Beta 参考方向](https://ansyshelp.ansys.com/public/Views/Secured/corp/v252/en/wb_bm/i346269909882346230939103.html)
- [ANSYS NDF 坐标合同](https://ansyshelp.ansys.com/public/Views/Secured/corp/v252/en/wb_bm/i157968684677956.html)

### 数据与产品合同

- schema：`2.1.0`
- generator：`unified-bspline-g2-v2.1`
- Geometry ID：只由解析后的局部曲线、边缘合同、生成器和坐标约定决定，不含安装角和显示参考系。
- Intent ID：由局部端点角和形状参数决定，不含安装角和显示参考系。
- 特征同时输出轴向/切向/局部进口及出口角、安装角和只读金属角差。
- NASA 原始 48 个截面及来源哈希保持只读；R1-e 异常告警不被静默修正。
- 工作室不再提供高级模式或控制点拖拽。控制点是 Plotly 原生只读轨迹；完整显式合同只能在“后台 Spec JSON”中查看、修改、恢复、复制或导入导出。

### 数据初始化

2026-09-03 已清空旧 v1/v2 正式库、anchor-audit、workspace 和批次状态。启动只创建空 schema，不生成任何记录。进入 Web 的“数据集初始化”后依次明确执行：

1. 在“NASA 锚点”页生成或恢复 48 个拟合。
2. 门禁通过后生成或恢复 20,000 个有效叶型。
3. 在“审计”页执行完整审计。

NASA 原始文件、源码、配置和已有导出制品未删除。被清理的数据库已移入 Windows 回收站，可在尚未清空回收站时恢复。

### 快捷启动

在项目根目录执行：

```powershell
.\UDF\start.ps1
```

或：

```powershell
cd UDF
.\.venv\Scripts\python.exe -m cascade_forge.cli serve --dataset datasets/geometry-v1.0.0 --v2-dataset datasets/geometry-v2.1.0 --workspace datasets/workspace-v2
```

浏览器打开 `http://127.0.0.1:8000/`。可通过 `.\UDF\start.ps1 -Port 8010` 改端口。

### 本轮验证

- 轴向/切向往返与同一叶型身份测试通过。
- 安装角同步变化保持局部坐标和 ID 不变，显示坐标满足二维刚体旋转。
- 实际端点切线误差门槛仍为 `0.05°`。
- 真实 Chromium 验证了数值/滑条同步、参考系切换、tooltip、原生控制点缩放，以及 320、375、768、1024、1440 px 无水平溢出、modebar 不越界。
- LE/TE 局部视图与主画布均使用轴向—切向坐标，显示密切圆、圆心、鼻点、切线和实际 `r/c`。
