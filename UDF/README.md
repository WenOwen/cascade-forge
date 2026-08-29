# Cascade Forge 叶型几何数据库 v1

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
uv run cascade-forge generate --config configs/geometry-v1.0.0.yaml --output datasets/geometry-v1.0.0
uv run cascade-forge serve --dataset datasets/geometry-v1.0.0
```

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

实现过程中在本节持续记录依赖版本、测试命令、数据集统计、已修复问题与剩余风险。

