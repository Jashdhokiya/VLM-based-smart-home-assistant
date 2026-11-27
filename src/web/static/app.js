/**
 * INOT Mark1 — Dashboard Controller
 * Handles dynamic device cards, WebSocket sync, room onboarding workflow,
 * voice/text commands, and system diagnostics.
 */

(() => {
  'use strict';

  const VALID_TYPES = ['light', 'fan', 'ac', 'tv', 'curtain', 'lock'];

  // --- State ---
  let socket = null;
  let deviceStates = {};
  let currentDevices = [];
  let deviceCardMap = new Map(); // lowercase name or id -> { card, badge, toggle }
  let activeRoomName = 'Room';

  let isRecording = false;
  let mediaRecorder = null;
  let recordedChunks = [];
  let audioContext = null;
  let analyserNode = null;
  let visualizerAnimId = null;
  let speechRecognizer = null;

  let selectedImageFile = null;
  let draftContext = null;
  let draftViewMode = 'table'; // 'table' | 'json'

  // --- DOM Elements ---
  const elIndMqtt = document.getElementById('indMqtt');
  const elValMqtt = document.getElementById('valMqtt');
  const elIndEsp32 = document.getElementById('indEsp32');
  const elValEsp32 = document.getElementById('valEsp32');
  const elIndAi = document.getElementById('indAi');
  const elValAi = document.getElementById('valAi');
  const elIndOnboard = document.getElementById('indOnboard');
  const elValOnboard = document.getElementById('valOnboard');

  const elRoomTitleBadge = document.getElementById('roomTitleBadge');
  const elDevicesGrid = document.getElementById('devicesGrid');
  const elBtnBatchAllOn = document.getElementById('btnBatchAllOn');
  const elBtnBatchAllOff = document.getElementById('btnBatchAllOff');
  const elBtnBatchStudy = document.getElementById('btnBatchStudy');
  const elBtnRefreshDevices = document.getElementById('btnRefreshDevices');

  const elBtnVoice = document.getElementById('btnVoiceRecord');
  const elMicBtnLabel = document.getElementById('micBtnLabel');
  const elMicStatus = document.getElementById('micStatusText');
  const elVoiceEngine = document.getElementById('selectVoiceEngine');
  const elVisualizerCanvas = document.getElementById('audioVisualizerCanvas');
  const elLiveTranscript = document.getElementById('liveTranscript');
  const elAiReasoningContent = document.getElementById('aiReasoningContent');
  const elLastLatency = document.getElementById('lastCommandLatency');
  const elTextForm = document.getElementById('textCommandForm');
  const elInputQuery = document.getElementById('inputCommandQuery');
  const elQuickChipsList = document.getElementById('quickChipsList');

  const elSpatialText = document.getElementById('spatialTextContent');
  const elSceneImg = document.getElementById('sceneImage');
  const elSceneContainer = document.getElementById('sceneImageContainer');
  const elBtnRefreshSpatial = document.getElementById('btnRefreshSpatial');

  const elActivityTerminal = document.getElementById('activityTerminal');
  const elBtnClearLog = document.getElementById('btnClearLog');

  // Onboarding elements
  const elOnboardReadinessBadge = document.getElementById('onboardReadinessBadge');
  const elBtnLoadDraft = document.getElementById('btnLoadDraft');
  const elOnboardDropzone = document.getElementById('onboardDropzone');
  const elOnboardImageInput = document.getElementById('onboardImageInput');
  const elOnboardPreviewImg = document.getElementById('onboardPreviewImg');
  const elOnboardDropPlaceholder = document.getElementById('onboardDropPlaceholder');
  const elOnboardFileMeta = document.getElementById('onboardFileMeta');
  const elOnboardRoomName = document.getElementById('onboardRoomName');
  const elOnboardProvider = document.getElementById('onboardProvider');
  const elOnboardDeviceHints = document.getElementById('onboardDeviceHints');
  const elBtnOnboardPreview = document.getElementById('btnOnboardPreview');
  const elBtnOnboardDirect = document.getElementById('btnOnboardDirect');
  const elOnboardAlert = document.getElementById('onboardAlert');

  const elDraftRoomName = document.getElementById('draftRoomName');
  const elBtnViewTable = document.getElementById('btnViewTable');
  const elBtnViewJson = document.getElementById('btnViewJson');
  const elBtnAddDeviceRow = document.getElementById('btnAddDeviceRow');
  const elDraftTableWrap = document.getElementById('draftTableWrap');
  const elDraftDevicesBody = document.getElementById('draftDevicesBody');
  const elDraftJsonWrap = document.getElementById('draftJsonWrap');
  const elDraftJsonEditor = document.getElementById('draftJsonEditor');
  const elDraftValidationMsg = document.getElementById('draftValidationMsg');
  const elBtnValidateDraft = document.getElementById('btnValidateDraft');
  const elBtnApplyDraft = document.getElementById('btnApplyDraft');

  // Diagnostics modal
  const elModal = document.getElementById('diagnosticsModal');
  const elBtnDiag = document.getElementById('btnDiagnostics');
  const elBtnCloseModal = document.getElementById('btnCloseModal');
  const elDiagSummaryGrid = document.getElementById('diagSummaryGrid');
  const elDiagDevicesBody = document.getElementById('diagDevicesBody');

  // --- Helpers ---
  function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

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

  async function readErrorDetail(res) {
    try {
      const data = await res.json();
      if (typeof data.detail === 'string') return data.detail;
      if (data.detail && data.detail.message) {
        const errs = Array.isArray(data.detail.errors) ? ` (${data.detail.errors.join('; ')})` : '';
        return `${data.detail.message}${errs}`;
      }
      if (data.detail) return JSON.stringify(data.detail);
      if (data.message) return data.message;
    } catch (_) {}
    return `HTTP ${res.status} ${res.statusText}`;
  }

  if (elBtnClearLog) {
    elBtnClearLog.addEventListener('click', () => {
      elActivityTerminal.innerHTML = '';
      appendLog('system', 'Log cleared.');
    });
  }

  // --- WebSocket ---
  function initWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws`;

    socket = new WebSocket(wsUrl);

    socket.onopen = () => {
      appendLog('system', 'WebSocket connected.');
    };

    socket.onmessage = (event) => {
      try {
        handleSocketMessage(JSON.parse(event.data));
      } catch (err) {
        console.error('WebSocket message parse error:', err);
      }
    };

    socket.onclose = () => {
      appendLog('system', 'WebSocket disconnected. Reconnecting in 2.5s...');
      setTimeout(initWebSocket, 2500);
    };

    socket.onerror = (err) => {
      console.warn('WebSocket error:', err);
    };
  }

  function handleSocketMessage(msg) {
    if (msg.type === 'initial_state') {
      deviceStates = msg.data.states || {};
      setMqttStatus(msg.data.mqtt_connected);
      setEsp32Status(msg.data.esp32_status);
      fetchDevices();
      appendLog('system', `Synced ${Object.keys(deviceStates).length} device state(s).`);
    } else if (msg.type === 'device_state') {
      const { device, state, source, latency_ms } = msg.data;
      deviceStates[device] = state;
      updateDeviceCardUI(device, state);
      const note = latency_ms ? ` (${latency_ms.toFixed(0)} ms)` : '';
      appendLog('ack', `${device} -> ${state} (${source}${note})`);
    } else if (msg.type === 'esp32_status') {
      setEsp32Status(msg.data.status);
      appendLog('mqtt', `ESP32 status: ${msg.data.status}`);
    } else if (msg.type === 'mqtt_connect') {
      setMqttStatus(msg.data.connected);
      appendLog('mqtt', `MQTT broker: ${msg.data.connected ? 'connected' : 'disconnected'}`);
    } else if (msg.type === 'context_updated') {
      appendLog('system', `Room context updated (${msg.data.device_count || 0} devices). Refreshing UI...`);
      refreshAllContextViews();
    }
  }

  // --- Telemetry ---
  async function fetchSystemStatus() {
    try {
      const res = await fetch('/api/status');
      if (!res.ok) return;
      const data = await res.json();
      setMqttStatus(Boolean(data.mqtt && data.mqtt.connected), data.mqtt && data.mqtt.dry_run);
      setEsp32Status(data.esp32 && data.esp32.status);
      if (data.ai && elValAi) {
        elValAi.textContent = `${data.ai.llm_backend || 'llm'}: ${data.ai.llm_model || 'default'}`;
        if (elIndAi) elIndAi.className = 'status-dot online';
      }
    } catch (err) {
      appendLog('error', `Failed to fetch /api/status: ${err.message}`);
    }
  }

  function setMqttStatus(connected, dryRun) {
    if (!elIndMqtt || !elValMqtt) return;
    if (connected) {
      elIndMqtt.className = 'status-dot online';
      elValMqtt.textContent = 'Connected';
    } else if (dryRun) {
      elIndMqtt.className = 'status-dot warning';
      elValMqtt.textContent = 'Dry-Run';
    } else {
      elIndMqtt.className = 'status-dot warning';
      elValMqtt.textContent = 'Standby';
    }
  }

  function setEsp32Status(status) {
    if (!elIndEsp32 || !elValEsp32) return;
    const clean = (status || 'unknown').toLowerCase();
    if (clean === 'online') {
      elIndEsp32.className = 'status-dot online';
      elValEsp32.textContent = 'Online';
    } else if (clean === 'offline') {
      elIndEsp32.className = 'status-dot offline';
      elValEsp32.textContent = 'Offline';
    } else {
      elIndEsp32.className = 'status-dot warning';
      elValEsp32.textContent = clean;
    }
  }

  // --- Devices & Dynamic Rendering ---
  async function fetchDevices() {
    try {
      const [devRes, ctxRes] = await Promise.all([
        fetch('/api/devices'),
        fetch('/context').catch(() => null),
      ]);
      if (!devRes.ok) throw new Error(await readErrorDetail(devRes));
      const data = await devRes.json();
      currentDevices = data.devices || [];

      if (ctxRes && ctxRes.ok) {
        const ctxData = await ctxRes.json();
        activeRoomName = ctxData.room_name || 'Room';
      } else {
        activeRoomName = 'Default';
      }
      if (elRoomTitleBadge) {
        elRoomTitleBadge.textContent = `${activeRoomName} (${currentDevices.length})`;
      }

      renderDeviceCards(currentDevices);
      renderQuickCommandChips(currentDevices);
    } catch (err) {
      appendLog('error', `Failed to load devices: ${err.message}`);
    }
  }

  function renderDeviceCards(devices) {
    if (!elDevicesGrid) return;
    elDevicesGrid.innerHTML = '';
    deviceCardMap.clear();

    if (!devices.length) {
      elDevicesGrid.innerHTML = '<div class="empty-state">No devices registered. Upload a room photo below to onboard devices.</div>';
      return;
    }

    devices.forEach((dev) => {
      const stateStr = (dev.state || 'OFF').toUpperCase();
      const isStateOn = stateStr === 'ON';
      const isEnabled = dev.enabled !== false;
      const devType = (dev.type || dev.icon || 'light').toLowerCase();
      const nearList = Array.isArray(dev.near) ? dev.near : [];
      const coordText =
        typeof dev.x === 'number' && typeof dev.y === 'number'
          ? `(${dev.x.toFixed(2)}, ${dev.y.toFixed(2)})`
          : (dev.spatial_position || '—');

      const card = document.createElement('div');
      card.className = `device-card ${isStateOn ? 'state-on' : ''} ${isEnabled ? '' : 'is-disabled'}`;
      card.dataset.deviceName = dev.name;
      card.dataset.deviceId = dev.id;

      const nearChips = nearList
        .map((n) => `<span class="tag-chip">near:${escapeHtml(n)}</span>`)
        .join('');

      card.innerHTML = `
        <div class="device-top-bar">
          <div>
            <h3 class="device-name">${escapeHtml(dev.label || dev.name)}</h3>
            <div class="device-sub">
              <span>${escapeHtml(dev.id)}</span>
              <span>&bull;</span>
              <span>${escapeHtml(devType)}</span>
              <span>&bull;</span>
              <span>zone: ${escapeHtml(dev.zone || dev.room || 'room')}</span>
            </div>
          </div>
          <div class="device-icon-box" title="${escapeHtml(devType)}">
            ${getDeviceIconSvg(devType)}
          </div>
        </div>

        <div class="device-tags">
          <span class="tag-chip">pos ${escapeHtml(coordText)}</span>
          ${nearChips}
        </div>

        <div class="device-mid-row">
          <div class="badge-group">
            <span class="state-badge">${escapeHtml(stateStr)}</span>
            ${isEnabled ? '' : '<span class="disabled-badge">Disabled</span>'}
          </div>
          <label class="toggle-switch" title="${isEnabled ? 'Toggle device' : 'Device not in ENABLED_DEVICES'}">
            <input type="checkbox" ${isStateOn ? 'checked' : ''} ${isEnabled ? '' : 'disabled'}>
            <span class="toggle-slider"></span>
          </label>
        </div>

        <div class="device-bottom-info">
          <span class="topic-label" title="${escapeHtml(dev.topic_set || '')}">Set: ${escapeHtml(dev.topic_set || '')}</span>
          <span class="topic-label" title="${escapeHtml(dev.topic_state || '')}">State: ${escapeHtml(dev.topic_state || '')}</span>
        </div>
      `;

      const badge = card.querySelector('.state-badge');
      const toggleInput = card.querySelector('input[type="checkbox"]');
      toggleInput.addEventListener('change', () => {
        handleToggleDevice(dev.name, toggleInput.checked, toggleInput);
      });

      const entry = { card, badge, toggle: toggleInput };
      if (dev.name) deviceCardMap.set(dev.name.toLowerCase(), entry);
      if (dev.id) deviceCardMap.set(dev.id.toLowerCase(), entry);

      elDevicesGrid.appendChild(card);
    });
  }

  function updateDeviceCardUI(devKey, state) {
    if (!devKey) return;
    const entry = deviceCardMap.get(String(devKey).toLowerCase());
    if (!entry) return;
    const norm = String(state || 'OFF').toUpperCase();
    const isOn = norm === 'ON';
    entry.card.classList.toggle('state-on', isOn);
    if (entry.badge) entry.badge.textContent = norm;
    if (entry.toggle) entry.toggle.checked = isOn;
  }

  async function handleToggleDevice(devName, targetOn, toggleEl) {
    const targetCmd = targetOn ? 'On' : 'Off';
    appendLog('mqtt', `Toggling ${devName} -> ${targetCmd}...`);
    try {
      const res = await fetch(`/api/device/${encodeURIComponent(devName)}/toggle`, {
        method: 'POST',
      });
      if (!res.ok) throw new Error(await readErrorDetail(res));
      const data = await res.json();
      if (data.success) {
        updateDeviceCardUI(data.device, data.new_state);
        appendLog('mqtt', `${data.device} is now ${data.new_state}`);
      } else {
        if (toggleEl) toggleEl.checked = !targetOn;
        appendLog('error', `Toggle rejected for ${devName}`);
      }
    } catch (err) {
      if (toggleEl) toggleEl.checked = !targetOn;
      appendLog('error', `Toggle failed: ${err.message}`);
    }
  }

  function renderQuickCommandChips(devices) {
    if (!elQuickChipsList) return;
    const cmds = [];
    const seen = new Set();
    const addCmd = (label, cmd) => {
      if (seen.has(cmd)) return;
      seen.add(cmd);
      cmds.push({ label, cmd });
    };

    devices.slice(0, 3).forEach((d) => {
      addCmd(`Turn on ${d.name}`, `turn on ${d.name.toLowerCase()}`);
    });

    const zones = [...new Set(devices.map((d) => d.zone).filter((z) => z && z !== 'room'))];
    zones.slice(0, 2).forEach((z) => {
      addCmd(`Turn on ${z}`, `turn on ${z}`);
    });

    addCmd('All lights on', 'turn on all lights');
    addCmd('Everything off', 'turn off everything');
    addCmd('Going to sleep', 'I am going to sleep');

    elQuickChipsList.innerHTML = '';
    cmds.forEach(({ label, cmd }) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'prompt-chip';
      btn.textContent = label;
      btn.addEventListener('click', () => executeNaturalLanguageCommand(cmd));
      elQuickChipsList.appendChild(btn);
    });
  }

  // Batch controls
  async function runBatchAction(action, label) {
    appendLog('mqtt', `Running batch action: ${label}...`);
    try {
      const res = await fetch('/api/devices/batch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action }),
      });
      if (!res.ok) throw new Error(await readErrorDetail(res));
      const data = await res.json();
      if (data.states) {
        Object.entries(data.states).forEach(([dev, st]) => updateDeviceCardUI(dev, st));
      } else {
        await fetchDevices();
      }
      appendLog('mqtt', `Batch ${label} completed.`);
    } catch (err) {
      appendLog('error', `Batch ${label} failed: ${err.message}`);
    }
  }

  if (elBtnBatchAllOn) elBtnBatchAllOn.addEventListener('click', () => runBatchAction('all_on', 'All On'));
  if (elBtnBatchAllOff) elBtnBatchAllOff.addEventListener('click', () => runBatchAction('all_off', 'All Off'));
  if (elBtnBatchStudy) elBtnBatchStudy.addEventListener('click', () => runBatchAction('study_mode', 'Study Mode'));
  if (elBtnRefreshDevices) elBtnRefreshDevices.addEventListener('click', fetchDevices);

  // --- Audio Visualizer & Voice ---
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
    } catch (_) {}
  }

  function drawVisualizer() {
    if (!elVisualizerCanvas || !analyserNode) return;
    const ctx = elVisualizerCanvas.getContext('2d');
    const { width, height } = elVisualizerCanvas;
    const dataArray = new Uint8Array(analyserNode.frequencyBinCount);

    function render() {
      if (!isRecording) {
        ctx.clearRect(0, 0, width, height);
        return;
      }
      visualizerAnimId = requestAnimationFrame(render);
      analyserNode.getByteFrequencyData(dataArray);
      ctx.clearRect(0, 0, width, height);
      const barWidth = Math.max(2, width / dataArray.length);
      ctx.fillStyle = '#2563eb';
      for (let i = 0; i < dataArray.length; i++) {
        const h = Math.max(2, (dataArray[i] / 255) * height);
        ctx.fillRect(i * barWidth, height - h, barWidth - 1, h);
      }
    }
    render();
  }

  function setMicState(state) {
    if (!elBtnVoice) return;
    if (state === 'recording') {
      isRecording = true;
      elBtnVoice.classList.add('recording');
      elBtnVoice.classList.remove('processing');
      if (elMicBtnLabel) elMicBtnLabel.textContent = 'Stop';
      if (elMicStatus) elMicStatus.textContent = 'Listening...';
    } else if (state === 'processing') {
      isRecording = false;
      elBtnVoice.classList.remove('recording');
      elBtnVoice.classList.add('processing');
      if (elMicBtnLabel) elMicBtnLabel.textContent = 'Processing...';
      if (elMicStatus) elMicStatus.textContent = 'Processing command...';
    } else {
      isRecording = false;
      elBtnVoice.classList.remove('recording', 'processing');
      if (elMicBtnLabel) elMicBtnLabel.textContent = 'Start Voice';
      if (elMicStatus) elMicStatus.textContent = 'Ready';
      if (visualizerAnimId) cancelAnimationFrame(visualizerAnimId);
      if (elVisualizerCanvas) {
        const ctx = elVisualizerCanvas.getContext('2d');
        ctx.clearRect(0, 0, elVisualizerCanvas.width, elVisualizerCanvas.height);
      }
    }
  }

  function startBrowserSpeechRecognition() {
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SpeechRecognition) {
      appendLog('system', 'Browser Speech API unavailable; switching to /api/voice/process-audio.');
      if (elVoiceEngine) elVoiceEngine.value = 'whisper';
      startMediaRecorderSpeech();
      return;
    }

    speechRecognizer = new SpeechRecognition();
    speechRecognizer.lang = 'en-US';
    speechRecognizer.continuous = false;
    speechRecognizer.interimResults = true;

    speechRecognizer.onstart = () => {
      setMicState('recording');
      appendLog('voice', 'Browser speech recognition active.');
    };

    speechRecognizer.onresult = (event) => {
      let interim = '';
      let final = '';
      for (let i = event.resultIndex; i < event.results.length; i++) {
        if (event.results[i].isFinal) final += event.results[i][0].transcript;
        else interim += event.results[i][0].transcript;
      }
      if (elLiveTranscript) elLiveTranscript.textContent = final || interim;
      if (final) {
        speechRecognizer.stop();
        executeNaturalLanguageCommand(final.trim());
      }
    };

    speechRecognizer.onerror = (event) => {
      setMicState('idle');
      appendLog('error', `Speech error: ${event.error}`);
    };

    speechRecognizer.onend = () => {
      if (isRecording) setMicState('idle');
    };

    try {
      speechRecognizer.start();
    } catch (err) {
      setMicState('idle');
      appendLog('error', `Could not start speech recognition: ${err.message}`);
    }
  }

  async function startMediaRecorderSpeech() {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      appendLog('error', 'Browser microphone recording is unavailable.');
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      setupVisualizer(stream);
      recordedChunks = [];
      mediaRecorder = new MediaRecorder(stream);

      mediaRecorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) recordedChunks.push(e.data);
      };

      mediaRecorder.onstart = () => {
        setMicState('recording');
        appendLog('voice', 'Recording up to 5s for /api/voice/process-audio...');
      };

      mediaRecorder.onstop = async () => {
        setMicState('processing');
        stream.getTracks().forEach((t) => t.stop());
        const blobType = (mediaRecorder && mediaRecorder.mimeType) || 'audio/webm';
        const audioBlob = new Blob(recordedChunks, { type: blobType });
        await uploadAudioForTranscription(audioBlob);
      };

      mediaRecorder.start();
      setTimeout(() => {
        if (mediaRecorder && mediaRecorder.state === 'recording') {
          mediaRecorder.stop();
        }
      }, 5000);
    } catch (err) {
      setMicState('idle');
      appendLog('error', `Microphone access failed: ${err.message}`);
    }
  }

  async function uploadAudioForTranscription(blob) {
    const formData = new FormData();
    formData.append('audio', blob, 'command_audio.webm');
    try {
      const res = await fetch('/api/voice/process-audio', {
        method: 'POST',
        body: formData,
      });
      if (!res.ok) throw new Error(await readErrorDetail(res));
      const data = await res.json();
      handleCommandResponse(data);
    } catch (err) {
      setMicState('idle');
      renderCommandError(err.message);
      appendLog('error', `Voice processing failed: ${err.message}`);
    }
  }

  async function triggerServerMic() {
    setMicState('recording');
    appendLog('voice', 'Triggering server microphone (/api/voice/server-mic)...');
    try {
      const res = await fetch('/api/voice/server-mic', { method: 'POST' });
      if (!res.ok) throw new Error(await readErrorDetail(res));
      const data = await res.json();
      handleCommandResponse(data);
    } catch (err) {
      setMicState('idle');
      renderCommandError(err.message);
      appendLog('error', `Server mic failed: ${err.message}`);
    }
  }

  if (elBtnVoice) {
    elBtnVoice.addEventListener('click', () => {
      if (isRecording) {
        if (speechRecognizer) speechRecognizer.stop();
        if (mediaRecorder && mediaRecorder.state === 'recording') mediaRecorder.stop();
        setMicState('processing');
        return;
      }
      const engine = elVoiceEngine ? elVoiceEngine.value : 'browser';
      if (engine === 'browser') startBrowserSpeechRecognition();
      else if (engine === 'whisper') startMediaRecorderSpeech();
      else triggerServerMic();
    });
  }

  async function executeNaturalLanguageCommand(query) {
    if (!query || !query.trim()) return;
    setMicState('processing');
    if (elLiveTranscript) elLiveTranscript.textContent = query;
    appendLog('voice', `Command: "${query}"`);

    try {
      const res = await fetch('/api/command', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: query.trim() }),
      });
      if (!res.ok) throw new Error(await readErrorDetail(res));
      const data = await res.json();
      handleCommandResponse(data);
    } catch (err) {
      setMicState('idle');
      renderCommandError(err.message);
      appendLog('error', `Command error: ${err.message}`);
    }
  }

  function renderCommandError(msg) {
    if (!elAiReasoningContent) return;
    elAiReasoningContent.innerHTML = `<div style="color:var(--err);">Error: ${escapeHtml(msg)}</div>`;
  }

  function handleCommandResponse(data) {
    setMicState('idle');

    const transcriptText = data.transcript || data.query;
    if (transcriptText && elLiveTranscript) {
      elLiveTranscript.textContent = transcriptText;
    } else if (data.message && elLiveTranscript) {
      elLiveTranscript.textContent = data.message;
    }

    if (typeof data.duration_ms === 'number' && elLastLatency) {
      elLastLatency.textContent = `${Math.round(data.duration_ms)} ms (${data.path || 'ok'})`;
    } else if (elLastLatency) {
      elLastLatency.textContent = data.path || '—';
    }

    if (elAiReasoningContent) {
      const commands = data.commands || data.valid_commands || {};
      const executed = data.executed || {};
      const entries = Object.entries(commands);
      let html = '';

      if (data.error) {
        html += `<div style="color:var(--err); margin-bottom:0.35rem;">Error: ${escapeHtml(data.error)}</div>`;
      }

      if (entries.length > 0) {
        entries.forEach(([dev, cmd]) => {
          const ex = executed[dev];
          const statusNote = ex
            ? (ex.success ? ' [OK]' : ` [FAILED${ex.reason ? ': ' + ex.reason : ''}]`)
            : '';
          const color = ex && !ex.success ? 'var(--err)' : 'var(--ok)';
          html += `<div><strong>${escapeHtml(dev)}</strong> &rarr; <span style="color:${color};">${escapeHtml(cmd)}${escapeHtml(statusNote)}</span></div>`;
        });
      } else if (data.message) {
        html += `<div class="muted">${escapeHtml(data.message)}</div>`;
      } else if (!data.error) {
        html += '<div class="muted">No matching device actions found.</div>';
      }

      const rawLlm = data.raw_llm || data.raw_response;
      if (rawLlm) {
        html += `<div style="margin-top:0.4rem; padding-top:0.35rem; border-top:1px solid var(--border); color:var(--text-muted);">LLM (${escapeHtml(data.backend || 'ai')}): ${escapeHtml(rawLlm)}</div>`;
      }

      elAiReasoningContent.innerHTML = html;
    }

    if (data.device_states) {
      Object.entries(data.device_states).forEach(([dev, st]) => updateDeviceCardUI(dev, st));
    } else {
      fetchDevices();
    }
  }

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

  // --- Spatial & Scene Image ---
  async function loadSpatialInfo() {
    try {
      const res = await fetch('/api/spatial');
      if (!res.ok) throw new Error(await readErrorDetail(res));
      const data = await res.json();
      if (elSpatialText) {
        elSpatialText.textContent = data.spatial_text || 'No spatial context available.';
      }
      if (elSceneImg && elSceneContainer) {
        if (data.image_url) {
          elSceneImg.onload = () => elSceneContainer.classList.remove('no-image');
          elSceneImg.onerror = () => elSceneContainer.classList.add('no-image');
          elSceneImg.src = `${data.image_url}?t=${Date.now()}`;
        } else {
          elSceneContainer.classList.add('no-image');
        }
      }
    } catch (err) {
      appendLog('error', `Failed to load spatial context: ${err.message}`);
    }
  }

  if (elBtnRefreshSpatial) {
    elBtnRefreshSpatial.addEventListener('click', loadSpatialInfo);
  }

  // --- Room Onboarding Engine UI ---
  function setOnboardAlert(message, kind = 'ok') {
    if (!elOnboardAlert) return;
    if (!message) {
      elOnboardAlert.classList.add('hidden');
      elOnboardAlert.textContent = '';
      return;
    }
    elOnboardAlert.className = `inline-alert ${kind}`;
    elOnboardAlert.textContent = message;
  }

  async function loadOnboardingStatus() {
    try {
      const res = await fetch('/api/onboard/status');
      if (!res.ok) return;
      const data = await res.json();

      if (elIndOnboard && elValOnboard) {
        elIndOnboard.className = `status-dot ${data.ready ? 'online' : 'warning'}`;
        elValOnboard.textContent = data.ready ? (data.provider || 'ready') : 'Unconfigured';
      }

      if (elOnboardReadinessBadge) {
        elOnboardReadinessBadge.className = `status-badge-pill ${data.ready ? 'ok' : 'warn'}`;
        elOnboardReadinessBadge.textContent = data.ready
          ? `Ready: ${data.provider} (${data.model || 'default'}) • ${data.device_count || 0} active`
          : `Cloud key missing for ${data.provider || 'provider'} • Configure .env`;
      }
    } catch (err) {
      if (elOnboardReadinessBadge) {
        elOnboardReadinessBadge.className = 'status-badge-pill warn';
        elOnboardReadinessBadge.textContent = 'Onboarding status unavailable';
      }
    }
  }

  function handleSelectedImageFile(file) {
    if (!file) return;
    selectedImageFile = file;
    if (elOnboardFileMeta) {
      elOnboardFileMeta.textContent = `${file.name} (${(file.size / 1024).toFixed(1)} KB)`;
    }
    const reader = new FileReader();
    reader.onload = (e) => {
      if (elOnboardPreviewImg && elOnboardDropPlaceholder) {
        elOnboardPreviewImg.src = e.target.result;
        elOnboardPreviewImg.classList.remove('hidden');
        elOnboardDropPlaceholder.classList.add('hidden');
      }
    };
    reader.readAsDataURL(file);
    setOnboardAlert('');
  }

  if (elOnboardImageInput) {
    elOnboardImageInput.addEventListener('change', (e) => {
      if (e.target.files && e.target.files[0]) {
        handleSelectedImageFile(e.target.files[0]);
      }
    });
  }

  if (elOnboardDropzone) {
    ['dragenter', 'dragover'].forEach((evt) => {
      elOnboardDropzone.addEventListener(evt, (e) => {
        e.preventDefault();
        elOnboardDropzone.classList.add('dragover');
      });
    });
    ['dragleave', 'drop'].forEach((evt) => {
      elOnboardDropzone.addEventListener(evt, (e) => {
        e.preventDefault();
        elOnboardDropzone.classList.remove('dragover');
      });
    });
    elOnboardDropzone.addEventListener('drop', (e) => {
      const file = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
      if (file) handleSelectedImageFile(file);
    });
  }

  function setDraftData(ctx, validation) {
    draftContext = {
      room_name: (ctx && ctx.room_name) || 'Room',
      devices: Array.isArray(ctx && ctx.devices)
        ? ctx.devices.map((d) => ({
            id: d.id || '',
            name: d.name || '',
            type: d.type || 'light',
            zone: d.zone || 'room',
            x: typeof d.x === 'number' ? d.x : Number(d.x || 0.5),
            y: typeof d.y === 'number' ? d.y : Number(d.y || 0.5),
            near: Array.isArray(d.near) ? d.near : [],
          }))
        : [],
    };

    if (elDraftRoomName) elDraftRoomName.value = draftContext.room_name;
    renderDraftTable();
    syncJsonEditorFromDraft();
    if (elBtnApplyDraft) elBtnApplyDraft.disabled = draftContext.devices.length === 0;

    if (validation) {
      updateDraftValidationUI(validation.valid, validation.errors || []);
    } else {
      updateDraftValidationUI(null, []);
    }
  }

  function updateDraftValidationUI(valid, errors) {
    if (!elDraftValidationMsg) return;
    if (valid === true) {
      elDraftValidationMsg.className = 'draft-validation-text ok';
      elDraftValidationMsg.textContent = `Valid schema (${draftContext ? draftContext.devices.length : 0} device(s)).`;
    } else if (valid === false) {
      elDraftValidationMsg.className = 'draft-validation-text err';
      elDraftValidationMsg.textContent = `Validation issues: ${(errors || []).join('; ')}`;
    } else {
      elDraftValidationMsg.className = 'draft-validation-text';
      elDraftValidationMsg.textContent = draftContext
        ? `${draftContext.devices.length} device(s) in draft.`
        : 'No draft loaded.';
    }
  }

  function syncDraftFromTable() {
    if (!draftContext) draftContext = { room_name: 'Room', devices: [] };
    draftContext.room_name = (elDraftRoomName && elDraftRoomName.value.trim()) || 'Room';
    const rows = elDraftDevicesBody ? elDraftDevicesBody.querySelectorAll('tr[data-idx]') : [];
    const devices = [];
    rows.forEach((tr) => {
      const getVal = (field) => {
        const el = tr.querySelector(`[data-field="${field}"]`);
        return el ? el.value : '';
      };
      const nearRaw = getVal('near');
      devices.push({
        id: getVal('id').trim(),
        name: getVal('name').trim(),
        type: getVal('type').trim() || 'light',
        zone: getVal('zone').trim() || 'room',
        x: Math.max(0, Math.min(1, parseFloat(getVal('x')) || 0)),
        y: Math.max(0, Math.min(1, parseFloat(getVal('y')) || 0)),
        near: nearRaw.split(',').map((s) => s.trim()).filter(Boolean),
      });
    });
    draftContext.devices = devices;
    if (elBtnApplyDraft) elBtnApplyDraft.disabled = devices.length === 0;
  }

  function syncJsonEditorFromDraft() {
    if (elDraftJsonEditor && draftContext) {
      elDraftJsonEditor.value = JSON.stringify(draftContext, null, 2);
    }
  }

  function getActiveDraftPayload() {
    if (draftViewMode === 'json' && elDraftJsonEditor) {
      const parsed = JSON.parse(elDraftJsonEditor.value);
      draftContext = parsed;
      if (elDraftRoomName && parsed.room_name) elDraftRoomName.value = parsed.room_name;
      return parsed;
    }
    syncDraftFromTable();
    return draftContext;
  }

  function renderDraftTable() {
    if (!elDraftDevicesBody) return;
    if (!draftContext || !draftContext.devices.length) {
      elDraftDevicesBody.innerHTML = '<tr><td colspan="8" class="empty-cell">No devices in draft. Add a device or upload a room image.</td></tr>';
      return;
    }

    elDraftDevicesBody.innerHTML = '';
    draftContext.devices.forEach((dev, idx) => {
      const tr = document.createElement('tr');
      tr.dataset.idx = String(idx);

      const typeOpts = VALID_TYPES.map((t) =>
        `<option value="${t}" ${dev.type === t ? 'selected' : ''}>${t}</option>`
      ).join('');

      tr.innerHTML = `
        <td><input type="text" class="input-control" data-field="id" value="${escapeHtml(dev.id)}"></td>
        <td><input type="text" class="input-control" data-field="name" value="${escapeHtml(dev.name)}"></td>
        <td><select class="input-control" data-field="type">${typeOpts}</select></td>
        <td><input type="text" class="input-control" data-field="zone" value="${escapeHtml(dev.zone)}"></td>
        <td><input type="number" step="0.01" min="0" max="1" class="input-control coord-input" data-field="x" value="${Number(dev.x).toFixed(2)}"></td>
        <td><input type="number" step="0.01" min="0" max="1" class="input-control coord-input" data-field="y" value="${Number(dev.y).toFixed(2)}"></td>
        <td><input type="text" class="input-control" data-field="near" value="${escapeHtml((dev.near || []).join(', '))}"></td>
        <td><button type="button" class="btn-danger-text" title="Remove">Remove</button></td>
      `;

      tr.querySelectorAll('input, select').forEach((inp) => {
        inp.addEventListener('change', () => {
          syncDraftFromTable();
          syncJsonEditorFromDraft();
        });
      });

      tr.querySelector('.btn-danger-text').addEventListener('click', () => {
        syncDraftFromTable();
        draftContext.devices.splice(idx, 1);
        renderDraftTable();
        syncJsonEditorFromDraft();
        if (elBtnApplyDraft) elBtnApplyDraft.disabled = draftContext.devices.length === 0;
      });

      elDraftDevicesBody.appendChild(tr);
    });
  }

  if (elDraftRoomName) {
    elDraftRoomName.addEventListener('input', () => {
      if (!draftContext) draftContext = { room_name: 'Room', devices: [] };
      draftContext.room_name = elDraftRoomName.value;
      syncJsonEditorFromDraft();
    });
  }

  if (elBtnAddDeviceRow) {
    elBtnAddDeviceRow.addEventListener('click', () => {
      if (draftViewMode === 'json') {
        try { getActiveDraftPayload(); } catch (_) {}
      } else {
        syncDraftFromTable();
      }
      if (!draftContext) draftContext = { room_name: (elDraftRoomName && elDraftRoomName.value.trim()) || 'Room', devices: [] };
      const nextNum = draftContext.devices.length + 1;
      draftContext.devices.push({
        id: `light${nextNum}`,
        name: `Device ${nextNum}`,
        type: 'light',
        zone: 'room',
        x: 0.5,
        y: 0.5,
        near: [],
      });
      renderDraftTable();
      syncJsonEditorFromDraft();
      if (elBtnApplyDraft) elBtnApplyDraft.disabled = false;
    });
  }

  function switchDraftView(mode) {
    if (mode === draftViewMode) return;
    if (mode === 'json') {
      syncDraftFromTable();
      syncJsonEditorFromDraft();
      elDraftTableWrap.classList.add('hidden');
      elDraftJsonWrap.classList.remove('hidden');
      elBtnViewJson.classList.add('active');
      elBtnViewTable.classList.remove('active');
    } else {
      try {
        const parsed = JSON.parse(elDraftJsonEditor.value || '{"room_name":"Room","devices":[]}');
        setDraftData(parsed);
      } catch (err) {
        setOnboardAlert(`Invalid JSON: ${err.message}`, 'err');
        return;
      }
      elDraftJsonWrap.classList.add('hidden');
      elDraftTableWrap.classList.remove('hidden');
      elBtnViewTable.classList.add('active');
      elBtnViewJson.classList.remove('active');
    }
    draftViewMode = mode;
  }

  if (elBtnViewTable) elBtnViewTable.addEventListener('click', () => switchDraftView('table'));
  if (elBtnViewJson) elBtnViewJson.addEventListener('click', () => switchDraftView('json'));

  async function uploadRoomPhoto(saveDirectly) {
    if (!selectedImageFile) {
      setOnboardAlert('Select a room photo first.', 'warn');
      return;
    }

    const formData = new FormData();
    formData.append('image', selectedImageFile);
    if (elOnboardRoomName && elOnboardRoomName.value.trim()) {
      formData.append('room_name', elOnboardRoomName.value.trim());
    }
    if (elOnboardDeviceHints && elOnboardDeviceHints.value.trim()) {
      formData.append('device_hints', elOnboardDeviceHints.value.trim());
    }
    if (elOnboardProvider && elOnboardProvider.value) {
      formData.append('provider', elOnboardProvider.value);
    }
    formData.append('save', saveDirectly ? 'true' : 'false');

    elBtnOnboardPreview.disabled = true;
    elBtnOnboardDirect.disabled = true;
    setOnboardAlert(saveDirectly ? 'Uploading image and applying context...' : 'Uploading image to cloud for analysis...', 'warn');
    appendLog('system', `Uploading ${selectedImageFile.name} to /api/onboard/upload (save=${saveDirectly})...`);

    try {
      const res = await fetch('/api/onboard/upload', {
        method: 'POST',
        body: formData,
      });
      if (!res.ok) throw new Error(await readErrorDetail(res));
      const data = await res.json();

      setDraftData(data.context, data.validation);
      await loadSpatialInfo();

      if (saveDirectly && data.saved) {
        setOnboardAlert(`Context saved and activated (${data.context.devices.length} devices, ${Math.round(data.duration_ms)} ms via ${data.provider}).`, 'ok');
        appendLog('ack', `Onboarding saved directly (${data.context.devices.length} devices).`);
        await refreshAllContextViews();
      } else {
        setOnboardAlert(`Draft generated in ${Math.round(data.duration_ms)} ms via ${data.provider}. Review below and click Apply Context.`, data.validation.valid ? 'ok' : 'warn');
        appendLog('system', `Onboarding draft ready (${data.context.devices.length} devices).`);
      }
    } catch (err) {
      setOnboardAlert(`Onboarding failed: ${err.message}`, 'err');
      appendLog('error', `Onboarding upload error: ${err.message}`);
    } finally {
      elBtnOnboardPreview.disabled = false;
      elBtnOnboardDirect.disabled = false;
    }
  }

  if (elBtnOnboardPreview) elBtnOnboardPreview.addEventListener('click', () => uploadRoomPhoto(false));
  if (elBtnOnboardDirect) elBtnOnboardDirect.addEventListener('click', () => uploadRoomPhoto(true));

  async function loadLastDraft(silent = false) {
    try {
      const res = await fetch('/api/onboard/draft');
      if (!res.ok) {
        if (!silent) setOnboardAlert('No saved onboarding draft found yet.', 'warn');
        return;
      }
      const data = await res.json();
      if (data.context) {
        setDraftData(data.context, data.validation);
        if (!silent) {
          setOnboardAlert(`Loaded last draft (${data.provider || 'cloud'}, ${data.context.devices.length} devices).`, 'ok');
        }
      }
    } catch (err) {
      if (!silent) setOnboardAlert(`Failed to load draft: ${err.message}`, 'err');
    }
  }

  if (elBtnLoadDraft) elBtnLoadDraft.addEventListener('click', () => loadLastDraft(false));

  async function validateDraft() {
    let payload;
    try {
      payload = getActiveDraftPayload();
    } catch (err) {
      setOnboardAlert(`Invalid JSON: ${err.message}`, 'err');
      return false;
    }
    if (!payload) return false;

    try {
      const res = await fetch('/api/onboard/validate?normalize=false', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error(await readErrorDetail(res));
      const data = await res.json();
      updateDraftValidationUI(data.valid, data.errors || []);
      return data.valid;
    } catch (err) {
      setOnboardAlert(`Validation error: ${err.message}`, 'err');
      return false;
    }
  }

  if (elBtnValidateDraft) elBtnValidateDraft.addEventListener('click', validateDraft);

  async function applyDraft() {
    let payload;
    try {
      payload = getActiveDraftPayload();
    } catch (err) {
      setOnboardAlert(`Invalid JSON: ${err.message}`, 'err');
      return;
    }
    if (!payload) {
      setOnboardAlert('No draft to apply.', 'warn');
      return;
    }

    elBtnApplyDraft.disabled = true;
    try {
      const res = await fetch('/api/onboard/apply', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error(await readErrorDetail(res));
      const data = await res.json();
      updateDraftValidationUI(true, []);
      setOnboardAlert(`Applied "${data.room_name || 'Room'}" (${data.device_count} device(s)) to active room_context.json.`, 'ok');
      appendLog('ack', `Applied room context (${data.device_count} devices: ${(data.device_ids || []).join(', ')}).`);
      await refreshAllContextViews();
    } catch (err) {
      setOnboardAlert(`Apply failed: ${err.message}`, 'err');
      appendLog('error', `Apply context failed: ${err.message}`);
    } finally {
      elBtnApplyDraft.disabled = false;
    }
  }

  if (elBtnApplyDraft) elBtnApplyDraft.addEventListener('click', applyDraft);

  async function refreshAllContextViews() {
    await Promise.all([
      fetchDevices(),
      loadSpatialInfo(),
      loadOnboardingStatus(),
      fetchSystemStatus(),
    ]);
  }

  // --- Dynamic Diagnostics Modal ---
  async function openDiagnosticsModal() {
    if (!elModal) return;
    elModal.classList.remove('hidden');

    try {
      const [statusRes, devRes, onbRes] = await Promise.all([
        fetch('/api/status'),
        fetch('/api/devices'),
        fetch('/api/onboard/status'),
      ]);
      const st = statusRes.ok ? await statusRes.json() : {};
      const devs = devRes.ok ? (await devRes.json()).devices || [] : [];
      const onb = onbRes.ok ? await onbRes.json() : {};

      if (elDiagSummaryGrid) {
        elDiagSummaryGrid.innerHTML = `
          <div class="diag-stat"><div class="diag-stat-label">Platform</div><div class="diag-stat-val">${escapeHtml(st.platform || 'pc')}</div></div>
          <div class="diag-stat"><div class="diag-stat-label">MQTT</div><div class="diag-stat-val">${st.mqtt && st.mqtt.connected ? 'Connected' : (st.mqtt && st.mqtt.dry_run ? 'Dry-Run' : 'Offline')}</div></div>
          <div class="diag-stat"><div class="diag-stat-label">LLM Backend</div><div class="diag-stat-val">${escapeHtml((st.ai && st.ai.llm_backend) || '—')} / ${escapeHtml((st.ai && st.ai.llm_model) || '—')}</div></div>
          <div class="diag-stat"><div class="diag-stat-label">Onboarding</div><div class="diag-stat-val">${escapeHtml(onb.provider || '—')} (${onb.ready ? 'ready' : 'unconfigured'})</div></div>
        `;
      }

      if (elDiagDevicesBody) {
        if (!devs.length) {
          elDiagDevicesBody.innerHTML = '<tr><td colspan="8" class="empty-cell">No devices loaded.</td></tr>';
        } else {
          elDiagDevicesBody.innerHTML = devs.map((d) => `
            <tr>
              <td><strong>${escapeHtml(d.label || d.name)}</strong></td>
              <td><code>${escapeHtml(d.id)}</code></td>
              <td>${escapeHtml(d.type || 'light')}</td>
              <td>${escapeHtml(d.zone || d.room || 'room')}</td>
              <td><code>${escapeHtml(d.spatial_position || '')}</code></td>
              <td>${d.enabled !== false ? 'Yes' : 'No'}</td>
              <td><code>${escapeHtml(d.topic_set || '')}</code></td>
              <td><code>${escapeHtml(d.topic_state || '')}</code></td>
            </tr>
          `).join('');
        }
      }
    } catch (err) {
      if (elDiagDevicesBody) {
        elDiagDevicesBody.innerHTML = `<tr><td colspan="8" class="empty-cell">Error: ${escapeHtml(err.message)}</td></tr>`;
      }
    }
  }

  if (elBtnDiag) elBtnDiag.addEventListener('click', openDiagnosticsModal);
  if (elBtnCloseModal && elModal) {
    elBtnCloseModal.addEventListener('click', () => elModal.classList.add('hidden'));
  }
  if (elModal) {
    elModal.addEventListener('click', (e) => {
      if (e.target === elModal) elModal.classList.add('hidden');
    });
  }

  // --- SVG Icons per Device Type ---
  function getDeviceIconSvg(type) {
    switch (type) {
      case 'fan':
        return `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="2.5"></circle><path d="M12 9.5C12 6 14.5 3.5 17.5 3.5c0 3-2 5.5-5.5 6z"></path><path d="M14.5 12c3.5 0 6 2.5 6 5.5-3 0-5.5-2-6-5.5z"></path><path d="M12 14.5c0 3.5-2.5 6-5.5 6 0-3 2-5.5 5.5-6z"></path><path d="M9.5 12c-3.5 0-6-2.5-6-5.5 3 0 5.5 2 6 5.5z"></path></svg>`;
      case 'ac':
        return `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="4" width="20" height="8" rx="2"></rect><path d="M6 12v2M10 12v3M14 12v3M18 12v2"></path><path d="M7 18c0 1.5 1 2 1 3M12 18c0 1.5 1 2 1 3M17 18c0 1.5 1 2 1 3"></path></svg>`;
      case 'tv':
        return `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="2" y="4" width="20" height="13" rx="2"></rect><path d="M8 21h8M12 17v4"></path></svg>`;
      case 'curtain':
        return `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 4h18"></path><path d="M5 4v16h5V4M14 4v16h5V4"></path></svg>`;
      case 'lock':
        return `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="5" y="11" width="14" height="10" rx="2"></rect><path d="M8 11V7a4 4 0 0 1 8 0v4"></path></svg>`;
      default:
        return `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 18h6"></path><path d="M10 22h4"></path><path d="M12 2a7 7 0 0 0-7 7c0 3 2 5.5 4 6.5V17h6v-1.5c2-1 4-3.5 4-6.5a7 7 0 0 0-7-7z"></path></svg>`;
    }
  }

  // --- Boot ---
  function init() {
    initWebSocket();
    fetchSystemStatus();
    fetchDevices();
    loadSpatialInfo();
    loadOnboardingStatus();
    loadLastDraft(true);
  }

  document.addEventListener('DOMContentLoaded', init);
})();
