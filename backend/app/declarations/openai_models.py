"""Bounded model discovery at the fixed OpenAI origin; no credential logging."""
from fastapi import HTTPException


async def list_models(api_key: str) -> list[str]:
    import openai
    client = openai.AsyncOpenAI(api_key=api_key, base_url='https://api.openai.com/v1', timeout=15, max_retries=0)
    try:
        result = await client.models.list()
        return sorted({model.id for model in result.data if isinstance(model.id, str) and model.id})
    except (openai.AuthenticationError, openai.PermissionDeniedError):
        raise HTTPException(422, 'OpenAI hat den API-Schlüssel abgelehnt oder erlaubt keinen Modellabruf. Bitte prüfen Sie den Schlüssel und seine Berechtigungen.') from None
    except (openai.APIConnectionError, openai.APITimeoutError):
        raise HTTPException(503, 'OpenAI ist derzeit nicht erreichbar. Bitte versuchen Sie es später erneut.') from None
    except Exception:
        raise HTTPException(503, 'Die Modellliste von OpenAI konnte nicht geladen werden. Bitte versuchen Sie es später erneut.') from None
    finally:
        await client.close()
