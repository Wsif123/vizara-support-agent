(function () {
  const scriptTag = document.currentScript;
  const BACKEND_URL = (scriptTag && scriptTag.getAttribute("data-api-url")) || "http://localhost:8000/chat";

  const STYLES = `
    #vizara-widget * { box-sizing: border-box; margin: 0; padding: 0; }
    #vizara-widget button { cursor: pointer; border: none; background: none; font-family: inherit; }
    #vizara-widget {
      position: fixed; bottom: 20px; right: 20px; z-index: 999999;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    }
    #vizara-bubble {
      width: 60px; height: 60px; border-radius: 50%;
      background: linear-gradient(135deg, #6C63FF, #4A42DB);
      color: #fff; font-size: 28px; display: flex; align-items: center; justify-content: center;
      box-shadow: 0 4px 20px rgba(108,99,255,0.4);
      transition: transform 0.2s, opacity 0.2s;
    }
    #vizara-bubble:hover { transform: scale(1.08); }
    #vizara-panel {
      position: absolute; bottom: 75px; right: 0; width: 360px; height: 520px;
      background: #fff; border-radius: 16px; box-shadow: 0 8px 32px rgba(0,0,0,0.18);
      display: none; flex-direction: column; overflow: hidden;
      animation: vizara-slide 0.25s ease-out;
    }
    @keyframes vizara-slide {
      from { opacity: 0; transform: translateY(12px) scale(0.96); }
      to   { opacity: 1; transform: translateY(0) scale(1); }
    }
    #vizara-panel.open { display: flex; }
    #vizara-header {
      background: linear-gradient(135deg, #6C63FF, #4A42DB); color: #fff;
      padding: 16px 18px; font-weight: 600; font-size: 15px;
      display: flex; align-items: center; justify-content: space-between;
    }
    #vizara-close { color: rgba(255,255,255,0.8); font-size: 20px; line-height: 1; }
    #vizara-close:hover { color: #fff; }
    #vizara-messages {
      flex: 1; overflow-y: auto; padding: 14px 16px;
      background: #F5F6FA;
      display: flex; flex-direction: column; gap: 10px;
    }
    .vizara-msg {
      max-width: 85%; padding: 10px 14px; border-radius: 14px;
      font-size: 14px; line-height: 1.5; white-space: pre-wrap; word-wrap: break-word;
    }
    .vizara-user {
      align-self: flex-end; background: #6C63FF; color: #fff;
      border-bottom-right-radius: 4px;
    }
    .vizara-bot {
      align-self: flex-start; background: #fff; color: #222;
      border-bottom-left-radius: 4px; box-shadow: 0 1px 3px rgba(0,0,0,0.08);
    }
    .vizara-escalated-tag {
      display: inline-block; margin-top: 6px; padding: 3px 8px;
      background: #FFF3CD; color: #856404; border-radius: 6px;
      font-size: 12px; font-weight: 500;
    }
    #vizara-input-area {
      display: flex; gap: 8px; padding: 12px 14px; border-top: 1px solid #E8E8EE;
      background: #fff;
    }
    #vizara-input {
      flex: 1; padding: 10px 14px; border: 1px solid #D0D0DE; border-radius: 24px;
      font-size: 14px; outline: none; font-family: inherit;
    }
    #vizara-input:focus { border-color: #6C63FF; }
    #vizara-send {
      width: 40px; height: 40px; border-radius: 50%; background: #6C63FF; color: #fff;
      font-size: 18px; display: flex; align-items: center; justify-content: center;
      flex-shrink: 0; transition: background 0.15s;
    }
    #vizara-send:hover { background: #4A42DB; }
    #vizara-send:disabled { opacity: 0.5; cursor: not-allowed; }
    .vizara-typing {
      align-self: flex-start; background: #E8E8F0; color: #888;
      padding: 10px 14px; border-radius: 14px; font-size: 14px;
      border-bottom-left-radius: 4px;
    }
  `;

  const SESSION_ID = "viz_" + Math.random().toString(36).slice(2, 10) + "_" + Date.now();

  let open = false;

  function injectStyles() {
    const el = document.createElement("style");
    el.textContent = STYLES;
    document.head.appendChild(el);
  }

  function buildDOM() {
    const root = document.createElement("div");
    root.id = "vizara-widget";

    root.innerHTML = `
      <div id="vizara-panel">
        <div id="vizara-header">
          <span>Vizara Support</span>
          <button id="vizara-close">&times;</button>
        </div>
        <div id="vizara-messages">
          <div class="vizara-msg vizara-bot">Hi! I can help with questions about Vizara Studio's video ads and product photography. How can I help you?</div>
        </div>
        <div id="vizara-input-area">
          <input id="vizara-input" type="text" placeholder="Type your question..." />
          <button id="vizara-send" title="Send">&#10148;</button>
        </div>
      </div>
      <button id="vizara-bubble" title="Chat with us">&#128172;</button>
    `;

    document.body.appendChild(root);

    const bubble = root.querySelector("#vizara-bubble");
    const panel = root.querySelector("#vizara-panel");
    const closeBtn = root.querySelector("#vizara-close");
    const input = root.querySelector("#vizara-input");
    const sendBtn = root.querySelector("#vizara-send");
    const messages = root.querySelector("#vizara-messages");

    bubble.addEventListener("click", () => {
      open = true;
      panel.classList.add("open");
      bubble.style.display = "none";
      input.focus();
    });

    closeBtn.addEventListener("click", () => {
      open = false;
      panel.classList.remove("open");
      bubble.style.display = "flex";
    });

    function send() {
      const text = input.value.trim();
      if (!text) return;
      input.value = "";
      appendMessage(text, "user");
      sendBtn.disabled = true;
      showTyping();
      fetch(BACKEND_URL, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: SESSION_ID, query: text }),
      })
        .then((r) => r.json())
        .then((data) => {
          removeTyping();
          appendBotMessage(data.answer, data.escalated);
        })
        .catch(() => {
          removeTyping();
          appendBotMessage("Sorry, something went wrong. Please try again.", false);
        })
        .finally(() => { sendBtn.disabled = false; input.focus(); });
    }

    sendBtn.addEventListener("click", send);
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") send(); });

    function appendMessage(text, role) {
      const div = document.createElement("div");
      div.className = "vizara-msg " + (role === "user" ? "vizara-user" : "vizara-bot");
      div.textContent = text;
      messages.appendChild(div);
      messages.scrollTop = messages.scrollHeight;
    }

    function appendBotMessage(text, escalated) {
      const div = document.createElement("div");
      div.className = "vizara-msg vizara-bot";
      div.textContent = text;
      if (escalated) {
        const tag = document.createElement("div");
        tag.className = "vizara-escalated-tag";
        tag.textContent = "Wasif will follow up";
        div.appendChild(tag);
      }
      messages.appendChild(div);
      messages.scrollTop = messages.scrollHeight;
    }

    function showTyping() {
      const el = document.createElement("div");
      el.className = "vizara-typing";
      el.id = "vizara-typing";
      el.textContent = "Typing...";
      messages.appendChild(el);
      messages.scrollTop = messages.scrollHeight;
    }

    function removeTyping() {
      const el = messages.querySelector("#vizara-typing");
      if (el) el.remove();
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => { injectStyles(); buildDOM(); });
  } else {
    injectStyles();
    buildDOM();
  }
})();
