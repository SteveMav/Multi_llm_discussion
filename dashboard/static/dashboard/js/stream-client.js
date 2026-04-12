(function() {
  const cockpitRoot = document.getElementById('cockpit-root');
  if (!cockpitRoot) return;

  const streamUrl = cockpitRoot.dataset.streamUrl;
  const logContainer = document.getElementById('event-stream-log');
  const killSwitch = document.getElementById('kill-switch-btn');
  
  // Archteype colors for glow (matched with genetic.py)
  const colors = {
    skeptic: 'rgba(242, 139, 130, 0.4)',
    optimist: 'rgba(165, 255, 184, 0.4)',
    pragmatist: 'rgba(153, 247, 255, 0.4)',
    conservative: 'rgba(253, 214, 99, 0.4)',
    innovator: 'rgba(209, 119, 255, 0.4)',
    moderator: 'rgba(232, 234, 237, 0.4)',
    system: 'rgba(232, 234, 237, 0.4)'
  };

  const labels = {
    skeptic: 'Le Sceptique',
    optimist: 'L\'Optimiste',
    pragmatist: 'Le Pragmatique',
    conservative: 'Le Conservateur',
    innovator: 'L\'Innovateur',
    moderator: 'Modérateur',
    system: 'Système'
  };

  let eventSource = null;
  let activeBlock = null;
  let activeAgentId = null;

  function initStream() {
    eventSource = new EventSource(streamUrl);

    eventSource.onmessage = function(event) {
      try {
        const data = JSON.parse(event.data);
        handleStreamEvent(data);
      } catch (err) {
        console.error("Failed to parse SSE JSON", err, event.data);
      }
    };

    eventSource.onerror = function() {
      // Typically EventSource auto-reconnects, but if we need to abort:
      console.warn("EventSource connection error.");
      appendMessage("system", "system", "[Stream Connection Error] Trying to reconnect...");
    };
    
    // Enable Kill Switch
    if (killSwitch) {
      killSwitch.disabled = false;
      killSwitch.addEventListener('click', showAbortModal);
    }
  }

  function handleStreamEvent(data) {
    if (data.type === 'done') {
      appendMessage("system", "system", "[STREAM FINISHED]");
      closeStream();
      return;
    }

    if (data.type === 'error') {
      appendMessage("error", data.agent_id || "system", `[ERROR] ${data.content}`);
      return;
    }

    // Determine target agent block
    const agentId = data.agent_id || "system";
    
    // If agent switched, create new block
    if (activeAgentId !== agentId || !activeBlock) {
      createActiveBlock(agentId);
    }

    // Generate inner styling based on message type
    const contentSpan = document.createElement('span');
    
    if (data.type === 'thought') {
      contentSpan.className = "text-text-muted font-mono text-sm block mb-1";
      contentSpan.innerHTML = `&gt; ${escapeHtml(data.content)}`;
    } else if (data.type === 'speech') {
      contentSpan.className = "text-text-primary font-sans text-base block mb-2";
      contentSpan.innerHTML = escapeHtml(data.content);
    } else {
      // system / fallback
      contentSpan.className = "text-text-muted font-mono text-xs block mb-1 italic";
      contentSpan.innerHTML = escapeHtml(data.content);
    }

    activeBlock.appendChild(contentSpan);
    autoScroll();
  }

  function createActiveBlock(agentId) {
    // Remove pulse from previous block
    if (activeBlock) {
      activeBlock.classList.remove('pulse-active');
    }

    activeAgentId = agentId;
    activeBlock = document.createElement('div');
    activeBlock.className = "p-3 mb-4 transition-all duration-300 border-l-2 border-transparent surface-2";
    
    // Apply agent-specific styles
    const agentColor = colors[agentId] || colors.system;
    const label = labels[agentId] || agentId.toUpperCase();
    
    activeBlock.classList.add('pulse-active');
    activeBlock.style.setProperty('--pulse-color', agentColor);
    activeBlock.style.borderColor = agentColor.replace(', 0.4)', ', 0.8)');
    
    // Agent Header
    const header = document.createElement('div');
    header.className = "font-headline text-xs uppercase tracking-widest mb-2 flex items-center";
    header.style.color = agentColor.replace(', 0.4)', ', 1)');
    header.innerHTML = `■ ${label}`;
    
    activeBlock.appendChild(header);
    logContainer.appendChild(activeBlock);
  }

  function escapeHtml(unsafe) {
    if (!unsafe) return "";
    return unsafe
         .replace(/&/g, "&amp;")
         .replace(/</g, "&lt;")
         .replace(/>/g, "&gt;")
         .replace(/"/g, "&quot;")
         .replace(/'/g, "&#039;");
  }

  function autoScroll() {
    logContainer.scrollTop = logContainer.scrollHeight;
  }

  function closeStream() {
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }
    if (activeBlock) {
      activeBlock.classList.remove('pulse-active');
      activeBlock = null;
    }
    activeAgentId = null;
    if (killSwitch) {
      killSwitch.disabled = true;
    }
  }
  
  function showAbortModal() {
    const modal = document.getElementById('abort-modal-overlay');
    if (modal) {
      modal.classList.remove('hidden');
      document.getElementById('abort-justification').focus();
    }
  }

  function hideAbortModal() {
    const modal = document.getElementById('abort-modal-overlay');
    if (modal) {
      modal.classList.add('hidden');
      document.getElementById('abort-justification').value = '';
    }
  }

  async function abortStream(justification) {
    const form = document.getElementById('abort-form');
    const sessionId = form.dataset.sessionId;
    const csrfToken = form.querySelector('[name=csrfmiddlewaretoken]').value;
    
    try {
      const response = await fetch(`/api/session/${sessionId}/abort/`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRFToken': csrfToken
        },
        body: JSON.stringify({ justification })
      });
      
      const data = await response.json();
      
      if (!data.success) {
        console.error("Failed to abort session:", data.error);
        alert(data.error);
        return;
      }
      
      hideAbortModal();
      closeStream();
      // Wait a moment for UI to settle, then show message (also engine sends a system error, but we log locally just in case)
      appendMessage("error", "system", `[ABORTED] Session terminated manually. Justification: ${justification}`);
    } catch (err) {
      console.error("Abort request failed", err);
      alert("Erreur de connexion. Impossible d'interrompre la session.");
    }
  }

  // Setup Modal Listeners
  const cancelBtn = document.getElementById('abort-cancel-btn');
  if (cancelBtn) {
    cancelBtn.addEventListener('click', hideAbortModal);
  }

  const abortForm = document.getElementById('abort-form');
  if (abortForm) {
    abortForm.addEventListener('submit', (e) => {
      e.preventDefault();
      const b = document.getElementById('abort-confirm-btn');
      b.disabled = true;
      b.textContent = "WAIT...";
      
      const justification = document.getElementById('abort-justification').value;
      abortStream(justification).finally(() => {
        b.disabled = false;
        b.textContent = "Confirm Abort";
      });
    });
  }

  function appendMessage(type, agentId, text) {
    createActiveBlock(agentId);
    const p = document.createElement('p');
    p.className = (type === 'error') ? "text-error font-mono text-sm" : "text-text-muted font-mono text-sm italic";
    p.textContent = text;
    activeBlock.appendChild(p);
    activeBlock.classList.remove('pulse-active');
    autoScroll();
  }

  // Ensure DOM is ready
  if (streamUrl) {
    initStream();
  } else {
    console.warn("No stream URL defined in data-stream-url");
  }

})();
