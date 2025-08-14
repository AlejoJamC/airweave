"""Module for async data streaming with backpressure."""

import asyncio
import logging
from typing import AsyncGenerator, Generic, Optional, TypeVar

from airweave.platform.entities._base import BaseEntity
from airweave.platform.utils.error_utils import get_error_message

T = TypeVar("T", bound=BaseEntity)


class AsyncSourceStream(Generic[T]):
    """Manages asynchronous processing of entity streams with separate producer/consumer loops.

    - Producer: generates entities from a source
    - Consumer: processes entities independently

    Uses async queue to buffer entities and implement backpressure.
    """

    def __init__(
        self,
        source_generator: AsyncGenerator[T, None],
        queue_size: int = 1000,
        logger: Optional[logging.Logger] = None,
    ):
        """Initialize the async source stream.

        Args:
            source_generator: The source async generator
            queue_size: Size of the queue connecting producer and consumer
            logger: Optional contextualized logger, falls back to global logger if not provided
        """
        self.source_generator = source_generator
        # Queue is used to buffer entities and implement backpressure
        self.queue: asyncio.Queue[Optional[T]] = asyncio.Queue(maxsize=queue_size)
        self.producer_task = None
        self.is_running = True
        self.producer_done = asyncio.Event()
        self.producer_exception = None
        self.logger = logger

    async def __aenter__(self):
        """Context manager entry point."""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit point."""
        await self.stop()
        # Don't suppress exceptions
        return False

    async def _producer(self):
        """Producer task that fills the queue from the source generator."""
        try:
            items_produced = 0
            async for item in self.source_generator:
                if not self.is_running:
                    self.logger.debug("Producer stopping early")
                    break

                # Put item in queue, waiting if queue is full.
                # This is a blocking call, so consumer will wait until the queue has space
                # Effectively, this is a backpressure mechanism.
                await self.queue.put(item)
                items_produced += 1

                # Log progress periodically
                if items_produced % 50 == 0:
                    self.logger.debug(
                        f"AsyncSourceStream producer progress: {items_produced} items queued, "
                        f"queue size: {self.queue.qsize()}/{self.queue.maxsize}"
                    )

            self.logger.info(f"Source generator exhausted after producing {items_produced} items")
        except Exception as e:
            self.logger.error(f"Error in producer: {get_error_message(e)}")
            self.producer_exception = e
            # Re-raise to ensure proper error handling -> THIS DOES NOT WORK
            raise
        finally:
            # Signal we're done by putting None in the queue and setting the done event
            await self.queue.put(None)
            self.producer_done.set()

    async def start(self):
        """Start the background producer task.

        Runs the producer in a separate task so that it can run independently of the consumer.
        """
        self.producer_task = asyncio.create_task(self._producer())

    async def stop(self):
        """Stop the producer and clean up resources."""
        self.is_running = False
        if self.producer_task:
            # Give producer a chance to finish gracefully
            try:
                await asyncio.wait_for(self.producer_task, timeout=5.0)
            except asyncio.TimeoutError:
                self.logger.warning("Producer task did not complete in time, cancelling")
                self.producer_task.cancel()
                try:
                    await self.producer_task
                except asyncio.CancelledError:
                    pass

    async def get_entities(self) -> AsyncGenerator[T, None]:
        """Get entities with timeout to prevent cleanup deadlock."""
        if not self.producer_task:
            await self.start()

        try:
            while True:
                item = await self._get_next_item()

                if item is None:
                    # End of stream
                    self._check_producer_exception()
                    break

                yield item

                # Check for producer errors after yielding
                self._check_producer_exception()

        except GeneratorExit:
            self.logger.debug("Generator cleanup initiated - stopping stream")
            raise
        finally:
            await self._drain_queue()

    async def _get_next_item(self) -> Optional[T]:
        """Get next item from queue with timeout handling.

        Returns:
            The next item, or None if stream is complete
        """
        while True:
            try:
                # Try to get with timeout
                item = await asyncio.wait_for(self.queue.get(), timeout=0.5)
                self.queue.task_done()
                return item

            except asyncio.TimeoutError:
                # Check if we should stop waiting
                if await self._should_stop_waiting():
                    return None
                # Otherwise continue waiting
                continue

    async def _should_stop_waiting(self) -> bool:
        """Check if we should stop waiting for items.

        Returns:
            True if we should stop, False to continue waiting
        """
        if not self.producer_done.is_set():
            # Producer still running
            return False

        # Producer is done, check for remaining items
        try:
            item = self.queue.get_nowait()
            self.queue.task_done()
            # Put it back since we're just checking
            await self.queue.put(item)
            return False  # Still have items
        except asyncio.QueueEmpty:
            return True  # Queue empty and producer done

    def _check_producer_exception(self) -> None:
        """Check and raise any producer exception."""
        if self.producer_exception:
            self.logger.error("Producer encountered an error")
            raise self.producer_exception

    async def _drain_queue(self) -> None:
        """Drain any remaining items to prevent producer deadlock."""
        try:
            while not self.queue.empty():
                self.queue.get_nowait()
                self.queue.task_done()
        except Exception:
            pass  # Best effort cleanup
