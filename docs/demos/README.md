# `docs/demos` — 独立演示页

放**单文件、零依赖**的浏览器演示页：双击任意 `.html` 即可运行，不需要起后端、不需要 npm。

| 文件 | 说明 |
| --- | --- |
| `pelican-on-bike.html` | 「鹈鹕骑自行车」：纯 SVG + 原生 SMIL 的 2D 循环动画（两段式腿部 IK 预采样、车轮/路面同速零打滑、多层视差无缝闭环、空格/按钮暂停） |
| `index.html` | 预览入口的 0 秒跳转页，仅为了方便 `python3 -m http.server` |

## 重新生成 `pelican-on-bike.html`

动画的腿部 IK 关键帧是烘进标记里的（SMIL 自己不会解方程），改动几何/周期请改生成器再重跑：

```bash
python3 scripts/gen_pelican_bike.py    # 生成 + 自检（脚-踏残差、可达域、膝分支、XML 平衡）
```

脚本头部注释里有完整的运动学契约（周期、齿比、地面速度怎么互相锁定），
自检失败会直接 non-zero 退出，不会产出错误文件。
