// Vite 构建配置。
//
// ## 为什么没有 Vue/React（TD-224 / TD-226）
// 曾经预装过 @vitejs/plugin-vue，但 2026-09-08 用户确认放弃框架方案：
// 前端体量低于框架回本线，且 CI 测试 job 不装 node_modules，
// 框架组件挂不上真 DOM、没法在 CI 里测。若将来真要加，先读 TD-226。
//
// ## 这个工具链的定位（TD-221）
//
// Node 只在**打包这一刻**用。产物 `app/static/js/*.js` 是普通静态文件、提交进
// 仓库，服务器上**不需要装 Node**，部署仍然是「pip install + 起一个 uvicorn 进程」。
//
// ## 为什么是多入口而不是一个大 bundle
//
// 单 bundle 会把 D3 依赖塞进每一个页面，包括完全用不到它的 shop /
// oauth 同意页。按页拆入口，每页只加载自己要的那份。
//
// ## 入口与分块的命名边界
// 模板引用的入口固定名；部分拆分模块的逻辑 name 本身包含打包器生成的 hash，
// 不是“所有文件名都没有 hash”。emptyOutDir 清理旧产物，CI 重建核对漂移。
// 固定名入口可变，不能为全部 JS 盲目加 immutable 长缓存。
import { defineConfig } from "vite";

export default defineConfig({
  // 产物由 FastAPI 挂在 /static/js/ 下。动态 import（Mermaid 按需加载、Mermaid 内部按图类型加载）
  // 会被 Vite 包一层预加载：给依赖分块插 <link rel="modulepreload" href="{base}{文件名}">。
  // 原来 base 是默认的 "/"，预加载地址成了 /mermaid.core.js 这类 404（JS 预加载失败不报错，
  // 所以功能正常、只是白白多出一串 404 且预加载无效）。模块之间的 import 仍是相对路径，不受影响。
  base: "/static/js/",
  build: {
    outDir: "app/static/js",
    emptyOutDir: true,
    // ⚠️ 不要写 `minify: "esbuild"`：Vite 8 改用 rolldown，那条已废弃且要求单独安装
    // esbuild 包，否则构建直接报 `Cannot find package 'esbuild'`。留空用默认压缩器。
    minify: true,
    // 当前不发布 source map；压缩堆栈需结合 app/frontend 源码和测试定位
    sourcemap: false,
    rollupOptions: {
      input: {
        "payments-admin": "app/frontend/payments-admin.js",
        "support-page": "app/frontend/support-page.js",
        // base.html 全站加载：登录态。
        auth: "app/frontend/auth.js",
        // ER 图页：d3 渲染。d3 从这里打进产物，不再走 CDN（TD-222）。
        "er-page": "app/frontend/er-page.js",
        // 其余各页的交互脚本（C2 从模板内联搬出来）
        "mermaid-page": "app/frontend/mermaid-page.js",
        "drawio-page": "app/frontend/drawio-page.js",
        "mock-pay-page": "app/frontend/mock-pay-page.js",
        // 下单页（C3）。它依赖 base.html 先加载的 window.CodeMaxAuth，
        // 所以模板里用经典 <script src>，加载顺序与内联时代完全一致。
        "shop-page": "app/frontend/shop-page.js",
      },
      output: {
        // 入口固定；分块 name 可能已包含打包器 hash（见上面的边界）
        entryFileNames: "[name].js",
        chunkFileNames: "[name].js",
        assetFileNames: "[name][extname]",
      },
    },
  },
});
