import asgi

from workers import WorkerEntrypoint

from main import app
from voice_rpc import VoiceEntrypoint

__all__ = ["Default", "VoiceEntrypoint"]


class Default(WorkerEntrypoint):

    async def fetch(self, request):
        return await asgi.fetch(
            app,
            request,
            self.env,
        )
