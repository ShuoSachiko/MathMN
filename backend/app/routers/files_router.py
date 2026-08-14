"""文件管理路由模块，提供文件下载、列表和目录打开等接口。"""

import os
import re
import subprocess

from fastapi import APIRouter, HTTPException
from icecream import ic  # type: ignore[import-unresolved]

from app.utils.common_utils import get_current_files, get_work_dir

router = APIRouter()

# task_id 只允许字母、数字、下划线和连字符，首字符必须是字母或数字，
# 长度不超过 64，用于防止路径遍历攻击
_TASK_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def _validate_task_id(task_id: str) -> str:
    """校验 task_id 合法性，非法时返回 400。

    Args:
        task_id: 待校验的任务 ID。

    Returns:
        通过校验的 task_id。

    Raises:
        HTTPException: task_id 不匹配白名单时抛出 400。
    """
    normalized = (task_id or "").strip()
    if not normalized or not _TASK_ID_PATTERN.fullmatch(normalized):
        raise HTTPException(status_code=400, detail="非法任务ID")
    return normalized


@router.get("/download_url")
async def get_download_url(task_id: str, filename: str):
    task_id = _validate_task_id(task_id)
    return {"download_url": f"http://localhost:8000/static/{task_id}/{filename}"}


@router.get("/download_all_url")
async def get_download_all_url(task_id: str):
    task_id = _validate_task_id(task_id)
    return {"download_url": f"http://localhost:8000/static/{task_id}/all.zip"}


@router.get("/files")
async def get_files(task_id: str):
    task_id = _validate_task_id(task_id)
    work_dir = get_work_dir(task_id)
    files = get_current_files(work_dir, "all")
    file_all = []

    for i in files:
        file_type = i.split(".")[-1]
        file_all.append({"filename": i, "file_type": file_type})

    return file_all


@router.get("/open_folder")
async def open_folder(task_id: str):
    task_id = _validate_task_id(task_id)
    ic(task_id)
    # 打开工作目录
    work_dir = get_work_dir(task_id)

    # 打开工作目录
    if os.name == "nt":
        subprocess.run(["explorer", work_dir])
    elif os.name == "posix":
        subprocess.run(["open", work_dir])
    else:
        raise HTTPException(status_code=500, detail=f"不支持的操作系统: {os.name}")

    return {"message": "打开工作目录成功", "work_dir": work_dir}
