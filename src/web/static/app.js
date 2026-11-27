/**
 * INOT Mark1 - Dashboard Controller
 * Real-time WebSocket sync, Web Speech / Groq Whisper audio recording,
 * Audio visualizer canvas, and instant device actuation.
 */

(() => {
  'use strict';

  // --- State Variables ---
  let socket = null;
  let deviceStates = {};
  let enabledDevices = [];
  let isRecording = false;
  let mediaRecorder = null;
  let recordedChunks = [];
  let audioContext = null;
  let analyserNode = null;
  let visualizerAnimId = null;
  let speechRecognizer = null;

  // --- DOM Elements ---
  const elIndMqtt = document.getElementById('indMqtt');
  const elValMqtt = document.getElementById('valMqtt');
  const elIndEsp32 = document.getElementById('indEsp32');
  const elValEsp32 = document.getElementById('valEsp32');
  const elValAi = document.getElementById('valAi');

  const elBtnVoice = document.getElementById('btnVoiceRecord');
  const elMicIcon = document.getElementById('micIconIdle');
  const elMicWave = document.getElementById('micWaveAnimation');
  const elMicStatus = document.getElementById('micStatusText');
  const elVoiceEngine = document.getElementById('selectVoiceEngine');
  const elVisualizerCanvas = document.getElementById('audioVisualizerCanvas');

  const elLiveTranscript = document.getElementById('liveTranscript');
  const elAiReasoningContent = document.getElementById('aiReasoningContent');
  const elLastLatency = document.getElementById('lastCommandLatency');

  const elTextForm = document.getElementById('textCommandForm');
  const elInputQuery = document.getElementById('inputCommandQuery');
  const elDevicesGrid = document.getElementById('devicesGrid');

  const elSpatialText = document.getElementById('spatialTextContent');
  const elSceneImg = document.getElementById('sceneImage');
  const elSceneContainer = document.getElementById('sceneImageContainer');
  const elBtnRefreshSpatial = document.getElementById('btnRefreshSpatial');

  const elActivityTerminal = document.getElementById('activityTerminal');
  const elBtnClearLog = document.getElementById('btnClearLog');

  const elModal = document.getElementById('diagnosticsModal');
  const elBtnDiag = document.getElementById('btnDiagnostics');
  const elBtnCloseModal = document.getElementById('btnCloseModal');

  const elBtnBatchAllOn = document.getElementById('btnBatchAllOn');
  const elBtnBatchAllOff = document.getElementById('btnBatchAllOff');
  const elBtnBatchStudy = document.getElementById('btnBatchStudy');

  // --- Sound Effects using Web Audio API ---
  function playChime(type) {
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain);
      gain.connect(ctx.destination);

      if (type === 'start') {
        osc.frequency.setValueAtTime(440, ctx.currentTime);
        osc.frequency.exponentialRampToValueAtTime(880, ctx.currentTime + 0.12);
        gain.gain.setValueAtTime(0.15, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.15);
        osc.start();
        osc.stop(ctx.currentTime + 0.15);
      } else if (type === 'stop') {
        osc.frequency.setValueAtTime(880, ctx.currentTime);
        osc.frequency.exponentialRampToValueAtTime(440, ctx.currentTime + 0.12);
        gain.gain.setValueAtTime(0.15, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.15);
        osc.start();
        osc.stop(ctx.currentTime + 0.15);
      } else if (type === 'success') {
        osc.frequency.setValueAtTime(587.33, ctx.currentTime); // D5
        osc.frequency.setValueAtTime(880, ctx.currentTime + 0.1); // A5
        gain.gain.setValueAtTime(0.15, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.25);
        osc.start();
        osc.stop(ctx.currentTime + 0.25);
      }
    } catch (_) {}
  }

  // --- Text-to-Speech (TTS) Browser Playback ---
  function speakResponse(text) {
    if (!text || !('speechSynthesis' in window)) return;
    try {
      window.speechSynthesis.cancel();
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.rate = 1.05;
      utterance.pitch = 1.0;
      window.speechSynthesis.speak(utterance);
    } catch (_) {}
  }

  // --- Terminal Logging ---
  function appendLog(category, message) {
    if (!elActivityTerminal) return;
    const now = new Date();
    const timeStr = now.toTimeString().split(' ')[0] + '.' + String(now.getMilliseconds()).padStart(3, '0');
    
    const entry = document.createElement('div');
    entry.className = `terminal-entry ${category}`;
    
    const timeSpan = document.createElement('span');
    timeSpan.className = 'time';
    timeSpan.textContent = `[${timeStr}]`;

    const contentSpan = document.createElement('span');
    contentSpan.className = 'content';
    contentSpan.textContent = `[${category.toUpperCase()}] ${message}`;

    entry.appendChild(timeSpan);
    entry.appendChild(contentSpan);
    elActivityTerminal.appendChild(entry);
    elActivityTerminal.scrollTop = elActivityTerminal.scrollHeight;
  }

  if (elBtnClearLog) {
    elBtnClearLog.addEventListener('click', () => {
      elActivityTerminal.innerHTML = '';
      appendLog('system', 'Terminal log cleared.');
    });
  }

  // --- WebSocket Connection ---
  function initWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws`;

    socket = new WebSocket(wsUrl);

    socket.onopen = () => {
      appendLog('system', 'WebSocket connection established.');
    };

    socket.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        handleSocketMessage(msg);
      } catch (err) {
        console.error('Socket message parse error:', err);
      }
    };

    socket.onclose = () => {
      appendLog('system', 'WebSocket disconnected. Retrying in 2.5s...');
      setTimeout(initWebSocket, 2500);
    };

    socket.onerror = (err) => {
      console.warn('WebSocket error:', err);
    };
  }

  function handleSocketMessage(msg) {
    if (msg.type === 'initial_state') {
      deviceStates = msg.data.states || {};
      enabledDevices = msg.data.enabled_devices || [];
      updateTelemetry(msg.data);
      fetchDevices(); // Refresh grid layout
      appendLog('system', `Initial state received: ${JSON.stringify(deviceStates)}`);
    } else if (msg.type === 'device_state') {
      const { device, state, source, latency_ms } = msg.data;
      deviceStates[device] = state;
      updateDeviceCardUI(device, state);
      const note = latency_ms ? ` (${latency_ms.toFixed(0)} ms)` : '';
      appendLog('ack', `${device} state updated to ${state} via ${source}${note}`);
    } else if (msg.type === 'esp32_status') {
      const st = msg.data.status;
      setEsp32Status(st);
      appendLog('mqtt', `ESP32 status is now '${st}'`);
    } else if (msg.type === 'mqtt_connect') {
      setMqttStatus(msg.data.connected);
      appendLog('mqtt', `MQTT Broker connection: ${msg.data.connected ? 'ONLINE' : 'OFFLINE'}`);
    }
  }

  function updateTelemetry(data) {
    setMqttStatus(data.mqtt_connected);
    setEsp32Status(data.esp32_status);
  }

  function setMqttStatus(connected) {
    if (!elIndMqtt || !elValMqtt) return;
    if (connected) {
      elIndMqtt.className = 'status-indicator online';
      elValMqtt.textContent = 'Connected (1883)';
    } else {
      elIndMqtt.className = 'status-indicator warning';
      elValMqtt.textContent = 'Dry-Run / Standby';
    }
  }

  function setEsp32Status(status) {
    if (!elIndEsp32 || !elValEsp32) return;
    const clean = (status || 'unknown').toLowerCase();
    if (clean === 'online') {
      elIndEsp32.className = 'status-indicator online';
      elValEsp32.textContent = 'Online';
    } else if (clean === 'offline') {
      elIndEsp32.className = 'status-indicator offline';
      elValEsp32.textContent = 'Offline';
    } else {
      elIndEsp32.className = 'status-indicator warning';
      elValEsp32.textContent = clean;
    }
  }

  // --- Device Management & Rendering ---
  async function fetchDevices() {
    try {
      const res = await fetch('/api/devices');
      const data = await res.json();
      renderDeviceCards(data.devices || []);
    } catch (err) {
      appendLog('error', `Failed to load devices: ${err.message}`);
    }
  }

  function renderDeviceCards(devices) {
    if (!elDevicesGrid) return;
    elDevicesGrid.innerHTML = '';

    devices.forEach((dev) => {
      const isStateOn = (dev.state === 'ON');
      const card = document.createElement('div');
      card.className = `device-card ${isStateOn ? 'state-on' : ''}`;
      card.id = `deviceCard-${dev.name}`;

      card.innerHTML = `
        <div class="device-top-bar">
          <div class="device-meta-group">
            <h3 class="device-name">${escapeHtml(dev.label || dev.name)}</h3>
            <span class="device-role-tag">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <circle cx="12" cy="10" r="3"></circle>
                <path d="M12 21.7C17.3 17 20 13 20 10a8 8 0 1 0-16 0c0 3 2.7 7 8 11.7z"></path>
              </svg>
              ${escapeHtml(dev.room || 'Room')} &bull; ${escapeHtml(dev.spatial_position || 'Center')}
            </span>
          </div>

          <div class="device-icon-box">
            ${dev.icon === 'fan' ? getFanSvg() : getLightbulbSvg()}
          </div>
        </div>

        <div class="device-mid-row">
          <div style="display:flex; align-items:center; gap:0.5rem;">
            <span class="state-badge" id="badgeState-${dev.name}">${dev.state}</span>
            <span class="pin-badge">${escapeHtml(dev.pin || 'Pin N/A')}</span>
          </div>

          <label class="toggle-switch" aria-label="Toggle ${escapeHtml(dev.name)}">
            <input type="checkbox" id="toggleInput-${dev.name}" ${isStateOn ? 'checked' : ''}>
            <span class="toggle-slider"></span>
          </label>
        </div>

        <div class="device-bottom-info">
          <span class="topic-label" title="Set Topic">Set: <code>${escapeHtml(dev.topic_set || '')}</code></span>
          <span class="topic-label" title="State Topic">State: <code>${escapeHtml(dev.topic_state || '')}</code></span>
        </div>
      `;

      elDevicesGrid.appendChild(card);

      // Attach toggle switch handler
      const toggleInput = card.querySelector(`#toggleInput-${dev.name}`);
      toggleInput.addEventListener('change', () => {
        handleToggleDevice(dev.name, toggleInput.checked);
      });
    });
  }

  function updateDeviceCardUI(devName, state) {
    const card = document.getElementById(`deviceCard-${devName}`);
    const badge = document.getElementById(`badgeState-${devName}`);
    const toggleInput = document.getElementById(`toggleInput-${devName}`);

    const isStateOn = (state === 'ON');
    if (card) {
      if (isStateOn) {
        card.classList.add('state-on');
      } else {
        card.classList.remove('state-on');
      }
    }
    if (badge) {
      badge.textContent = state;
    }
    if (toggleInput) {
      toggleInput.checked = isStateOn;
    }
  }

  async function handleToggleDevice(devName, targetOn) {
    const targetCmd = targetOn ? 'On' : 'Off';
    appendLog('mqtt', `Sending toggle for ${devName} ➔ ${targetCmd}...`);

    try {
      const res = await fetch(`/api/device/${encodeURIComponent(devName)}/toggle`, {
        method: 'POST'
      });
      const data = await res.json();
      if (data.success) {
        playChime('success');
        updateDeviceCardUI(data.device, data.new_state);
        appendLog('mqtt', `Toggle success: ${data.device} is now ${data.new_state}`);
      } else {
        appendLog('error', `Toggle rejected for ${devName}`);
      }
    } catch (err) {
      appendLog('error', `Toggle API error: ${err.message}`);
    }
  }

  // --- Batch Actions ---
  if (elBtnBatchAllOn) {
    elBtnBatchAllOn.addEventListener('click', async () => {
      appendLog('mqtt', 'Executing batch ALL ON...');
      await fetch('/api/devices/batch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'all_on' })
      });
      playChime('success');
      fetchDevices();
    });
  }

  if (elBtnBatchAllOff) {
    elBtnBatchAllOff.addEventListener('click', async () => {
      appendLog('mqtt', 'Executing batch ALL OFF...');
      await fetch('/api/devices/batch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'all_off' })
      });
      playChime('stop');
      fetchDevices();
    });
  }

  if (elBtnBatchStudy) {
    elBtnBatchStudy.addEventListener('click', async () => {
      appendLog('mqtt', 'Applying Study Mode preset (Desk Light ON)...');
      await fetch('/api/devices/batch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: 'study_mode' })
      });
      playChime('success');
      fetchDevices();
    });
  }

  // --- Audio Visualizer ---
  function setupVisualizer(stream) {
    try {
      if (!audioContext) {
        audioContext = new (window.AudioContext || window.webkitAudioContext)();
      }
      analyserNode = audioContext.createAnalyser();
      analyserNode.fftSize = 64;

      const source = audioContext.createMediaStreamSource(stream);
      source.connect(analyserNode);

      drawVisualizer();
    } catch (e) {
      console.warn('Visualizer setup error:', e);
    }
  }

  function drawVisualizer() {
    if (!elVisualizerCanvas || !analyserNode) return;
    const ctx = elVisualizerCanvas.getContext('2d');
    const width = elVisualizerCanvas.width;
    const height = elVisualizerCanvas.height;

    const bufferLength = analyserNode.frequencyBinCount;
    const dataArray = new Uint8Array(bufferLength);

    function renderFrame() {
      if (!isRecording) {
        ctx.clearRect(0, 0, width, height);
        return;
      }
      visualizerAnimId = requestAnimationFrame(renderFrame);

      analyserNode.getByteFrequencyData(dataArray);

      ctx.clearRect(0, 0, width, height);
      const barWidth = (width / bufferLength) * 1.8;
      let x = 0;

      for (let i = 0; i < bufferLength; i++) {
        const barHeight = (dataArray[i] / 255) * height * 0.9 + 4;
        
        // Gradient from cyan to violet
        const grad = ctx.createLinearGradient(0, height, 0, height - barHeight);
        grad.addColorStop(0, '#00f2fe');
        grad.addColorStop(1, '#8b5cf6');

        ctx.fillStyle = grad;
        ctx.fillRect(x, height - barHeight, barWidth - 2, barHeight);

        x += barWidth;
      }
    }

    renderFrame();
  }

  function simulateVisualizer() {
    if (!elVisualizerCanvas) return;
    const ctx = elVisualizerCanvas.getContext('2d');
    const width = elVisualizerCanvas.width;
    const height = elVisualizerCanvas.height;

    function renderSimFrame() {
      if (!isRecording) {
        ctx.clearRect(0, 0, width, height);
        return;
      }
      visualizerAnimId = requestAnimationFrame(renderSimFrame);

      ctx.clearRect(0, 0, width, height);
      const bars = 24;
      const barWidth = width / bars;

      for (let i = 0; i < bars; i++) {
        const barHeight = Math.random() * (height * 0.75) + 6;
        const grad = ctx.createLinearGradient(0, height, 0, height - barHeight);
        grad.addColorStop(0, '#00f2fe');
        grad.addColorStop(1, '#8b5cf6');

        ctx.fillStyle = grad;
        ctx.fillRect(i * barWidth, height - barHeight, barWidth - 3, barHeight);
      }
    }
    renderSimFrame();
  }

  // --- Voice Control Logic ---
  function setMicState(state) {
    if (state === 'recording') {
      isRecording = true;
      elBtnVoice.classList.add('recording');
      elBtnVoice.classList.remove('processing');
      elMicIcon.classList.add('hidden');
      elMicWave.classList.remove('hidden');
      elMicStatus.textContent = 'Listening... Speak your command';
      elMicStatus.style.color = 'var(--neon-rose)';
    } else if (state === 'processing') {
      isRecording = false;
      elBtnVoice.classList.remove('recording');
      elBtnVoice.classList.add('processing');
      elMicIcon.classList.remove('hidden');
      elMicWave.classList.add('hidden');
      elMicStatus.textContent = 'Thinking with Groq Qwen...';
      elMicStatus.style.color = 'var(--neon-blue)';
    } else {
      isRecording = false;
      elBtnVoice.classList.remove('recording');
      elBtnVoice.classList.remove('processing');
      elMicIcon.classList.remove('hidden');
      elMicWave.classList.add('hidden');
      elMicStatus.textContent = 'Click to Speak';
      elMicStatus.style.color = 'var(--neon-cyan)';
      if (visualizerAnimId) cancelAnimationFrame(visualizerAnimId);
    }
  }

  // Option 1: Browser Web Speech API (Instant & Interactive)
  function startBrowserSpeechRecognition() {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
      appendLog('error', 'Browser Speech Recognition not supported. Falling back to Groq Whisper.');
      elVoiceEngine.value = 'whisper';
      startMediaRecorderSpeech();
      return;
    }

    speechRecognizer = new SpeechRecognition();
    speechRecognizer.lang = 'en-US';
    speechRecognizer.continuous = false;
    speechRecognizer.interimResults = true;

    speechRecognizer.onstart = () => {
      setMicState('recording');
      playChime('start');
      simulateVisualizer();
      appendLog('voice', 'Microphone active (Browser Web Speech API).');
    };

    speechRecognizer.onresult = (event) => {
      let interim = '';
      let final = '';
      for (let i = event.resultIndex; i < event.results.length; ++i) {
        if (event.results[i].isFinal) {
          final += event.results[i][0].transcript;
        } else {
          interim += event.results[i][0].transcript;
        }
      }
      elLiveTranscript.textContent = `"${final || interim}"`;
      if (final) {
        speechRecognizer.stop();
        executeNaturalLanguageCommand(final.trim());
      }
    };

    speechRecognizer.onerror = (event) => {
      setMicState('idle');
      appendLog('error', `Speech recognition error: ${event.error}`);
    };

    speechRecognizer.onend = () => {
      if (isRecording) setMicState('idle');
    };

    try {
      speechRecognizer.start();
    } catch (err) {
      console.warn('SpeechRecognizer start error:', err);
    }
  }

  // Option 2: Browser MediaRecorder -> Backend Groq Whisper API
  async function startMediaRecorderSpeech() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      appendLog('error', 'Microphone recording not supported on this browser.');
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      setupVisualizer(stream);

      recordedChunks = [];
      mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });

      mediaRecorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) {
          recordedChunks.push(e.data);
        }
      };

      mediaRecorder.onstart = () => {
        setMicState('recording');
        playChime('start');
        appendLog('voice', 'Recording audio for Groq Whisper transcription...');
      };

      mediaRecorder.onstop = async () => {
        setMicState('processing');
        playChime('stop');
        stream.getTracks().forEach(track => track.stop());

        const audioBlob = new Blob(recordedChunks, { type: 'audio/webm' });
        await uploadAudioForTranscription(audioBlob);
      };

      mediaRecorder.start();

      // Automatically stop after 5 seconds to match transcriptor.py
      setTimeout(() => {
        if (mediaRecorder && mediaRecorder.state === 'recording') {
          mediaRecorder.stop();
        }
      }, 5000);

    } catch (err) {
      appendLog('error', `Microphone access error: ${err.message}`);
      setMicState('idle');
    }
  }

  async function uploadAudioForTranscription(blob) {
    appendLog('voice', 'Uploading audio to Groq Whisper Large-v3...');
    const formData = new FormData();
    formData.append('audio', blob, 'command_audio.webm');

    try {
      const res = await fetch('/api/voice/process-audio', {
        method: 'POST',
        body: formData
      });
      const data = await res.json();
      handleCommandResponse(data);
    } catch (err) {
      appendLog('error', `Voice upload failed: ${err.message}`);
      setMicState('idle');
    }
  }

  // Option 3: Host Machine Server Mic (PyAudio)
  async function triggerServerMic() {
    setMicState('recording');
    playChime('start');
    simulateVisualizer();
    appendLog('voice', 'Host machine microphone recording for 5s...');

    try {
      const res = await fetch('/api/voice/server-mic', { method: 'POST' });
      const data = await res.json();
      handleCommandResponse(data);
    } catch (err) {
      appendLog('error', `Server mic error: ${err.message}`);
      setMicState('idle');
    }
  }

  // Mic Button Click Dispatcher
  if (elBtnVoice) {
    elBtnVoice.addEventListener('click', () => {
      if (isRecording) {
        // Stop current recording
        if (speechRecognizer) speechRecognizer.stop();
        if (mediaRecorder && mediaRecorder.state === 'recording') mediaRecorder.stop();
        setMicState('processing');
        return;
      }

      const engine = elVoiceEngine ? elVoiceEngine.value : 'browser';
      if (engine === 'browser') {
        startBrowserSpeechRecognition();
      } else if (engine === 'whisper') {
        startMediaRecorderSpeech();
      } else if (engine === 'server') {
        triggerServerMic();
      }
    });
  }

  // --- Natural Language Command Execution ---
  async function executeNaturalLanguageCommand(query) {
    if (!query || !query.trim()) return;

    setMicState('processing');
    elLiveTranscript.textContent = `"${query}"`;
    appendLog('voice', `Prompt: "${query}"`);

    try {
      const res = await fetch('/api/command', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: query.trim() })
      });
      const data = await res.json();
      handleCommandResponse(data);
    } catch (err) {
      appendLog('error', `Command failed: ${err.message}`);
      setMicState('idle');
    }
  }

  function handleCommandResponse(data) {
    setMicState('idle');
    playChime('success');

    if (data.query) {
      elLiveTranscript.textContent = `"${data.query}"`;
    }

    if (data.duration_ms && elLastLatency) {
      elLastLatency.textContent = `⚡ ${Math.round(data.duration_ms)}ms`;
    }

    // Format AI Decision Output
    if (elAiReasoningContent) {
      let html = '';
      if (data.valid_commands && Object.keys(data.valid_commands).length > 0) {
        html += '<div style="margin-bottom:0.4rem; color:var(--neon-emerald); font-weight:600;">Action Plan:</div>';
        for (const [dev, cmd] of Object.entries(data.valid_commands)) {
          html += `<div>&bull; <strong>${escapeHtml(dev)}</strong> ➔ <span style="color:${cmd === 'On' ? 'var(--neon-cyan)' : 'var(--text-dim)'};">${cmd}</span></div>`;
        }
      } else {
        html = '<div style="color:var(--text-muted);">No appliance state modifications required.</div>';
      }

      if (data.raw_response) {
        html += `<div style="margin-top:0.5rem; font-size:0.75rem; color:var(--text-dim); border-top:1px dashed rgba(255,255,255,0.06); padding-top:0.35rem;">LLM: ${escapeHtml(data.raw_response)}</div>`;
      }

      elAiReasoningContent.innerHTML = html;
    }

    // Speak audio feedback
    if (data.tts_message) {
      speakResponse(data.tts_message);
      appendLog('ack', `TTS: "${data.tts_message}"`);
    }

    // Refresh devices if states returned
    if (data.device_states) {
      for (const [d, st] of Object.entries(data.device_states)) {
        updateDeviceCardUI(d, st);
      }
    } else {
      fetchDevices();
    }
  }

  // --- Text Command Form Submission ---
  if (elTextForm) {
    elTextForm.addEventListener('submit', (e) => {
      e.preventDefault();
      const val = elInputQuery ? elInputQuery.value.trim() : '';
      if (val) {
        executeNaturalLanguageCommand(val);
        elInputQuery.value = '';
      }
    });
  }

  // Prompt Chips Click Handlers
  document.querySelectorAll('.prompt-chip').forEach((chip) => {
    chip.addEventListener('click', () => {
      const cmd = chip.getAttribute('data-cmd');
      if (cmd) {
        executeNaturalLanguageCommand(cmd);
      }
    });
  });

  // --- Spatial Perception Viewer ---
  async function loadSpatialInfo() {
    try {
      const res = await fetch('/api/spatial');
      const data = await res.json();
      if (elSpatialText) {
        elSpatialText.textContent = data.spatial_text || 'No spatial context available.';
      }
      if (elSceneImg && data.image_url) {
        elSceneImg.src = data.image_url + '?t=' + Date.now();
        if (elSceneContainer) elSceneContainer.classList.remove('no-image');
      }
      appendLog('system', 'Spatial context synchronized.');
    } catch (err) {
      appendLog('error', `Failed to load spatial info: ${err.message}`);
    }
  }

  if (elBtnRefreshSpatial) {
    elBtnRefreshSpatial.addEventListener('click', loadSpatialInfo);
  }

  // --- Diagnostics Modal ---
  if (elBtnDiag && elModal) {
    elBtnDiag.addEventListener('click', () => elModal.classList.remove('hidden'));
  }
  if (elBtnCloseModal && elModal) {
    elBtnCloseModal.addEventListener('click', () => elModal.classList.add('hidden'));
  }
  if (elModal) {
    elModal.addEventListener('click', (e) => {
      if (e.target === elModal) elModal.classList.add('hidden');
    });
  }

  // --- Utility SVG Icons ---
  function getLightbulbSvg() {
    return `
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M9 18h6"></path>
        <path d="M10 22h4"></path>
        <path d="M12 2a7 7 0 0 0-7 7c0 3 2 5.5 4 6.5V17h6v-1.5c2-1 4-3.5 4-6.5a7 7 0 0 0-7-7z"></path>
      </svg>
    `;
  }

  function getFanSvg() {
    return `
      <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <circle cx="12" cy="12" r="3"></circle>
        <path d="M12 9C12 5.5 14.5 3 18 3c0 3.5-2.5 6-6 6z"></path>
        <path d="M15 12c3.5 0 6 2.5 6 6 0-3.5-2.5-6-6-6z"></path>
        <path d="M12 15c0 3.5-2.5 6-6 6 0-3.5 2.5-6 6-6z"></path>
        <path d="M9 12C5.5 12 3 9.5 3 6c3.5 0 6 2.5 6 6z"></path>
      </svg>
    `;
  }

  function escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  // --- Application Bootstrapping ---
  function init() {
    initWebSocket();
    fetchDevices();
    loadSpatialInfo();
    appendLog('system', 'INOT Mark1 Client Controller ready.');
  }

  document.addEventListener('DOMContentLoaded', init);
})();
