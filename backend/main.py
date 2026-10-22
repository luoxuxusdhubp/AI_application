import logging
import os
import sys
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# 兼容直接脚本运行：确保可以使用绝对导入 backend.*
if __name__ == "__main__":
    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)

from backend.db import ensure_all_tables
from backend.scheduler import start_scheduler
from backend.routes import router as api_router
from backend.stickers import STICKER_DIR


def create_app() -> FastAPI:
    """
    创建并返回 FastAPI 应用
    - 初始化数据库表
    - 注册路由
    - 启动后台调度
    """
    logging.basicConfig(level=logging.INFO)
    app = FastAPI()

    # 允许前端跨域访问
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 初始化数据库
    ensure_all_tables()

    # 注册 API 路由
    app.include_router(api_router)

    # 表情包预览（仅静态读取素材库目录）
    os.makedirs(STICKER_DIR, exist_ok=True)
    app.mount("/sticker-files", StaticFiles(directory=str(STICKER_DIR)), name="sticker-files")

    # 启动调度器
    start_scheduler()

    return app


# 供 ASGI 使用
app = create_app()


if __name__ == "__main__":
    # 允许直接运行：uv run backend\main.py 或 python backend/main.py
    import uvicorn
    # reload 会在 Windows 启动额外的监控/子进程，且可能重复初始化
    # wxauto4 和调度线程；需要热重载时可设置 UVICORN_RELOAD=1。
    reload_enabled = os.getenv("UVICORN_RELOAD", "").strip().lower() in {"1", "true", "yes"}
    uvicorn.run(
        "backend.main:app",
        host="127.0.0.1",
        port=8000,
        reload=reload_enabled,
    )
