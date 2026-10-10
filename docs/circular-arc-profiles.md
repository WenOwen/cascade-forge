# 圆弧族叶型：单圆弧 CA、双圆弧 DCA 与多圆弧 MCA

> 本文是 `src/cascade_forge/domain/circular_arc.py` 与 `models_circular_arc.py` 的数学合同。
> 任何与实现不一致的描述都算缺陷，应当以本文修正实现或以实现修正本文。
> 坐标约定：弦线局部坐标，前缘 `(0, 0)`，尾缘 `(1, 0)`，`y > 0` 指向吸力面。

## 1. 定义：N 段内切圆弧链

中弧线由 **N 段圆弧**组成，相邻圆弧在公共节点处**共享切线**（G1 连续）。
节点 `J₀ … J_N` 处的切线角为 `φ₀ > φ₁ > … > φ_N`，以弦线为基准：

$$\phi_0 = +\chi_1,\qquad \phi_N = -\chi_2 .$$

`χ₁`、`χ₂` 是进出口金属角（中弧线端点切线与弦线的夹角）。凸性要求切线角严格递减。

第 `i` 段圆弧由 `(J_{i-1}, φ_{i-1}, φ_i, r_i)` 唯一确定，圆心为

$$C_i = J_{i-1} + r_i\,(\sin\phi_{i-1},\ -\cos\phi_{i-1}),$$

弧上的点由切线角参数化：

$$P(\theta) = C_i + r_i\,(-\sin\theta,\ \cos\theta).$$

### 1.1 闭式闭合条件

令 `μ_i = (φ_{i-1} + φ_i)/2`、`Δ_i = φ_{i-1} − φ_i`，则第 `i` 段的弦向位移是
`(2 r_i sin(Δ_i/2))·(cos μ_i, sin μ_i)`。定义该段的**弦向跨度占比**

$$\delta_i \;=\; 2\,r_i\,\sin(\Delta_i/2)\,\cos\mu_i ,$$

于是「尾缘落在 `(1, 0)`」等价于两条**线性**条件：

$$\boxed{\ \sum_i \delta_i = 1\ ,\qquad \sum_i \delta_i \tan\mu_i = 0\ }$$

这是本内核最重要的结构性质：`δ` 只受两条线性等式约束，因此

* 圆弧链**闭式可解**，不需要迭代或最小二乘；
* 自由度为 `2N − 1`：两端切线角 `χ₁`、`χ₂`（2 个）＋ `N−1` 个内节点切线角
  ＋ `N−2` 个内节点弦向站位。双圆弧把「内节点切线角」换成闭合方式
  （`max_camber`／`junction_station`／`node_angles`），自由度不变。

半径由跨度反解（弦长归一化为 1）：

$$r_i \;=\; \frac{\delta_i}{2\,\sin(\Delta_i/2)\,\cos\mu_i}.$$

### 1.2 特例与退化

| 情形 | 结果 |
|---|---|
| `N = 1` | 闭合条件退化为 `φ₀ + φ₁ = 0`，即 **`χ₁ = χ₂`**。单圆弧的两端角必然相等，这是几何事实而非约定。半径 `R = 1/(2 sin χ)`。 |
| `N = 2`，`χ₁ = χ₂` | 两段圆弧圆心重合、半径相等，**严格退化为单圆弧**。双圆弧把单圆弧包含为特例。 |
| `N = 2`，`χ₁ ≠ χ₂` | 经典双圆弧。切点自由度由闭合方式确定。 |
| `N ≥ 3` | 多圆弧；`N−2` 个内节点站位给定，其余两段跨度由两条线性条件解出。 |

### 1.3 双圆弧的切点闭合方式

`N = 2` 时两段圆弧的跨度为

$$\delta_1 = \frac{\tan\mu_2}{\tan\mu_2 - \tan\mu_1},\qquad \delta_2 = 1 - \delta_1 .$$

切点 `φ₁` 是唯一自由度，可行性区间为 `(−χ₂, χ₁)`（由 `φ₀ > φ₁ > φ₂` 给出）。三种闭合方式：

* **`max_camber`（最大弯度处相切）**：`φ₁ = 0`。两段圆弧的公共切线平行于弦线，
  切点即最大弯度点。这是最自然的选择，也是默认值。
* **`junction_station`（指定切点弦向站位 `x_t`）**：解 `δ₁(φ₁) = x_t`。
  `δ₁` 在**可达站位区间内部**严格单调递减，故有唯一解（实现用 Brent 法，
  `xtol = 5e-16`）。
* **`node_angles`（显式给出切点切线角）**：用于复现已有设计。

**可达站位是真子区间。** `φ₁ → χ₁` 时前段平均切线角趋于 0、前段退化为直线段，
`δ₁` 可以变成负数；`φ₁ → −χ₂` 时后段同样退化，`δ₁` 可以超过 1。两个端点值都要
**先取 min/max 再截断**，可达站位是

$$x_{\min} = \max\left(0,\ \min\left(\delta_1(\phi_2),\ \delta_1(\phi_0)\right)\right),\qquad
  x_{\max} = \min\left(1,\ \max\left(\delta_1(\phi_2),\ \delta_1(\phi_0)\right)\right).$$

