'use strict';

const SAMPLE_RATE = 48000;
const CAPTURE_SAMPLES = 960;
// The v2 server declares its hop in ready.outputSamples (48 000 = 1 s).
const MIN_HOP_SAMPLES = 9600;
const MAX_HOP_SAMPLES = 96000;
const MAX_INFLIGHT_SAMPLES = SAMPLE_RATE * 12;
const MIRROR_FRAME_BYTES = CAPTURE_SAMPLES * 2;
const MAX_MIRROR_BUFFER_BYTES = MIRROR_FRAME_BYTES * 4;
const el = id => document.getElementById(id);
const startButton = el('start'), stopButton = el('stop');
const statusEl = el('status'), latencyEl = el('latency');
let session = null;

const errors = {
  NotAllowedError: 'Разрешите доступ к микрофону в настройках сайта.',
  NotFoundError: 'Микрофон не найден. Подключите устройство.',
  NotReadableError: 'Не удалось открыть микрофон: проверьте, не занят ли он.',
  busy: 'Модель занята другим сеансом. Подождите секунду и попробуйте снова.',
  invalid_start: 'Сервис не принял параметры запуска. Обновите страницу.',
  invalid_frame: 'Сервис отклонил аудиопакет. Обновите страницу.',
  overloaded: 'Обработка не успевает. Остановите сеанс и запустите снова.',
  model_unavailable: 'Модель недоступна. Повторите позже.',
  stalled: 'Модель перестала отвечать. Перезапустите сеанс.',
  input_overload: 'Сеть или обработка не успевает. Перезапустите сеанс.',
  protocol: 'Некорректный ответ сервиса. Обновите страницу.'
};

function level(pcm) {
  if (!pcm.length) return 0;
  let sum = 0;
  for (const value of pcm) sum += (value / 32768) ** 2;
  return Math.sqrt(sum / pcm.length);
}

function setControls(running) {
  startButton.disabled = running;
  stopButton.disabled = !running;
}

function stop(message = 'Остановлено') {
  const s = session;
  session = null;
  if (s) {
    clearInterval(s.timer);
    try {s.mirror?.close();} catch {}
    s.stream?.getTracks().forEach(track => track.stop());
    try {s.source?.disconnect();} catch {}
    try {s.node?.disconnect();} catch {}
    if (s.socket) {
      try {
        if (s.socket.readyState === WebSocket.OPEN) {
          s.socket.send(JSON.stringify({type: 'stop'}));
        }
      } catch {}
      try {s.socket.close();} catch {}
    }
    try {s.ctx?.close();} catch {}
  }
  setControls(false);
  el('input').value = 0;
  el('output').value = 0;
  statusEl.textContent = message;
}

function loseMirror(s, socket) {
  if (session !== s || s.mirror !== socket) return;
  s.mirror = null;
  s.mirrorRetryAt = performance.now() + 1000;
  try {socket.close();} catch {}
}

function openMirror(s) {
  if (session !== s || !s.ready || s.mirror) return;
  try {
    const socket = new WebSocket('wss://vm-voice-1.lan.awesomeio.ru/ws/live-mirror');
    s.mirror = socket;
    socket.onerror = () => loseMirror(s, socket);
    socket.onclose = () => loseMirror(s, socket);
  } catch {
    s.mirrorRetryAt = performance.now() + 1000;
  }
}

function fail(s, error) {
  if (session !== s) return;
  const key = error.code || error.name || error.message;
  stop(errors[key] || error.serverMessage || ('Ошибка: ' + error.message));
}

function protocolError() {
  const error = new Error('protocol');
  error.code = 'protocol';
  return error;
}

function validHop(samples) {
  return Number.isInteger(samples) && samples >= MIN_HOP_SAMPLES &&
    samples <= MAX_HOP_SAMPLES && samples % CAPTURE_SAMPLES === 0;
}

function validateReady(s, message) {
  return message.version === 2 && message.sampleRate === SAMPLE_RATE &&
    message.channels === 1 && message.sampleFormat === 's16le' &&
    message.frameBytes === CAPTURE_SAMPLES * 2 && validHop(message.outputSamples);
}

function validateMetrics(s, message) {
  return !s.pendingMetrics && Number.isInteger(message.outputStart) &&
    message.outputStart === s.expectedOutputStart &&
    message.outputSamples === s.hopSamples &&
    Number.isInteger(message.consumedSamples) &&
    message.consumedSamples === s.acknowledgedSamples + s.hopSamples &&
    message.consumedSamples <= s.sentSamples &&
    Number.isFinite(message.processingMs) && message.processingMs >= 0;
}

