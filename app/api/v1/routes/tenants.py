import logging
import re
import time
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool
from storage3.utils import StorageException
from supabase import Client

from app.api.v1.routes.auth import get_current_user
from app.core.config import get_settings
from app.core.db import get_db
from app.core.supabase import get_supabase
from app.crud import team
from app.db.models.tenant import Tenant
from app.db.models.tenant_gallery_image import TenantGalleryImage
from app.db.models.users import User

log = logging.getLogger(__name__)

router = APIRouter()

ALLOWED_IMAGE_EXTENSIONS = {"png", "jpg", "jpeg", "webp"}

# Public bucket: gallery photos are shown on the school's public listing page.
GALLERY_BUCKET = "tenant-gallery"
GALLERY_MAX_PHOTOS = 50
_UUID_RE = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


class ImageUploadUrlResponse(BaseModel):
    path: str
    token: str


class GalleryImageCreate(BaseModel):
    path: str
    caption: str | None = Field(default=None, max_length=500)


class GalleryImageUpdate(BaseModel):
    caption: str | None = Field(default=None, max_length=500)


class GalleryOrderUpdate(BaseModel):
    image_ids: list[UUID]


class GalleryImageRead(BaseModel):
    id: UUID
    caption: str | None
    position: int
    url: str


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------
async def _require_tenant_editor(db: AsyncSession, user: User, tenant_id: UUID) -> None:
    # Same rule as editing the school profile: any active member of this tenant.
    actor = await team.get_actor(db, user)
    if actor.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to edit this tenant",
        )


def _validated_ext(ext: str) -> str:
    ext = ext.lower()
    if ext not in ALLOWED_IMAGE_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type. Allowed: {', '.join(sorted(ALLOWED_IMAGE_EXTENSIONS))}",
        )
    return ext


async def _signed_upload_url(supabase: Client, bucket: str, path: str) -> ImageUploadUrlResponse:
    """Signed upload token so the browser can PUT the image straight to storage.

    Only the path and one-time token are returned; the admin key stays here.
    """
    try:
        signed = await run_in_threadpool(
            supabase.storage.from_(bucket).create_signed_upload_url, path
        )
    except StorageException as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not create upload URL",
        ) from exc

    return ImageUploadUrlResponse(path=path, token=signed["token"])


async def _create_image_upload_url(
    db: AsyncSession,
    supabase: Client,
    current_user: User,
    tenant_id: UUID,
    kind: str,
    ext: str,
) -> ImageUploadUrlResponse:
    await _require_tenant_editor(db, current_user, tenant_id)
    ext = _validated_ext(ext)
    path = f"{tenant_id}/{kind}-{int(time.time() * 1000)}.{ext}"
    return await _signed_upload_url(supabase, get_settings().SUPABASE_LOGO_BUCKET_NAME, path)


def _gallery_image_read(supabase: Client, image: TenantGalleryImage) -> GalleryImageRead:
    # get_public_url only formats a string (no request); it appends an empty "?".
    url = supabase.storage.from_(GALLERY_BUCKET).get_public_url(image.storage_path).rstrip("?")
    return GalleryImageRead(id=image.id, caption=image.caption, position=image.position, url=url)


def _clean_caption(caption: str | None) -> str | None:
    return (caption or "").strip() or None


async def _lock_tenant(db: AsyncSession, tenant_id: UUID) -> None:
    """Serialise gallery writes per tenant so the 50-photo cap and positions hold
    even when the browser saves several uploads in parallel."""
    await db.execute(select(Tenant.id).where(Tenant.id == tenant_id).with_for_update())


async def _gallery_count(db: AsyncSession, tenant_id: UUID) -> int:
    count = await db.scalar(
        select(func.count())
        .select_from(TenantGalleryImage)
        .where(TenantGalleryImage.tenant_id == tenant_id)
    )
    return count or 0


def _raise_gallery_full() -> None:
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"A gallery can hold at most {GALLERY_MAX_PHOTOS} photos",
    )


async def _get_gallery_image(
    db: AsyncSession, tenant_id: UUID, image_id: UUID
) -> TenantGalleryImage:
    image = await db.scalar(
        select(TenantGalleryImage).where(
            TenantGalleryImage.id == image_id, TenantGalleryImage.tenant_id == tenant_id
        )
    )
    if image is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Photo not found")
    return image


# ---------------------------------------------------------------------------
# Logo / cover image
# ---------------------------------------------------------------------------
@router.post("/{tenant_id}/logo-upload-url", response_model=ImageUploadUrlResponse)
async def create_logo_upload_url(
    tenant_id: UUID,
    ext: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
) -> ImageUploadUrlResponse:
    return await _create_image_upload_url(db, supabase, current_user, tenant_id, "logo", ext)


@router.post("/{tenant_id}/cover-upload-url", response_model=ImageUploadUrlResponse)
async def create_cover_upload_url(
    tenant_id: UUID,
    ext: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
) -> ImageUploadUrlResponse:
    return await _create_image_upload_url(db, supabase, current_user, tenant_id, "cover", ext)


