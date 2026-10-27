(function () {
  const scriptTag = document.currentScript;
  const BACKEND_URL = (scriptTag && scriptTag.getAttribute("data-api-url")) || "http://localhost:8000/chat";
  const CLIENT_ID = (scriptTag && scriptTag.getAttribute("data-client-id")) || "vizara";
  const BRAND_NAME = "Vizara Studio";

  const VOID_BLACK = "#0a0a0a";
  const NEBULA_PURPLE = "#7c3aed";
  const NEBULA_GLOW = "rgba(124, 58, 237, 0.5)";
  const SURFACE = "#141414";
  const SURFACE_LIGHT = "#1e1e1e";
  const TEXT_PRIMARY = "#f5f5f5";
  const TEXT_SECONDARY = "#a3a3a3";
  const BOT_BG = "#1c1c1e";
  const USER_BG = "#7c3aed";
  const USER_TEXT = "#ffffff";
  const BORDER = "rgba(255,255,255,0.06)";

  const STYLES = `
    #vizara-widget * { box-sizing: border-box; margin: 0; padding: 0; }
    #vizara-widget button { cursor: pointer; border: none; background: none; font-family: inherit; }
    #vizara-widget {
      position: fixed; bottom: 24px; right: 24px; z-index: 999999;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    }
    #vizara-bubble {
      width: 56px; height: 56px; border-radius: 50%;
      background: ${NEBULA_PURPLE};
      color: #fff; display: flex; align-items: center; justify-content: center;
      box-shadow: 0 4px 24px ${NEBULA_GLOW};
      transition: transform 0.2s, opacity 0.2s, box-shadow 0.2s;
      cursor: pointer;
    }
    #vizara-bubble:hover { transform: scale(1.06); box-shadow: 0 6px 32px ${NEBULA_GLOW}; }
    #vizara-panel {
      position: absolute; bottom: 68px; right: 0; width: 360px; height: 540px;
      background: ${SURFACE}; border-radius: 16px;
      box-shadow: 0 8px 40px rgba(0,0,0,0.5);
      display: none; flex-direction: column; overflow: hidden;
      border: 1px solid ${BORDER};
      animation: vizara-slide 0.25s ease-out;
    }
    @keyframes vizara-slide {
      from { opacity: 0; transform: translateY(12px) scale(0.96); }
      to   { opacity: 1; transform: translateY(0) scale(1); }
    }
    #vizara-panel.open { display: flex; }
    #vizara-header {
      background: ${VOID_BLACK}; color: ${TEXT_PRIMARY};
      padding: 16px 18px; font-weight: 600; font-size: 15px;
      display: flex; align-items: center; gap: 10px;
      border-bottom: 1px solid ${BORDER};
    }
    #vizara-avatar {
      width: 32px; height: 32px; border-radius: 50%;
      background: ${NEBULA_PURPLE};
      display: flex; align-items: center; justify-content: center;
      font-size: 14px; flex-shrink: 0;
    }
    #vizara-header-label { flex: 1; font-size: 14px; font-weight: 600; }
    #vizara-header-sub { font-size: 11px; color: ${TEXT_SECONDARY}; font-weight: 400; }
    #vizara-close {
      width: 28px; height: 28px; border-radius: 6px;
      display: flex; align-items: center; justify-content: center;
      color: ${TEXT_SECONDARY}; font-size: 18px; flex-shrink: 0; transition: background 0.15s;
    }
    #vizara-close:hover { background: ${SURFACE_LIGHT}; color: #fff; }
    #vizara-messages {
      flex: 1; overflow-y: auto; padding: 14px 14px;
      background: ${SURFACE};
      display: flex; flex-direction: column; gap: 8px;
    }
    .vizara-msg {
      max-width: 85%; padding: 12px 16px; border-radius: 14px;
      font-size: 14px; line-height: 1.6;
      word-wrap: break-word; overflow-wrap: break-word; word-break: break-word;
      white-space: pre-wrap;
    }
    .vizara-user {
      align-self: flex-end; background: ${USER_BG}; color: ${USER_TEXT};
      border-bottom-right-radius: 4px;
    }
    .vizara-bot {
      align-self: flex-start; background: ${BOT_BG}; color: ${TEXT_PRIMARY};
      border-bottom-left-radius: 4px; border: 1px solid ${BORDER};
    }
    .vizara-escalated-tag {
      display: inline-block; margin-top: 6px; padding: 3px 8px;
      background: rgba(255, 179, 0, 0.15); color: #fbbf24; border-radius: 6px;
      font-size: 12px; font-weight: 500;
    }
    #vizara-input-area {
      display: flex; gap: 8px; padding: 12px 14px;
      border-top: 1px solid ${BORDER}; background: ${VOID_BLACK};
    }
    #vizara-input {
      flex: 1; padding: 10px 14px; border: 1px solid ${BORDER}; border-radius: 24px;
      font-size: 14px; outline: none; font-family: inherit;
      background: ${SURFACE_LIGHT}; color: ${TEXT_PRIMARY};
    }
    #vizara-input::placeholder { color: ${TEXT_SECONDARY}; }
    #vizara-input:focus { border-color: ${NEBULA_PURPLE}; }
    #vizara-send {
      width: 38px; height: 38px; border-radius: 50%; background: ${NEBULA_PURPLE};
      display: flex; align-items: center; justify-content: center;
      flex-shrink: 0; transition: background 0.15s, opacity 0.15s;
    }
    #vizara-send:hover { background: #6d28d9; }
    #vizara-send:disabled { opacity: 0.4; cursor: not-allowed; }
    .vizara-typing {
      align-self: flex-start;
      background: ${BOT_BG}; color: ${TEXT_SECONDARY};
      padding: 10px 14px; border-radius: 14px; font-size: 14px;
      border-bottom-left-radius: 4px; border: 1px solid ${BORDER};
    }
  `;

  const SESSION_ID = "viz_" + Math.random().toString(36).slice(2, 10) + "_" + Date.now();
  let open = false;

  const CHAT_ICON = '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/></svg>';
  const SEND_ICON = '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="22" y1="2" x2="11" y2="13"/><polygon points="22 2 15 22 11 13 2 9 22 2"/></svg>';
  const CLOSE_ICON = '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>';

  function injectStyles() {
    const el = document.createElement("style");
    el.textContent = STYLES;
    document.head.appendChild(el);
  }

  function buildDOM() {
    const root = document.createElement("div");
    root.id = "vizara-widget";

    root.innerHTML =
      '<div id="vizara-panel">' +
        '<div id="vizara-header">' +
          '<div id="vizara-avatar">' + CHAT_ICON.replace('width="24"', 'width="16"').replace('height="24"', 'height="16"') + '</div>' +
          '<div>' +
            '<div id="vizara-header-label">' + BRAND_NAME + ' support</div>' +
            '<div id="vizara-header-sub">Usually responds in minutes</div>' +
          '</div>' +
          '<button id="vizara-close" title="Close">' + CLOSE_ICON + '</button>' +
        '</div>' +
        '<div id="vizara-messages">' +
          '<div class="vizara-msg vizara-bot">Hi! I can help with questions about ' + BRAND_NAME + "'s video ads and product photography. How can I help you?</div>" +
        '</div>' +
        '<div id="vizara-input-area">' +
          '<input id="vizara-input" type="text" placeholder="Type your question..." />' +
          '<button id="vizara-send" title="Send">' + SEND_ICON + '</button>' +
        '</div>' +
      '</div>' +
      '<div id="vizara-bubble" title="Chat with us">' + CHAT_ICON + '</div>';

    document.body.appendChild(root);

    const bubble = root.querySelector("#vizara-bubble");
    const panel = root.querySelector("#vizara-panel");
    const closeBtn = root.querySelector("#vizara-close");
    const input = root.querySelector("#vizara-input");
    const sendBtn = root.querySelector("#vizara-send");
    const messages = root.querySelector("#vizara-messages");

    bubble.addEventListener("click", function () {
      open = true;
      panel.classList.add("open");
      bubble.style.display = "none";
      input.focus();
    });

    closeBtn.addEventListener("click", function () {
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
        body: JSON.stringify({ session_id: SESSION_ID, query: text, client_id: CLIENT_ID }),
      })
        .then(function (r) { return r.json(); })
        .then(function (data) {
          removeTyping();
          appendBotMessage(data.answer, data.escalated);
        })
        .catch(function () {
          removeTyping();
          appendBotMessage("Sorry, something went wrong. Please try again.", false);
        })
        .finally(function () { sendBtn.disabled = false; input.focus(); });
    }

    sendBtn.addEventListener("click", send);
    input.addEventListener("keydown", function (e) { if (e.key === "Enter") send(); });

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
    document.addEventListener("DOMContentLoaded", function () { injectStyles(); buildDOM(); });
  } else {
    injectStyles();
    buildDOM();
  }
})();