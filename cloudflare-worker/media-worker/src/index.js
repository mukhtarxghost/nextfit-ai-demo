const SAMPLE_RATE = 8000;
const CHANNELS = 1;
const SAMPLE_WIDTH = 2;
const SILENCE_MS = 750;
const MIN_AUDIO_BYTES = 8000;
const MAX_AUDIO_BYTES = 8000 * 2 * 30;
const VAD_THRESHOLD = 500;
const VAD_CHECK_INTERVAL_PACKETS = 5;
const OUTBOUND_CHUNK_BYTES = 6400;

function emptyConversationState() {
  return {
    messages: [],
    lead: {
      name: null,
      phone_number: null,
      intent: null,
      goal: null,
      current_situation: null,
      problem: null,
      previous_attempts: null,
      desired_outcome: null,
      experience: "unknown",
      location: null,
      timeline: "unknown",
      training_preference: "unknown",
      availability: null,
      engagement: 0,
      program_fit: 0,
      goal_clarity: 0,
      next_step_intent: "unknown",
      needs_human: false,
    },
    conversation_complete: false,
    handoff_required: false,
    turn_count: 0,
    conversation_phase: "greeting",
    active_intent: "unknown",
    previous_intent: null,
    pending_topic: null,
    last_ai_response: null,
    last_user_answer: null,
    last_question_asked: null,
    corrections: [],
    conversation_summary: null,
    clarification_requested: false,
    consecutive_clarifications: 0,
  };
}

function decodeBase64(payload) {
  const binary = atob(payload);
  const decoded = new Uint8Array(binary.length);

  for (let index = 0; index < binary.length; index += 1) {
    decoded[index] = binary.charCodeAt(index);
  }

  if (decoded.byteLength % 2 === 0) {
    return decoded;
  }

  return decoded.slice(0, decoded.byteLength - 1);
}

function pcmRms(pcm) {
  if (pcm.byteLength < 2) {
    return 0;
  }

  const sampleCount = Math.floor(pcm.byteLength / 2);
  const view = new DataView(
    pcm.buffer,
    pcm.byteOffset,
    sampleCount * 2,
  );
  let energy = 0;

  for (let index = 0; index < sampleCount; index += 1) {
    const sample = view.getInt16(index * 2, true);
    energy += sample * sample;
  }

  return Math.sqrt(energy / sampleCount);
}

function encodeBase64(bytes) {
  let binary = "";

  for (let index = 0; index < bytes.length; index += 1) {
    binary += String.fromCharCode(bytes[index]);
  }

  return btoa(binary);
}

function asUint8Array(value) {
  if (value instanceof Uint8Array) {
    return value;
  }

  if (value instanceof ArrayBuffer) {
    return new Uint8Array(value);
  }

  if (Array.isArray(value)) {
    return Uint8Array.from(value);
  }

  if (value && value.buffer instanceof ArrayBuffer) {
    return new Uint8Array(
      value.buffer,
      value.byteOffset || 0,
      value.byteLength,
    );
  }

  throw new TypeError("RPC audio must be binary data");
}

function concatAudio(chunks, totalBytes) {
  const audio = new Uint8Array(totalBytes);
  let offset = 0;

  for (const chunk of chunks) {
    audio.set(chunk, offset);
    offset += chunk.byteLength;
  }

  return audio;
}

function sendAudioToExotel(server, state, audio) {
  if (!audio.byteLength || !state.connected) {
    return false;
  }

  const alignedAudio = audio.byteLength % 2 === 0
    ? audio
    : audio.slice(0, audio.byteLength - 1);
  let offset = 0;
  let chunkNumber = 0;

  while (offset < alignedAudio.byteLength) {
    const rawEnd = Math.min(
      offset + OUTBOUND_CHUNK_BYTES,
      alignedAudio.byteLength,
    );
    let chunk = alignedAudio.slice(offset, rawEnd);
    const remainder = chunk.byteLength % 320;

    if (remainder) {
      const padded = new Uint8Array(
        chunk.byteLength + (320 - remainder),
      );
      padded.set(chunk);
      chunk = padded;
    }

    server.send(JSON.stringify({
      event: "media",
      stream_sid: state.streamSid,
      media: {
        payload: encodeBase64(chunk),
      },
    }));

    chunkNumber += 1;
    offset = rawEnd;
  }

  state.outboundSequence += 1;
  const markName = `nextfit-tts-${state.outboundSequence}`;
  state.pendingMark = markName;
  resetAudioBuffer(state);

  server.send(JSON.stringify({
    event: "mark",
    stream_sid: state.streamSid,
    mark: {
      name: markName,
    },
  }));

  console.log("TTS AUDIO SENT:", chunkNumber, "chunks", "mark=", markName);
  return true;
}

