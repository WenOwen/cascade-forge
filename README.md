# Cascade Forge

本地离线叶型几何工作台。通用参数化只使用 **统一三次 B-spline G2 v2.1**；NACA65 与圆弧族作为独立标准叶型模块保留。几何验证不等同于气动性能、网格或 CFD 验收。

## 启动与开发

在项目根目录运行，推荐 Python 3.11（版本由 `.python-version` 固定）：

```powershell
uv sync --extra dev
.\start.cmd
# 自定义端口：.\start.cmd 8010
```

默认地址：<http://127.0.0.1:8000>。启动器从自身位置解析根目录，无须进入其他子目录。

```powershell
uv run cascade-forge serve --dataset datasets/geometry-v2.1.0 --workspace datasets/workspace-v2 --databases databases
uv run pytest
uv run ruff check src tests
uv build
```

CLI 顶层仅保留 `serve / naca65 / arc`，各标准叶型子命令见 `--help`。通用叶型生成、参数扫描和样本三维导出通过 Web 工作台使用，不再提供旧顶层 CLI。

## 页面与操作

日常导航只保留三页：

| 页面 | 用途 |
|---|---|
| `/studio` | 自定义 B-spline G2 叶型，实时预览、参数导入/导出、历史快照 |
| `/standard` | 原生 NACA65 与双圆弧，直接生成、不强制拟合 |
| `/database` | 命名数据库、DAT 扫描、样本预览、最多四样本并排比较、删除与导出 |

生成页采用参数—画布—操作栏布局。保存到当前数据库后，可从已保存列表重新打开。工作区历史快照独立保存，仍支持 Web 导入/导出与修订。

数据库每行显示**工程标签 + 原始 ID**：自定义标签含进出口角、安装角、厚度和厚度位置；NACA65、双圆弧显示各自的系列与参数。标签只用于展示，不参与几何哈希或修改旧合同。

垃圾桶按钮需要确认。删除是**当前数据库内的逻辑删除**：预览、列表及数据库 ZIP 不再包含该样本，重启或重新扫描不会复活。原始 DAT、CSV、JSON、正式 Parquet 数据和工作区历史保留；重新显式保存同一原生叶型可以恢复样本。数据库之间的删除互不影响。

默认数据库同时可浏览已有正式 v2 几何与工作区截面；对这些样本的删除只隐藏数据库浏览副本，不破坏设计历史和正式数据。其他命名数据库只显示自身样本。样本并排比较与同系列三维导出在 database 页完成，不再另设比较、叶栅或三维叶片页面。

## 工程布局

```text
cascade-forge/
├── pyproject.toml / uv.lock / .python-version
├── start.cmd
├── src/cascade_forge/
│   ├── api/          # FastAPI 工厂、请求合同、按功能注册的 HTTP 路由
│   ├── domain/       # 几何、曲线、模型、校验、标准族与格式计算；不依赖 HTTP/存储
│   ├── storage/      # DuckDB/Parquet、SQLite 样本、工作区与数据库目录
│   ├── pages/        # 页面路由、默认参数与模板上下文
│   ├── workbench/    # 原生生成、参数扫描配置、预览载荷、NACA65 基准编排
│   ├── static/       # 本地 JavaScript/CSS/HTMX
│   └── templates/    # 三页模板与共享组件
├── tests/            # 单元/集成/API；browser/ 为浏览器回归脚本
├── docs/             # 数学合同与使用说明
├── databases/        # 用户命名库（不提交）
├── datasets/         # 正式 v2、工作区、NACA65 基准（不提交运行数据）
└── output/           # 构建验证/浏览器截图等临时产物（不提交）
```

不再使用 `UDF` 嵌套。运行数据、缓存、截图、构建包统一留在忽略目录；源码和测试不引用旧路径。原来误放在 data/raw 下的三份 NACA65 拟合导出已原样移至 `output/exports/naca65/`，不再冒充原始输入；标准族参考表随 domain 模块保留。

## 数学合同与边界

- 自定义几何：显式三次开放夹持 B-spline 中弧线/厚度合同，G2 头尾连接；金属角支持轴向与互补切向表示。
- 局部坐标中弦长归一化为 1；安装角是放置旋转。同步旋转两端金属角和安装角时，局部坐标与几何身份不变。
- 曲率与曲率梯度作诊断，不使用固定曲率阈值拒绝叶型；仍检查闭合、正厚度、自交、约束误差与叶栅干涉。
- NACA65 原生生成和独立法向 B-spline 拟合不是通用 G2 合同的替代入口。
- DAT 扫描保留原始坐标和单位，不自动归一化或拟合；单侧、非平面和非有限坐标拒绝导入。
- 参数扫描每批最多 100 项；不可行项报告错误，不钳制替换参数。异步轮询保留 `/api/v2/jobs/{job_id}`。
- GeomTurbo 用显式米制半径/弦长与安装角；跨系列插值不支持。项目解析器往返测试通过不代表 NUMECA 原生导入已验证。

详情：[圆弧合同](docs/circular-arc-profiles.md)、[NACA65 压气机](docs/naca65-compressor.md)、[操作流程](docs/simple-workflow.md)、[设计系统](design-system.md)。

旧 v1 通用解析模型、NASA 48 锚点、锚点拟合门禁、批量 Sobol 建库、运维页和 `/api/v1/*` 已移除；历史 profile-library-v1 不再暴露。保留标准族文档中的历史数学对照不意味着保留旧实现。

NACA65 专项、圆弧专项、叶型比较、叶栅检查、三维叶片五页及独占 HTTP 接口已移除。标准页仍提供 NACA65／双圆弧生成和圆弧可行域检查；独立标准族、拟合、基准、叶片构造与格式模块保留，原有基准数据不会因 Web 启动而改写。NACA65 专用拟合/IGG 导出和单圆弧/多圆弧通过 Python／CLI 使用。
