import { useEffect, useRef, useState } from "react";
import "./App.css";

const API_URL = "http://127.0.0.1:8787";

function App() {
  const [messages, setMessages] = useState([
    {
      role: "assistant",
      content:
        "Hey, welcome to NextFit 👋 What brings you in today?",
    },
  ]);

  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);

  const [lead, setLead] = useState({
    score: 0,
    classification: "INFORMATION",
    profile: {},
    reasons: [],
    recommended_action: "Waiting for conversation...",
  });

  const [isListening, setIsListening] = useState(false);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [voiceSupported, setVoiceSupported] = useState(true);
  const [voiceError, setVoiceError] = useState("");

  const recognitionRef = useRef(null);
  const messagesEndRef = useRef(null);

  // ============================================================
  // SPEECH RECOGNITION
  // ============================================================

  useEffect(() => {
    const SpeechRecognition =
      window.SpeechRecognition ||
      window.webkitSpeechRecognition;

    if (!SpeechRecognition) {
      setVoiceSupported(false);
      setVoiceError(
        "Speech recognition is not supported in this browser."
      );
      return;
    }

    const recognition = new SpeechRecognition();

    recognition.lang = "en-IN";
    recognition.continuous = false;
    recognition.interimResults = false;
    recognition.maxAlternatives = 1;

    recognition.onstart = () => {
      setIsListening(true);
      setVoiceError("");
    };

    recognition.onresult = (event) => {
      const transcript =
        event.results?.[0]?.[0]?.transcript?.trim() || "";

      if (!transcript) {
        setVoiceError("I didn't catch that. Try again.");
        return;
      }

      setInput(transcript);
      sendMessage(transcript);
    };

    recognition.onerror = (event) => {
      setIsListening(false);

      switch (event.error) {
        case "not-allowed":
          setVoiceError(
            "Microphone permission was blocked. Allow microphone access for localhost."
          );
          break;

        case "service-not-allowed":
          setVoiceError(
            "The browser speech service is unavailable."
          );
          break;

        case "audio-capture":
          setVoiceError(
            "The microphone could not be accessed."
          );
          break;

        case "no-speech":
          setVoiceError(
            "I didn't hear anything. Try speaking again."
          );
          break;

        case "network":
          setVoiceError(
            "Speech recognition could not connect to the browser service."
          );
          break;

        case "aborted":
          setVoiceError("");
          break;

        default:
          setVoiceError(`Voice error: ${event.error}`);
      }
    };

    recognition.onend = () => {
      setIsListening(false);
    };

    recognitionRef.current = recognition;

    // Initialize browser speech voices
    if ("speechSynthesis" in window) {
      window.speechSynthesis.getVoices();

      window.speechSynthesis.onvoiceschanged = () => {
        window.speechSynthesis.getVoices();
      };
    }

    return () => {
      try {
        recognition.abort();
      } catch {
        // Ignore cleanup errors.
      }

      if ("speechSynthesis" in window) {
        window.speechSynthesis.cancel();
      }
    };
  }, []);

  // ============================================================
  // AUTO SCROLL
  // ============================================================

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({
      behavior: "smooth",
    });
  }, [messages, loading]);

  // ============================================================
  // FEMALE VOICE SELECTION
  // ============================================================

  const getPreferredVoice = () => {
    if (!("speechSynthesis" in window)) {
      return null;
    }

    const voices = window.speechSynthesis.getVoices();

    if (!voices.length) {
      return null;
    }

    /*
     * Preferred natural female voices.
     *
     * Voice availability depends on:
     * - Windows
     * - Chrome
     * - installed language packs
     * - browser speech engine
     */

    const preferredNames = [
      "Google UK English Female",
      "Google US English Female",
      "Microsoft Zira",
      "Microsoft Jenny",
      "Microsoft Aria",
      "Samantha",
      "Karen",
      "Moira",
      "Aditi",
      "Veena",
      "Heera",
    ];

    // ----------------------------------------------------------
    // 1. Exact preferred female voices
    // ----------------------------------------------------------

    for (const preferred of preferredNames) {
      const match = voices.find((voice) =>
        voice.name
          .toLowerCase()
          .includes(preferred.toLowerCase())
      );

      if (match) {
        return match;
      }
    }

    // ----------------------------------------------------------
    // 2. Female voice keywords
    // ----------------------------------------------------------

    const femaleKeywords = [
      "female",
      "woman",
      "zira",
      "jenny",
      "aria",
      "samantha",
      "karen",
      "moira",
      "veena",
      "aditi",
      "heera",
      "google uk english female",
      "google us english female",
    ];

    const englishFemale = voices.find(
      (voice) =>
        /^en[-_]/i.test(voice.lang) &&
        femaleKeywords.some((keyword) =>
          voice.name
            .toLowerCase()
            .includes(keyword)
        )
    );

    if (englishFemale) {
      return englishFemale;
    }

    // ----------------------------------------------------------
    // 3. Indian English voice
    // ----------------------------------------------------------

    const indianEnglish = voices.find(
      (voice) =>
        /en[-_]IN/i.test(voice.lang)
    );

    if (indianEnglish) {
      return indianEnglish;
    }

    // ----------------------------------------------------------
    // 4. Any English voice
    // ----------------------------------------------------------

    const englishVoice = voices.find(
      (voice) =>
        /^en[-_]/i.test(voice.lang)
    );

    if (englishVoice) {
      return englishVoice;
    }

    return voices[0];
  };

  // ============================================================
  // SPEAK RESPONSE
  // ============================================================

  const speakResponse = (text) => {
    if (!("speechSynthesis" in window)) {
      return;
    }

    window.speechSynthesis.cancel();

    const utterance =
      new SpeechSynthesisUtterance(text);

    const voice = getPreferredVoice();

    if (voice) {
      utterance.voice = voice;
      utterance.lang = voice.lang;
    } else {
      utterance.lang = "en-IN";
    }

    /*
     * Female receptionist settings:
     *
     * Slightly higher pitch
     * Slightly slower delivery
     * Full volume
     */

    utterance.rate = 0.92;
    utterance.pitch = 1.12;
    utterance.volume = 1;

    utterance.onstart = () => {
      setIsSpeaking(true);
    };

    utterance.onend = () => {
      setIsSpeaking(false);
    };

    utterance.onerror = () => {
      setIsSpeaking(false);
    };

    window.speechSynthesis.speak(utterance);
  };

  // ============================================================
  // DEFENSIVE AI RESPONSE CLEANING
  // ============================================================

  const cleanAIResponse = (text) => {
    if (!text) {
      return "";
    }

    let cleaned = String(text).trim();

    // Remove complete <think>...</think> blocks
    cleaned = cleaned.replace(
      /<think>.*?<\/think>/gis,
      ""
    );

    // Handle unclosed <think> blocks
    if (/<think>/i.test(cleaned)) {
      const parts = cleaned.split(/<think>/i);
      if (parts.length > 1) {
        const afterThink = parts[parts.length - 1];
        const closingMatch = afterThink.match(
          /<\/think>/i
        );

        if (closingMatch) {
          cleaned = afterThink.substring(
            closingMatch.index + closingMatch[0].length
          );
        } else {
          // Try to extract from final answer markers
          const finalMarkers = [
            /\bfinal answer\s*:/i,
            /\bfinal response\s*:/i,
            /\bresponse\s*:/i,
            /\banswer\s*:/i,
          ];

          let extracted = null;
          for (const marker of finalMarkers) {
            const match = afterThink.match(marker);
            if (match) {
              extracted = afterThink.substring(
                match.index + match[0].length
              );
              break;
            }
          }

          cleaned = extracted || "";
        }
      }
    }

    // Remove remaining <think> or </think> tags
    cleaned = cleaned.replace(/<\/?think>/gi, "");

    // Remove reasoning prefixes
    cleaned = cleaned.replace(
      /^\s*(analysis|reasoning|chain[- ]of[- ]thought)\s*:\s*/i,
      ""
    );

    cleaned = cleaned.replace(
      /^\s*(final answer|final response|assistant response)\s*:\s*/i,
      ""
    );

    cleaned = cleaned.replace(
      /^\s*here'?s what (?:i|the ai|the assistant)\s+(?:should|will)\s+say\s*:\s*/i,
      ""
    );

    // Remove markdown fences
    cleaned = cleaned.replace(
      /^\s*```(?:text)?\s*/i,
      ""
    );

    cleaned = cleaned.replace(/\s*```\s*$/i, "");

    // Remove XML/meta wrappers
    cleaned = cleaned.replace(
      /^\s*<response>\s*/i,
      ""
    );

    cleaned = cleaned.replace(
      /\s*<\/response>\s*$/i,
      ""
    );

    // Normalize whitespace
    cleaned = cleaned.replace(/[ \t]+/g, " ");
    cleaned = cleaned.replace(/\n{3,}/g, "\n\n");

    return cleaned.trim();
  };

  // ============================================================
  // MICROPHONE
  // ============================================================

  const toggleListening = () => {
    if (!voiceSupported) {
      return;
    }

    const recognition =
      recognitionRef.current;

    if (!recognition) {
      setVoiceError(
        "Speech recognition could not be initialized."
      );
      return;
    }

    if (isListening) {
      try {
        recognition.stop();
      } catch {
        // Ignore.
      }

      setIsListening(false);
      return;
    }

    setVoiceError("");

    if ("speechSynthesis" in window) {
      window.speechSynthesis.cancel();
    }

    setIsSpeaking(false);

    try {
      recognition.start();
    } catch {
      try {
        recognition.abort();
      } catch {
        // Ignore.
      }

      setIsListening(false);
    }
  };

  // ============================================================
  // SEND MESSAGE
  // ============================================================

  const sendMessage = async (
    messageOverride = null
  ) => {
    const userMessage = (
      messageOverride !== null
        ? messageOverride
        : input
    ).trim();

    if (!userMessage || loading) {
      return;
    }

    if (
      isListening &&
      recognitionRef.current
    ) {
      try {
        recognitionRef.current.stop();
      } catch {
        // Ignore.
      }
    }

    setIsListening(false);

    if ("speechSynthesis" in window) {
      window.speechSynthesis.cancel();
    }

    setIsSpeaking(false);

    setMessages((prev) => [
      ...prev,
      {
        role: "user",
        content: userMessage,
      },
    ]);

    setInput("");
    setVoiceError("");
    setLoading(true);

    try {
      const response = await fetch(
        `${API_URL}/chat`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            message: userMessage,
          }),
        }
      );

      if (!response.ok) {
        throw new Error(
          `Backend request failed: ${response.status}`
        );
      }

      const data =
        await response.json();

      const cleanedResponse = cleanAIResponse(
        data.response
      );

      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: cleanedResponse,
        },
      ]);

      setLead({
        score: data.score,
        classification:
          data.classification,
        profile: data.lead || {},
        reasons: data.reasons || [],
        recommended_action:
          data.recommended_action,
      });

      speakResponse(cleanedResponse);
    } catch (error) {
      console.error(
        "Chat request failed:",
        error
      );

      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content:
            "I'm having a little trouble connecting right now. Please try again.",
        },
      ]);

      setVoiceError(
        "Could not connect to the NextFit AI backend."
      );
    } finally {
      setLoading(false);
    }
  };

  // ============================================================
  // ENTER KEY
  // ============================================================

  const handleKeyDown = (event) => {
    if (
      event.key === "Enter" &&
      !event.shiftKey
    ) {
      event.preventDefault();
      sendMessage();
    }
  };

  // ============================================================
  // RESET
  // ============================================================

  const resetDemo = async () => {
    if (recognitionRef.current) {
      try {
        recognitionRef.current.abort();
      } catch {
        // Ignore.
      }
    }

    if ("speechSynthesis" in window) {
      window.speechSynthesis.cancel();
    }

    try {
      await fetch(
        `${API_URL}/reset`,
        {
          method: "POST",
        }
      );
    } catch (error) {
      console.error(
        "Backend reset failed:",
        error
      );
    }

    setMessages([
      {
        role: "assistant",
        content:
          "Hey, welcome to NextFit 👋 What brings you in today?",
      },
    ]);

    setInput("");
    setLoading(false);
    setIsListening(false);
    setIsSpeaking(false);
    setVoiceError("");

    setLead({
      score: 0,
      classification: "INFORMATION",
      profile: {},
      reasons: [],
      recommended_action:
        "Waiting for conversation...",
    });
  };

  const profile = lead.profile || {};

  // ============================================================
  // UI
  // ============================================================

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark">
            N
          </div>

          <div>
            <h1>NextFit</h1>

            <span>
              AI Receptionist
            </span>
          </div>
        </div>

        <div className="status">
          <span className="status-dot" />

          {isListening
            ? "LISTENING"
            : isSpeaking
            ? "AI SPEAKING"
            : "AI ONLINE"}
        </div>
      </header>

      <main className="dashboard">
        {/* ====================================================
            CHAT PANEL
        ==================================================== */}

        <section className="chat-panel">
          <div className="chat-header">
            <div>
              <h2>
                NextFit Receptionist
              </h2>

              <p>
                Conversational lead qualification
              </p>
            </div>

            <button
              className="reset-button"
              onClick={resetDemo}
            >
              Reset
            </button>
          </div>

          <div className="messages">
            {messages.map(
              (message, index) => (
                <div
                  key={index}
                  className={`message-row ${message.role}`}
                >
                  <div
                    className={`message ${
                      message.role ===
                      "assistant"
                        ? "assistant-message"
                        : "user-message"
                    }`}
                  >
                    {message.content}
                  </div>
                </div>
              )
            )}

            {loading && (
              <div className="message-row assistant">
                <div className="message assistant-message typing">
                  <span />
                  <span />
                  <span />
                </div>
              </div>
            )}

            <div ref={messagesEndRef} />
          </div>

          <div className="input-area">
            <button
              className={`mic-button ${
                isListening
                  ? "listening"
                  : ""
              }`}
              onClick={
                toggleListening
              }
              disabled={
                loading ||
                !voiceSupported
              }
              title={
                isListening
                  ? "Stop listening"
                  : "Speak to NextFit"
              }
            >
              {isListening
                ? "■"
                : "🎙️"}
            </button>

            <input
              type="text"
              placeholder={
                isListening
                  ? "Listening..."
                  : "Type a message..."
              }
              value={input}
              onChange={(event) =>
                setInput(
                  event.target.value
                )
              }
              onKeyDown={
                handleKeyDown
              }
              disabled={loading}
            />

            <button
              className="send-button"
              onClick={() =>
                sendMessage()
              }
              disabled={
                loading ||
                !input.trim()
              }
            >
              Send
            </button>
          </div>

          <div className="voice-footer">
            {voiceError ? (
              <span className="voice-error">
                ⚠ {voiceError}
              </span>
            ) : isListening ? (
              <span className="voice-active">
                <i />
                Listening... speak now
              </span>
            ) : isSpeaking ? (
              <span className="voice-active">
                <i />
                NextFit AI is speaking...
              </span>
            ) : (
              <span>
                🎙️ Speak naturally or type
                your message
              </span>
            )}
          </div>
        </section>

        {/* ====================================================
            LEAD PANEL
        ==================================================== */}

        <aside className="lead-panel">
          <div className="panel-title">
            <div>
              <span>
                LIVE LEAD
              </span>

              <h2>
                Qualification
              </h2>
            </div>

            <div
              className={`classification ${lead.classification.toLowerCase()}`}
            >
              {lead.classification}
            </div>
          </div>

          <div className="score-card">
            <span>
              LEAD SCORE
            </span>

            <div className="score">
              {lead.score}
              <small>/100</small>
            </div>

            <div className="score-bar">
              <div
                className="score-fill"
                style={{
                  width: `${lead.score}%`,
                }}
              />
            </div>
          </div>

          <div className="details">
            <LeadDetail
              label="Name"
              value={profile.name}
            />

            <LeadDetail
              label="Intent"
              value={profile.intent}
            />

            <LeadDetail
              label="Goal"
              value={profile.goal}
            />

            <LeadDetail
              label="Current Situation"
              value={
                profile.current_situation
              }
            />

            <LeadDetail
              label="Experience"
              value={
                profile.experience
              }
            />

            <LeadDetail
              label="Problem"
              value={
                profile.problem
              }
            />

            <LeadDetail
              label="Previous Attempts"
              value={
                profile.previous_attempts
              }
            />

            <LeadDetail
              label="Desired Outcome"
              value={
                profile.desired_outcome
              }
            />

            <LeadDetail
              label="Service Need"
              value={
                profile.training_preference
              }
            />

            <LeadDetail
              label="Location"
              value={
                profile.location
              }
            />

            <LeadDetail
              label="Timeline"
              value={
                profile.timeline
              }
            />

            <LeadDetail
              label="Availability"
              value={
                profile.availability
              }
            />
          </div>

          <div className="signal-card">
            <div className="signal-header">
              <span>
                QUALIFICATION SIGNALS
              </span>
            </div>

            <Signal
              label="Goal clarity"
              value={
                profile.goal_clarity
              }
            />

            <Signal
              label="Program fit"
              value={
                profile.program_fit
              }
            />

            <Signal
              label="Engagement"
              value={
                profile.engagement
              }
            />

            <div className="signal-row">
              <span>
                Next step
              </span>

              <strong>
                {formatValue(
                  profile.next_step_intent
                )}
              </strong>
            </div>
          </div>

          <div
            className={`handoff ${
              profile.needs_human
                ? "active"
                : ""
            }`}
          >
            <div className="handoff-icon">
              {profile.needs_human
                ? "✓"
                : "—"}
            </div>

            <div>
              <strong>
                {profile.needs_human
                  ? "Human follow-up recommended"
                  : "No human handoff yet"}
              </strong>

              <p>
                {profile.needs_human
                  ? "Lead has passed the qualification gate and indicated interest in continuing."
                  : "AI is continuing the conversation."}
              </p>
            </div>
          </div>

          <div className="action-card">
            <span>
              RECOMMENDED ACTION
            </span>

            <p>
              {lead.recommended_action}
            </p>
          </div>
        </aside>
      </main>
    </div>
  );
}

// ============================================================
// FORMAT VALUE
// ============================================================

function formatValue(value) {
  if (
    value === null ||
    value === undefined ||
    value === "" ||
    value === "unknown"
  ) {
    return "—";
  }

  return String(value).replaceAll(
    "_",
    " "
  );
}

// ============================================================
// LEAD DETAIL
// ============================================================

function LeadDetail({
  label,
  value,
}) {
  const displayValue =
    formatValue(value);

  return (
    <div className="detail">
      <span>
        {label}
      </span>

      <strong>
        {displayValue}
      </strong>
    </div>
  );
}

// ============================================================
// SIGNAL
// ============================================================

function Signal({
  label,
  value,
}) {
  const numericValue =
    typeof value === "number"
      ? value
      : 0;

  return (
    <div className="signal-row">
      <span>{label}</span>

      <strong>
        {numericValue}/10
      </strong>
    </div>
  );
}

export default App;
