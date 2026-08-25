import asgi
import asyncio
import base64
import json
import math
import struct
import time

from js import Response as JSResponse
from js import WebSocketPair
from pyodide.ffi import create_proxy
from workers import WorkerEntrypoint

from main import app


# ============================================================
# CONFIG
# ============================================================

STT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
STT_MODEL = "whisper-large-v3-turbo"

SAMPLE_RATE = 8000
CHANNELS = 1
SAMPLE_WIDTH = 2

# Caller must be silent this long before processing.
SILENCE_MS = 750

# Minimum 0.5 seconds of audio.
MIN_AUDIO_BYTES = 8000

# Maximum 30 seconds per utterance.
MAX_AUDIO_BYTES = 8000 * 2 * 30

# Simple PCM RMS threshold.
VAD_THRESHOLD = 500

# 400 ms at 8kHz mono PCM16.
OUTBOUND_CHUNK_BYTES = 6400

# IMPORTANT:
# Do NOT print every Exotel media packet.
# Exotel sends packets roughly every 20ms and the Worker log
# limit is easy to hit.


class Default(WorkerEntrypoint):

    async def fetch(self, request):

        url = str(request.url)

        # ========================================================
        # EXOTEL WEBSOCKET
        # ========================================================

        if "/media" in url:

            upgrade = request.headers.get(
                "Upgrade",
                "",
            )

            if upgrade.lower() != "websocket":

                return JSResponse.new(
                    "WebSocket endpoint. Upgrade required.",
                    {
                        "status": 426,
                        "headers": {
                            "Content-Type": "text/plain",
                        },
                    },
                )

            # ----------------------------------------------------
            # WEBSOCKET PAIR
            # ----------------------------------------------------

            pair = WebSocketPair.new()

            values = pair.object_values()

            client = values[0]
            server = values[1]

            server.accept()

            # ----------------------------------------------------
            # CONNECTION STATE
            # ----------------------------------------------------

            state = {
                "connected": False,
                "started": False,

                "stream_sid": None,
                "call_sid": None,

                "sample_rate": SAMPLE_RATE,
                "encoding": "audio/x-l16",

                # inbound
                "audio_buffer": bytearray(),

                # VAD
                "speech_started": False,
                "silence_started": None,

                # processing
                "processing_audio": False,
                "processing_task": None,
                "monitor_task": None,

                # outbound
                "sending_audio": False,
                "outbound_sequence": 0,

                # debug
                "utterance_number": 0,

                # proxies
                "message_proxy": None,
                "close_proxy": None,
                "error_proxy": None,
            }

            # ====================================================
            # AUDIO BUFFER
            # ====================================================

            def reset_audio_buffer():

                state["audio_buffer"] = bytearray()
                state["speech_started"] = False
                state["silence_started"] = None

            # ====================================================
            # PCM RMS
            # ====================================================

            def pcm_rms(pcm_bytes):

                if len(pcm_bytes) < 2:
                    return 0.0

                usable_length = len(pcm_bytes)

                usable_length -= usable_length % 2

                if usable_length <= 0:
                    return 0.0

                sample_count = usable_length // 2

                try:

                    samples = struct.unpack(
                        f"<{sample_count}h",
                        pcm_bytes[:usable_length],
                    )

                except Exception:

                    return 0.0

                if not samples:
                    return 0.0

                energy = sum(
                    sample * sample
                    for sample in samples
                )

                return math.sqrt(
                    energy / len(samples)
                )

            # ====================================================
            # VAD
            # ====================================================

            def detect_speech(pcm_bytes):

                return pcm_rms(
                    pcm_bytes
                ) >= VAD_THRESHOLD

            # ====================================================
            # APPEND AUDIO
            # ====================================================

            def append_audio(payload):

                try:

                    decoded = base64.b64decode(
                        payload
                    )

                except Exception as error:

                    print(
                        "EXOTEL BASE64 ERROR:",
                        type(error).__name__,
                        str(error),
                    )

                    return False

                if not decoded:
                    return False

                # PCM16 complete samples only.
                if len(decoded) % 2:
                    decoded = decoded[:-1]

                if not decoded:
                    return False

                current_size = len(
                    state["audio_buffer"]
                )

                # ------------------------------------------------
                # MAX BUFFER
                # ------------------------------------------------

                if (
                    current_size + len(decoded)
                    > MAX_AUDIO_BYTES
                ):

                    print(
                        "MAX UTTERANCE LENGTH REACHED"
                    )

                    return True

                state["audio_buffer"].extend(
                    decoded
                )

                now = time.monotonic()

                # ------------------------------------------------
                # VAD
                # ------------------------------------------------

                if detect_speech(decoded):

                    state["speech_started"] = True
                    state["silence_started"] = None

                elif state["speech_started"]:

                    if (
                        state["silence_started"]
                        is None
                    ):

                        state[
                            "silence_started"
                        ] = now

                return True

            # ====================================================
            # PCM -> WAV
            # ====================================================

            def pcm_to_wav(
                pcm_bytes,
                sample_rate=SAMPLE_RATE,
            ):

                data_size = len(
                    pcm_bytes
                )

                byte_rate = (
                    sample_rate
                    * CHANNELS
                    * SAMPLE_WIDTH
                )

                block_align = (
                    CHANNELS
                    * SAMPLE_WIDTH
                )

                header = bytearray()

                header.extend(b"RIFF")

                header.extend(
                    (
                        36 + data_size
                    ).to_bytes(
                        4,
                        "little",
                    )
                )

                header.extend(b"WAVE")
                header.extend(b"fmt ")

                header.extend(
                    (16).to_bytes(
                        4,
                        "little",
                    )
                )

                # PCM
                header.extend(
                    (1).to_bytes(
                        2,
                        "little",
                    )
                )

                # Mono
                header.extend(
                    CHANNELS.to_bytes(
                        2,
                        "little",
                    )
                )

                # Sample rate
                header.extend(
                    sample_rate.to_bytes(
                        4,
                        "little",
                    )
                )

                # Byte rate
                header.extend(
                    byte_rate.to_bytes(
                        4,
                        "little",
                    )
                )

                # Block alignment
                header.extend(
                    block_align.to_bytes(
                        2,
                        "little",
                    )
                )

                # Bits/sample
                header.extend(
                    (16).to_bytes(
                        2,
                        "little",
                    )
                )

                header.extend(b"data")

                header.extend(
                    data_size.to_bytes(
                        4,
                        "little",
                    )
                )

                header.extend(
                    pcm_bytes
                )

                return bytes(header)

            # ====================================================
            # GROQ STT
            # ====================================================

            async def transcribe_audio(
                pcm_bytes,
            ):

                if (
                    len(pcm_bytes)
                    < MIN_AUDIO_BYTES
                ):

                    print(
                        "STT SKIPPED:",
                        len(pcm_bytes),
                        "bytes",
                    )

                    return None

                api_key = getattr(
                    self.env,
                    "GROQ_API_KEY",
                    None,
                )

                if not api_key:

                    print(
                        "STT ERROR: GROQ_API_KEY missing"
                    )

                    return None

                wav_bytes = pcm_to_wav(
                    pcm_bytes
                )

                print(
                    "STT REQUEST:",
                    len(pcm_bytes),
                    "PCM bytes",
                    round(
                        len(pcm_bytes)
                        / (
                            SAMPLE_RATE
                            * CHANNELS
                            * SAMPLE_WIDTH
                        ),
                        2,
                    ),
                    "seconds",
                )

                try:

                    import httpx

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

                    timeout = httpx.Timeout(
                        30.0
                    )

                    async with httpx.AsyncClient(
                        timeout=timeout
                    ) as client:

                        response = await client.post(
                            STT_URL,
                            headers={
                                "Authorization": (
                                    f"Bearer {api_key}"
                                )
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

                    result = response.json()

                    transcript = (
                        result.get(
                            "text",
                            "",
                        )
                        or ""
                    ).strip()

                    print(
                        "STT RESULT:",
                        repr(transcript),
                    )

                    return transcript

                except Exception as error:

                    print(
                        "STT REQUEST ERROR:",
                        type(error).__name__,
                        str(error),
                    )

                    return None

            # ====================================================
            # SEND AUDIO TO EXOTEL
            # ====================================================

            def send_audio_to_exotel(
                pcm_bytes,
            ):

                if not pcm_bytes:

                    print(
                        "EXOTEL TTS ERROR: empty audio"
                    )

                    return False

                if not state["connected"]:

                    print(
                        "EXOTEL TTS SKIPPED: socket closed"
                    )

                    return False

                # PCM16 alignment.
                if len(pcm_bytes) % 2:

                    pcm_bytes = pcm_bytes[:-1]

                if not pcm_bytes:
                    return False

                chunk_size = (
                    OUTBOUND_CHUNK_BYTES
                )

                total_bytes = len(
                    pcm_bytes
                )

                state["sending_audio"] = True

                try:

                    offset = 0
                    chunk_number = 0

                    while offset < total_bytes:

                        raw_end = min(
                            offset + chunk_size,
                            total_bytes,
                        )

                        chunk = pcm_bytes[
                            offset:raw_end
                        ]

                        # Exotel audio chunks should be
                        # divisible by 320 bytes.
                        remainder = (
                            len(chunk) % 320
                        )

                        if remainder:

                            padding = (
                                320 - remainder
                            )

                            chunk += (
                                b"\x00"
                                * padding
                            )

                        payload = (
                            base64.b64encode(
                                chunk
                            ).decode("ascii")
                        )

                        message = {
                            "event": "media",
                            "stream_sid": (
                                state[
                                    "stream_sid"
                                ]
                            ),
                            "media": {
                                "payload": payload
                            },
                        }

                        server.send(
                            json.dumps(message)
                        )

                        chunk_number += 1

                        # IMPORTANT:
                        # Advance by the ORIGINAL audio
                        # chunk, not padded length.
                        offset = raw_end

                    # ------------------------------------------------
                    # MARK
                    # ------------------------------------------------

                    state[
                        "outbound_sequence"
                    ] += 1

                    mark_name = (
                        "nextfit-tts-"
                        + str(
                            state[
                                "outbound_sequence"
                            ]
                        )
                    )

                    mark_message = {
                        "event": "mark",
                        "stream_sid": (
                            state[
                                "stream_sid"
                            ]
                        ),
                        "mark": {
                            "name": mark_name
                        },
                    }

                    server.send(
                        json.dumps(
                            mark_message
                        )
                    )

                    print(
                        "TTS AUDIO SENT:",
                        chunk_number,
                        "chunks",
                        "mark=",
                        mark_name,
                    )

                    return True

                except Exception as error:

                    print(
                        "EXOTEL OUTBOUND ERROR:",
                        type(error).__name__,
                        str(error),
                    )

                    return False

                finally:

                    state[
                        "sending_audio"
                    ] = False

            # ====================================================
            # PROCESS UTTERANCE
            # ====================================================

            async def process_utterance():

                if state["processing_audio"]:
                    return

                audio = bytes(
                    state["audio_buffer"]
                )

                reset_audio_buffer()

                if len(audio) < MIN_AUDIO_BYTES:

                    print(
                        "UTTERANCE SKIPPED:",
                        len(audio),
                        "bytes",
                    )

                    return

                state[
                    "processing_audio"
                ] = True

                state[
                    "utterance_number"
                ] += 1

                utterance_id = state[
                    "utterance_number"
                ]

                duration = (
                    len(audio)
                    / (
                        SAMPLE_RATE
                        * CHANNELS
                        * SAMPLE_WIDTH
                    )
                )

                print(
                    "=================================================="
                )

                print(
                    "PROCESSING UTTERANCE",
                    utterance_id,
                    "|",
                    round(duration, 2),
                    "seconds",
                )

                print(
                    "=================================================="
                )

                try:

                    # =================================================
                    # STT
                    # =================================================

                    transcript = (
                        await transcribe_audio(
                            audio
                        )
                    )

                    if not transcript:

                        print(
                            "NO TRANSCRIPT"
                        )

                        return

                    print(
                        "CALLER:",
                        transcript,
                    )

                    # =================================================
                    # CHAT
                    # =================================================

                    try:

                        from main import (
                            ChatRequest,
                            chat,
                        )

                        chat_response = (
                            await chat(
                                ChatRequest(
                                    message=transcript
                                )
                            )
                        )

                        response_text = (
                            chat_response.response
                        )

                        print(
                            "AI:",
                            response_text,
                        )

                    except Exception as error:

                        print(
                            "CHAT ERROR:",
                            type(error).__name__,
                            str(error),
                        )

                        return

                    if not response_text:

                        print(
                            "CHAT RETURNED EMPTY RESPONSE"
                        )

                        return

                    # =================================================
                    # ELEVENLABS TTS
                    # =================================================

                    try:

                        from main import (
                            elevenlabs_tts,
                        )

                        audio_response = (
                            await elevenlabs_tts(
                                response_text,
                                output_format=(
                                    "pcm_8000"
                                ),
                            )
                        )

                        print(
                            "TTS GENERATED:",
                            len(
                                audio_response
                            ),
                            "bytes",
                        )

                    except Exception as error:

                        print(
                            "TTS ERROR:",
                            type(error).__name__,
                            str(error),
                        )

                        return

                    # =================================================
                    # SEND VOICE
                    # =================================================

                    success = (
                        send_audio_to_exotel(
                            audio_response
                        )
                    )

                    if success:

                        print(
                            "AI RESPONSE SENT TO CALLER"
                        )

                except asyncio.CancelledError:

                    print(
                        "UTTERANCE CANCELLED:",
                        utterance_id,
                    )

                    raise

                except Exception as error:

                    print(
                        "UTTERANCE ERROR:",
                        type(error).__name__,
                        str(error),
                    )

                finally:

                    state[
                        "processing_audio"
                    ] = False

                    print(
                        "UTTERANCE COMPLETE:",
                        utterance_id,
                    )

            # ====================================================
            # PROCESSING TASK CALLBACK
            # ====================================================

            def processing_task_done(
                task,
            ):

                try:

                    if task.cancelled():
                        return

                    task.exception()

                except Exception:
                    pass

                if (
                    state.get(
                        "processing_task"
                    )
                    is task
                ):

                    state[
                        "processing_task"
                    ] = None

            # ====================================================
            # SCHEDULE PROCESSING
            # ====================================================

            def schedule_processing():

                if state[
                    "processing_audio"
                ]:

                    return

                existing = state.get(
                    "processing_task"
                )

                if existing is not None:

                    try:

                        if not existing.done():
                            return

                    except Exception:
                        pass

                    state[
                        "processing_task"
                    ] = None

                try:

                    task = asyncio.create_task(
                        process_utterance()
                    )

                    state[
                        "processing_task"
                    ] = task

                    task.add_done_callback(
                        processing_task_done
                    )

                except Exception as error:

                    print(
                        "CREATE PROCESS TASK ERROR:",
                        type(error).__name__,
                        str(error),
                    )

            # ====================================================
            # SILENCE MONITOR
            # ====================================================

            async def silence_monitor():

                print(
                    "SILENCE MONITOR STARTED"
                )

                try:

                    while (
                        state["connected"]
                        and state["started"]
                    ):

                        await asyncio.sleep(
                            0.05
                        )

                        if not state[
                            "speech_started"
                        ]:

                            continue

                        if state[
                            "processing_audio"
                        ]:

                            continue

                        silence_started = (
                            state[
                                "silence_started"
                            ]
                        )

                        if silence_started is None:
                            continue

                        silence_ms = (
                            time.monotonic()
                            - silence_started
                        ) * 1000

                        if (
                            silence_ms
                            >= SILENCE_MS
                        ):

                            buffer_size = len(
                                state[
                                    "audio_buffer"
                                ]
                            )

                            print(
                                "SILENCE DETECTED:",
                                round(silence_ms),
                                "ms |",
                                buffer_size,
                                "bytes",
                            )

                            if (
                                buffer_size
                                >= MIN_AUDIO_BYTES
                            ):

                                schedule_processing()

                            else:

                                reset_audio_buffer()

                except asyncio.CancelledError:

                    pass

                except Exception as error:

                    print(
                        "SILENCE MONITOR ERROR:",
                        type(error).__name__,
                        str(error),
                    )

                finally:

                    print(
                        "SILENCE MONITOR STOPPED"
                    )

            # ====================================================
            # START MONITOR
            # ====================================================

            def start_silence_monitor():

                existing = state.get(
                    "monitor_task"
                )

                if (
                    existing is not None
                    and not existing.done()
                ):

                    return

                try:

                    task = asyncio.create_task(
                        silence_monitor()
                    )

                    state[
                        "monitor_task"
                    ] = task

                except Exception as error:

                    print(
                        "CREATE MONITOR ERROR:",
                        type(error).__name__,
                        str(error),
                    )

            # ====================================================
            # STOP MONITOR
            # ====================================================

            def stop_silence_monitor():

                task = state.get(
                    "monitor_task"
                )

                if task is not None:

                    try:

                        if not task.done():
                            task.cancel()

                    except Exception:
                        pass

                state[
                    "monitor_task"
                ] = None

            # ====================================================
            # PROXY CLEANUP
            # ====================================================

            def cleanup_proxies():

                for key in (
                    "message_proxy",
                    "close_proxy",
                    "error_proxy",
                ):

                    proxy = state.get(key)

                    if proxy is None:
                        continue

                    try:
                        proxy.destroy()
                    except Exception:
                        pass

                    state[key] = None

            # ====================================================
            # MESSAGE HANDLER
            # ====================================================

            def on_message(event):

                try:

                    raw = event.data

                    if not isinstance(
                        raw,
                        str,
                    ):

                        return

                    message = json.loads(
                        raw
                    )

                    event_type = (
                        message.get("event")
                        or message.get("type")
                        or ""
                    ).lower()

                    # ------------------------------------------------
                    # CONNECTED
                    # ------------------------------------------------

                    if event_type == "connected":

                        state[
                            "connected"
                        ] = True

                        print(
                            "EXOTEL WEBSOCKET CONNECTED"
                        )

                        return

                    # ------------------------------------------------
                    # START
                    # ------------------------------------------------

                    if event_type == "start":

                        state[
                            "started"
                        ] = True

                        start = (
                            message.get(
                                "start"
                            )
                            or {}
                        )

                        state[
                            "stream_sid"
                        ] = (
                            message.get(
                                "stream_sid"
                            )
                            or start.get(
                                "stream_sid"
                            )
                            or start.get(
                                "streamSid"
                            )
                        )

                        state[
                            "call_sid"
                        ] = (
                            start.get(
                                "call_sid"
                            )
                            or start.get(
                                "callSid"
                            )
                        )

                        media_format = (
                            start.get(
                                "media_format"
                            )
                            or start.get(
                                "mediaFormat"
                            )
                            or {}
                        )

                        state[
                            "sample_rate"
                        ] = int(
                            media_format.get(
                                "sample_rate",
                                8000,
                            )
                        )

                        state[
                            "encoding"
                        ] = (
                            media_format.get(
                                "encoding",
                                "audio/x-l16",
                            )
                        )

                        reset_audio_buffer()

                        start_silence_monitor()

                        print(
                            "EXOTEL STREAM STARTED:",
                            "stream=",
                            state[
                                "stream_sid"
                            ],
                            "call=",
                            state[
                                "call_sid"
                            ],
                            "rate=",
                            state[
                                "sample_rate"
                            ],
                            "encoding=",
                            state[
                                "encoding"
                            ],
                        )

                        return

                    # ------------------------------------------------
                    # MEDIA
                    # ------------------------------------------------

                    if event_type == "media":

                        media = (
                            message.get(
                                "media"
                            )
                            or {}
                        )

                        payload = (
                            media.get(
                                "payload"
                            )
                        )

                        if payload:

                            append_audio(
                                payload
                            )

                        # IMPORTANT:
                        # NO per-packet logging here.
                        #
                        # Exotel can send hundreds/thousands
                        # of packets per call.
                        #
                        # Logging each one causes the
                        # 256KB Worker log limit to be hit.

                        return

                    # ------------------------------------------------
                    # DTMF
                    # ------------------------------------------------

                    if event_type == "dtmf":

                        print(
                            "EXOTEL DTMF RECEIVED"
                        )

                        return

                    # ------------------------------------------------
                    # MARK
                    # ------------------------------------------------

                    if event_type == "mark":

                        mark = (
                            message.get(
                                "mark"
                            )
                            or {}
                        )

                        print(
                            "EXOTEL MARK:",
                            mark.get(
                                "name"
                            ),
                        )

                        return

                    # ------------------------------------------------
                    # CLEAR
                    # ------------------------------------------------

                    if event_type == "clear":

                        print(
                            "EXOTEL CLEAR"
                        )

                        return

                    # ------------------------------------------------
                    # STOP
                    # ------------------------------------------------

                    if event_type == "stop":

                        print(
                            "EXOTEL STREAM STOPPED"
                        )

                        state[
                            "started"
                        ] = False

                        stop_silence_monitor()

                        buffer_size = len(
                            state[
                                "audio_buffer"
                            ]
                        )

                        if (
                            buffer_size
                            >= MIN_AUDIO_BYTES
                        ):

                            print(
                                "FINAL BUFFER:",
                                buffer_size,
                                "bytes",
                            )

                            schedule_processing()

                        return

                except Exception as error:

                    print(
                        "EXOTEL MESSAGE ERROR:",
                        type(error).__name__,
                        str(error),
                    )

            # ====================================================
            # CLOSE
            # ====================================================

            def on_close(event):

                print(
                    "EXOTEL WEBSOCKET CLOSED"
                )

                state[
                    "connected"
                ] = False

                state[
                    "started"
                ] = False

                stop_silence_monitor()

                task = state.get(
                    "processing_task"
                )

                if (
                    task is not None
                    and not task.done()
                ):

                    try:
                        task.cancel()
                    except Exception:
                        pass

                state[
                    "processing_task"
                ] = None

                reset_audio_buffer()

                # Give the callback time to return
                # before destroying its own proxy.
                async def deferred_cleanup():

                    try:

                        await asyncio.sleep(
                            0
                        )

                    except Exception:
                        pass

                    cleanup_proxies()

                try:

                    asyncio.create_task(
                        deferred_cleanup()
                    )

                except Exception:

                    cleanup_proxies()

            # ====================================================
            # ERROR
            # ====================================================

            def on_error(event):

                print(
                    "EXOTEL WEBSOCKET ERROR"
                )

            # ====================================================
            # PERSISTENT PROXIES
            # ====================================================

            state[
                "message_proxy"
            ] = create_proxy(
                on_message
            )

            state[
                "close_proxy"
            ] = create_proxy(
                on_close
            )

            state[
                "error_proxy"
            ] = create_proxy(
                on_error
            )

            # ====================================================
            # EVENT LISTENERS
            # ====================================================

            server.addEventListener(
                "message",
                state[
                    "message_proxy"
                ],
            )

            server.addEventListener(
                "close",
                state[
                    "close_proxy"
                ],
            )

            server.addEventListener(
                "error",
                state[
                    "error_proxy"
                ],
            )

            print(
                "EXOTEL WEBSOCKET READY"
            )

            # ====================================================
            # RETURN WEBSOCKET
            # ====================================================

            return JSResponse.new(
                None,
                {
                    "status": 101,
                    "webSocket": client,
                },
            )

        # ========================================================
        # EVERYTHING ELSE -> FASTAPI
        # ========================================================

        return await asgi.fetch(
            app,
            request,
            self.env,
        )