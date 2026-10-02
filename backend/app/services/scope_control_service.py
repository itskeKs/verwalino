"""Admin rollback keeps canonical scopes and grants intact."""
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from app.schemas.scope_control import ScopeControlEvent, ScopeControlState
from app.services.authorization_service import AuthorizationService


class ScopeControlService(AuthorizationService):
    async def current(self):
        await self._require_administration()
        result = await self.session.scalar(text('SELECT to_jsonb(e) FROM scope_control_event e WHERE organization_id=:org ORDER BY sequence DESC LIMIT 1'),
            {'org': self.auth.organization_id})
        event = ScopeControlEvent.model_validate(result) if result else None
        return ScopeControlState(mode=event.mode if event else 'compatibility', event=event)

    async def change(self, command, request_key):
        await self._require_administration()
        try:
            async with self.session.begin_nested():
                result = await self.session.scalar(text('SELECT change_scoped_effect_control(:org,:key,:mode,:expected,:reason)'),
                    {'org': self.auth.organization_id, 'key': request_key, 'mode': command.mode,
                     'expected': command.expected_event_id, 'reason': command.reason})
        except DBAPIError as error:
            code = getattr(error.orig, 'sqlstate', None)
            if code == 'P0001':
                raise HTTPException(409, 'Scoped control changed or the retry key belongs to another command. Reload the current state.') from None
            if code == '42501':
                raise HTTPException(403, 'Scope control requires an active administrator') from None
            raise
        return ScopeControlEvent.model_validate(result)
