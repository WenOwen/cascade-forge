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

## 12. Cascade Forge v2 开发合同

### 目标与兼容边界

v2 新增 Bezier、B-spline 与 NURBS 可组合中弧线/厚度内核，厚度沿中弧线法向叠加，并以真实圆弧闭合前尾缘。现有 `GeometrySpec`、`geometry-v1.0.0`、`/api/v1` 和全部 v1 ID 不迁移、不重写。v2 使用 schema `2.0.0`、独立配置、独立 ID 和 `/api/v2`。

AutoBlade 仅作为几何语义参考：Simple Bezier 中弧线、双侧 B-spline 厚度、`ADDITION_TYPE NORMAL`、圆边和楔角。v2 不读写 `.par/.abp`，也不调用商业软件。

### 数学合同

每条曲线必须提供位置及一、二阶解析导数。中弧线 `C(u)` 的吸力侧单位法向为：

$$\mathbf n(u)=\frac{(-C_y'(u),C_x'(u))}{\|\mathbf C'(u)\|}$$

中央吸力面和压力面由双侧标量半厚度给出：

$$\mathbf S(u)=\mathbf C(u)+t_s(u)\mathbf n(u)$$

$$\mathbf P(u)=\mathbf C(u)-t_p(u)\mathbf n(u)$$

Bezier 中弧线使用 4 个控制点；B-spline/NURBS 中弧线基础模式使用 6 个三次开放夹持控制点。Bezier 厚度使用 7 个控制值；B-spline/NURBS 厚度使用 7 个三次开放夹持控制值。NURBS 权重必须严格为正。

基础模式的工程参数不能冒充唯一曲线定义。解析器以固定初值和边界约束求解显式控制点、节点和权重，并把解析残差写入 spec；高级模式直接接收完整显式曲线。Geometry ID 只根据解析后的显式定义、边缘合同、生成器版本和坐标约定计算。

前缘和尾缘分别求解给定半径的圆与两侧中央曲线的 G1 接触点，裁剪中央曲线后插入真实圆弧。最终每侧从前缘鼻点到尾缘鼻点重采样 201 点，并额外保存完整边缘圆弧用于审计和 Web 放大视图。

### 数据与产品合同

- v2 正式库恰好交付 20,000 个有效生成叶型：2,222 个完整九组合 intent 组产生 19,998 个记录，另固定补充 `Bezier×B-spline` 与 `NURBS×NURBS`。
- 48 个 NASA 原始锚点保持不变；每个锚点产生九种派生拟合，共 432 条，并保存 RMS、最大点距、Hausdorff 近似和拟合状态。
- 正式数据集只读；手工预览和控制点编辑保存到独立 workspace。
- Web UI 覆盖全部公共 Python API 和 CLI 工作流，采用多页面本地工作台；不把内部私有函数暴露为产品功能。

### v2 验收门槛

- 中央法向偏置误差不超过 `1e-10c`，轮廓闭合不超过 `1e-10c`。
- LE/TE 半径误差不超过 `1e-8c`，圆弧 G1 相切误差不超过 `0.05°`。
- 金属角误差不超过 `0.05°`，最大厚度误差不超过 `5e-4c`，位置误差不超过 `0.005c`。
- 九种曲线组合均无负厚度、自交和非法节点/权重；同配置重复运行得到相同 intent、ID、坐标、配额和 manifest。
- Web 在 320、375、768、1024、1440px 无页面横向溢出，Plotly modebar 始终位于画布容器内。

### v2 曲线内核验证记录（2026-08-31）

- `ruff check src tests`：通过。
- `pytest -q`：27 项通过，包含全部 v1 回归测试。
- 九种中弧线/厚度组合均完成 201 点闭合轮廓生成；解析导数、NURBS 等权退化为 B-spline、权重整体缩放不变性均通过。
- 默认验证意图的法向偏置残差小于 `1e-10c`，LE/TE 圆弧半径误差小于 `1e-8c`，解析 G1 相切误差小于 `0.05°`。

### v2 特征、验证与锚点拟合记录（2026-08-31）

- `pytest -q`：29 项通过；v1 特征、验证和 API 回归保持通过。
- v2 验证使用实测边缘圆弧、解析端点切向、完整闭合轮廓和法向偏置残差；规则版本为 `geometry-rules-v2`。
- 只读加载 NASA R1/R2/S1/S2 共 48 个锚点，批处理数量合同验证为 432 个派生拟合；每个派生记录均保留原锚点 ID、显式曲线 spec、三项距离误差与有效/警告/拒绝状态。
- R1-e 的异常尾缘半径继续作为来源告警保留；若边缘值超出 v2 合同，只在派生 spec 中限幅并同时记录原值，不写回原始锚点。

### v2 数据层与批处理记录（2026-08-31）

- 新增独立 `DatasetStoreV2`：schema migration、正式只追加 Parquet、DuckDB 过滤视图、逐批哈希 manifest、失败提案和锚点拟合分区均与 v1 隔离。
- 新增独立 `WorkspaceStore`：手工设计按名称递增修订号，支持 JSON 导入/导出，不允许修改正式数据分区。
- Sobol 采样配额固定为 2,222 个完整九组合 intent 组和两个固定补额；失败组不保存坐标，只保存工程意图、组合、规则和原因。
- 恢复点只在 Parquet 与批次 manifest 落盘后原子推进；相同配置重复执行不会追加重复 Geometry ID。
- `audit_dataset()` 和 `generate_dataset()` 已按 manifest/config schema 自动分派 v1/v2；34 项 pytest 与 Ruff 全部通过。
- 将边缘半径固定点迭代与轮廓自交检查向量化后，默认九组合基准由约 17.4 秒降至约 7.0 秒；正式 20k 仍采用多进程、分批落盘和中断恢复。
