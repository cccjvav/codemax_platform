// Vite 构建配置。
//
// ## 为什么没有 Vue/React（TD-224 / TD-226）
// 曾经预装过 @vitejs/plugin-vue，但 2026-09-08 用户确认放弃框架方案：
// 前端体量（约 800 行）低于框架回本线，且 CI 测试 job 不装 node_modules，
// 框架组件挂不上真 DOM、没法在 CI 里测。若将来真要加，先读 TD-226。
//
// ## 这个工具链的定位（TD-221）
//
// Node 只在**打包这一刻**用。产物 `app/static/js/*.js` 是普通静态文件、提交进
// 仓库，服务器上**不需要装 Node**，部署仍然是「pip install + 起一个 uvicorn 进程」。
//
// ## 为什么是多入口而不是一个大 bundle
//
// 单 bundle 会把 d3（压缩后约 90KB）塞进每一个页面，包括完全用不到它的 shop /
// oauth 同意页。按页拆入口，每页只加载自己要的那份。
//
// ## 为什么关掉文件名 hash
//
// 产物要提交进 Git，并在 CI 里做「产物是否与源码同步」的漂移检查（见
// .github/workflows/ci.yml）。带 hash 的文件名每次构建都变，diff 全是噪音，
// 模板里的 <script src> 也得跟着改。改用固定文件名 + 中间件的 Cache-Control。
import { defineConfig } from "vite";

export default defineConfig({
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
        // 固定文件名（见上面「为什么关掉 hash」）
        entryFileNames: "[name].js",
        chunkFileNames: "[name].js",
        assetFileNames: "[name][extname]",
      },
    },
  },
});
