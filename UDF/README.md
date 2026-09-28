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
圆弧族另有独立入口（见第 17 节）：`build_arc_chain`、`build_mca_mean_line`、`generate_arc_profile`、`build_thickness_law`、`junction_station_range`、`maximum_feasible_half_wedge_deg`。

CLI：`cascade-forge ingest-anchors|generate|audit|export|build-blade|serve|naca65|arc`。

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
.\start.cmd
```

或：

```powershell
cd UDF
.\.venv\Scripts\python.exe -m cascade_forge.cli serve --dataset datasets/geometry-v1.0.0 --v2-dataset datasets/geometry-v2.1.0 --workspace datasets/workspace-v2
```

也可以直接双击项目根目录的 `start.cmd`。浏览器打开 `http://127.0.0.1:8000/`；在命令提示符中执行 `start.cmd 8010` 可改用 8010 端口。启动器会从自身位置解析项目路径，因此不要求预先切换目录。

### 本轮验证

- 轴向/切向往返与同一叶型身份测试通过。
- 安装角同步变化保持局部坐标和 ID 不变，显示坐标满足二维刚体旋转。
- 实际端点切线误差门槛仍为 `0.05°`。
- 真实 Chromium 验证了数值/滑条同步、参考系切换、tooltip、原生控制点缩放，以及 320、375、768、1024、1440 px 无水平溢出、modebar 不越界。
- LE/TE 局部视图与主画布均使用轴向—切向坐标，显示密切圆、圆心、鼻点、切线和实际 `r/c`。

## 13. NACA 65/65A 原生生成与拟合基准

> **历史版本说明：本节 v1.0.0 拟合结论已因中弧线分解与距离评估问题失效，不能用于证明参数化质量。原始 sixseries 坐标仍有效。当前方法与结果以第 14 节为准；旧库仅作归档，不覆盖。**

### 数学内核与来源

新增纯 Python/NumPy 的 NASA sixseries 移植，支持 NACA 65、65A、`t/c`、设计升力系数 `Clᵢ` 与载荷参数 `a`。65A 按原始算法固定 `a=0.8`。厚度由 sixseries 共形映射表生成，中弧线采用 a 型载荷分布，厚度沿中弧线法线叠加。每个案例同时保存 176 点/侧的 `raw_native` 和 201 点/侧、统一尾缘的 `normalized_closed`。

开发参考固定在 OpenVSP commit `51bdec01d9a50fa4bdbc960b0def21dcd6330f72`，运行时不依赖 OpenVSP、C 或 Fortran：

