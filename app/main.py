"""入口: 手动启动本服务(不做开机自启)。
启动时自动检查数据是否过期, 错过的池内更新立即补跑。
运行:  python -m app.main   (或双击 start.bat)
访问:  http://127.0.0.1:8600
"""
import threading

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import config, db
from .api import router
from .updater import catchup, scheduler_loop

app = FastAPI(title="股票筛选系统", docs_url="/docs")
app.include_router(router)
app.mount("/static", StaticFiles(directory=config.WEB_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(config.WEB_DIR + "/index.html")


_stop = threading.Event()


@app.on_event("startup")
def on_startup():
    db.init_db()

    def _catchup():
        try:
            msg = catchup()
            print(f"[启动补跑] {msg}")
        except Exception as e:
            print(f"[启动补跑] 失败: {e}")

    threading.Thread(target=_catchup, daemon=True).start()
    threading.Thread(target=scheduler_loop, args=(_stop,), daemon=True).start()


if __name__ == "__main__":
    print(f"股票筛选系统启动: http://127.0.0.1:{config.PORT}  (Ctrl+C 停止)")
    uvicorn.run(app, host="127.0.0.1", port=config.PORT, log_level="warning")