写成「`x_min = max(0, δ₁(φ₂))`、`x_max = min(1, δ₁(φ₀))`」是错的：`χ₁ = 30°`、
`χ₂ = 20°` 时 `δ₁(φ₂) = 0.806209`、`δ₁(φ₀) = −0.178599`，那样会得到倒置的空区间。
`χ₁ = χ₂` 时可达区间恰好是 `(0, 1)`；非对称时是它的真子区间（30/20 为
`(0, 0.806209)`，80/10 为 `(0, 0.201164)`）。超出该区间必须显式报错，不能静默截断。

**单调性（如实说明）。** `δ₁(φ₁)` 在整段可行域 `(−χ₂, χ₁)` 上**并不**处处单调。
由

$$\delta_1 = \frac{\tan\mu_2}{\tan\mu_2 - \tan\mu_1},\qquad
  \operatorname{sign}\frac{\mathrm{d}\delta_1}{\mathrm{d}\phi_1}
  = -\,\operatorname{sign}\cos\!\left(\phi_1 + \frac{\chi_1 - \chi_2}{2}\right)$$

可知当 `χ₁ > 60° + χ₂/3`（或对称地 `χ₂ > 60° + χ₁/3`）时，在可行域靠近
`φ₁ = χ₁`（对称情形为靠近 `φ₁ = −χ₂`）的一侧会出现一段**上升支**。反例：
`χ₁ = 80°`、`χ₂ = 10°`，上升支为 `φ₁ ∈ (55°, 80°)`。

但这不影响上面的结论：数值扫描（`χ₁, χ₂ ∈ [5°, 89°)`，步长 1°）确认上升支完全落在
`0 < δ₁ < 1` 之外——`χ₁` 侧（如 80/10）为 `δ₁ ∈ (−0.2071, −0.1409) < 0`，
对称侧（如 5/62、10/70）为 `δ₁ ∈ (1.0, 1.4059] > 1`——因此在可达区间内部 `δ₁`
仍严格递减，根唯一、区间端点也仍由两个端点的 min/max 与 `[0, 1]` 截断给出。

### 1.4 多圆弧的可行站位

多圆弧的 `N−2` 个内节点站位必须使所有 `δ_i > 0`（仿射可行域
`{Σδ = 1, Σδ tan μ = 0, δ > 0}` 的内点）。`build_mca_mean_line` 由完整节点角序列
自动给出站位，分两步：

1. **启发式初值**：取 `δ_i ∝ 1 / (2 sin(Δ_i/2) cos μ_i)` 归一化，再用两条等式约束
   解出**末两段**（前 `N−2` 段保持初值）。记权重 `w_i = 1 / (2 sin(Δ_i/2) cos μ_i)`、
   `W = Σw`，则初值 `δ_i = w_i / W`，隐含半径
   `r_i = δ_i / (2 sin(Δ_i/2) cos μ_i) = w_i² / W`。
   它既**不是等弧长解**，也**不是等半径解**（半径是权重的二次函数，而不是权重的线性
   函数）；写 `δ_i² / Σδ` 是错的，`Σδ = 1` 而 `Σw ≠ 1`，两者相差一个 `W` 因子。
   该初值只对**近似等步长**的节点角族通常可行。
2. **修复**：若初值出现非正跨度，函数不直接失败，而是求解「最大化 `min_i δ_i`」的
   线性规划，在仿射可行域非空时给出严格为正的跨度序列。只有可行域本身为空
   （不存在全部为正的跨度）才抛 `ValueError`。

**已知限制（旧版本被误拒）。** 旧实现只有第 1 步，于是把大量可行域非空的节点角序列
误判为不可行。可复现的统计：取 `[−80°, 80°]` 上步长 8° 的 21 个角度、枚举全部严格
递减的 4 元节点角序列共 5985 组，旧实现对其中 4712 组抛错，而在这 4712 组里有
3392 组（**72.0%**）仿射可行域非空（另一轮使用更细网格的独立枚举报告了更高的
比例，约 84.8%；该轮的网格与枚举口径未随报告保存，**不可复现**，只作旁证）。
最小反例是节点角 `(60, 30, 0, −30)`：权重初值为
`δ = [0.405827, −0.460198, 1.054371]`，而显式站位 `0.211283` 可以构造出合法链
（`solve_span_distribution` 返回 `δ = [0.211283, 0.0000991, 0.7886179]`，严格为正、
两条闭合条件都满足）。现在这类序列会自动走第 2 步；
需要复现某个特定跨度分布时，请显式给 `interior_node_stations`。
`tests/test_circular_arc.py::test_mca_builder_repairs_a_feasible_but_non_positive_weight_solution`
固化该反例。

### 1.5 站位是派生量：自动求解与可达区间

**站位不是自由参数。** 给定全部节点切线角后，两条线性闭合条件只留下 `N−2` 维仿射
可行域；`N−2` 个内节点站位恰好把它参数化，所以「给定节点角 + 给定站位」才唯一确定
一条链。反过来说，**省略站位是合法输入**：`ArcMeanLineSpec` 在 pydantic 验证**之前**
（`mode="before"`）调用 `build_mca_mean_line` 自动解出一组严格为正的跨度，因此 API、
CLI 与 Web 三条路径行为一致。旧版只有 CLI 做了这层回退，网页端的多圆弧因此 100%
报「需要 N-2 个内节点弦向站位」。

必须用 `mode="before"`：`mode="after"` 的校验器返回新实例会被 pydantic 在 `__init__`
路径上丢弃（并发出 `UserWarning`），调用方拿到的仍是未填充的对象，`mean_line_id`
也就会基于错误的站位计算。