function createConnectionState() {
  return {
    connected: false,
    started: false,
    streamSid: null,
    callSid: null,
    callerPhone: null,
    audioChunks: [],
    audioBytes: 0,
    speechStarted: false,
    silenceStartedAt: null,
    silenceTimer: null,
    silenceGeneration: 0,
    vadPacketsSinceCheck: 0,
    processing: false,
    conversationState: emptyConversationState(),
    outboundSequence: 0,
    pendingMark: null,
  };
}

function cancelSilenceTimer(state) {
  state.silenceGeneration += 1;

  if (state.silenceTimer !== null) {
    clearTimeout(state.silenceTimer);
    state.silenceTimer = null;
  }
}

function resetAudioBuffer(state) {
  cancelSilenceTimer(state);
  state.audioChunks = [];
  state.audioBytes = 0;
  state.speechStarted = false;
  state.silenceStartedAt = null;
  state.vadPacketsSinceCheck = 0;
}

function scheduleSilenceTimer(state, processUtterance) {
  if (state.silenceTimer !== null) {
    return;
  }

  const generation = state.silenceGeneration;
  const silenceStartedAt = state.silenceStartedAt;

  state.silenceTimer = setTimeout(async () => {
    state.silenceTimer = null;

    if (
      generation !== state.silenceGeneration
      || silenceStartedAt !== state.silenceStartedAt
      || !state.connected
      || !state.started
      || !state.speechStarted
      || state.processing
    ) {
      return;
    }

    const silenceMs = performance.now() - silenceStartedAt;

    if (silenceMs < SILENCE_MS) {
      scheduleSilenceTimer(state, processUtterance);
      return;
    }

    if (state.audioBytes >= MIN_AUDIO_BYTES) {
      await processUtterance();
    } else {
      resetAudioBuffer(state);
    }
  }, SILENCE_MS);
}

function appendAudio(state, payload, processUtterance) {
  if (state.pendingMark) {
    return;
  }

  const decoded = decodeBase64(payload);

  if (!decoded.byteLength) {
    return;
  }

  if (state.audioBytes + decoded.byteLength > MAX_AUDIO_BYTES) {
    console.log("MAX UTTERANCE LENGTH REACHED");
    return;
  }

  state.audioChunks.push(decoded);
  state.audioBytes += decoded.byteLength;
  state.vadPacketsSinceCheck += 1;

  if (state.vadPacketsSinceCheck < VAD_CHECK_INTERVAL_PACKETS) {
    return;
  }

  state.vadPacketsSinceCheck = 0;

  if (pcmRms(decoded) >= VAD_THRESHOLD) {
    cancelSilenceTimer(state);
    state.speechStarted = true;
    state.silenceStartedAt = null;
  } else if (state.speechStarted && state.silenceStartedAt === null) {
    state.silenceStartedAt = performance.now();
    scheduleSilenceTimer(state, processUtterance);
  }
}

