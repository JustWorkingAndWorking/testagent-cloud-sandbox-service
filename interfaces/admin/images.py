"""
管理端镜像 API。
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import BinaryIO, Optional, cast

from fastapi import APIRouter, File, Form, Response, UploadFile

from application import image as image_service
from domain.errors import ExternalDependencyError
from config import Constants
from interfaces.admin.schemas import (
    DefaultImageResponse,
    ImageDeleteRequest,
    ImageListItem,
    ImageListResponse,
    ImageReferenceRequest,
    ImageUploadResponse,
)
from interfaces.common import api_responses

__all__ = [
    "router",
]


router = APIRouter(prefix="/admin/images", tags=["管理员 API (镜像操作)"])


# 镜像接口顺序：上传、推送、获取清单、删除、设置默认、删除默认、获取默认
@router.post(
    "/upload",
    response_model=ImageUploadResponse,
    status_code=200,
    responses=api_responses("成功", 200, 400, 502),
)
def upload_image(
    file: UploadFile = File(...),
    registry: Optional[str] = Form(default=None),
    namespace: Optional[str] = Form(default=None),
    auto_push: bool = Form(default=False),
) -> ImageUploadResponse:
    """上传镜像文件。"""
    temp_path = _save_upload(file)
    try:
        result = image_service.upload_image(
            temp_path,
            registry=registry,
            namespace=namespace,
            auto_push=auto_push,
        )
        return ImageUploadResponse(
            full_names=result.full_names,
            pushed_full_names=result.pushed_refs,
        )
    finally:
        # 应用层负责正常流程清理，这里覆盖保存后调用失败等边界。
        try:
            Path(temp_path).unlink(missing_ok=True)
        except OSError:
            pass


@router.post(
    "/push",
    status_code=204,
    responses=api_responses("成功 (无内容)", 204, 400, 502),
)
def push_image(request: ImageReferenceRequest) -> Response:
    """推送指定镜像至注册表。"""
    image_service.push_image(request.full_name)
    return Response(status_code=204)


@router.get(
    "",
    response_model=ImageListResponse,
    responses=api_responses("成功", 200, 502),
)
def list_images() -> ImageListResponse:
    """获取本地镜像清单。"""
    rows = image_service.list_images()
    return ImageListResponse(images=[_image_item(row) for row in rows])


@router.post(
    "/delete",
    status_code=204,
    responses=api_responses("成功 (无内容)", 204, 400, 409, 502),
)
def delete_image(request: ImageDeleteRequest) -> Response:
    """删除指定镜像。"""
    result = image_service.delete_image(request.full_name, request.also_registry)
    headers = {"X-Registry-Delete-Failed": "true"} if result.registry_failed else None
    return Response(status_code=204, headers=headers)


@router.post(
    "/default",
    response_model=DefaultImageResponse,
    status_code=200,
    responses=api_responses("成功", 200, 400, 409, 502),
)
def set_default_image(request: ImageReferenceRequest) -> DefaultImageResponse:
    """设置默认镜像。"""
    full_name = image_service.set_default_image(request.full_name)
    return DefaultImageResponse(full_name=full_name)


@router.post(
    "/default/unset",
    status_code=204,
    responses=api_responses("成功 (无内容)", 204),
)
def unset_default_image() -> Response:
    """取消设置默认镜像。"""
    image_service.unset_default_image()
    return Response(status_code=204)


@router.get(
    "/default",
    response_model=DefaultImageResponse,
    responses=api_responses("成功", 200),
)
def get_default_image() -> DefaultImageResponse:
    """获取默认镜像。"""
    return DefaultImageResponse(full_name=image_service.get_default_image())


def _save_upload(file: UploadFile) -> str:
    filename = (file.filename or "").lower()
    if filename.endswith(".tar.gz"):
        suffix = ".tar.gz"
    elif filename.endswith(".tar"):
        suffix = ".tar"
    else:
        suffix = Path(filename).suffix or ".upload"

    directory = Path(Constants.UPLOAD_TEMP_PATH.value)
    directory.mkdir(parents=True, exist_ok=True)
    descriptor, path = tempfile.mkstemp(prefix="image-", suffix=suffix, dir=directory)
    try:
        source: BinaryIO = file.file
        with cast(BinaryIO, os.fdopen(descriptor, "wb")) as output:
            shutil.copyfileobj(source, output)
    except Exception as exc:
        try:
            os.close(descriptor)
        except OSError:
            pass
        Path(path).unlink(missing_ok=True)
        raise ExternalDependencyError("保存上传镜像失败") from exc
    return path


def _image_item(row: image_service.ImageRow) -> ImageListItem:
    return ImageListItem(
        id=row.id,
        full_name=row.full_name,
        registry=row.registry,
        namespace=row.namespace,
        name=row.name,
        version=row.version,
        created_at=row.created_at.isoformat() if row.created_at is not None else None,
        size=row.size,
        status=row.status.value,
    )