- `NACA_6_series.c` SHA-256：`e41a48399647340fa09eb4f015a1d119a0da8d7959f6cfc43badea849964c342`
- `NACA_6_series.f` SHA-256：`59f5caf05868e9cb15accba897396f3f24b90837540e3980e4cc7f261b2c232d`
- 参考源码：[OpenVSP sixseries](https://github.com/OpenVSP/OpenVSP/tree/51bdec01d9a50fa4bdbc960b0def21dcd6330f72/src/external/sixseries)

压气机编号解析支持 `65-010`、`65-410`、`65-810` 和 `65-(12)10`。编号是 NACA 65 通用参数的快捷输入，不作为 65A 别名。`native_id` 由规范参数、算法版本和尾缘规则确定；显示安装角不参与身份。

### API、CLI 与 Web

公开 Python API 为 `generate_naca65`、`fit_reference_geometry`、`evaluate_reference_fit` 和 `run_naca65_benchmark`。HTTP 路径集中在 `/api/v2/naca65`，CLI 使用以下命令：

```powershell
cd UDF
.\.venv\Scripts\python.exe -m cascade_forge.cli naca65 generate --preset 65-810 --destination output/naca65-810.json
.\.venv\Scripts\python.exe -m cascade_forge.cli naca65 fit --preset 65-810 --destination output/naca65-810-fit.json
.\.venv\Scripts\python.exe -m cascade_forge.cli naca65 benchmark --output datasets/naca65-benchmark-v1.0.0
.\.venv\Scripts\python.exe -m cascade_forge.cli naca65 export <fit_id> --dataset datasets/naca65-benchmark-v1.0.0 --destination output/naca65-case.csv
```

Web 侧栏的“NACA 65 实验室”提供同步数字框/滑动条、通用参数/标准编号、原生与 B-spline 轮廓叠加、叶面有符号法向误差、LE/TE 密切圆视图、36 型热力图、案例表及 CSV/JSON/Parquet 导出。页面复用统一 Plotly 控制器，几何计算只在 Python 后端执行。

### 2026-09-03：36 型完整评估

正式基准写入独立的 `datasets/naca65-benchmark-v1.0.0/`，没有修改 v1/v2.1 正式叶型库或其 Geometry ID。固定矩阵全部完成：NACA 65 为 27 型，NACA 65A 为 9 型；36 个 `native_id` 和 36 个 `fit_id` 均唯一，所有案例均收敛并保留完整报告。

- 状态：0 valid、8 warning、28 rejected、0 failed。
- 全轮廓 RMS：最小 `7.069×10⁻⁴c`、中位数 `1.741×10⁻³c`、最大 `2.285×10⁻³c`。
- 全轮廓最大距离：最小 `2.265×10⁻³c`、中位数 `4.240×10⁻³c`、最大 `6.315×10⁻³c`。
- 最佳案例：NACA 65A-006，RMS `7.069×10⁻⁴c`、最大距离 `2.265×10⁻³c`，状态 warning。
- 分族状态：NACA 65 为 6 warning / 21 rejected；NACA 65A 为 2 warning / 7 rejected。

这说明当前 `unified-bspline-g2-v2.1` 的固定控制自由度和密切圆边缘合同可以稳定拟合整个矩阵，但尚未达到冻结的 valid 门槛。基准库的价值正是量化这项模型偏差；后续若增加控制点、调整端区权重或边缘合同，应创建新的拟合配置/基准版本，不能覆盖本结果。

真实 Chromium 已在 320、375、768、1024、1440 px 检查 NACA 页面：无水平溢出，Plotly modebar 未越出画布，单型生成/拟合成功且控制台无错误。视觉回归截图位于忽略 Git 的 `output/playwright/`。

## 14. NACA 法向分解修复：2026-09-03

修复只作用于 NACA 实验室，未改变 v2.1 正式生成器、NASA 锚点或工作室 Geometry ID。

### 数学合同

保留 sixseries 原始构造站位的中弧线、半厚度、构造法向和弧长参数；上下表面独立重采样只用于显示与轮廓误差计算，不能取同索引中点作为中弧线。原始 176 点/侧坐标的黄金测试仍通过。

先拟合真实中弧线，再独立拟合一条共享厚度曲线。两者使用三次、非均匀夹持 B-spline（各 27 个控制点），源中弧线归一化弧长为参数 `s`。叶面权威公式为：

$$t(s)=\sqrt{s}(1-s)B(s),\quad S(s)=C(s)+t(s)n(s),\quad P(s)=C(s)-t(s)n(s).$$

`B(s)` 是正值 B-spline 形状函数，不是最终半厚度本身。平方根前缘因子和线性尾缘因子允许自然闭合；不再把边缘半径当作厚度曲线端值，也不再做任何独立叶面再拟合。前缘密切圆半径由极限曲率得到 `rLE=B(0)^2/(2|C′(0)|)`。尾缘是尖尾缘，公共圆角半径为 0，界面不显示伪造的尾缘圆。

新公开类型为 `NACA65SplineSpec`、`NACA65FitGeometry`，新生成器为 `naca65-normal-bspline-v2`；`generate_normal_offset_profile(spec)` 可重建完整坐标。它不冒用 `unified-bspline-g2-v2.1` 合同。`fit_reference_geometry` 保留原 API/CLI 的迭代参数以兼容调用，但不再交替优化独立叶面。

NACA 原生算法在前缘 `x/c<0.005` 使用受限构造斜率；65A 还有额外的直线尾段替换。这些规则与严格法向模型并非处处完全等价，剩余偏差如实进入报告，不修改原生轮廓。

### 评估与页面

轮廓距离改为点到 4001 点加密参考轮廓的**线段距离**，消除旧版最近离散点带来的采样误差下限。RMS 与近似 Hausdorff 仍为有限采样近似，不声称解析曲线精确距离。误差曲线使用对应叶面弧长配对及外向叶面法向；另输出中弧线 RMS、半厚度 RMS、中点恒等误差和法向正交误差。

Web 新增可开关的法向厚度连线、共享半厚度对比图和“法向分解校验”卡片；LE 显示推导出的密切圆、圆心与鼻点，TE 标明尖尾缘。

实测修复 NACA 页面 Plotly 工具栏容器零高度导致的按钮裁剪：容器设置最小高度和层级，空白区域不拦截画布事件。真实按钮点击验证缩放生效，切换法向连线后缩放范围保持不变；轴标题兼容当前 Plotly 的对象格式。

### 新基准结果

新库：`datasets/naca65-benchmark-v2.0.0/benchmark_manifest.json`。旧 `naca65-benchmark-v1.0.0` 的全部 Parquet 哈希保持不变；失效原因记录在 `configs/naca65-benchmark-deprecations.json`。启动脚本与 Web 默认已切至新库，显式打开旧目录会拒绝写入。

- 36/36 valid、36 个唯一 native ID；无拟合失败、无加密轮廓自交。
- RMS 最小/中位数/最大：`2.7860e-6 / 8.8043e-5 / 4.1957e-4 c`。
- 最大轮廓距离的最大值：`1.3863e-3 c`，低于原门槛 `2e-3 c`。
- 全矩阵中点误差最大 `5.5512e-17 c`，法向正交误差最大 `5.5945e-17 c`。
- 默认 NACA 65-810（a=0.8）：轮廓 RMS `5.1261e-5 c`；中弧线 RMS `3.5583e-6 c`。

**旧版与新版评估器不同，不使用两组旧/新 RMS 直接宣称提升倍数。**

验证：89 项 pytest 通过、Ruff 通过；36 组合的非采样节点法向/中点/闭合不变量、LE 极限曲率、JSON 坐标重建和旧库写入门禁均通过。Chromium 五种宽度无水平或 modebar 溢出，页面显示新 RMS 与浮点量级的分解校验值，控制台无错误。

CLI 单型拟合与新版基准恢复通过；恢复后仍为 36 条，无重复分区。视觉证据：`output/playwright/naca65-corrected-1440.png`。测试有一条既存 Starlette/httpx 弃用提示，不影响通过。

Windows 启动使用项目根目录 `./start.cmd`；单型命令不变。新基准命令：

```powershell
cd UDF
.\.venv\Scripts\python.exe -m cascade_forge.cli naca65 benchmark --output datasets/naca65-benchmark-v2.0.0
```

## 15. NACA 端区光顺与尖尾缘合同：2026-09-03

`naca65-normal-bspline-v3` 修复 v2 端区曲率振荡。根因有三项：极密端区节点上的无正则最小二乘；sixseries 前缘截断斜率及 `a=1` 尾缘奇异导数与有限 B-spline 端点约束不一致；三次 C2 中弧线的法向场连续阶次不足以保证变化厚度偏置叶面的 C2 曲率连续。

- 中弧线改为五次、简单内节点的 C4 B-spline；共享厚度仍为三次 C2 B-spline。
- 两者加入可复现的二阶导数公平项；端点切向采用原始有限站位几何，不在 `1-epsilon` 处采样奇异导数。
- 65A 尾段拟合目标改为直线替换后两张原生叶面的配对中点与半间距，不再误用替换前的解析厚度。
- 叶面仍严格满足 `S=C+t n`、`P=C-t n`；解析计算节点两侧曲率，并以最大相对跳变 `1e-4` 作为拒绝门禁。
- LE 保留由极限曲率推导的密切圆。当前 OpenVSP sixseries 基础截面拟合采用规范化尖尾缘：两侧以非零楔角相交，圆角半径的工程约定值为 0，公共密切圆半径在数学上不定义。sixseries 输出的微小端点间隙作为数值追溯信息保留，不解释为 TE 半径。

“NACA 65 没有尾缘半径”不是通用结论。NASA 压气机叶栅资料还定义和测试了有限圆尾缘变体；例如常规 `0.15% c`，以及 `1% c`、`2% c` 的改型。它们是独立几何合同，不能通过把 sixseries 的数值尾缘间隙当半径来获得。本版本未静默选择其中任何一种；Web 的编号快捷输入明确保持 sixseries 尖尾缘语义。若数据库需要压气机圆尾缘，应新增显式 `trailing_edge_radius_ratio` 变体及圆弧接触/相切求解，并建立独立 benchmark/ID。

独立库 `datasets/naca65-benchmark-v3.0.0/` 的 36 型全部 valid：RMS 最大 `1.2614e-4 c`，最大轮廓距离最大 `5.9965e-4 c`，节点相对曲率跳变最大 `1.7241e-7`，无自交。默认 NACA 65-810 (`a=0.8`) 的 RMS 为 `6.9297e-5 c`、最大距离为 `3.8183e-4 c`、尾缘楔角为 `1.7217°`。v1/v2 基准只读保留，不覆盖也不直接比较评估倍数。

Web 的 LE/TE 局部图使用后端生成的 501 点加密端区曲线，避免 201 点显示折线造成“前缘不光顺”的视觉假象，并显示真实 LE 密切圆及 TE 楔角。

原生移植另与固定 commit 附带的 `sixseries.dll` 对 36 型、全部 176 个原始站位逐坐标比较，最大绝对差 `1.9989e-6 c`；v1、v2、v3 三套 manifest 所列 108 个 Parquet 哈希全部复核一致。全套 90 项 pytest 与 Ruff 通过，真实 Chromium 对 65/65A 端区及 320、375、768、1024、1440 px 画布通过，无控制台错误。

来源：[OpenVSP 固定版本 sixseries 源码](https://github.com/OpenVSP/OpenVSP/blob/51bdec01d9a50fa4bdbc960b0def21dcd6330f72/src/external/sixseries/NACA_6_series.f)、[NASA NACA 65 系尾缘半径试验记录](https://ntrs.nasa.gov/citations/19930086925)。


## 16. NACA 65 压气机圆尾缘：2026-09-07

现已新增独立 `65-compressor` 模式，并设为 Web/CLI 单型默认。使用 TR 1368 blower 厚度表、a=1 中弧线和工程圆尾缘；名义 10% 型的尾缘半径为 0.0015c。第 13–15 节为历史 sixseries 实现记录，其中页面默认与编号映射的描述由本节替代。原基础翼型仍可显式选择。

完整来源、G1 圆整约定、参数、导出、兼容性及验证见 [NACA 65 压气机说明](docs/naca65-compressor.md)。新 9 型库独立保存，全部 valid；全项目测试 103 项通过。

## 17. 圆弧族叶型：单圆弧 CA、双圆弧 DCA 与多圆弧 MCA（2026-09-08，2026-09-23 修订）
新增独立圆弧族内核（`src/cascade_forge/circular_arc.py`、`models_circular_arc.py`），
完整数学合同见 [圆弧族叶型说明](docs/circular-arc-profiles.md)。本节只记录结论与验收。

### 定义

中弧线是 **N 段内切圆弧链**：节点切线角 `φ₀ = +χ₁ > … > φ_N = −χ₂`（以弦线为基准，
`χ` 为进出口金属角），相邻圆弧共享切线。每段的**弦向跨度占比** `δ_i` 只受两条
**线性**条件约束：`Σδ_i = 1` 与 `Σδ_i·tan μ_i = 0`（`μ_i` 为该段平均切线角）。
因此**中弧线本身**闭式可解，没有迭代也没有最小二乘；半径由
`r_i = δ_i / (2 sin(Δ_i/2) cos μ_i)` 反解。厚度律的尾段形状由一个单调性求解器确定，
那是整条管线里唯一需要数值搜索的部分。

自由度是 `2N − 1`。单圆弧的闭合条件退化为 `χ₁ = χ₂`（几何必然，不是约定）；
`N = 2` 且 `χ₁ = χ₂` 时两段圆弧圆心重合，**严格退化为单圆弧**。

双圆弧切点有三种闭合方式：最大弯度处相切（默认）、指定切点弦向站位、显式节点角。
切点角可行域是 `(−χ₂, χ₁)`；可达站位是该区间像的**真子集**（`χ₁ = 30°`、`χ₂ = 20°`
时为 `(0, 0.806209)`），由 `junction_station_range` 如实给出。

**多圆弧的内节点站位是派生量。** 给定全部节点切线角后，两条线性闭合条件只留下 `N−2`
维仿射可行域；省略站位时 `ArcMeanLineSpec` 在 pydantic 验证**之前**自动解出一组严格为
正的跨度，因此 API、CLI 与 Web 三条路径行为一致（旧版只有 CLI 有这层回退，网页端的
多圆弧因此 100% 报「需要 N-2 个内节点弦向站位」）。每个站位的可达区间由
`interior_station_bounds` 闭式给出：把末两段的解代入 `δ > 0` 后可行域等价于三条关于
`x` 的**线性**不等式，逐坐标取交即得区间，不需要线性规划或扫描。

### 尾缘楔角约定与本轮的关键修正

尾缘半楔角按**弦线局部坐标**中两侧叶面切线的夹角之半定义。叶面切向量含弧长因子 `L`
与曲率项 `hκ`，所以 `β` 与厚度律末端斜率之间**不是**简单的正切比例，由
`aft_end_slope_for_wedge` 精确反解。只有**直线中弧线**（`L = 1`、`χ₂ = 0`、`κ = 0`）才
退化为 `h'(1) = −tan β`——这正是 `geometry-v1` 的 `slope_te = -tan(radians(...))` 约定，
本内核在该退化条件下与它逐位一致（测试用 v1 实现直接对照固化）。默认 DCA30/20 请求
`6°` 时实际是 `h'(1) = −0.108379`，而不是退化值 `−0.105104`（相差 3.1%）。

修正了一处早期实现的真实缺陷：把 `t_TE/c` 与楔角都当作独立输入时，给定 `t/c = 0.10`、
`t_TE/c = 0.006`，一条形状保持五次多项式会把请求的半厚度 `0.025` 过冲到 `1.70`。
根因是几何性的——尾段要在长度 `ℓ = 1 − s_t` 上从 `h_max` 单调降到 `h_TE` 且末端斜率幅值
为 `m`，必须满足 `m·ℓ ≲ 3 (h_max − h_TE)`；超过它时任何二阶连续的单调曲线都会先回升。
现在尾段入口二阶导数由**单调性求解器**确定：不可行时抛 `ThicknessLawInfeasible`
并给出可达上界，不静默钳制。

**本轮修复的可行性判据缺陷。** 旧实现的可行性判据（601 点粗扫 + 2001 点采样 + 值差分
容差）与 `_verify_law` 门禁相差约三个数量级，于是「搜索说可行、门禁说不可行」：报出的
上界之下 `0.01°` 就被拒绝。现在判据只有一个来源——解析极值（尾段取四次导数的精确最大
值、前段取 `N(q)/(2q)` 的精确最小值）+ 共用容差 `1e-9·h_max`；搜索目标改为**合并违反
量** `max(尾段违反量, 前段违反量)`，它仍是凸函数，因此三分搜索定位最小值、二分收上端点
都成立。实测报出上界与生成器真实接受边界相差 `< 10⁻⁶°`，且 `β ≤ 报出上界` 严格蕴含
生成成功。

**可行域报告的两个维度必须分开。** `arc_feasibility_report` 报的是：

* `maximum_feasible_half_wedge_deg`——有效楔角上界，与参数域**两端**取交：数学上界
  高于 `45°` 时截到其下最近的可表示值（实测 `t/c = 0.25`、`s_t = 0.75` 时为 `53.915°`），
  低于 `0.5°` 时返回 `null` 并令 `wedge_bound_exists = false`（实测有合同的上界恰好
  `0.0`）。旧实现只做上端截断，于是会把 `0.0` 这种必然 422 的数当成「可行上界」报出去。
* `geometry_feasible` / `geometry_error`——这条 spec **是否真的能生成**。法向偏置自交
  （厚度超过中弧线曲率半径）与楔角无关，实测有合同在任何允许的楔角下都失败；只报楔角
  上界会让用户把「楔角可行」误读成「能生成」。

### 接口与验收

Python 公共入口新增 `build_arc_chain`、`build_mca_mean_line`、`generate_arc_profile`、
`build_thickness_law`、`double_arc_first_span`、`interior_station_bounds`、
`junction_station_bounds_deg`、`junction_station_range`、`arc_feasibility_report`、
`maximum_feasible_half_wedge_deg`、`trailing_edge_half_wedges_deg`、
`ThicknessLawInfeasible`。CLI 新增 `arc generate` 与 `arc limits`（支持
`--family multiple` 查询站位可达区间）。Web 侧栏新增「圆弧族实验室」（`/arc`）：
参数变化时实时查询可行域、把楔角与切点站位的滑动条上限收敛到真实可达区间、显示各段
圆弧与半径、节点表与全部解析不变量。API 为 `/api/v2/arc/generate` 与
`/api/v2/arc/limits`。

解析不变量实测（默认双圆弧 `χ₁ = 30°`、`χ₂ = 20°`、`t/c = 0.10`、
`t_TE/c = 0.006`、`β = 6°`）：

| 不变量 | 门槛 | 实测 |
|---|---|---|
| 弦线闭合误差 | `< 10⁻¹²` | `0.0`（双圆弧）／`1.4×10⁻¹⁷`（MCA4） |
| 节点重建误差 | `< 10⁻¹²` | `2.2×10⁻¹⁶` |
| 节点相切误差 | `< 10⁻¹⁴` | `0.0` |
| 前缘半径极限值 | 相对误差 `< 2×10⁻⁵` | `4.9×10⁻⁷` |
| 轮廓自交 | 必须为 0 | `0`（装配门禁未抛错；独立折线检测在测试里另做） |
| 默认合同可行楔角上界 | `> 14°` | `14.380177°` |
| 报出上界 vs 生成器真实接受边界 | 一致 | 差 `< 10⁻⁶°`（恰好一个 `1e-9` 安全内缩） |
| 报出上界落在参数域内（含 `null` 语义） | 必须成立 | 460 组合同 0 不一致 |
| 内节点站位区间 vs 真实可行性 | 完全一致 | 区间内外探针 0 不一致 |
| 搜索返回的 `Y` 通过全部门禁 | 必须成立 | 137,200 组网格 / 20,000 组随机，0 反例 |

测试：`tests/test_circular_arc.py` 86 项解析不变量测试、`tests/test_arc_integration.py`
18 项 CLI/Web 集成测试；全项目 213 项 `pytest` 通过，`ruff check src tests` 通过。

### 已知限制

* 对称法向厚度律**无法**形成真实尖尾缘楔角（总落差只有 `h_max − h_TE`，而单调段
  最小长度就是尾段长度）。需要尖尾缘时应改用圆弧帽（参考
  `compressor_naca65.round_trailing_edge` 的 G1 半圆拼接）或非对称厚度模型。
* `interior_station_bounds` 是逐坐标敏感性，不是联合可行域的投影：盒内可以有不满足
  联合约束的点。
* 相邻节点角间距极小时半径按 `1/Δφ` 发散，链本身病态。求值已改为无灾难性抵消的弦向量
  形式（`10⁻¹³°` 间距下与 60 位 `Decimal` 参考的偏差仍是 `2.2×10⁻¹⁶`），但这类输入不应
  出现在真实设计里。
* 曲率在圆弧节点处跳变，这是分片圆弧的固有性质；数值微分校验须避开节点邻域。
* 本内核只保证几何精确与可追溯，不代表叶型具备良好气动性能。
