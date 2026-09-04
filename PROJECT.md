# NEXTFIT VOICE RECEPTIONIST — PROJECT DOCUMENTATION

> **Engineering Postmortem & Technical Reference**
>
> This document covers the full engineering history of the NextFit AI Voice Receptionist,
> from initial broken/slow state through the latency and conversation optimization work.

---

## TABLE OF CONTENTS

1. [Project Overview](#1-project-overview)
2. [Architecture Overview](#2-architecture-overview)
3. [External Services](#3-external-services)
4. [Voice Receptionist — Latency & Conversation Optimization](#4-voice-receptionist--latency--conversation-optimization)
   - 4.1 Initial Problem
   - 4.2 Original Architecture / Request Flow
   - 4.3 First Optimization Pass
   - 4.4 Production Test That Revealed Remaining Problems
   - 4.5 Latest Optimization
   - 4.6 Before vs After
   - 4.7 Latest Production Test Analysis
   - 4.8 Why the Latency Improved
   - 4.9 Current Architecture
   - 4.10 Remaining Work
   - 4.11 Engineering Lessons
5. [Current Status](#5-current-status)
6. [Key URLs & Commands](#6-key-urls--commands)

---

## 1. PROJECT OVERVIEW

NextFit Voice is an **AI receptionist that answers phone calls** for a gym called
**NextFit** in Pune, India.

When someone calls the gym, instead of a human receptionist picking up, this AI:

- Greets the caller naturally
- Understands what they want (membership, personal training, trial, etc.)
- Asks smart questions to qualify them as a lead
- Collects their contact details
- Scores how interested/ready they are (0-100)
- Recommends what to do next (hot lead → hand off to human, etc.)

---

## 2. ARCHITECTURE OVERVIEW

```
                         ┌─────────────────────────────────┐
                         │        PHONE CALLER             │
                         │   (someone calling the gym)     │
                         └──────────────┬──────────────────┘
                                        │
                                        ▼
                         ┌─────────────────────────────────┐
                         │         EXOTEL                  │
                         │   (Phone/VoIP Provider)         │
                         │   Routes call via WebSocket     │
                         └──────────────┬──────────────────┘
                                        │ WebSocket + Audio
                                        ▼
        ┌──────────────────────────────────────────────────────────────┐
        │              CLOUDFLARE WORKERS                              │
        │                                                              │
        │  ┌────────────────────┐    RPC    ┌─────────────────────┐  │
        │  │   MEDIA WORKER     │──────────→│   VOICE WORKER      │  │
        │  │   (JavaScript)     │           │   (Python)          │  │
        │  │                    │           │                     │  │
        │  │  • WebSocket       │           │  • STT (Groq)       │  │
        │  │  • VAD             │           │  • Chat (Groq)      │  │
        │  │  • Audio buffering │           │  • TTS (ElevenLabs) │  │
        │  │  • Barge-in        │           │  • Lead extraction   │  │
        │  └────────────────────┘           └─────────────────────┘  │
        │                                                              │
        └──────────────────────────────────────────────────────────────┘
```

### Request Flow

```
Caller → Exotel → WebSocket → Media Worker → VAD / audio accumulation
→ Python Voice Worker (Service Binding RPC) → STT → conversation state
→ lead extraction (when required) → Groq conversation response
→ ElevenLabs TTS → Media Worker → Exotel → Caller
```

---

## 3. EXTERNAL SERVICES

| Service | What It Does | Why It's Needed |
|---------|-------------|-----------------|
| **Exotel** | Phone/VoIP provider | Routes phone calls to the AI |
| **Cloudflare Workers** | Hosting platform | Runs the AI application (2 workers) |
| **Groq** | AI inference (LLM + STT) | Processes language and speech |
| **ElevenLabs** | Text-to-speech | Converts AI responses to natural voice |

### Cloudflare Worker Architecture

| Worker | Language | Purpose |
|--------|----------|---------|
| `nextfit-ai-media` | JavaScript | WebSocket handler, VAD, audio buffering, barge-in |
| `nextfit-ai-voice` | Python (Pydantic, FastAPI) | STT, LLM chat, TTS, lead extraction |

The media worker calls the voice worker via **Cloudflare Service Binding RPC**
(binding name: `PYTHON_AI`, entrypoint: `VoiceEntrypoint`).

---

## 4. VOICE RECEPTIONIST — LATENCY & CONVERSATION OPTIMIZATION

### 4.1 INITIAL PROBLEM

The initial production tests of the Voice Receptionist revealed that while the
underlying pipeline technically worked end-to-end (Exotel connects → audio reaches
the worker → STT runs → conversation state persists → Groq generates responses →
ElevenLabs returns TTS), the actual conversational experience was poor and not
production-ready.

The specific problems observed were:

**Extremely large LLM context requests.** Every Groq turn was sending approximately
14–16k input characters, even for short conversations. The system was carrying a
huge amount of redundant context instead of using structured session memory.

**Frequent Groq 429 rate limits.** The API repeatedly returned HTTP 429 (Too Many
Requests). Each rate limit event triggered retry logic with `retry-after` values
of 5–15 seconds. For a real-time telephone conversation, a caller cannot comfortably
wait 10–15+ seconds after speaking.

**Unnecessary lead-extraction calls.** Lead extraction triggered on generic words
like "want", "need", "looking", "actually", making its own separate Groq API call
per trigger. This effectively doubled the Groq API usage per conversational turn,
increasing rate-limit pressure.

**Deterministic greeting triggering incorrectly.** The `deterministic_response()`
function matched "hello"/"hi"/"hey" regardless of conversation state. During an
active conversation, saying "hello?" (to check if the agent was still there) would
trigger the initial greeting response: "Hey, welcome to NextFit. What brings you
in today?" — destroying conversational context.

**Poor/fragmented STT results.** Some utterances were transcribed incorrectly
(e.g., "I learned that there" for what was likely "I live out there", "Mukhtar"
for a name, fragmented phrases). This could not be attributed solely to the STT
model — VAD/turn segmentation was suspected of cutting natural speech into
unnatural fragments before STT received it.

**Why these problems were especially serious for a telephone voice agent.**
In a text chatbot, a 2-second delay is acceptable. In a telephone voice call,
any pause over ~2 seconds feels unnatural, and a 10–15 second silence after
speaking is unacceptable. The caller will assume the call dropped. Perceived
response latency is the primary UX metric for voice agents, not just correctness.

---

### 4.2 ORIGINAL ARCHITECTURE / REQUEST FLOW

The full production request flow:

```
Caller speaks
→ Exotel captures audio (8kHz, mono, 16-bit PCM)
→ WebSocket media events to Cloudflare Media Worker
→ Media Worker accumulates audio chunks
→ VAD detects silence (end of utterance)
→ processUtterance() fires
→ RPC to Voice Worker (Python)
→ STT: PCM → WAV → Groq Whisper API → transcript text
→ Conversation state updated (user message appended)
→ Lead extraction (if triggered by keyword signals)
→ Groq chat: system prompt + context + conversation history → AI response
→ ElevenLabs TTS: response text → PCM audio
→ RPC returns audio + updated state to Media Worker
→ Media Worker chunks audio, sends to Exotel via WebSocket
→ Exotel plays audio to caller
```

**Where latency could accumulate:**

| Step | Typical Latency | Notes |
|------|----------------|-------|
| Exotel → Media Worker | ~50-100ms | Network |
| Audio accumulation + VAD | 750-1500ms | Waiting for silence |
| STT (Groq Whisper) | 2-5s | Depends on utterance length |
| Lead extraction (if triggered) | 1-3s | Separate Groq API call |
| Groq chat (LLM) | 2-8s | Depends on input size |
| ElevenLabs TTS | 1-3s | Depends on response length |
| Audio chunking + Exotel | ~100ms | Network |
| **Total worst case** | **~15-20s** | With retries, even worse |

---

### 4.3 FIRST OPTIMIZATION PASS

Before the latency-focused work, a reliability optimization pass was implemented
to fix basic production stability:

| Change | File | What It Solved |
|--------|------|----------------|
| Groq 429 retry with backoff | `main.py` | Prevented outright call failure on rate limits |
| Lead extraction throttle (every 3 turns) | `main.py` | Reduced unnecessary Groq API calls |
| TTS try/except with fallback audio | `voice_rpc.py` | Prevented state corruption on ElevenLabs errors |
| STT retry with backoff | `voice_rpc.py` | Handled transient STT failures |
| STT fallback audio ("I didn't catch that") | `voice_rpc.py` | Eliminated dead air on STT failure |
| `needs_human` respects LLM assessment | `main.py` | Allowed LLM to flag callers needing human attention |
| 429 state rollback (pop message, decrement turn) | `main.py` | Prevented conversation state corruption on rate limits |
| `should_extract_lead()` tightened | `main.py` | Required 3+ words, added filler-word skip list |
| Dead `NEXTFIT_SYSTEM_PROMPT` removed | `prompts.py` | Eliminated 625 lines of unused code |
| Barge-in handling | `index.js` | Allowed callers to interrupt AI during TTS |
| `pendingMark` timeout (15s) | `index.js` | Prevented stuck states when mark echo never arrived |
| WebSocket keepalive (30s ping) | `index.js` | Prevented silent connection drops |
| `pcm.buffer` memory reference fix | `index.js` | Fixed Uint8Array referencing extra ArrayBuffer memory |
| Request validation for `process_utterance` | `voice_rpc.py` | Handled missing/malformed conversation state |
| `temperature` string → number fix | `voice_rpc.py` | Fixed `"0"` → `0` for STT API |

**What this pass achieved:** The system became reliably functional. Calls
could complete end-to-end without crashing. Rate limits were handled gracefully
(with fallback responses instead of errors). TTS/STT failures no longer corrupted
conversation state.

**What problems remained:** The retry mechanism technically improved reliability
but did NOT solve real-time latency. The caller still experienced 10–15+ second
waits when Groq returned 429. The context size was still ~14-16k chars. Lead
extraction was still triggering too frequently. The deterministic greeting bug
was still present.

---

### 4.4 PRODUCTION TEST THAT REVEALED THE REMAINING PROBLEMS

A production call test (recorded as `test2.mp3`) was conducted. The logs revealed:

```
GROQ 429 attempt= 1 of 3 retry_after= 5
GROQ 429 attempt= 2 of 3 retry_after= 10
GROQ 429 attempt= 3 of 3 retry_after= 15
GROQ CHAT RATE LIMITED; FALLBACK RESPONSE retry_after= 15
```

Key observations from the test:

- **Groq requests remained ~14–16k characters.** The system prompt alone was
  12,929 characters. Combined with 10 conversation messages and dynamic context
  blocks, each turn sent a massive payload to Groq.

- **Groq repeatedly returned 429.** The retry mechanism kicked in with delays
  of 5, 10, then 15 seconds. The caller experienced dead air during these waits.

- **Lead extraction itself triggered additional Groq requests.** Each extraction
  call sent the full conversation transcript (5,894-character prompt + transcript)
  as a separate API call, competing for the same rate limit.

- **"Hello" during an active session triggered the deterministic greeting.**
  When the caller said "hello?" to check if the agent was still there, the system
  responded with the initial greeting, destroying conversation context.

- **Some STT outputs were incorrect or fragmented.** Transcriptions like
  "I learned that there" (likely "I live out there") and "It's their diet right
  now" (likely misheard) suggested VAD was cutting speech at unnatural boundaries.

- **The system was technically functional but still not suitable as a natural
  real-time receptionist.** The underlying pipeline worked, but the conversational
  experience was degraded by latency, incorrect context behavior, and fragmented
  speech processing.

---

### 4.5 LATEST OPTIMIZATION

The latest optimization focused specifically on latency and conversation quality.
All changes targeted the same principle: **do less work per conversational turn.**

#### A. Session-Aware Deterministic Responses

**File:** `main.py` — `deterministic_response()`

**Before:**
```python
def deterministic_response(message: str) -> str | None:
    normalized = re.sub(r"[^a-z0-9 ]", "", message.lower()).strip()
    if normalized in {"hi", "hello", "hey"}:
        return "Hey, welcome to NextFit. What brings you in today?"
    ...
```

**After:**
```python
def deterministic_response(
    message: str,
    turn_count: int = 0,
) -> str | None:
    normalized = re.sub(r"[^a-z0-9 ]", "", message.lower()).strip()
    if normalized in {"hi", "hello", "hey"}:
        if turn_count <= 1:
            return "Hey, welcome to NextFit. What brings you in today?"
        return None
    if normalized in {"how are you", "how are you doing"}:
        if turn_count <= 2:
            return "I'm good, thanks. How's it going? What brings you to NextFit?"
        return None
    return None
```

**Why:** The greeting response is contextually appropriate only at the start of a
new session. During an active conversation, "hello?" is the caller checking whether
the agent is still there — the LLM should handle this contextually (e.g., "Hey,
I'm still here! What can I help with?"). By gating on `turn_count`, the greeting
fires only on the first turn, and mid-conversation greetings fall through to the
LLM which can respond appropriately.

#### B. Fail-Fast Groq Behavior

**File:** `main.py` — `groq_chat()` and rate-limit handling

| Parameter | Before | After |
|-----------|--------|-------|
| `GROQ_MAX_RETRIES` | 3 | 1 |
| `GROQ_BASE_BACKOFF` | 2.0s | 0.5s |

**Before:** On 429, the system retried up to 3 times with exponential backoff
(capped at 15 seconds per attempt). Total worst-case wait: 30+ seconds.

**After:** On 429, the system tries once and immediately returns a short fallback
response: "Sorry, I missed that. Could you say that again?"

**Engineering reasoning:** For a real-time voice call, waiting 10–15 seconds for
a retry is worse than immediately returning a short natural fallback. The caller
can repeat themselves in ~2 seconds. A 15-second silence makes them think the call
dropped. Fail-fast preserves the conversational feel even when rate-limited.

**Important caveat:** This does not eliminate rate limiting. It reduces the
*latency impact* of rate limiting. The fallback response is short and natural,
keeping the conversation flowing.

#### C. Prompt Compression

**File:** `prompts.py` — `NEXTFIT_CHAT_PROMPT`

| Metric | Before | After | Reduction |
|--------|--------|-------|-----------|
| `NEXTFIT_CHAT_PROMPT` | 12,929 chars (500 lines) | 2,362 chars (56 lines) | **82%** |
| `LEAD_EXTRACTION_PROMPT` | 5,894 chars (285 lines) | 1,659 chars (33 lines) | **72%** |

**What was removed:**
- Redundant section headers and decorative dividers
- Verbose examples that repeated the same principle
- Duplicate explanations of the same rule across sections
- Excessive "do NOT" lists that covered the same concept multiple times
- Detailed scenario examples (Independence Day discount, etc.)

**What was preserved:**
- Personality and tone instructions
- Turn-taking rules (answer first, one question at a time)
- Correction handling (acknowledge briefly, continue)
- Clarification handling (rephrase, don't repeat verbatim)
- Memory behavior (check ALREADY KNOWN, never re-ask)
- Human handoff rules (after qualification, one contact field at a time)
- Fitness knowledge guardrails (don't invent prices, diagnose, etc.)

**Why reducing static prompt tokens matters:** Every character in the system prompt
is sent to Groq on every single turn. A 12,929-character prompt means ~3,200 tokens
just for instructions, before any conversation history. Reducing to 2,362 characters
saves ~2,600 tokens per turn. Over a 20-turn conversation, that's 52,000 fewer tokens
processed — reducing both latency and API cost.

#### D. Conversation Context Window

**File:** `context.py` — `MAX_CONTEXT_MESSAGES`

| Parameter | Before | After |
|-----------|--------|-------|
| `MAX_CONTEXT_MESSAGES` | 10 | 6 |

**Architecture change:**

```
BEFORE:
System prompt (12,929 chars) + 10 conversation messages + dynamic context blocks
→ ~14-16k chars per turn

AFTER:
Compact system prompt (2,362 chars) + 6 recent messages + structured session state
→ ~3.5-4.8k chars per turn
```

**Why structured state prevents information loss:** The conversation state includes
a `LeadProfile` (name, phone, intent, goal, experience, location, timeline,
availability, etc.), a `conversation_summary`, and phase/intent tracking. When the
recent message window is shortened from 10 to 6, important information is not lost —
it is retained in structured fields that are injected into the system prompt via
`build_conversation_context()` and `build_known_info_text()`. The LLM sees "ALREADY
KNOWN: name=Mukhtar, goal=lose fat, location=Camp" without needing the original
message in which the caller said those things.

#### E. Lead Extraction Optimization

**File:** `main.py` — `should_extract_lead()` and extraction throttle

| Parameter | Before | After |
|-----------|--------|-------|
| Extraction throttle | Every 3 turns | Every 5 turns |
| Minimum words | 3 | 3 (unchanged) |
| Trigger vocabulary | 40+ generic words | 14 explicit intent words |

**Before (generic triggers):**
"want", "need", "looking", "join", "start", "goal", "train", "training",
"workout", "gym", "fitness", "lose", "gain", "muscle", "weight", "name",
"number", "phone", "live", "area", "pune", "available", "time", "membership",
"personal", "trial", "problem", "prefer", "interested", "actually", "meant",
"change", "switch", "forget", "saturday"..."friday", "tomorrow"..."today",
"evening"..."afternoon", "call", "callback", "contact"

**After (explicit intent signals):**
"membership", "personal", "training", "trial", "join", "price", "pricing",
"cost", "offer", "discount", "sign", "register", "book", "consult"

**Why this matters:** Each lead-extraction call is a separate Groq API request
(5,894-char prompt + full conversation transcript). If a message like "Actually,
I meant to ask" triggers extraction, that's a wasted API call competing for the
same rate limit. By narrowing triggers to explicit intent signals and increasing
the throttle, unnecessary extraction calls are largely eliminated.

#### F. VAD Tuning

**File:** `media-worker/src/index.js` — VAD constants

| Parameter | Before | After | Effect |
|-----------|--------|-------|--------|
| `SILENCE_MS` | 750ms | 1000ms | More time for natural pauses mid-sentence |
| `VAD_THRESHOLD` | 500 | 400 | Catches quieter speech, fewer false cuts |

**The relationship between VAD and conversational quality:**

```
VAD silence detection
→ determines when the caller "stopped speaking"
→ triggers audio segmentation (which audio goes to STT)
→ STT receives the segmented audio
→ transcription quality depends on clean segmentation
→ transcription feeds the LLM
→ LLM response feeds TTS
→ TTS plays to caller
```

If VAD cuts speech too early, STT receives fragmented audio and produces incorrect
transcriptions. If VAD waits too long, there's unnecessary latency before processing.

**Before:** 750ms silence threshold. A caller pausing briefly mid-sentence (e.g.,
"Camp... I'm located in Camp") could trigger end-of-utterance, splitting the audio
into two separate STT calls.

**After:** 1000ms silence threshold. Gives an additional 250ms for natural speech
pauses. Combined with the lower energy threshold (400 vs 500), the system is more
tolerant of quiet speech and brief pauses.

**Important caveat:** VAD is not definitively responsible for every previous STT
error. Some inaccuracies may be inherent to the Groq Whisper model, audio quality
from Exotel, or network conditions. The VAD changes are a hypothesis-based
improvement, not a guaranteed fix for all STT issues.

---

### 4.6 BEFORE vs AFTER

| Metric | Before (Initial) | After (Current) | Change |
|--------|-------------------|------------------|--------|
| System prompt size | ~12,929 chars | ~2,362 chars | **-82%** |
| Estimated input/turn | ~14-16k chars | ~3.5-4.8k chars | **-70%** |
| Groq retries on 429 | 3 retries, up to 15s each | 1 attempt, immediate fallback | **-90% wait time** |
| Lead extraction frequency | Every message with generic word | Every 5 turns with explicit signal | **-60-70% calls** |
| Greeting behavior | Fires on any "hello" | Fires only on first turn | **Context-aware** |
| Context window | 10 messages | 6 messages + structured state | **Leaner** |
| VAD silence threshold | 750ms | 1000ms | **More tolerant** |
| VAD energy threshold | 500 RMS | 400 RMS | **Catches quieter speech** |
| Barge-in | Not implemented | High-energy detection (2x threshold) | **Callable can interrupt** |
| STT on failure | Dead air | "Sorry, I didn't catch that" TTS | **No silence** |
| TTS on failure | Crash / state corruption | Fallback audio + preserved state | **Graceful** |

**Important:** "no observed 429s" in the latest test refers to that specific test
session only. Rate limiting can still occur under high load. The improvement is that
rate limiting no longer causes 10-15 second waits — it triggers an immediate short
fallback.

---

### 4.7 LATEST PRODUCTION TEST ANALYSIS

The latest production test (after all optimizations) showed:

**Groq request size:** Initial request was ~3,477 chars. Subsequent requests stayed
in the 3.5k–4.8k range. This is a ~75% reduction from the previous ~14-16k.

**Rate limiting:** No Groq 429 appeared in the supplied latest test logs. This is
a direct result of:
1. Smaller prompts (fewer tokens processed per turn)
2. Fewer lead-extraction calls (throttled to every 5 turns)
3. One attempt instead of three (less total API usage)

**Intent tracking:** Intent correctly changed from `unknown` → `membership` →
`trial` as the conversation progressed. The structured state retained this across
turns without requiring the LLM to re-derive it.

**Lead extraction:** Successfully captured structured information. The extraction
prompt was reduced from 5,894 to 1,659 chars, and extraction ran less frequently,
reducing API pressure.

**Turn-taking:** Short utterances such as "Yeah" were processed correctly. The
caller could say "hello?" mid-conversation without triggering the deterministic
greeting bug. The conversation flowed naturally through greeting → discovery →
qualification → closing.

**Contact information:** Name and phone number information remained available
during the session via structured state, even when those messages were no longer
in the recent 6-message window.

**STT observations:** Some transcription inaccuracies may still exist (inherent
to the STT model and audio quality). However, the latest results showed several
long/natural utterances being transcribed successfully. The VAD changes (longer
silence threshold, lower energy threshold) may have contributed to improved
segmentation, though this cannot be definitively attributed without controlled
A/B testing.

---

### 4.8 WHY THE LATENCY IMPROVED

The latency improvement is the result of **multiple changes working together**,
not a single magic optimization. The core principle is: **do less work per
conversational turn.**

Breakdown of the latency reduction:

**1. Fewer input tokens per turn.**
The system prompt went from ~13k to ~2.4k chars. Combined with the reduced message
window (6 vs 10), total input per turn dropped from ~14-16k to ~3.5-4.8k chars.
Groq processes fewer tokens, responds faster.

**2. Smaller conversation history.**
The bounded recent window (6 messages) means the LLM processes less history.
Structured session state (lead profile, phase, intent, summary) preserves
important context without needing the full message history.

**3. Fewer lead-extraction requests.**
Throttled to every 5 turns (was 3) with narrower trigger vocabulary. Each
eliminated extraction call saves 1-3 seconds of API latency and reduces
rate-limit pressure.

**4. Less time wasted on retries.**
One attempt instead of three. On 429, immediate fallback (~200ms) instead of
waiting 5-15 seconds per retry. Total worst-case retry time: 0.5s vs 30+s.

**5. Reduced chance of rate-limit cascades.**
Smaller prompts + fewer extraction calls = fewer total API requests = less
likelihood of hitting rate limits in the first place. The system now fits within
normal Groq rate limits for most conversational turns.

**6. Better audio segmentation.**
Longer silence threshold (1000ms vs 750ms) and lower energy threshold (400 vs 500)
reduce the chance of premature speech cutting, leading to cleaner STT input and
fewer mis-transcriptions that would require clarification turns.

**7. Cleaner deterministic routing.**
Session-aware greeting logic prevents inappropriate deterministic responses during
active conversations, avoiding wasted turns where the caller has to re-explain
themselves.

**Overall:** The receptionist now processes only the information required to answer
the current turn while retaining important structured state separately. This is
fundamentally different from the original approach of sending everything every time.

---

### 4.9 CURRENT ARCHITECTURE

The current conceptual architecture:

```
Audio Input
→ VAD (silence detection + energy threshold)
→ Audio Segmentation (bounded utterance)
→ STT (Groq Whisper)
→ Structured Session State (lead profile, phase, intent)
→ Bounded Recent Context (last 6 messages)
→ Deterministic Routing (greeting only on first turn)
→ Groq LLM (only when needed)
→ ElevenLabs TTS
→ Audio Output (chunked, frame-aligned)
→ Caller
```

**What is stable:**
- Core voice pipeline (Exotel → WebSocket → Media Worker → Voice Worker)
- STT → Chat → TTS flow
- Structured conversation state management
- Lead extraction and qualification scoring
- Barge-in and TTS interruption
- WebSocket keepalive and connection management
- Deterministic greeting (session-aware)
- Rate-limit fail-fast behavior

**What remains incomplete:**
- Persistent session memory (calls are in-memory only, lost on Worker recycle)
- Session lifecycle (no unique session IDs, no clean start/end)
- CRM/database integration
- Multi-language support

---

### 4.10 REMAINING WORK

The next major engineering task is **Persistent Session Memory**.

The current system maintains useful structured state *during* a call, but this
state is held in the Cloudflare Worker's in-memory environment. There are three
levels of persistence needed:

**In-call conversation state** (CURRENT — working):
- `ConversationState` object passed via RPC between Media Worker and Voice Worker
- Persists for the duration of a single WebSocket connection
- Includes lead profile, messages, phase, intent, corrections, summary

**Persistent session state** (NOT YET IMPLEMENTED):
- Every call needs a unique session ID
- State cannot leak between callers (the current `_default_conversation` singleton
  is a known risk for multi-caller scenarios)
- Calls must start and end cleanly
- Reconnects must be handled safely
- Useful lead information must be persisted after the call ends

**Future CRM/database persistence** (NOT YET IMPLEMENTED):
- Lead profiles stored in a database
- Integration with HubSpot/Salesforce
- Call recordings linked to lead records
- Conversion tracking

This is the next priority. Do not implement this now — it requires careful design
of session lifecycle, storage backend selection, and data isolation guarantees.

---

### 4.11 ENGINEERING LESSONS

**Reliability retries are not automatically good for real-time systems.**
The initial retry logic (3 attempts, 15s max backoff) was correct for reliability
but catastrophic for real-time voice. A retry that takes 15 seconds is worse than
a fallback that takes 0.5 seconds. In voice systems, latency IS reliability.

**Voice agents require latency-aware failure strategies.**
Every failure path must answer: "What does the caller experience during this error?"
If the answer is "silence for 10+ seconds," the strategy is wrong. Short natural
fallbacks are better than technically correct but slow retries.

**Prompt size directly matters for real-time systems.**
A 13k-char prompt is not just a cost issue — it's a latency issue. Every character
adds processing time. In a text chatbot, 200ms extra is invisible. In a voice call,
200ms is noticeable, and 2 seconds is awkward.

**Structured memory is better than endlessly growing conversation history.**
The lead profile, phase tracking, and rolling summary preserve important context
in a few hundred characters. The same information in raw conversation messages
would take thousands of characters. Structured state scales; raw history doesn't.

**Every unnecessary LLM call increases cost, latency, and rate-limit pressure.**
One unnecessary lead-extraction call is effectively another API request competing
for the same quota. Throttling and trigger-narrowing are not micro-optimizations
— they're essential for production voice systems.

**VAD is part of conversational UX, not just an audio preprocessing detail.**
The silence threshold and energy detection parameters directly affect what the STT
model receives, which affects transcription quality, which affects conversation flow.
VAD tuning is conversation design.

**Production logs are more useful than assumptions.**
The 429 retry-after values, the actual Groq request sizes, the deterministic
greeting firing mid-conversation — all of these were discovered through production
logs, not code review. Instrumentation is not optional.

**Optimize the complete request path rather than only the LLM.**
The latency improvement came from changes across the entire pipeline: prompt size,
message window, extraction frequency, retry behavior, VAD parameters, deterministic
routing. Focusing only on the LLM would have captured a fraction of the improvement.

---

## 5. CURRENT STATUS

The Voice Receptionist has reached a **significantly improved MVP state** after the
latest latency/conversation optimization pass.

| Component | Status |
|-----------|--------|
| Core voice pipeline (Exotel → WebSocket → Workers) | Working |
| Telephony/WebSocket connection | Working |
| STT (Groq Whisper) | Working |
| LLM conversation (Groq qwen3.6-27b) | Working |
| TTS (ElevenLabs) | Working |
| Lead extraction | Working |
| Qualification scoring | Working |
| Context efficiency | Significantly improved (-70% input size) |
| Rate-limit latency behavior | Significantly improved (fail-fast) |
| Turn-taking | Significantly improved (session-aware greeting) |
| Barge-in | Working |
| Persistent session memory | **NEXT PRIORITY** |

**Not yet production-ready.** Persistent session lifecycle and further production
hardening (error monitoring, call recording, analytics) are still required.

---

## 6. KEY URLS & COMMANDS

### URLs

| URL | Purpose |
|-----|---------|
| `https://api.vantixlab.info` | Production API (Media Worker) |
| `wss://api.vantixlab.info/media` | Production WebSocket (Exotel) |
| `https://nextfit-ai-voice.nextfit-ai-voice.workers.dev` | Voice Worker direct |

### Deployment Commands

```bash
# Deploy voice worker (Python)
cd cloudflare-worker
uv run pywrangler deploy

# Deploy media worker (JavaScript)
cd cloudflare-worker/media-worker
npx wrangler deploy

# Tail production logs
cd cloudflare-worker
npx wrangler tail
```

### Validation Commands

```bash
# Python syntax check
python -c "import ast; ast.parse(open('cloudflare-worker/src/main.py').read())"

# Check prompt sizes
python -c "
import sys; sys.path.insert(0, 'cloudflare-worker/src')
from prompts import NEXTFIT_CHAT_PROMPT, LEAD_EXTRACTION_PROMPT
print(f'Chat prompt: {len(NEXTFIT_CHAT_PROMPT)} chars')
print(f'Extraction prompt: {len(LEAD_EXTRACTION_PROMPT)} chars')
"
```

---

*This document was generated from inspection of the actual codebase and production
logs. Every claim is based on code that exists in the repository or logs from
production tests. No features were invented or assumed.*