function installMediaSocket(server, env) {
  const state = createConnectionState();

  const processUtterance = async () => {
    if (state.processing || state.audioBytes < MIN_AUDIO_BYTES) {
      return;
    }

    cancelSilenceTimer(state);
    state.processing = true;
    const pcm = concatAudio(state.audioChunks, state.audioBytes);
    resetAudioBuffer(state);

    try {
      const result = await env.PYTHON_AI.process_utterance({
        callSid: state.callSid,
        callerPhone: state.callerPhone,
        pcm: pcm.buffer,
        conversationState: state.conversationState,
        isFirstUtterance: state.outboundSequence === 0,
      });

      state.conversationState = result.conversationState;
      const audio = asUint8Array(result.audio);

      if (audio.byteLength) {
        sendAudioToExotel(server, state, audio);
      }
    } catch (error) {
      console.log("PYTHON RPC ERROR:", error?.name, error?.message);
    } finally {
      state.processing = false;

      // Media can arrive while the utterance RPC is in flight.  If that
      // buffered audio has already entered silence, arm the same one-shot
      // timer for the next utterance instead of leaving it stranded.
      if (
        state.connected
        && state.started
        && state.audioBytes >= MIN_AUDIO_BYTES
        && state.speechStarted
        && state.silenceStartedAt !== null
      ) {
        scheduleSilenceTimer(state, processUtterance);
      }
    }
  };

  server.addEventListener("message", async (event) => {
    try {
      if (typeof event.data !== "string") {
        return;
      }

      const message = JSON.parse(event.data);
      const eventType = String(
        message.event || message.type || "",
      ).toLowerCase();

      if (eventType === "connected") {
        state.connected = true;
        console.log("EXOTEL WEBSOCKET CONNECTED");
        return;
      }

      if (eventType === "start") {
        state.connected = true;
        state.started = true;
        const start = message.start || {};
        state.streamSid = (
          message.stream_sid
          || start.stream_sid
          || start.streamSid
          || null
        );
        state.callSid = start.call_sid || start.callSid || null;
        state.callerPhone = (
          start.caller_number
          || start.callerNumber
          || start.From
          || start.from
          || start.customField
          || null
        );
        resetAudioBuffer(state);
        console.log(
          "EXOTEL STREAM STARTED:",
          "stream=",
          state.streamSid,
          "call=",
          state.callSid,
          "phone=",
          state.callerPhone,
        );
        return;
      }

      if (eventType === "media") {
        const payload = message.media?.payload;

        if (payload) {
          appendAudio(state, payload, processUtterance);
        }

        return;
      }

      if (eventType === "dtmf") {
        console.log("EXOTEL DTMF RECEIVED");
        return;
      }

      if (eventType === "mark") {
        const markName = message.mark?.name;
        console.log("EXOTEL MARK:", markName);
        if (markName && markName === state.pendingMark) {
          state.pendingMark = null;
          resetAudioBuffer(state);
        }
        return;
      }

      if (eventType === "clear") {
        console.log("EXOTEL CLEAR");
        return;
      }

      if (eventType === "stop") {
        console.log("EXOTEL STREAM STOPPED");
        state.started = false;
        cancelSilenceTimer(state);

        if (state.audioBytes >= MIN_AUDIO_BYTES) {
          void processUtterance();
        }

        // Finalize the call session in the Python Worker
        if (state.callSid) {
          try {
            await env.PYTHON_AI.end_call({
              callSid: state.callSid,
            });
          } catch (endErr) {
            console.log("END_CALL ERROR:", endErr?.name, endErr?.message);
          }
        }
      }
    } catch (error) {
      console.log("EXOTEL MESSAGE ERROR:", error?.name, error?.message);
    }
  });

  server.addEventListener("close", () => {
    state.connected = false;
    state.started = false;
    cancelSilenceTimer(state);
    resetAudioBuffer(state);
    console.log("EXOTEL WEBSOCKET CLOSED");
  });

  server.addEventListener("error", () => {
    console.log("EXOTEL WEBSOCKET ERROR");
  });

  console.log("EXOTEL WEBSOCKET READY");
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);

    if (url.pathname === "/media") {
      if (request.headers.get("Upgrade")?.toLowerCase() !== "websocket") {
        return new Response(
          "WebSocket endpoint. Upgrade required.",
          { status: 426 },
        );
      }

      const pair = new WebSocketPair();
      const client = pair[0];
      const server = pair[1];
      server.accept();
      installMediaSocket(server, env);

      return new Response(null, {
        status: 101,
        webSocket: client,
      });
    }

    return env.PYTHON_AI.fetch(request);
  },
};
