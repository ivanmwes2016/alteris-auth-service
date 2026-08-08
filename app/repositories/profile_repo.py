from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models.profile import Profile


class ProfileRepository:
    """Repository for profile operations."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_by_user_id(self, user_id: UUID) -> Profile | None:
        result = await self.db.execute(select(Profile).where(Profile.user_id == user_id))
        return result.scalar_one_or_none()

    async def upsert(
        self,
        user_id: UUID,
        email: str,
        full_name: str,
    ) -> Profile:
        profile = await self.get_by_user_id(user_id)

        if profile:
            profile.email = email
            profile.full_name = full_name
        else:
            profile = Profile(
                user_id=user_id,
                email=email,
                full_name=full_name,
            )
            self.db.add(profile)

        await self.db.commit()
        await self.db.refresh(profile)

        return profile