**每个站位的可达区间是闭式的。** 记前 `N−2` 段跨度 `δ_k = x_k − x_{k−1}`、
`S = Σ_{k≤N−2}δ_k`、`P = Σ_{k≤N−2}δ_k tan μ_k`、`a = tan μ_{N−1}`、`b = tan μ_N`。
把末两段的闭式解代入 `δ > 0` 后，可行域等价于三条**线性**不等式：

$$S < 1,\qquad P + b\,(1 - S) < 0,\qquad P + a\,(1 - S) > 0 .$$

节点角严格递减且都落在 `(−90°, 90°)` 内，故 `tan` 单调、`a > b`，第二个不等式的方向
不依赖任何额外假设。`x_j` 只通过 `δ_j`（系数 `+1`）与 `δ_{j+1}`（系数 `−1`，仅当
`j+1` 仍是自由段时存在）进入，所以 `S` 对它的导数只有 `0` 或 `1` 两种取值，`P` 的导数
是 `tan μ_j − tan μ_{j+1}` 或 `tan μ_j`：每条不等式给出一个闭式半轴，取交即得区间。

`interior_station_bounds` 返回**其余站位固定在当前值**时的逐坐标可达开区间，因此是
「单变量敏感性」而不是可行域的完整投影。区间端点是严格不等式给出的开端点（取到即
某段跨度为零、半径发散）；**其余站位已使链不可行时返回 `(nan, nan)`**，与「区间很窄」
严格区分。可复现的核对：对若干组可行合同，在区间内外各取探针调用
`solve_span_distribution`，结论与区间完全一致（`tests/test_circular_arc.py`）。


## 2. 厚度：沿中弧线法向的对称分布

`h(s)` 是**半厚度**，`s` 为归一化中弧线弧长；真实中弧线弧长是 `t = L s`，
`L` 为中弧线总弧长（弦长归一化为 1，故 `L ≥ 1`）。

### 2.1 前段 `s ≤ s_t`

$$h = a_1 q + a_2 q^2 + a_3 q^3 + a_4 q^4,\qquad q = \sqrt{s}.$$

四个系数由四个条件唯一确定：`h(0) = 0`（由 `q` 的零次项为零自动满足）、
`h(s_t) = h_max`、`h'(s_t) = 0`、`h''(s_t) = h''_aft(s_t)`。

首项 `a₁ = √(2 r_LE)` 是**精确**的前缘控制：法向偏置叶面在 `h ~ a₁√s` 下的极限曲率是
`∓2L/a₁²`。这里必须带上 `L`：`s` 是归一化弧长，换成真实弧长后
`h = a₁√s = (a₁/√L)·√t`，鼻部抛物线 `y² = 2Rx` 的曲率半径随之缩短 `L` 倍。故

$$R_{LE} = \frac{a_1^2}{2L} = \frac{r_{LE}}{L},$$

且该极限**与中弧线曲率无关**。这不是近似——实现用 Richardson 外推在 `s → 0` 处数值
验证（残差 `< 2×10⁻⁵` 相对误差），并在真实生成点上用三点外接圆独立复核（DCA30/20：
`a₁²/(2L) = 0.00581871`，`L` 倍标称值 `a₁²/2 = 0.006` 明显偏大）。

**注意参数语义。** `ArcProfileSpec.leading_edge_radius_ratio` 是
`a₁²/2` 这个标称量；**真实**前缘曲率半径是它除以 `L`。`diagnostics` 里
`leading_edge_radius_requested_ratio` 是标称值，
`leading_edge_radius_limit_ratio`／`leading_edge_radius_analytic_ratio`／
`leading_edge_radius_probe_ratio` 都是真实值，`leading_edge_circle` 也按真实半径绘制。

### 2.2 后段 `s_t < s ≤ 1`

五次 Hermite，端点条件 `h(s_t) = h_max`、`h'(s_t) = 0`、`h(1) = h_TE`、
`h'(1) = −m`（`m > 0`，由楔角精确反解，见 §2.3）、`h''(1) = 0`。入口二阶导数
`h''(s_t)` 是唯一剩余的自由度，由**单调性求解器**确定（见 §3）。

### 2.3 尾缘半楔角的定义

半楔角是**两侧叶面在尾缘的切线夹角之半**，按弦线局部坐标度量。叶面
`S = M + h n`、`P = M − h n` 在归一化弧长下的切向量是

$$\frac{\mathrm{d}S}{\mathrm{d}s} = L(1 - h\kappa)\,T + h'\,n,\qquad
  \frac{\mathrm{d}P}{\mathrm{d}s} = L(1 + h\kappa)\,T - h'\,n .$$

中弧线在尾缘的切线角是 `−χ₂`，故两侧叶面的绝对切线角为

