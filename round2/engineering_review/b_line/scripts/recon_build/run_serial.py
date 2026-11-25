"""One pytest worker with a periodic asyncio wakeup on restricted hosts."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import sys
import pytest


class SerialPolicy(asyncio.DefaultEventLoopPolicy):
    def new_event_loop(self):
        loop = super().new_event_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=1))

        def wake():
            loop.call_later(0.02, wake)
        loop.call_soon(wake)
        return loop


asyncio.set_event_loop_policy(SerialPolicy())
sys.exit(pytest.main(sys.argv[1:]))
