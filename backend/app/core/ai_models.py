"""Documented OpenAI prices; unknown models remain selectable and unpriced.

Standard USD per million tokens, verified 2026-11-07:
https://developers.openai.com/api/docs/pricing
Evaluation promotion is explicit: defaults and synthetic fixtures are not evidence.
"""
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class ModelPrice:
    input: Decimal
    output: Decimal
    cached: Decimal
    cache_write_multiplier: Decimal | None = None


MODEL_PRICES = {
    'gpt-5.6-luna': ModelPrice(Decimal('.20'), Decimal('1.20'), Decimal('.02'), Decimal('1.25')),
    'gpt-5.4-mini': ModelPrice(Decimal('.75'), Decimal('4.50'), Decimal('.075')),
}
EVALUATED_MODELS: frozenset[str] = frozenset()
DEFAULT_TEST_MODEL = 'gpt-5.6-luna'


ANTHROPIC_MODEL_PRICES = {
    # https://platform.claude.com/docs/en/about-claude/pricing (2026-11-08)
    name: ModelPrice(Decimal('1'), Decimal('5'), Decimal('.1'), Decimal('1.25'))
    for name in ('claude-haiku-4-5', 'claude-haiku-4-5-20251001')
}


def model_price(model: str | None, provider: str = 'openai') -> ModelPrice | None:
    return (MODEL_PRICES if provider == 'openai' else ANTHROPIC_MODEL_PRICES if provider == 'anthropic' else {}).get(model)


def token_cost(model: str | None, input_tokens: int, output_tokens: int, provider: str = 'openai') -> Decimal:
    price = model_price(model, provider)
    if price is None:
        return Decimal('0')
    input_multiplier = Decimal('2') if provider == 'openai' and model == 'gpt-5.6-luna' and input_tokens > 272_000 else Decimal('1')
    output_multiplier = Decimal('1.5') if input_multiplier == 2 else Decimal('1')
    return (input_tokens * price.input * input_multiplier + output_tokens * price.output * output_multiplier) / 1_000_000
