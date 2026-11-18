# Запуск веб-версии Карен на хостинге (Render / Railway): python start.py
import os, uvicorn
uvicorn.run("app:app", app_dir="web", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
