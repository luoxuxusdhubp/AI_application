# 可选网页管理界面

当前默认入口是桌面小宠物，首次安装、API 配置和使用步骤见 [README](README.md)。

## 启动与访问

在项目根目录执行：

```powershell
npm ci
.\启动微信助手.bat --web
```

网页入口为 `http://127.0.0.1:3000`，介绍页为 `/intro`，AI 答复页为 `/ai`，系统配置页为 `/settings`。网页模式需要 Node.js 18+。

网页保留原有的好友、分组、消息和知识库管理页面，具体微信操作能力受 `wxauto4` 免费版限制。

## 页面维护

- `app/intro/page.tsx`：网页介绍页和页脚。
- `app/components/HeaderNav.tsx`：导航和当前仓库链接。
- `app/components/SystemSettings.tsx`：提示词、目标联系人和功能开关。
- `tailwind.config.ts`：网页样式配置。

默认联系人为空，由使用者自行填写。产品说明以当前代码和 README 为准。

## 联系方式

邮箱：[2910870043@qq.com](mailto:2910870043@qq.com)