$$\alpha_S = -\chi_2 - \arctan\frac{|h'|}{L(1 - h\kappa)},\qquad
  \alpha_P = -\chi_2 + \arctan\frac{|h'|}{L(1 + h\kappa)},\qquad
  \beta = \tfrac{1}{2}(\alpha_P - \alpha_S).$$

这正是 `trailing_edge_surface_angles_deg`／`half_wedge_from_slope_deg` 的公式
（注意前者返回的是两个**绝对切线角**，不是半楔角），并由
`aft_end_slope_for_wedge` 用 Brent 法精确反解 `h'(1) = −m`。**曲率项 `hκ` 不可忽略**：
它让两侧楔角不再对称，也让 `β` 与 `h'` 的关系不再是简单的正切比例。

**只在直线中弧线**（`L = 1`，等价于 `χ₂ = 0`、`κ = 0`）上，上述关系才退化为
教科书恒等式：两侧切线分别是 `(1, h')` 与 `(1, −h')`，夹角为 `2 atan(−h')`，故

$$\beta = \arctan(-h'(1))\quad\Longleftrightarrow\quad h'(1) = -\tan\beta ,$$

——这正是 `geometry-v1` 的 `slope_te = -tan(radians(trailing_edge_half_wedge_deg))`
约定，本内核在同样的直线条件下与它逐位一致。默认 DCA30/20 的 `L = 1.031156`、
`hκ = −0.0017`，忽略换算会让实现的楔角偏小约 3%：请求 6° 时
`h'(1) = −0.108379`，而不是 `−0.105104`。

> **与 `geometry-v1` 的对照（已修正）。** v1 的 `dh/dx = −tan β` 只是**参数**，
> 在弯中弧线上并不等于几何半楔角：`χ₁ = 40°`、`χ₂ = 20°`、`β = 6°` 时 v1 的实际
> 几何半楔角只有 **5.8224°**。本内核在 v1 的直线条件（`L = 1`、`χ₂ = 0`、`κ = 0`）
> 下与 v1 逐位一致，而在弯中弧线上让**几何实现值**等于请求值（6.0000°）。
> 测试 `test_trailing_edge_wedge_convention_matches_geometry_v1` 同时固化这两个事实，
> `test_wedge_slope_conversion_includes_arc_length_and_curvature` 固化「忽略 `L`
> 与 `hκ` 会把 6° 低估成 5.82°」。

### 2.4 尾缘厚度与楔角不独立

尾段要在长度 `ℓ = 1 − s_t` 上从 `h_max` 单调降到 `h_TE`，同时末端斜率幅值由 `β`
精确反解。这给出一条量级约束：

$$m \cdot \ell \lesssim 3\,(h_{max} - h_{TE}),\qquad m = -h'(1).$$

超过它时，**任何**二阶连续的单调曲线都会在 `s_t` 之后先回升。实测边界
（`t/c = 0.10`、`s_t = 0.40`、`t_TE/c = 0.006`、DCA30/20）：

| `β` 请求值 | 是否可行 |
|---|---|
| 6° | 可行（上界约 14.38°） |
| 10° | 可行 |
| 14.33° | 可行（`ceiling − 0.05°`） |
| 35° | **不可行**，报错并给出可达上界 |

`maximum_feasible_half_wedge_deg` 给出该上界；`arc limits` 子命令与
`POST /api/v2/arc/limits` 暴露同一数值。不可行时**必须报错**，不能静默钳制——
这正是早期实现（把 `t_TE/c` 与楔角都当独立输入）会把请求的半厚度 0.025
过冲到 1.70 的根因。

## 3. 可行性求解

尾段入口二阶导数 `h''(s_t)` 是唯一可调形状自由度。可行集**不是**关于 `|h''(s_t)|`
单调的：实测它是区间 `[Y_{hi}(m), Y_{lo}(m)]`——`|Y|` 太小则曲线先在尾段回升，
太大又会在中间塌陷。两者在解析判据下都表现为 `max dh/ds > 0`。

### 3.1 判据只有一个来源

`_monotone_slope_tolerance(h_max) = 1e-9 · h_max`（`dh/ds` 单位）是**唯一**的容差来源，
可行性搜索、楔角上界求解与 `_verify_law` 门禁共用它。单调性用**解析极值**判定，没有
采样网格：

* 尾段 `dh/dδ` 是四次多项式，其极值只可能落在 `δ ∈ {0, 1}` 或二阶导数的实根（驻点）
  上；三次驻点方程用闭式解（Cardano + 三角解法），不调用 `numpy.roots`。
* 前段 `dh/ds = N(q)/(2q)`（`q = √s`）在 `q → 0⁺` 时发散到 `+∞`，故最小值只可能落在
  `q = √s_t` 或 `qN'(q) − N(q) = 0` 的驻点上——同样是三次方程闭式求根。

容差量级：`1e-9 · h_max` 对应整段厚度上约 `6e-10 · h_max` 的伪回升，远低于任何几何
意义；而 Hermite 系数的尺度是 `h_max / ℓ⁵`（默认合同 `0.05 / 0.6⁵ ≈ 0.643`，实际存储
系数的最大幅值约 `0.25`），舍入误差量级 `1e-16`，比该容差小五个数量级。

**为什么必须同源。** 旧实现用「601 点 `Y` 粗扫 + 2001 点 `δ` 采样 + 值差分容差」做判据，
与门禁相差约三个数量级：默认合同下它会接受 `max dh/ds = 2.3e-7` 的解，而门禁只允许
`5e-11`。后果是报出的上界比生成器真正能实现的上界高出约 `0.1°`，并且上界之下 `0.01°`
就被拒绝——「上界不可信」正是由此而来。

**目标函数必须是合并违反量。** 尾段约束 `max dh/ds ≤ tol` 与前段约束
`min dh/ds ≥ −tol` 是两个不同的 `Y` 的函数；前段更严时「尾段最优点」本身不可行，只按
尾段最小值点做二分就会返回一个被自家门禁否决的解（实测 `t/c=0.02`、`s_t=0.20`、
`t_TE/c=5e-4`、`r_LE/c=0.006`、`β=0.6°` 会被误拒）。因此目标是

$$\text{violation}(Y) = \max\bigl(\max_\delta h'(\delta;Y) - \text{tol},\;
-\min_q \tfrac{N(q;Y)}{2q} - \text{tol}\bigr).$$

它仍然凸：前段项是仿射函数族的下确界（凹）取负，因此与尾段项一样是凸函数。

### 3.2 搜索：凸性 + 三分 + 二分

`max_δ h'(δ; Y)` 是**仿射函数族的上确界**（Hermite 系数对全部边界数据线性），因此关于
`Y` **凸**。凸函数的下水平集 `{Y : max dh/ds ≤ tol}` 是区间，也就是可行集本身。于是：

1. 搜索上界取 `−max(200, 1e4 · (h_max − h_TE)/ℓ²)`：`Y` 的量纲是 `h/s²`，自然尺度是
   「厚度落差 / 尾段长度²」＝ `0.047 / 0.6² ≈ 0.1306`（默认合同）。实测可行 `|Y|` 最多是
   该尺度的几十倍（默认合同按尾段判据测得可行上界 `3.838`，是自然尺度的 29 倍），
   `1e4` 倍留出两个数量级以上余量。
2. 在该区间上用**三分搜索**（80 步，每步缩到 `2/3`，`(2/3)^80 ≈ 8e-15`）定位
   `max dh/ds` 的极小值点。凸函数是单峰的，三分搜索因此收敛到全局最小值点，
   不需要任何初值猜测。若极小值点仍钉在左边界**且尚未找到可行解**，把左边界乘 `1e6`
   再搜，最多扩 3 次（默认合同下从不触发）。
3. 极小值仍大于容差 → 返回 `None`，由 `_aft_entry_second` 抛
   `ThicknessLawInfeasible`，消息里给出 `maximum_feasible_half_wedge_deg` 作为可达上界。
4. 否则：若 `Y = −1e-12` 本身可行就直接采用（最平滑）；否则用 **80 次二分**在
   `[Y*, −1e-12]` 上求可行区间的上端点，返回其中最靠近 0 的解。

早期实现「先测 `|Y| = 200`，不可行就直接二分回 `−1e-12`」在 `|Y| = 200` 不可行时
**立刻判负**，于是把一整段可行楔角误报为不可行：默认合同的上界被低估到 `11.10°`，
真值是 `14.380°`（低估约 `22.8%`，或说真值高出约 `29.5%`；两种读法的分母不同）。

### 3.3 上界求解与安全内缩

`maximum_feasible_half_wedge_deg` 在斜率域二分 `60` 次（上界初值取
`100 · (h_max − h_TE)/ℓ`，比「`m·ℓ ≲ 3(h_max − h_TE)`」这条量级约束宽两个数量级；
若该上界本身可行则回退到 `1e6`），再用 `half_wedge_from_slope_deg` 精确换算成角度。
最后内缩 `_CEILING_SAFETY_MARGIN_DEG = 1e-9`：二分给出的可行幅值精度约 `1e-17`（相对），
但用户把上界再喂回 `aft_end_slope_for_wedge` 反解时会有约 `5e-16` 的往返误差，足以把
结果推到可行域之外一个「最后一位」。内缩后 **「β ≤ 报出的上界」严格蕴含生成成功**，
这条不变量由测试固化。

**上界与参数域取交（两端都要）。** 厚度律的数学可行上界可以**高于**
`ArcProfileSpec` 的硬上限 `TRAILING_EDGE_HALF_WEDGE_MAX_DEG = 45°`（实测 `t/c = 0.25`、
`s_t = 0.75`、`t_TE/c = 5e-4`、`r_LE/c = 5e-4` 时为 `53.915°`）；也可以**低于**硬下限
`TRAILING_EDGE_HALF_WEDGE_MIN_DEG = 0.5°`（实测低至 `2.4×10⁻⁹`，甚至恰为 `0.0`）。
`arc_feasibility_report` 因此报告：

| 字段 | 含义 |
|---|---|
| `maximum_feasible_half_wedge_deg` | **有效**上界 = `min(数学上界, nextafter(45°))`；数学上界 `≤ 0.5°` 时为 `null` |
| `wedge_bound_exists` | 有效上界是否存在。`false` 表示该厚度合同连 `0.51°` 都实现不了 |
| `thickness_law_half_wedge_ceiling_deg` | 未截断的数学上界，仅用于审计 |
| `half_wedge_domain_deg` | 参数域 `[0.5, 45]` |
| `wedge_feasible` | **请求的**楔角是否不超过有效上界（与几何可行性无关） |
| `geometry_feasible` / `geometry_error` | 这条 spec 是否**真的能生成**（法向偏置自交、厚度超过中弧线曲率半径等与楔角无关的门禁） |

只报数学上界会让界面把上限设成一个模型不接受的值；只报楔角上界会让用户以为
「楔角可行」就等于「能生成」——实测有合同在任何楔角下都因压力面法向偏置自交而失败。
因此这两个维度必须分开报。

### 3.4 门禁

构造完成后 `_verify_law` 在 `2001` 个站位上做数值兜底检查（有限性、正性、累积回升、
峰值位置、峰值等于 `h_max`、尾缘值等于 `h_TE`），并**直接审计已构造的系数**：
尾段取 `aft_coefficients` 的解析最大斜率、前段取 `fore_coefficients` 的解析最小斜率。
门禁不重新跑一遍搜索，因此搜索与构造之间任何不一致都会在这里暴露，而不是被同义反复
地掩盖。实现的楔角同样按两侧叶面切线的真实夹角独立反算。**任何违反都抛错，不静默修正。**

峰值位置是**解析**判定（构造已保证 `h'(s_t) = 0`，再确认两侧斜率符号），不依赖 `s_t`
是否落在均匀网格上——实测 `s_t = 0.33332` 用固定网格容差会被误判。


## 4. 叶面装配

$$S(s) = C(s) + h(s)\,n(s),\qquad P(s) = C(s) - h(s)\,n(s),$$

其中 `C(s)` 是圆弧链、`n(s)` 是左法线。中弧线是分片圆弧，故 `κ' = 0`，
叶面有符号曲率有显式形式（`h'`、`h''` 是**对真实弧长** `t` 的导数；厚度律给的是
归一化弧长导数，调用点必须传 `h'/L`、`h''/L²`）：

$$\kappa_{S/P} = \frac{\kappa\,(1 \mp h\kappa)^2 \pm (1 \mp h\kappa)\,h'' + 2\,h'^2\kappa}
{\left[(1 \mp h\kappa)^2 + h'^2\right]^{3/2}} .$$

**前缘**：`h(0) = 0` 使两侧共点；`h ~ a₁√s` 给出有限极限曲率，因此前缘是
**自然圆头**，密切圆圆心在 `C(0) + R_LE T(0)`、距鼻点 `R_LE = a₁²/(2L)`（见 §2.1）。
不需要圆角拼接。

**尾缘**：垂直于中弧线的截断段，长度 `2 h(1)`。注意中弧线在尾缘以 `−χ₂` 俯冲，
其外法线指向下游，因此**吸力面端点 `x > 1`、压力面端点 `x < 1`**——这是正确的
几何结果，不是闭合误差。中弧线本身精确落在 `(1, 0)`。

**自交防护**：构造时检查 `1/|κ| − h > 0`（厚度不超过中弧线曲率半径），
并对闭合折线做线段相交检测。两者任一失败即抛 `ArithmeticError`。

## 5. 身份与追溯

* `mean_line_id`：由节点角序列、内节点站位、族与闭合方式决定，**不含厚度**。
* `profile_id`：由完整 `ArcProfileSpec` 决定（含厚度与采样数）。

两者都是 SHA-256 截断（24 位十六进制），前缀分别为 `arcml_` 与 `arcgeo_`。

## 6. 接口

### Python

```python
from cascade_forge import (
    ArcMeanLineSpec, ArcProfileSpec, ArcFamily, MeanLineClosure,
    build_arc_chain, build_mca_mean_line, build_thickness_law,
    default_double_arc_spec, generate_arc_profile,
    interior_station_bounds, junction_station_bounds_deg, junction_station_range,
    maximum_feasible_half_wedge_deg,
)

# 闭式圆弧链（chord 缩放整体尺度，默认弦长 1；check_closure=False 会跳过全部链门禁，
# 只应在已经独立验证过不变量时使用）
chain = build_arc_chain(ArcMeanLineSpec(
    family=ArcFamily.DOUBLE, inlet_metal_angle_deg=30.0, outlet_metal_angle_deg=20.0))
chain.node_points      # (N+1, 2) 节点坐标
chain.arcs             # 每段的圆心/半径/起止切线角
chain.evaluate(u)      # 按归一化弧长精确求值

# 完整叶型
geometry = generate_arc_profile(default_double_arc_spec())
geometry.suction, geometry.pressure, geometry.camber, geometry.thickness
geometry.diagnostics      # 全部不变量
```

公开名字的完整清单（`cascade_forge` 顶层可导入）：

| 名字 | 说明 |
|---|---|
| `ArcFamily` / `MeanLineClosure` / `ArcMeanLineSpec` / `ArcProfileSpec` | 模型合同 |
| `ArcProfileGeometry` / `CircularArc` / `CircularArcChain` | 几何对象 |
| `build_arc_chain` / `build_mca_mean_line` / `build_thickness_law` | 构造入口 |
| `generate_arc_profile` / `default_double_arc_spec` / `build_designation` | 生成入口 |
| `double_arc_first_span` | 双圆弧第一段弦向跨度占比的闭式（模型用它做可行域前置校验） |
| `interior_station_bounds` | 多圆弧内节点站位可达区间（§1.5） |
| `junction_station_range` / `junction_station_bounds_deg` | 双圆弧切点可达站位／切线角区间 |
| `maximum_feasible_half_wedge_deg` | 厚度合同的可行楔角上界（见下） |
| `aft_end_slope_for_wedge` | 由楔角反解厚度律末端斜率 `dh/ds` |
| `trailing_edge_half_wedges_deg` | 尾缘两侧叶面的**绝对切线角**（不是半楔角本身） |
| `arc_feasibility_report` | 把上述边界打包成一份可行域报告（Web/CLI 共用） |
| `ThicknessLawInfeasible` | 厚度合同不可行。**是 `ArithmeticError` 的子类**，因此 Web 层 `except (ValueError, ArithmeticError)` 会把它转成 `422` |
| `ARC_ALGORITHM_VERSION` | 算法版本字符串，参与 `mean_line_id` / `profile_id` 的哈希 |

`maximum_feasible_half_wedge_deg` 的两种口径必须区分：

* **给出** `leading_edge_radius_ratio` —— 尾段 + 前段判据，等于生成器的真实接受边界。
  `arc_feasibility_report`（Web / CLI 对外路径）用这个口径。
* **省略**该参数 —— 只判尾段，返回值是**仅对尾段成立的宽松上界**，会高于真实边界
  （实测 `t/c=0.05`、`s_t=0.55`、`t_TE/c=5e-4`、`r_LE/c=0.00125`：省略时 `10.2049°`，
  真实的 `9.7397°`）。旧版 docstring 声称该前段项「从不绑定」，**这是错的**。

### 参数域与校验规则

越界输入一律由 pydantic 拒绝（Web 返回 `422`，CLI 由 `typer` 提示），**不静默钳制**：

| 字段 | 约束 |
|---|---|
| `inlet/outlet_metal_angle_deg` | `(0, 89)`；单圆弧要求两端角在**弧度域**上相差 `≤ 1e-11` 度（`SINGLE_ARC_ANGLE_TOLERANCE_DEG`，与生成器共用同一谓词） |
| 全部节点切线角 | 落在 `(−90°, 90°)`，且**按弧度**严格递减；单段转角 `< 180°` |
| 双圆弧末两段 | `|tan μ_{N−1} − tan μ_N| ≥ 1e-15`（否则跨度方程退化，模型直接拒绝） |
| `junction_station` | `(0, 1)`，且必须落在 `junction_station_range` 内 |
| `interior_node_stations` | 可省略（自动求解）；给出时必须严格递增、落在 `(0, 1)`，且**跨度全为正**——模型层直接调用 `solve_span_distribution` 前置校验，因此「模型合法」蕴含「生成器能构造」 |
| `max_thickness_ratio` | `[0.02, 0.25]` |
| `max_thickness_station` | `[0.20, 0.75]` |
| `leading_edge_radius_ratio` | `[5e-4, 0.05]`，且 `≤ 0.5 · max_thickness_ratio` |
| `trailing_edge_thickness_ratio` | `[5e-4, 0.05]`，且**严格小于** `max_thickness_ratio` |
| `trailing_edge_half_wedge_deg` | `(0.5, 45)`（常量 `TRAILING_EDGE_HALF_WEDGE_MIN/MAX_DEG`），且 `≤ maximum_feasible_half_wedge_deg`（超出抛 `ThicknessLawInfeasible`） |
| `points_per_side` | `[51, 1001]` |

### CLI

```powershell
# 在项目根目录运行
.\.venv\Scripts\python.exe -m cascade_forge.cli arc generate --family double `
    --inlet-metal-angle-deg 30 --outlet-metal-angle-deg 20 `
    --destination output/arc-dca.json

# 多圆弧省略 --interior-node-stations 时自动求解站位
.\.venv\Scripts\python.exe -m cascade_forge.cli arc generate --family multiple `
    --interior-node-angles "12.5,0,-12.5" --destination output/arc-mca.json

.\.venv\Scripts\python.exe -m cascade_forge.cli arc limits
.\.venv\Scripts\python.exe -m cascade_forge.cli arc limits --family multiple `
    --inlet-metal-angle-deg 25 --outlet-metal-angle-deg 25 --interior-node-angles "12.5,0,-12.5"
```

### Web

侧栏「圆弧族实验室」（`/arc`）。页面提供族选择、闭合方式、金属角、厚度、楔角与采样
点数控件，**参数变化时实时查询可行域**并把楔角滑动条与切点站位滑动条的上下限收敛到
真实可达区间；右侧显示轮廓、各段圆弧（含圆心与半径）、法向厚度连线、可行域卡片、
全部解析不变量、几何特征与圆弧节点表。参数不合法时错误信息按字段路径逐条展开
（Pydantic 的 `detail` 是数组，不能直接当字符串渲染）。

API：

* `POST /api/v2/arc/generate` — 由 `ArcProfileSpec` 返回完整几何；
* `POST /api/v2/arc/limits` — 返回 `arc_feasibility_report` 的全部字段：可行楔角上界、
  切点可达站位区间与切线角区间、多圆弧内节点站位可达区间、中弧线弧长与尾缘曲率、
  前缘半径的标称值与真实值。不适用于当前族的字段返回 `null`，不伪造数值。

不可行参数返回 `422` 与可读的中文原因。

## 7. 验收准则

| 不变量 | 门槛 | 实测 |
|---|---|---|
| 弦线闭合误差 | `< 10⁻¹²` | `0.0`（双圆弧）／`1.4×10⁻¹⁷`（MCA4） |
| 节点重建误差 | `< 10⁻¹²` | `2.2×10⁻¹⁶` |
| 节点相切误差 | `< 10⁻¹⁴` | `0.0` |
| 前缘半径极限值 vs `a₁²/(2L)`（解析 Richardson 外推） | 相对误差 `< 2×10⁻⁵` | `4.9×10⁻⁷` |
| 前缘半径（折线三点外接圆，`s = 10⁻⁶/10⁻⁸` 外推，**吸力面**） | 相对误差 `< 10⁻³`（测试容差） | `4.9×10⁻⁵`（30/20）／`5.8×10⁻⁵`（45/45） |
| 轮廓自交 | 必须为 0 | `0`（见下） |
| 实现楔角 vs 请求值 | `< 10⁻⁹` | `< 10⁻⁹` |
| 几何半楔角（折线反算）vs 请求值 | `< 10⁻²` | `1.1×10⁻⁷` |
| 默认合同可行楔角上界 | `> 14°` | `14.380177°`（含 `arc_length` 与曲率修正） |
| 「β ≤ 报出上界」蕴含生成成功 | 必须成立 | 上界处接受、`+10⁻⁶°` 拒绝 |
| 报出上界 vs 生成器真实接受边界 | 一致 | 差 `< 10⁻⁶°`（恰好差一个 `1e-9` 安全内缩） |
| 内节点站位区间 vs `solve_span_distribution` 真实可行性 | 完全一致 | 区间内外探针 0 不一致 |
| 报出上界落在参数域内（含 `null` 语义） | 必须成立 | 460 组合同的生成结果与报出上界 0 不一致 |
| 搜索返回的 `Y` 通过全部门禁 | 必须成立 | 137,200 组网格 / 20,000 组随机，0 反例 |

`diagnostics["self_intersection"]` 是「装配未抛错」的标记（构造时会先跑
`has_self_intersection` 并抛 `ArithmeticError`），不是独立测量；独立复核在测试里用
折线相交检测另做一遍。

三点外接圆那一行的门槛之所以比解析外推宽：折线曲率估计在 `s → 0` 处只有 `O(√s)`
收敛，`5×10⁻⁵` 是该方法本身的分辨率，不是实现误差。两行不能用同一个门槛。

全部由 `tests/test_circular_arc.py`（86 项）与 `tests/test_arc_integration.py`（18 项）
固化。

## 8. 已知限制

* **尾缘必须截断。** 对称法向厚度律无法形成真实尖尾缘楔角：`h` 在弦长尺度上的
  总落差只有 `h_max − h_TE`，而单调段的最小长度就是尾段长度本身。若需要尖尾缘，
  应改用圆弧帽（参考 `compressor_naca65.round_trailing_edge` 的 G1 半圆拼接）
  或非对称厚度模型——两者都超出本内核合同。
* **`t_TE/c` 与楔角强耦合。** 见 §2.4；调参时先用 `arc limits` 确认可行域。
* **`leading_edge_radius_ratio` 是标称量。** 真实前缘半径是
  `leading_edge_radius_ratio / L`（见 §2.1）。要指定真实半径需自行除以 `L`；
  这也意味着同一个 `leading_edge_radius_ratio` 在不同弯度下给出不同大小的鼻部。
* **`δ₁(φ₁)` 在极端非对称角下非单调。** 见 §1.3：`χ₁ > 60° + χ₂/3`（或对称地
  `χ₂ > 60° + χ₁/3`）时会出现上升支，但它完全落在可达区间之外，不影响求根。
* **`build_mca_mean_line` 的启发式初值只对近似等步长族可靠。** 见 §1.4：非等步长族
  依赖线性规划修复，修复解与「等弧长」直觉无关；要复现特定跨度分布请显式给站位。
  当相邻节点角几乎相等（所需跨度 `≲ 10⁻⁷`）时线性规划会因 HiGHS 的原始可行性容差
  返回 `t = 0`，此时实现退回到 §1.4 的显式内点构造，而不是误报「可行域为空」。
* **`interior_station_bounds` 是逐坐标敏感性，不是可行域的投影。** 见 §1.5：它固定
  其余站位后给出单个站位的可达区间；联合可行域是一个凸多边形，逐坐标区间之积是它的
  外接盒，盒内可以有不满足联合约束的点。
* **相邻节点角间距极小时圆弧链本身是病态的。** 半径按 `1/Δφ` 发散（间距 `10⁻¹³°` 时
  `r ~ 10¹⁴`），此时曲率与密切圆半径的物理意义已经消失。求值本身是稳定的（见下），
  但这类输入不应出现在真实设计里；内核只保证坐标正确，不保证几何有意义。
* **曲率在圆弧节点处跳变。** 这是分片圆弧的固有性质（曲率分片常值），不是缺陷；
  但数值微分校验必须避开节点邻域。
* **不做气动评价。** 本内核只保证几何精确与可追溯，不代表叶型具备良好气动性能。

### 数值稳定性说明

`CircularArcChain.evaluate` 用弦向量形式
`P(θ) = J + 2r sin((φ−θ)/2)·(cos((φ+θ)/2), sin((φ+θ)/2))`，其中 `(φ−θ)/2 = local/(2r)`
直接由弧长参数给出。数学上与 `圆心 + r(−sinθ, cosθ)` 恒等，但后者在半径很大时会发生
灾难性抵消。用 60 位 `Decimal` 参考实测（下表数值由一次性参考脚本复算；
`tests/test_circular_arc.py::test_arc_evaluation_stays_accurate_for_near_degenerate_node_angles`
固化的是「节点被精确复现」这一可判定结论，不是下表的具体数字）：

| 相邻角间距 | `r_max` | 旧形式误差 | 新形式误差 |
|---|---|---|---|
| `10⁻⁹°` | `2.3×10¹⁰` | `2.9×10⁻⁶` | `2.2×10⁻¹⁶` |
| `10⁻¹¹°` | `2.3×10¹²` | `1.8×10⁻⁴` | `1.1×10⁻¹⁶` |
| `10⁻¹³°` | `2.3×10¹⁴` | `2.0×10⁻²` | `2.2×10⁻¹⁶` |

旧实现会**静默**输出这些错误坐标，而 `check_chain_invariants` 的容差本就按 `r` 缩放
（`10⁻¹²·max(1, r_max)`），看不见这个问题——判据的绝对精度上限就是 `2.2×10⁻¹⁶·r_max`。
