# NACA 65 压气机系列

2026-09-07：Web 和 CLI 单型生成默认采用 `65-compressor`。编号 `65-010`、`65-810`、`65-(12)10` 现在解析为压气机系列。原 `65` / `65A` sixseries 基础翼型仍可显式选择；已有 Python `NACA65Spec()` 默认值及旧型线 ID 保留兼容。

## 几何来源与圆整约定

- 厚度取 NACA TR 1368 表 I 的 **65(216)-010 + 0.0015x（blower）列**，不是 OpenVSP sixseries 共形映射厚度。表格中的半厚度已经含增厚量，不重复添加。采用 `sqrt(x/c)` 坐标下的三次插值，名义厚度按 `t/c / 0.10` 缩放。
- 名义 10% 型的表格半厚度峰值为 `0.05057c`，总厚度约 `0.10114c`；保留该差异。原始构造在弦长站位 1 的总端厚为 `0.003c`。
- 中弧线使用 a=1 公式在表格站位的纵坐标，随设计升力系数线性缩放；厚度沿中弧线法线叠加。解析公式端点斜率奇异，本实现采用首个已解析站位的有限切线，三次插值连接表格纵坐标。它是明确的端区数值处理。
- 工程圆尾缘半径 `r/c = 0.0015 × (名义 t/c / 0.10)`。圆心沿出口切线位于名义尾点前方一个半径，圆弧鼻点保持 `(1,0)`；最后 5% 弦长采用局部三次 Hermite 光顺，连接两侧与精确半圆。
- 接点达到 **G1 相切连续**，曲率可以跳变，不宣称 G2。圆整后的坐标不是历史试验叶片的精确复刻；1% / 2% 弦长大圆尾缘试验变体也不在本版范围内。
- `raw_suction/raw_pressure` 保留圆整前的表格构造；`normalized_suction/normalized_pressure` 是包含光顺与圆弧的完整闭合轮廓。弦长归一化以名义中弧线端点为基准，不按曲面最小/最大 x 再归一化。

