"""프론트(뷰어 저장소) 파일을 백엔드 서버가 같이 보낸다. 서버 하나로 화면과 API를 함께 운영하기 위함.

- FRONTEND_DIR: 프론트 저장소 폴더. 비우면 백엔드 옆의 GDGoC_TUK_pssk 폴더를 찾고, 없으면 프론트를 보내지 않음
- /api, /docs 등 API 주소가 먼저이고, 나머지 주소만 프론트 파일로 보냄 (main.py 맨 끝에서 mount)
- .git, .env처럼 점(.)으로 시작하는 파일·폴더는 보내지 않음 (프론트 저장소를 통째로 받아 오면 .git이 같이 들어옴)
"""

import logging
import os
from pathlib import Path

from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles

log = logging.getLogger(__name__)

SIBLING_DIR = Path(__file__).resolve().parent.parent.parent / "GDGoC_TUK_pssk"


class PublicStaticFiles(StaticFiles):
    """점으로 시작하는 경로(.git, .env 등)는 404"""

    def get_path(self, scope) -> str:
        path = super().get_path(scope)
        if any(part.startswith(".") for part in Path(path).parts):
            raise HTTPException(status_code=404)
        return path


def frontend_dir() -> Path | None:
    configured = os.getenv("FRONTEND_DIR")
    path = Path(configured) if configured else SIBLING_DIR
    if path.is_dir() and (path / "index.html").is_file():
        return path.resolve()
    if configured:
        log.warning("FRONTEND_DIR에 index.html이 없어 프론트를 보내지 않습니다: %s", path)
    return None


def static_app(directory: Path) -> PublicStaticFiles:
    return PublicStaticFiles(directory=str(directory), html=True)  # html=True: 폴더 주소면 index.html
