from typing import Any

import asgi
import httpx
from workers import WorkerEntrypoint

from conversation import ConversationState
from main import (
    ChatRequest,
    app,
    chat,
    elevenlabs_tts,
    get_groq_api_key,
    sync_env,
)


STT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
STT_MODEL = "whisper-large-v3-turbo"
SAMPLE_RATE = 8000
CHANNELS = 1
SAMPLE_WIDTH = 2
MIN_AUDIO_BYTES = 8000


def _plain_value(value: Any) -> Any:

    converter = getattr(value, "to_py", None)

    if converter is not None:
        value = converter()

    if isinstance(value, dict):
        return {
            key: _plain_value(item)
            for key, item in value.items()
        }

    if isinstance(value, list):
        return [
            _plain_value(item)
            for item in value
        ]

    return value


def _coerce_pcm(value: Any) -> bytes:

    value = _plain_value(value)

    if isinstance(value, bytes):
        return value

    if isinstance(value, bytearray):
        return bytes(value)

    if isinstance(value, memoryview):
        return value.tobytes()

    if isinstance(value, (list, tuple)):
        return bytes(value)

    raise TypeError(
        "RPC pcm must be bytes, ArrayBuffer, Uint8Array, or a byte list"
    )


def _pcm_to_wav(pcm_bytes: bytes) -> bytes:

    data_size = len(pcm_bytes)
    byte_rate = SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH
    block_align = CHANNELS * SAMPLE_WIDTH

    header = bytearray()
    header.extend(b"RIFF")
    header.extend((36 + data_size).to_bytes(4, "little"))
    header.extend(b"WAVEfmt ")
    header.extend((16).to_bytes(4, "little"))
    header.extend((1).to_bytes(2, "little"))
    header.extend(CHANNELS.to_bytes(2, "little"))
    header.extend(SAMPLE_RATE.to_bytes(4, "little"))
    header.extend(byte_rate.to_bytes(4, "little"))
    header.extend(block_align.to_bytes(2, "little"))
    header.extend((16).to_bytes(2, "little"))
    header.extend(b"data")
    header.extend(data_size.to_bytes(4, "little"))
    header.extend(pcm_bytes)

    return bytes(header)


async def _transcribe_audio(pcm_bytes: bytes) -> str | None:

    if len(pcm_bytes) < MIN_AUDIO_BYTES:
        print("STT SKIPPED:", len(pcm_bytes), "bytes")
        return None

    api_key = get_groq_api_key()

    if not api_key:
        print("STT ERROR: GROQ_API_KEY missing")
        return None

    wav_bytes = _pcm_to_wav(pcm_bytes)

    print(
        "STT REQUEST:",
        len(pcm_bytes),
        "PCM bytes",
        round(len(pcm_bytes) / (SAMPLE_RATE * CHANNELS * SAMPLE_WIDTH), 2),
        "seconds",
    )

    try:
        files = {
            "file": (
                "audio.wav",
                wav_bytes,
                "audio/wav",
            )
        }

        data = {
            "model": STT_MODEL,
            "language": "en",
            "response_format": "json",
            "temperature": "0",
        }

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(30.0)
        ) as client:
            response = await client.post(
                STT_URL,
                headers={
                    "Authorization": f"Bearer {api_key}"
                },
                data=data,
                files=files,
            )

        if response.status_code >= 400:
            print(
                "GROQ STT ERROR:",
                response.status_code,
                response.text[:1000],
            )
            return None

        transcript = (
            response.json().get("text", "")
            or ""
        ).strip()

        print("STT RESULT:", repr(transcript))
        return transcript

    except Exception as error:
        print(
            "STT REQUEST ERROR:",
            type(error).__name__,
            str(error),
        )
        return None


class VoiceEntrypoint(WorkerEntrypoint):
    """Utterance-level Python service called by the native media Worker."""

    async def fetch(self, request):
        sync_env(self.env)
        return await asgi.fetch(
            app,
            request,
            self.env,
        )

    async def process_utterance(
        self,
        request: dict[str, Any],
    ) -> dict[str, Any]:

        sync_env(self.env)

        request = _plain_value(request)
        call_sid = request.get("callSid")
        pcm_bytes = _coerce_pcm(request.get("pcm"))
        conversation_state = ConversationState(
            **request["conversationState"]
        )

        print(
            "RPC UTTERANCE:",
            call_sid,
            len(pcm_bytes),
            "PCM bytes",
        )

        transcript = await _transcribe_audio(pcm_bytes)

        if not transcript:
            return {
                "audio": b"",
                "conversationState": conversation_state.model_dump(),
                "responseMetadata": None,
            }

        chat_response = await chat(
            ChatRequest(message=transcript),
            conversation_state=conversation_state,
        )

        audio = await elevenlabs_tts(
            chat_response.response,
            output_format="pcm_8000",
        )

        return {
            "audio": audio,
            "conversationState": conversation_state.model_dump(),
            "responseMetadata": {
                "response": chat_response.response,
                "lead": chat_response.lead.model_dump(),
                "score": chat_response.score,
                "classification": chat_response.classification,
                "reasons": chat_response.reasons,
                "recommended_action": (
                    chat_response.recommended_action
                ),
            },
        }
