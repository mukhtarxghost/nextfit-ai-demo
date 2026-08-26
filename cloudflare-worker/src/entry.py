import asgi

from workers import WorkerEntrypoint

from main import app, sync_env
from voice_rpc import VoiceEntrypoint

__all__ = ["Default", "VoiceEntrypoint"]


class Default(WorkerEntrypoint):

    async def fetch(self, request):
        sync_env(self.env)
        return await asgi.fetch(
            app,
            request,
            self.env,
        )
