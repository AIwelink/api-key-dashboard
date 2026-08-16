# 全站流畅动效 Implementation Plan

**Goal:** 为全站补齐连续的页面切换与通用交互反馈，并重点优化 API 账号池、访问流量分析、运营管理的加载和刷新体验。

**Architecture:** CSS-first。`App` 提供 keyed route stage；重点页面复用真实请求状态添加 `aria-busy`、同步轨和分段 class；加载占位保持稳定尺寸。不开启动画帧驱动，不增加依赖。

**Tech Stack:** React 19、TypeScript、Vite、CSS animation/transition、Vitest。

## Task 1: 固定全站动效契约

- 在样式测试中断言全局 motion token、路由舞台、同步轨、通用弹层和 reduced-motion 规则。
- 在 App 测试中断言路由舞台 class/key 的渲染边界。
- 先运行聚焦测试并确认失败。

## Task 2: 实现全站路由与通用交互动效

- 在 `App.tsx` 中用 `.app-view-stage` 包裹当前页面，Toast 保持在外层。
- 在 `styles.css` 添加统一 token、页面进入、导航状态、按钮按压、Toast/modal/drawer 过渡与 reduced-motion 覆盖。
- 保持导航、鉴权和页面组件挂载逻辑不变。

## Task 3: 优化 API 账号池加载

- 用已有加载和刷新状态生成页面 busy class 与同步轨。
- 为工具栏、分组、健康摘要、账号面板添加分段 class。
- 用稳定加载行替换纯文本加载单元格。
- 添加组件和样式测试。

## Task 4: 优化访问流量分析加载

- 工作区和标签内容增加稳定 stage。
- 将配置页纯文本加载替换为结构化加载面。
- 概览用已有 `aria-busy` 驱动同步轨并分段显示指标和数据区。
- 添加组件和样式测试。

## Task 5: 优化运营管理加载

- 页面根节点提供 busy/refresh class、同步轨和内容分段。
- 查询、指标、生命周期与数据区使用短错峰。
- 兑换码结果层和首屏表格行增加一次性进入反馈。
- 添加组件和样式测试。

## Task 6: 验证与交付

- 运行聚焦测试、完整 `npm test`、`npm run build` 与 `git diff --check`。
- 启动 Vite，检查桌面/手机、路由切换、加载、刷新、标签、弹层、reduced-motion、控制台和横向溢出。
- 保留工作计划页已有未提交修改，只提交本次范围文件并推送到现有 PR 分支。

## Task 7: 扩展到全部页面

- 为路由舞台增加统一 scope 与页面身份标记。
- 普通页面的一级内容带、标签内容、前 6 个表格/列表项目以及加载、空态、错误态使用通用短过渡。
- 排除已有精细编排页面和弹层，避免重复动画与 transform 冲突。
- 路由舞台只动画透明度；内容动画结束后清除 transform，包含 fixed 浮层的祖先在浮层挂载时立即退出位移动画。
- 在桌面、手机和 reduced-motion 模式抽查表单、密集表格、Agent 工作台、前台在线与系统管理页面。
