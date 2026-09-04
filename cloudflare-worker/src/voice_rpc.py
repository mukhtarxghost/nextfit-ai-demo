import asyncio
import struct
import wave
import io
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

STT_MAX_RETRIES = 2
STT_BASE_BACKOFF = 1.5


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

    buf = io.BytesIO()

    with wave.open(buf, "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(SAMPLE_WIDTH)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm_bytes)

    return buf.getvalue()


def _silence_pcm(duration_ms: int) -> bytes:
    num_samples = int(SAMPLE_RATE * duration_ms / 1000)
    return struct.pack(f"<{num_samples}h", *([0] * num_samples))


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

    last_error: Exception | None = None

    for attempt in range(STT_MAX_RETRIES):

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
                "temperature": 0,
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

            if response.status_code == 429:

                retry_after_raw = response.headers.get("retry-after")
                retry_after = float(retry_after_raw) if retry_after_raw else STT_BASE_BACKOFF * (2 ** attempt)
                retry_after = min(retry_after, 10.0)

                print(
                    "STT 429 attempt=",
                    attempt + 1,
                    "of",
                    STT_MAX_RETRIES,
                    "retry_after=",
                    retry_after,
                )

                if attempt < STT_MAX_RETRIES - 1:
                    await asyncio.sleep(retry_after)
                    continue

                print("STT 429 EXHAUSTED RETRIES")
                return None

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
            last_error = error
            print(
                "STT REQUEST ERROR:",
                type(error).__name__,
                str(error),
            )

            if attempt < STT_MAX_RETRIES - 1:
                await asyncio.sleep(STT_BASE_BACKOFF * (2 ** attempt))
                continue

            return None

    return None


async def _generate_fallback_audio(text: str) -> bytes:
    try:
        return await elevenlabs_tts(
            text,
            output_format="pcm_8000",
        )
    except Exception as error:
        print(
            "FALLBACK TTS ERROR:",
            type(error).__name__,
            str(error),
        )
        return _silence_pcm(2000)


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

        raw_state = request.get("conversationState")
        if not isinstance(raw_state, dict):
            raw_state = {}

        conversation_state = ConversationState(**raw_state)

        print(
            "RPC UTTERANCE:",
            call_sid,
            len(pcm_bytes),
            "PCM bytes",
        )

        transcript = await _transcribe_audio(pcm_bytes)

        if not transcript:
            fallback_audio = await _generate_fallback_audio(
                "Sorry, I didn't catch that. Could you say it again?"
            )
            return {
                "audio": fallback_audio,
                "conversationState": conversation_state.model_dump(),
                "responseMetadata": None,
            }

        try:
            chat_response = await chat(
                ChatRequest(message=transcript),
                conversation_state=conversation_state,
            )
        except Exception as error:
            print(
                "CHAT ERROR:",
                type(error).__name__,
                str(error),
            )
            fallback_audio = await _generate_fallback_audio(
                "Sorry, something went wrong. Could you try again?"
            )
            return {
                "audio": fallback_audio,
                "conversationState": conversation_state.model_dump(),
                "responseMetadata": None,
            }

        try:
            audio = await elevenlabs_tts(
                chat_response.response,
                output_format="pcm_8000",
            )
        except Exception as error:
            print(
                "TTS ERROR AFTER CHAT:",
                type(error).__name__,
                str(error),
            )
            fallback_audio = await _generate_fallback_audio(
                "Sorry, I couldn't respond. Could you repeat that?"
            )
            return {
                "audio": fallback_audio,
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