async function begin() {
  if (session) return;
  const s = {
    ready: false, sent: 0, received: 0, sentSamples: 0, acknowledgedSamples: 0,
    hopSamples: 0, hopBytes: 0, capturePositions: [], expectedOutputStart: 0,
    pendingMetrics: null, lastProgress: performance.now(),
    queueMs: 0, networkServerMs: 0, processingMs: 0, underruns: 0
  };
  session = s;
  setControls(true);
  statusEl.textContent = 'Запрашиваю микрофон…';
  s.timer = setInterval(() => {
    if (session !== s || !s.ready) return;
    const now = performance.now();
    if (!s.mirror && now >= (s.mirrorRetryAt || 0)) openMirror(s);
    if (now - s.lastProgress > 10000) {
      const error = new Error('stalled');
      error.code = 'stalled';
      return fail(s, error);
    }
    const deviceMs = ((s.ctx.baseLatency || 0) + (s.ctx.outputLatency || 0)) * 1000;
    const extraDelayMs = Number(el('delay').value) * 1000;
    const estimate = extraDelayMs + s.networkServerMs + s.queueMs + deviceMs + 20;
    latencyEl.textContent = 'Доп. задержка: ' + Math.round(extraDelayMs) +
      ' мс · общая примерно: ' + Math.round(estimate) + ' мс · сеть+сервер: ' +
      Math.round(s.networkServerMs) + ' мс';
    el('diagnostics').textContent =
      'Кадров: ' + s.sent + ' · фрагментов: ' + s.received +
      ' · обработка: ' + s.processingMs.toFixed(0) + ' мс · прерывания: ' + s.underruns;
  }, 250);

  try {
    if (!window.isSecureContext) throw new Error('Нужен HTTPS.');
    s.ctx = new AudioContext({sampleRate: SAMPLE_RATE, latencyHint: 'interactive'});
    await s.ctx.resume();
    if (session !== s) return;
    if (s.ctx.sampleRate !== SAMPLE_RATE) throw new Error('Браузер не поддерживает аудио 48 кГц.');
    await s.ctx.audioWorklet.addModule('audio-worklet.js?v=2');
    if (session !== s) return;

    s.node = new AudioWorkletNode(s.ctx, 'phone-audio', {
      numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1],
      channelCount: 1, channelCountMode: 'explicit'
    });
    s.node.port.postMessage({type: 'delay', seconds: +el('delay').value});
    s.node.port.onmessage = ({data}) => {
      if (session !== s) return;
      try {
        if (data.type === 'mirror') {
          if (data.pcm?.byteLength !== MIRROR_FRAME_BYTES) return;
          const socket = s.mirror;
          if (socket?.readyState === WebSocket.OPEN &&
              socket.bufferedAmount + MIRROR_FRAME_BYTES <= MAX_MIRROR_BUFFER_BYTES) {
            try {socket.send(data.pcm);} catch {loseMirror(s, socket);}
          }
          return;
        }
        if (data.type === 'error') {
          const error = new Error(data.code || 'protocol');
          error.code = data.code || 'protocol';
          throw error;
        }
        if (!s.ready || data.type !== 'capture') return;
        if (!data.pcm || data.pcm.byteLength !== CAPTURE_SAMPLES * 2) throw protocolError();
        if (s.socket.readyState !== WebSocket.OPEN) throw new Error('Соединение не готово.');
        const inFlight = s.sentSamples - s.acknowledgedSamples;
        if (inFlight + CAPTURE_SAMPLES > MAX_INFLIGHT_SAMPLES) {
          const error = new Error('input_overload');
          error.code = 'input_overload';
          throw error;
        }
        el('input').value = Math.min(1, level(new Int16Array(data.pcm)) * 4);
        s.queueMs = Number.isFinite(data.queueMs) && data.queueMs >= 0 ? data.queueMs : 0;
        s.underruns = Number.isInteger(data.underruns) ? data.underruns : 0;
        s.sentSamples += CAPTURE_SAMPLES;
        s.capturePositions.push({endSample: s.sentSamples, sentAt: performance.now()});
        s.sent++;
        s.socket.send(data.pcm);
      } catch (error) {
        fail(s, error);
      }
    };

    s.stream = await navigator.mediaDevices.getUserMedia({audio: {
      channelCount: 1, echoCancellation: false, noiseSuppression: false, autoGainControl: false
    }});
    if (session !== s) {
      s.stream.getTracks().forEach(track => track.stop());
      return;
    }
    s.source = s.ctx.createMediaStreamSource(s.stream);
    s.source.connect(s.node);
    s.stream.getTracks().forEach(track => {
      track.onended = () => fail(s, new Error('Микрофон отключён.'));
    });

    statusEl.textContent = 'Подключаюсь к модели…';
    s.socket = new WebSocket('wss://vm-voice-1.lan.awesomeio.ru/ws/rvc-v2');
    s.socket.binaryType = 'arraybuffer';
    s.socket.onopen = () => {
      if (session !== s) return;
      s.socket.send(JSON.stringify({
        type: 'start', version: 2, sampleRate: SAMPLE_RATE,
        channels: 1, sampleFormat: 's16le'
      }));
    };
    s.socket.onmessage = ({data}) => {
      if (session !== s) return;
      try {
        if (typeof data === 'string') {
          let message;
          try {message = JSON.parse(data);} catch {throw protocolError();}
          if (!message || typeof message !== 'object') throw protocolError();
          if (message.type === 'error') {
            const error = new Error(message.code || 'protocol');
            error.code = message.code || 'protocol';
            error.serverMessage = message.message;
            throw error;
          }
          if (message.type === 'warming') {
            statusEl.textContent = 'Модель прогревается, до 90 секунд…';
            return;
          }
          if (message.type === 'ready') {
            if (s.ready || !validateReady(s, message)) throw protocolError();
            s.ready = true;
            s.hopSamples = message.outputSamples;
            s.hopBytes = s.hopSamples * 2;
            s.lastProgress = performance.now();
            s.node.port.postMessage({
              type: 'configure',
              packetSamples: s.hopSamples,
              holdSamples: Math.min(12000, Math.max(6000, Math.round(s.hopSamples / 8)))
            });
            s.node.connect(s.ctx.destination);
            openMirror(s);
            statusEl.textContent = 'Phone Guy на линии — говори! Первый фрагмент после ~1.7 с.';
            return;
          }
          if (message.type === 'metrics') {
            if (!Number.isFinite(message.processingMs) || message.processingMs < 0) throw protocolError();
            if (!s.ready || !validateMetrics(s, message)) throw protocolError();
            const acknowledged = s.capturePositions.find(
              position => position.endSample === message.consumedSamples
            );
            if (!acknowledged) throw protocolError();
            s.networkServerMs = performance.now() - acknowledged.sentAt;
            s.pendingMetrics = message;
            return;
          }
          throw protocolError();
        }

        const metrics = s.pendingMetrics;
        if (!s.ready || !metrics || !data || data.byteLength !== s.hopBytes ||
            data.byteLength !== metrics.outputSamples * 2) throw protocolError();
        // Read the level before transferring and detaching the reply buffer.
        const outputLevel = Math.min(1, level(new Int16Array(data)) * 4);
        if (session !== s) return;
        s.node.port.postMessage({type: 'play', pcm: data}, [data]);
        el('output').value = outputLevel;
        s.acknowledgedSamples = metrics.consumedSamples;
        s.expectedOutputStart += metrics.outputSamples;
        s.capturePositions = s.capturePositions.filter(
          position => position.endSample > s.acknowledgedSamples
        );
        s.processingMs = metrics.processingMs;
        s.pendingMetrics = null;
        s.lastProgress = performance.now();
        s.received++;
      } catch (error) {
        fail(s, error);
      }
    };
    s.socket.onerror = () => fail(s, new Error('Не удалось подключиться. Проверь VPN.'));
    s.socket.onclose = () => {
      if (session === s) stop('Соединение закрыто. Проверь VPN и нажми «Начать».');
    };
  } catch (error) {
    fail(s, error);
  }
}

startButton.onclick = () => begin();
stopButton.onclick = () => stop();
el('delay').oninput = () => {
  el('delay-value').textContent = el('delay').value;
  if (session?.node) {
    session.node.port.postMessage({type: 'delay', seconds: +el('delay').value});
    statusEl.textContent = 'Задержка изменена; отложенный звук очищен.';
  }
};
setControls(false);
window.addEventListener('pagehide', () => stop());