来源：[TR 1368 表 I](https://digital.library.unt.edu/ark:/67531/metadc60788/m1/6/)、[NASA RM L51G31](https://ntrs.nasa.gov/citations/19930093730)、[尾缘半径试验 RM L51J16](https://ntrs.nasa.gov/citations/19930086925)。表格数值保存在 `src/cascade_forge/domain/compressor_naca65.py`，无需联网生成。

## 拟合、显示与导出

压气机拟合使用独立版本 `naca65-compressor-bspline-v1`：五次中弧线与共享半厚度 `h(s)=sqrt(s)B(s)`，保留有限端厚，再对最后 5% 弦长应用相同的工程圆整。模型 JSON 保存半径、版本和全部 B-spline 控制参数，可重建坐标。主体法向不变量针对圆整前构造；圆弧与光顺不冒用该不变量。两侧圆整后采用独立弧长重采样，其数组同下标不代表相同法向站位。

完整轮廓距离、自交检查、CSV/Parquet 导出均包含圆尾缘。JSON 还保存圆弧、圆心、接点、原始表格构造与来源。相关尾缘诊断继续由独立 Python 模块提供，专项可视化页面已移除。

在项目根目录运行：

```powershell
.venv/Scripts/python.exe -m cascade_forge.cli naca65 generate --preset 65-810 --destination output/naca65-compressor/native.json
.venv/Scripts/python.exe -m cascade_forge.cli naca65 fit --preset 65-810 --destination output/naca65-compressor/fit.json
.venv/Scripts/python.exe -m cascade_forge.cli naca65 benchmark --compressor --output datasets/naca65-compressor-benchmark-v1.0.0
# 旧基础翼型模式
.venv/Scripts/python.exe -m cascade_forge.cli naca65 generate --family 65 --loading-parameter-a 0.8
```

标准页通过 `POST /api/workbench/preview` 提交 `{"family":"naca65","spec":{"family":"65-compressor","thickness_ratio":0.1,"design_lift_coefficient":0.8}}`；a 默认 1，显式提交其他 a 会被拒绝。独立专项 HTTP 接口已移除；拟合和基准通过上述 CLI／Python 模块使用。

新库 `datasets/naca65-compressor-benchmark-v1.0.0` 独立于旧 sixseries v3 库；Web 启动不再加载或改写任何 NACA65 基准库，CLI 使用显式 dataset/output 路径。存储/评估框架仍标记 `naca65-benchmark-3.0.0`，具体几何版本由各记录的 `algorithm_version` / `generator_version` 和 `config.compressor` 区分。恢复运行验证配置，避免把不同矩阵追加到同一库。

## 验证结果

9 型矩阵：名义厚度 6%、10%、15%，设计升力系数 0、0.8、1.2，a=1。全部 valid，无自交；最大 RMS `1.472e-4c`，最大轮廓距离 `9.931e-4c`。默认 65-810：RMS `9.073e-5c`，最大距离 `5.491e-4c`。

回归覆盖表格独立数值、有限端厚、圆弧几何半径、数值接点切线、完整轮廓自交、模型重建、标准页生成与 CLI/导出、基准恢复及旧 36 型。另检查 t/c=0.03、0.18 且 Cl=2 的边界组合。这些是几何验证，未进行气动 CFD 验证。


## IGG DAT 导出（2026-09-08）

IGG 专用 XYZ 曲线导出通过 CLI 选择原生或拟合轮廓。标准页和数据库页的普通 DAT 坐标导出继续保留，但不等同于此 IGG 专用格式。

单文件为 IGG 简单曲线格式：曲线名称、`XYZ`、点数、逐行三列坐标。IGG 16.1 对这个格式只读一条曲线，不能通过直接串接两条记录导入两侧。因此本程序输出 **401 点闭合轮廓**，顺序为尾缘 → 吸力面 → 前缘 → 压力面 → 尾缘，前缘只记录一次，末点闭合到首点。保留圆尾缘轮廓，使用默认 C-spline 插值；它是坐标曲线交换，不是精确解析圆弧或 B-spline 参数交换。

在 IGG 选择 **File → Import → IGG Data**，打开 `.dat` 文件。坐标为 `(x/c, y/c, 0)`，导入弦长为 1；按所需实际弦长统一缩放。该文件不包含叶栅周期、流道或网格拓扑。

```powershell
.venv/Scripts/python.exe -m cascade_forge.cli naca65 export <fit_id> --dataset datasets/naca65-compressor-benchmark-v1.0.0 --destination output/blade.dat --source native
# 拟合轮廓：--source fitted
```

独占专项 HTTP 导出接口已移除；Python 调用 `naca65_igg_dat` 或 `naca65_igg_split_zip` 也可导出未入库轮廓。

本机 IGG 16.1 的 `import_data_file` 已分别导入原生与拟合 DAT，两条曲线各 401 控制点；逐点读回与导出坐标的最大差为 **0**。证据：`output/naca65-compressor/igg-dat-import-audit.json`、`igg-dat-import.py`、`igg-dat-import.log`。格式参考 NUMECA《IGG User Manual》的 Curve data files 节；实际兼容性以本机 16.1 导入为证。


## 精确定义前缘的拆分导出（2026-09-08）

CLI 的 `--source native/fitted` 选择来源，`--layout closed/split` 选择单条闭合 DAT 或两侧拆分 ZIP。此独立导出能力不依赖已移除的专项页面。

拆分直接使用所选原生/拟合结果的两侧坐标数组，保留全部点，不重新拟合，不按最小 x 找前缘，也不硬编码闭合曲线的第 201 点。对目前 201 点/侧的型线，ZIP 包含：

- `65-810-fitted-suction.dat`：201 点，尾缘 → 吸力面 → 定义前缘。
- `65-810-fitted-pressure.dat`：201 点，定义前缘 → 压力面 → 尾缘。

两份文件都保留定义前缘 `(0,0,0)`；几何最左点可能 x<0，仍属于吸力面。圆尾缘也保留，按定义尾缘端点分到两侧。解压后在 IGG 中分别用 File → Import → IGG Data 导入，即得到名称以 Suction / Pressure 结尾的两条独立曲线，无需鼠标 Divide。

CLI 拆分：

```powershell
.venv/Scripts/python.exe -m cascade_forge.cli naca65 export <fit_id> --dataset datasets/naca65-compressor-benchmark-v1.0.0 --destination output/65-810-fitted-split.zip --source fitted --layout split
```

CLI `--layout split` 要求目标扩展名 `.zip`；默认 `closed` 保留单条闭合导出。

实际 IGG 16.1 验证覆盖原生/拟合两种来源：共四条曲线各 201 点，定义前缘端点均精确为零，控制点逐点读回误差为零。样例及验证脚本、日志保存在 `output/naca65-compressor/split/`。
