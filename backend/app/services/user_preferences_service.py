"""Personal preferences always resolve the user from the live session."""
from fastapi import HTTPException

from app.repositories.user_repo import UserRepository


class UserPreferencesService:
    def __init__(self, db, auth):
        self.repo = UserRepository(db, auth.organization_id)
        self.user_id = auth.user_id

    async def get(self):
        return self._output(await self.repo.get_by_id(self.user_id))

    async def update(self, design_theme):
        return self._output(await self.repo.set_design_theme(self.user_id, design_theme))

    @staticmethod
    def _output(user):
        if user is None:
            raise HTTPException(401, 'Not authenticated')
        return {'design_theme': user.design_theme}
