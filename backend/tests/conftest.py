"""pytest 全局配置。

关键:必须在任何测试模块导入 app 之前把数据库指向临时库,
否则 app.database 会按默认配置连到真实的 data/knowledge_atlas.db。
conftest 由 pytest 最先加载,适合做这件事。
"""

import os
import sys
import tempfile
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

_TMP_DB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP_DB.close()
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP_DB.name}"
