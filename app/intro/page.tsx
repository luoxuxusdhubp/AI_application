"use client";

import { useState } from "react";
import Link from "next/link";

export default function IntroPage() {
  const [activeFeature, setActiveFeature] = useState(0);
  const [menuOpen, setMenuOpen] = useState(false);

  const features = [
    {
      id: 0,
      icon: "👥",
      title: "好友管理",
      description: "智能化的好友管理系统，让客户管理更轻松",
      details: [
        "一键同步微信好友列表",
        "灵活的分组管理功能",
        "快速修改好友备注",
        "完整的聊天记录查看",
      ],
      color: "from-blue-500 to-cyan-500",
    },
    {
      id: 1,
      icon: "🤖",
      title: "AI 智能答复",
      description: "基于知识库的智能问答系统，自动回复客户消息",
      details: [
        "自定义知识库管理",
        "智谱 GLM AI 模型支持",
        "智能语义理解与匹配",
        "24/7 自动回复服务",
      ],
      color: "from-purple-500 to-pink-500",
    },
    {
      id: 2,
      icon: "📨",
      title: "营销推广",
      description: "高效的消息群发系统，精准触达目标客户",
      details: [
        "按分组批量发送消息",
        "灵活的定时发送功能",
        "完整的发送历史记录",
        "实时发送状态追踪",
      ],
      color: "from-green-500 to-emerald-500",
    },
    {
      id: 3,
      icon: "⚙️",
      title: "系统配置",
      description: "灵活的系统设置，自定义 AI 行为和回复策略",
      details: [
        "自定义 AI 系统提示词",
        "一键启动/停止自动回复",
        "实时运行状态监控",
        "详细的操作日志记录",
      ],
      color: "from-orange-500 to-red-500",
    },
  ];

  const stats = [
    { label: "功能模块", value: "4+", icon: "🎯" },
    { label: "消息处理", value: "毫秒级", icon: "⚡" },
    { label: "同时管理", value: "1000+", icon: "👥" },
    { label: "自动回复", value: "24/7", icon: "🤖" },
  ];

  const techStack = [
    { name: "Next.js 14", icon: "⚛️", desc: "现代化前端框架" },
    { name: "FastAPI", icon: "🚀", desc: "高性能 Python 后端" },
    { name: "智谱 GLM AI", icon: "🧠", desc: "先进的大语言模型" },
    { name: "SQLite", icon: "💾", desc: "轻量级数据库" },
    { name: "wxauto4", icon: "💬", desc: "免费版微信自动化控制" },
    { name: "Tailwind CSS", icon: "🎨", desc: "现代化 UI 设计" },
  ];

  return (
    <div className="min-h-screen bg-white">
      {/* 移动端顶部导航 */}
      <nav className="fixed top-0 left-0 right-0 z-50 bg-white/95 backdrop-blur-sm border-b border-gray-100 shadow-sm">
        <div className="px-4">
          <div className="flex items-center justify-between h-14">
            <div className="flex items-center gap-2">
              <div className="text-xl">💬</div>
              <span className="text-base font-bold bg-gradient-to-r from-primary-500 to-secondary-500 bg-clip-text text-transparent">
                微信 AI 助手
              </span>
            </div>
            <Link
              href="/friends"
              className="px-4 py-1.5 rounded-lg bg-gradient-to-r from-primary-500 to-secondary-500 text-white text-sm font-medium active:scale-95 transition-transform"
            >
              进入系统
            </Link>
          </div>
        </div>
      </nav>

      {/* Hero 区域 - 移动端优化 */}
      <section className="pt-20 pb-12 px-4">
        <div className="text-center">
          <div className="inline-block mb-3 px-3 py-1 rounded-full bg-primary-100 text-primary-700 text-xs font-medium">
            🚀 智能客服管理平台
          </div>
          <h1 className="text-3xl font-bold text-gray-900 mb-4 leading-tight">
            微信 AI 助手
            <br />
            <span className="bg-gradient-to-r from-primary-500 to-secondary-500 bg-clip-text text-transparent">
              让客户管理更智能
            </span>
          </h1>
          <p className="text-sm text-gray-600 mb-8 leading-relaxed px-2">
            基于 AI 的智能微信助手管理平台，集成好友管理、智能问答、自动回复、消息群发等功能，
            助力企业提升客户服务效率。
          </p>
          <div className="flex flex-col gap-3 px-4">
            <Link
              href="/friends"
              className="w-full py-3 rounded-xl bg-gradient-to-r from-primary-500 to-secondary-500 text-white text-base font-semibold active:scale-95 transition-transform shadow-lg"
            >
              立即体验
            </Link>
            <a
              href="#features"
              className="w-full py-3 rounded-xl bg-white text-gray-700 text-base font-semibold border border-gray-200 active:bg-gray-50 transition-colors"
            >
              了解更多
            </a>
          </div>
        </div>
      </section>

      {/* 数据统计 - 移动端优化 */}
      <section className="py-8 px-4 bg-gradient-to-br from-gray-50 to-gray-100">
        <div className="grid grid-cols-2 gap-3">
          {stats.map((stat, index) => (
            <div
              key={index}
              className="text-center p-4 rounded-xl bg-white shadow-sm active:scale-95 transition-transform"
            >
              <div className="text-2xl mb-2">{stat.icon}</div>
              <div className="text-xl font-bold text-gray-900 mb-1">
                {stat.value}
              </div>
              <div className="text-xs text-gray-600">
                {stat.label}
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* 核心功能 - 移动端优化 */}
      <section id="features" className="py-10 px-4 bg-white">
        <div className="text-center mb-6">
          <h2 className="text-2xl font-bold text-gray-900 mb-2">
            核心功能
          </h2>
          <p className="text-sm text-gray-600">
            四大核心模块，全方位提升效率
          </p>
        </div>

        {/* 功能卡片列表 */}
        <div className="space-y-3">
          {features.map((feature, index) => (
            <div
              key={feature.id}
              onClick={() => setActiveFeature(activeFeature === index ? -1 : index)}
              className={`p-4 rounded-xl cursor-pointer transition-all ${
                activeFeature === index
                  ? "bg-white shadow-lg border-2 border-primary-500"
                  : "bg-gray-50 active:bg-gray-100"
              }`}
            >
              <div className="flex items-start gap-3">
                <div className="text-3xl p-2 rounded-lg bg-white shadow-sm">
                  {feature.icon}
                </div>
                <div className="flex-1 min-w-0">
                  <h3 className="text-base font-bold text-gray-900 mb-1">
                    {feature.title}
                  </h3>
                  <p className="text-xs text-gray-600 line-clamp-2">
                    {feature.description}
                  </p>
                </div>
                <div
                  className={`text-gray-400 transition-transform flex-shrink-0 ${
                    activeFeature === index ? "rotate-90" : ""
                  }`}
                >
                  →
                </div>
              </div>

              {/* 展开的详细信息 */}
              {activeFeature === index && (
                <div className="mt-4 pt-4 border-t border-gray-200 space-y-2">
                  {feature.details.map((detail, idx) => (
                    <div
                      key={idx}
                      className="flex items-start gap-2 text-sm"
                    >
                      <div className="w-4 h-4 mt-0.5 rounded-full bg-gradient-to-br from-primary-500 to-secondary-500 flex items-center justify-center text-white text-xs flex-shrink-0">
                        ✓
                      </div>
                      <span className="text-gray-700">{detail}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      </section>

      {/* 技术栈 - 移动端优化 */}
      <section className="py-10 px-4 bg-gradient-to-br from-gray-50 to-gray-100">
        <div className="text-center mb-6">
          <h2 className="text-2xl font-bold text-gray-900 mb-2">
            技术架构
          </h2>
          <p className="text-sm text-gray-600">
            采用现代化技术栈，稳定高效
          </p>
        </div>

        <div className="grid grid-cols-2 gap-3">
          {techStack.map((tech, index) => (
            <div
              key={index}
              className="p-4 rounded-xl bg-white shadow-sm active:scale-95 transition-transform"
            >
              <div className="text-2xl mb-2">{tech.icon}</div>
              <h4 className="text-sm font-bold text-gray-900 mb-1">
                {tech.name}
              </h4>
              <p className="text-xs text-gray-600 line-clamp-2">{tech.desc}</p>
            </div>
          ))}
        </div>
      </section>

      {/* 应用场景 - 移动端优化 */}
      <section className="py-10 px-4 bg-white">
        <div className="text-center mb-6">
          <h2 className="text-2xl font-bold text-gray-900 mb-2">
            应用场景
          </h2>
          <p className="text-sm text-gray-600">
            适用于多种业务场景
          </p>
        </div>

        <div className="space-y-3">
          {[
            {
              title: "电商客服",
              icon: "🛍️",
              desc: "自动回复产品咨询，处理订单问题，提升客户满意度",
            },
            {
              title: "企业服务",
              icon: "🏢",
              desc: "智能客户管理，批量营销推广，提高业务转化率",
            },
            {
              title: "教育培训",
              icon: "📚",
              desc: "课程咨询自动答复，学员管理，通知及时推送",
            },
          ].map((scenario, index) => (
            <div
              key={index}
              className="p-4 rounded-xl bg-gradient-to-br from-gray-50 to-gray-100 shadow-sm active:scale-95 transition-transform"
            >
              <div className="flex items-center gap-4">
                <div className="text-4xl flex-shrink-0">{scenario.icon}</div>
                <div className="flex-1 min-w-0">
                  <h4 className="text-base font-bold text-gray-900 mb-1">
                    {scenario.title}
                  </h4>
                  <p className="text-sm text-gray-600">{scenario.desc}</p>
                </div>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* CTA 区域 - 移动端优化 */}
      <section className="py-12 px-4 bg-gradient-to-br from-primary-500 to-secondary-500">
        <div className="text-center">
          <h2 className="text-2xl font-bold text-white mb-3">
            准备好开始了吗？
          </h2>
          <p className="text-sm text-white/90 mb-6 px-4">
            立即体验微信 AI 助手<br/>让客户管理更智能、更高效
          </p>
          <Link
            href="/friends"
            className="inline-block w-full max-w-xs px-8 py-3 rounded-xl bg-white text-primary-600 text-base font-bold active:scale-95 transition-transform shadow-xl"
          >
            开始使用
          </Link>
        </div>
      </section>

      {/* 页脚 - 移动端优化 */}
      <footer className="py-8 px-4 bg-gray-900 text-white">
        <div className="space-y-6">
          <div className="text-center">
            <div className="flex items-center justify-center gap-2 mb-2">
              <span className="text-xl">💬</span>
              <span className="text-base font-bold">微信 AI 助手</span>
            </div>
            <p className="text-gray-400 text-xs">
              智能化的微信客服管理平台
            </p>
          </div>

          <div className="grid grid-cols-2 gap-6 text-sm">
            <div>
              <h4 className="font-bold mb-3 text-sm">快速链接</h4>
              <div className="space-y-2">
                <Link
                  href="/friends"
                  className="block text-gray-400 text-xs active:text-white transition-colors"
                >
                  好友管理
                </Link>
                <Link
                  href="/ai"
                  className="block text-gray-400 text-xs active:text-white transition-colors"
                >
                  AI 答复
                </Link>
                <Link
                  href="/marketing"
                  className="block text-gray-400 text-xs active:text-white transition-colors"
                >
                  营销推广
                </Link>
                <Link
                  href="/settings"
                  className="block text-gray-400 text-xs active:text-white transition-colors"
                >
                  系统配置
                </Link>
              </div>
            </div>
            <div>
              <h4 className="font-bold mb-3 text-sm">技术支持</h4>
              <div className="space-y-2 text-gray-400 text-xs">
                <a href="mailto:2910870043@qq.com" className="break-all active:text-white transition-colors">
                  2910870043@qq.com
                </a>
              </div>
            </div>
          </div>

          <div className="pt-6 border-t border-gray-800 text-center text-gray-400 text-xs">
            <p>© 2025 微信 AI 助手</p>
          </div>
        </div>
      </footer>
    </div>
  );
}