# ---------------------------------------------------------------------------
# Photo gallery
# ---------------------------------------------------------------------------
@router.post("/{tenant_id}/gallery-upload-url", response_model=ImageUploadUrlResponse)
async def create_gallery_upload_url(
    tenant_id: UUID,
    ext: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
) -> ImageUploadUrlResponse:
    await _require_tenant_editor(db, current_user, tenant_id)
    ext = _validated_ext(ext)

    # Early rejection only; the cap is enforced under a lock when the photo is saved.
    if await _gallery_count(db, tenant_id) >= GALLERY_MAX_PHOTOS:
        _raise_gallery_full()

    path = f"{tenant_id}/gallery/{uuid4()}.{ext}"
    return await _signed_upload_url(supabase, GALLERY_BUCKET, path)


@router.get("/{tenant_id}/gallery", response_model=list[GalleryImageRead])
async def list_gallery(
    tenant_id: UUID,
    db: AsyncSession = Depends(get_db),
    supabase: Client = Depends(get_supabase),
) -> list[GalleryImageRead]:
    """Public: the listing page shows these, and the bucket is public anyway."""
    images = await db.scalars(
        select(TenantGalleryImage)
        .where(TenantGalleryImage.tenant_id == tenant_id)
        .order_by(TenantGalleryImage.position, TenantGalleryImage.created_at)
    )
    return [_gallery_image_read(supabase, image) for image in images]


@router.post(
    "/{tenant_id}/gallery",
    response_model=GalleryImageRead,
    status_code=status.HTTP_201_CREATED,
)
async def add_gallery_image(
    tenant_id: UUID,
    payload: GalleryImageCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
) -> GalleryImageRead:
    await _require_tenant_editor(db, current_user, tenant_id)

    prefix = f"{tenant_id}/gallery/"
    # Exact shape the upload-url endpoint hands out, so no "..", other tenants'
    # folders, or other buckets' naming can be smuggled in and later deleted.
    pattern = rf"{re.escape(prefix)}{_UUID_RE}\.(png|jpg|jpeg|webp)"
    if not payload.path.startswith(prefix) or not re.fullmatch(pattern, payload.path):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid photo path for this school",
        )

    await _lock_tenant(db, tenant_id)
    if await _gallery_count(db, tenant_id) >= GALLERY_MAX_PHOTOS:
        _raise_gallery_full()

    max_position = await db.scalar(
        select(func.max(TenantGalleryImage.position)).where(
            TenantGalleryImage.tenant_id == tenant_id
        )
    )

    image = TenantGalleryImage(
        tenant_id=tenant_id,
        storage_path=payload.path,
        caption=_clean_caption(payload.caption),
        position=0 if max_position is None else max_position + 1,
        uploaded_by=current_user.id,
    )
    db.add(image)

    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This photo has already been added",
        ) from exc

    return _gallery_image_read(supabase, image)


@router.put("/{tenant_id}/gallery/order", response_model=list[GalleryImageRead])
async def reorder_gallery(
    tenant_id: UUID,
    payload: GalleryOrderUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
) -> list[GalleryImageRead]:
    await _require_tenant_editor(db, current_user, tenant_id)
    await _lock_tenant(db, tenant_id)

    images = {
        image.id: image
        for image in await db.scalars(
            select(TenantGalleryImage).where(TenantGalleryImage.tenant_id == tenant_id)
        )
    }

    if len(payload.image_ids) != len(images) or set(payload.image_ids) != set(images):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="image_ids must list every photo in this gallery exactly once",
        )

    for position, image_id in enumerate(payload.image_ids):
        images[image_id].position = position

    await db.commit()
    return [_gallery_image_read(supabase, images[image_id]) for image_id in payload.image_ids]


@router.patch("/{tenant_id}/gallery/{image_id}", response_model=GalleryImageRead)
async def update_gallery_caption(
    tenant_id: UUID,
    image_id: UUID,
    payload: GalleryImageUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
) -> GalleryImageRead:
    await _require_tenant_editor(db, current_user, tenant_id)
    image = await _get_gallery_image(db, tenant_id, image_id)

    image.caption = _clean_caption(payload.caption)
    await db.commit()

    return _gallery_image_read(supabase, image)


@router.delete("/{tenant_id}/gallery/{image_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_gallery_image(
    tenant_id: UUID,
    image_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    supabase: Client = Depends(get_supabase),
) -> Response:
    await _require_tenant_editor(db, current_user, tenant_id)
    image = await _get_gallery_image(db, tenant_id, image_id)

    try:
        await run_in_threadpool(supabase.storage.from_(GALLERY_BUCKET).remove, [image.storage_path])
    except Exception:
        # The row goes regardless, so the photo disappears from the school's page;
        # the orphaned object can be cleaned up from the log.
        log.exception("Failed to delete gallery object %s from storage", image.storage_path)

    await db.delete(image)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
