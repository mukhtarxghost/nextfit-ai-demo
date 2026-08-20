import { useState } from "react";
import "./App.css";

const API_URL = "http://127.0.0.1:8000";

function App() {
  const [messages, setMessages] = useState([
    {
      role: "assistant",
      content:
        "Hey! Welcome to NextFit 👋 How can I help you today?",
    },
  ]);

  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);

  const [lead, setLead] = useState({
    score: 0,
    classification: "INFORMATION",
    profile: {},
    recommended_action: "Waiting for conversation...",
  });

  const sendMessage = async () => {
    if (!input.trim() || loading) return;

    const userMessage = input.trim();

    setMessages((prev) => [
      ...prev,
      {
        role: "user",
        content: userMessage,
      },
    ]);

    setInput("");
    setLoading(true);

    try {
      const response = await fetch(`${API_URL}/chat`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          message: userMessage,
        }),
      });

      if (!response.ok) {
        throw new Error("Backend request failed");
      }

      const data = await response.json();

      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: data.response,
        },
      ]);

      setLead({
        score: data.score,
        classification: data.classification,
        profile: data.lead,
        recommended_action: data.recommended_action,
      });
    } catch (error) {
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content:
            "I'm having a little trouble connecting right now. Please try again.",
        },
      ]);
    } finally {
      setLoading(false);
    }
  };

  const handleKeyDown = (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendMessage();
    }
  };

  const resetDemo = () => {
    window.location.reload();
  };

  const profile = lead.profile || {};

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark">N</div>

          <div>
            <h1>NextFit</h1>
            <span>AI Receptionist</span>
          </div>
        </div>

        <div className="status">
          <span className="status-dot"></span>
          AI ONLINE
        </div>
      </header>

      <main className="dashboard">
        <section className="chat-panel">
          <div className="chat-header">
            <div>
              <h2>NextFit Receptionist</h2>
              <p>Conversational lead qualification</p>
            </div>

            <button
              className="reset-button"
              onClick={resetDemo}
            >
              Reset
            </button>
          </div>

          <div className="messages">
            {messages.map((message, index) => (
              <div
                key={index}
                className={`message-row ${message.role}`}
              >
                <div
                  className={`message ${
                    message.role === "assistant"
                      ? "assistant-message"
                      : "user-message"
                  }`}
                >
                  {message.content}
                </div>
              </div>
            ))}

            {loading && (
              <div className="message-row assistant">
                <div className="message assistant-message typing">
                  <span></span>
                  <span></span>
                  <span></span>
                </div>
              </div>
            )}
          </div>

          <div className="input-area">
            <input
              type="text"
              placeholder="Type a message..."
              value={input}
              onChange={(event) =>
                setInput(event.target.value)
              }
              onKeyDown={handleKeyDown}
              disabled={loading}
            />

            <button
              onClick={sendMessage}
              disabled={loading || !input.trim()}
            >
              Send
            </button>
          </div>
        </section>

        <aside className="lead-panel">
          <div className="panel-title">
            <div>
              <span>LIVE LEAD</span>
              <h2>Qualification</h2>
            </div>

            <div
              className={`classification ${lead.classification.toLowerCase()}`}
            >
              {lead.classification}
            </div>
          </div>

          <div className="score-card">
            <span>LEAD SCORE</span>

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
              label="Goal"
              value={profile.goal}
            />

            <LeadDetail
              label="Experience"
              value={profile.experience}
            />

            <LeadDetail
              label="Service"
              value={profile.training_preference}
            />

            <LeadDetail
              label="Timeline"
              value={profile.timeline}
            />

            <LeadDetail
              label="Availability"
              value={profile.availability}
            />

            <LeadDetail
              label="Problem"
              value={profile.problem}
            />
          </div>

          <div
            className={`handoff ${
              profile.needs_human ? "active" : ""
            }`}
          >
            <div className="handoff-icon">
              {profile.needs_human ? "✓" : "—"}
            </div>

            <div>
              <strong>
                {profile.needs_human
                  ? "Human follow-up recommended"
                  : "No human handoff yet"}
              </strong>

              <p>
                {profile.needs_human
                  ? "Lead should be passed to the NextFit team."
                  : "AI is continuing the conversation."}
              </p>
            </div>
          </div>

          <div className="action-card">
            <span>RECOMMENDED ACTION</span>
            <p>{lead.recommended_action}</p>
          </div>
        </aside>
      </main>
    </div>
  );
}

function LeadDetail({ label, value }) {
  const displayValue =
    value && value !== "unknown"
      ? String(value).replaceAll("_", " ")
      : "—";

  return (
    <div className="detail">
      <span>{label}</span>
      <strong>{displayValue}</strong>
    </div>
  );
}

export default App;