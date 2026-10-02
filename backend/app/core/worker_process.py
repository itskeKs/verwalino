"""Cancellation boundary for synchronous processors with their own hard deadline."""
import asyncio


async def run_bounded_processor(processor,*args):
    """Drain only processor work; database and queue waits stay cancellable.

    asyncio.to_thread does not stop its thread when the awaiter is cancelled.
    The processor must enforce its own subprocess deadline. Keep the caller's
    serial slot until that processor exits, then propagate cancellation before
    the caller can commit its result. Never wrap database work in this helper.
    """
    task=asyncio.create_task(asyncio.to_thread(processor,*args))
    try:return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:await asyncio.shield(task)
            except asyncio.CancelledError:continue
            except Exception:break
        if task.done() and not task.cancelled():task.exception()
        raise
