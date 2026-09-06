'use strict';

const SAMPLE_RATE = 48000;
const CAPTURE_SAMPLES = 960;
const RVC_OUTPUT_SAMPLES = 96000;
const RVC_OUTPUT_BYTES = RVC_OUTPUT_SAMPLES * 2;
const MAX_RVC_INFLIGHT_SAMPLES = SAMPLE_RATE * 12;
const MAX_RVC_BUFFERED_BYTES = MAX_RVC_INFLIGHT_SAMPLES * 2;
const el = id => document.getElementById(id);
const startButton = el('start'), stopButton = el('stop'), testButton = el('test');
const statusEl = el('status'), latencyEl = el('latency'), profileEl = el('profile');
let session = null;

const settings = () => ({
  pitchSemitones: +el('pitch').value, effectMix: +el('effect').value,
  noiseMix: +el('noise').value, outputGainDb: +el('gain').value
});

const errors = {
  NotAllowedError: 'Разрешите доступ к микрофону в настройках сайта.',
  NotFoundError: 'Микрофон не найден. Подключите устройство.',
  NotReadableError: 'Не удалось открыть микрофон: проверьте, не занят ли он.',
  busy: 'Модель занята другой вкладкой или завершает предыдущий сеанс. Подождите секунду и попробуйте снова.',
  invalid_start: 'Сервис не принял параметры запуска. Обновите страницу и повторите попытку.',
  invalid_frame: 'Сервис отклонил аудиопакет. Обновите страницу и повторите попытку.',
  overloaded: 'Обработка не успевает за звуком. Остановите сеанс и повторите запуск.',
  model_unavailable: 'Модель голоса недоступна. Повторите попытку позже или выберите простой эффект вручную.',
  stalled: 'Модель перестала отвечать. Остановите сеанс и запустите его снова.',
  connecting_timeout: 'Сервис не ответил при подключении за 10 секунд. Проверьте VPN и повторите запуск.',
  protocol: 'Некорректный ответ сервиса голоса. Обновите страницу и повторите попытку.',
  input_overload: 'Сеть или обработка не успевает принимать микрофон. Остановите сеанс и подключитесь снова.'
};

function level(pcm) {
  if (!pcm.length) return 0;
  let sum = 0;
  for (const value of pcm) sum += (value / 32768) ** 2;
  return Math.sqrt(sum / pcm.length);
}

function showProfile() {
  el('dsp-settings').hidden = profileEl.value !== 'dsp';
}

function setControls(running, test = false) {
  startButton.disabled = running;
  testButton.disabled = running;
  stopButton.disabled = !running;
  profileEl.disabled = running;
  el('delay').disabled = running && test;
}

function stop(message = 'Остановлено') {
  const s = session;
  session = null;
  if (s) {
    clearInterval(s.timer);
    clearTimeout(s.testTimer);
    s.stream?.getTracks().forEach(track => track.stop());
    if (s.source?.stop) {try {s.source.stop();} catch {}}
    try {s.source?.disconnect();} catch {}
    try {s.node?.disconnect();} catch {}
    if (s.socket) {
      try {
        if (s.mode === 'rvc' && s.socket.readyState === WebSocket.OPEN) {
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

function validateRvcReady(message) {
  return message.version === 1 && message.sampleRate === SAMPLE_RATE &&
    message.channels === 1 && message.sampleFormat === 's16le' &&
    message.frameBytes === CAPTURE_SAMPLES * 2 &&
    message.outputSamples === RVC_OUTPUT_SAMPLES;
}

function validateRvcMetrics(s, message) {
  return !s.pendingMetrics && Number.isInteger(message.outputStart) &&
    message.outputStart === s.expectedOutputStart &&
    Number.isInteger(message.outputSamples) && message.outputSamples === RVC_OUTPUT_SAMPLES &&
    Number.isInteger(message.consumedSamples) &&
    message.consumedSamples === s.acknowledgedSamples + RVC_OUTPUT_SAMPLES &&
    message.consumedSamples <= s.sentSamples &&
    Number.isFinite(message.processingMs) && message.processingMs >= 0;
}

async function begin(test = false) {
  if (session) return;
  const mode = test ? 'dsp' : profileEl.value;
  const s = {
    mode, test, ready: false, phase: 'connecting', phaseStarted: performance.now(),
    sent: 0, received: 0, pending: [], sentSamples: 0, acknowledgedSamples: 0,
    capturePositions: [], expectedOutputStart: 0, pendingMetrics: null,
    lastProgress: performance.now(), warmupTimeoutMs: 90000,
    queueMs: 0, networkServerMs: 0, processingMs: 0, underruns: 0, dropped: 0
  };
  session = s;
  setControls(true, test);
  statusEl.textContent = test ? 'Проверяю DSP-линию…' : 'Запрашиваю микрофон…';
  s.timer = setInterval(() => {
    if (session !== s) return;
    const now = performance.now();
    if (s.phase === 'connecting') {
      if (now - s.phaseStarted > 10000) {
        const error = new Error('connecting_timeout');
        error.code = 'connecting_timeout';
        fail(s, error);
      }
      return;
    }
    if (s.phase === 'warming') {
      if (now - s.phaseStarted > s.warmupTimeoutMs) {
        const error = new Error('model_unavailable');
        error.code = 'model_unavailable';
        fail(s, error);
      }
      return;
    }
    const progressDeadline = mode === 'rvc' ? 10000 : 6000;
    if (now - s.lastProgress > progressDeadline) {
      const error = new Error(mode === 'rvc' ? 'stalled' : 'Нет аудиоответа более 6 секунд.');
      if (mode === 'rvc') error.code = 'stalled';
      return fail(s, error);
    }
    const deviceMs = ((s.ctx.baseLatency || 0) + (s.ctx.outputLatency || 0)) * 1000;
    const extraDelayMs = Number(el('delay').value) * 1000;
    const pitchMs = mode === 'dsp' && Math.abs(settings().pitchSemitones) > .001 ? 40 : 0;
    // RVC queueMs already contains the full burst and its render-clock hold;
    // using the cumulative consumed-sample timestamp avoids adding that 2 s twice.
    const estimate = extraDelayMs + s.networkServerMs + s.queueMs + deviceMs + 20 + pitchMs;
    latencyEl.textContent = 'Дополнительная задержка: ' + Math.round(extraDelayMs) +
      ' мс · общая примерно: ' + Math.round(estimate) +
      ' мс · сеть + сервер: ' + Math.round(s.networkServerMs) + ' мс';
    const progress = mode === 'rvc'
      ? 'Отправлено кадров: ' + s.sent + ' · получено фрагментов: ' + s.received +
        ' · подтверждено: ' + s.acknowledgedSamples + ' сэмплов'
      : 'Отправлено / получено: ' + s.sent + ' / ' + s.received;
    el('diagnostics').textContent = progress + ' · обработка: ' + s.processingMs.toFixed(1) +
      ' мс · прерывания: ' + s.underruns + ' · сброшено: ' + s.dropped;
  }, 250);

  try {
    if (!window.isSecureContext) throw new Error('Нужен HTTPS.');
    s.ctx = new AudioContext({sampleRate: SAMPLE_RATE, latencyHint: 'interactive'});
    await s.ctx.resume();
    if (session !== s) return;
    if (s.ctx.sampleRate !== SAMPLE_RATE) throw new Error('Браузер не поддерживает аудио 48 кГц.');
    await s.ctx.audioWorklet.addModule('/static/audio-worklet.js?v=4');
    if (session !== s) return;

    s.node = new AudioWorkletNode(s.ctx, 'phone-audio', {
      numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1],
      channelCount: 1, channelCountMode: 'explicit'
    });
    // Configure the packet contract before source connection or any playback.
    s.node.port.postMessage({type: 'configure', mode});
    s.node.port.postMessage({type: 'delay', seconds: +el('delay').value});
    s.node.port.onmessage = ({data}) => {
      if (session !== s) return;
      try {
        if (data.type === 'error') {
          const error = new Error(data.code || 'protocol');
          error.code = data.code || 'protocol';
          error.serverMessage = data.message;
          throw error;
        }
        if (!s.ready || data.type !== 'capture') return;
        if (!data.pcm || data.pcm.byteLength !== CAPTURE_SAMPLES * 2) throw protocolError();
        if (s.socket.readyState !== WebSocket.OPEN) throw new Error('Соединение ещё не готово.');
        if (s.mode === 'rvc') {
          const inFlight = s.sentSamples - s.acknowledgedSamples;
          if (inFlight + CAPTURE_SAMPLES > MAX_RVC_INFLIGHT_SAMPLES ||
              s.socket.bufferedAmount > MAX_RVC_BUFFERED_BYTES) {
            const error = new Error('input_overload');
            error.code = 'input_overload';
            throw error;
          }
        } else if (s.pending.length > 25 || s.socket.bufferedAmount > 48000) {
          throw new Error('Сеть не успевает передавать звук. Подключитесь заново.');
        }

        el('input').value = Math.min(1, level(new Int16Array(data.pcm)) * 4);
        s.queueMs = Number.isFinite(data.queueMs) && data.queueMs >= 0 ? data.queueMs : 0;
        s.underruns = Number.isInteger(data.underruns) ? data.underruns : 0;
        s.dropped = Number.isInteger(data.dropped) ? data.dropped : 0;
        if (s.mode === 'rvc') {
          s.sentSamples += CAPTURE_SAMPLES;
          s.capturePositions.push({endSample: s.sentSamples, sentAt: performance.now()});
        } else {
          s.pending.push(performance.now());
        }
        s.sent++;
        s.socket.send(data.pcm);
      } catch (error) {
        fail(s, error);
      }
    };

    if (test) {
      const oscillator = s.ctx.createOscillator();
      oscillator.frequency.value = 440;
      const gain = s.ctx.createGain();
      gain.gain.value = .12;
      oscillator.connect(gain);
      gain.connect(s.node);
      s.source = oscillator;
    } else {
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
    }

    if (session !== s) return;
    const path = mode === 'rvc' ? '/ws/rvc' : '/ws/audio';
    s.socket = new WebSocket('wss://vm-voice-1.lan.awesomeio.ru' + path);
    s.socket.binaryType = 'arraybuffer';
    s.socket.onopen = () => {
      if (session !== s) return;
      const start = {type: 'start', sampleRate: SAMPLE_RATE, channels: 1, sampleFormat: 's16le'};
      if (mode === 'rvc') start.version = 1;
      else start.settings = settings();
      s.socket.send(JSON.stringify(start));
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
          if (message.type === 'warming' && mode === 'rvc') {
            if (!Number.isFinite(message.timeoutSeconds) || message.timeoutSeconds <= 0 || message.timeoutSeconds > 90) {
              throw protocolError();
            }
            s.phase = 'warming';
            s.phaseStarted = performance.now();
            s.warmupTimeoutMs = message.timeoutSeconds * 1000;
            statusEl.textContent = 'Модель прогревается. Это может занять до 90 секунд…';
            return;
          }
          if (message.type === 'ready') {
            if (s.ready || (mode === 'rvc' && !validateRvcReady(message))) throw protocolError();
            s.ready = true;
            s.phase = 'ready';
            s.lastProgress = performance.now();
            s.node.connect(s.ctx.destination);
            if (test) {
              s.source.start();
              s.testTimer = setTimeout(() => {
                if (session === s) stop(s.received
                  ? 'Проверка DSP-линии завершена: звук вернулся с сервера. Если тона не было слышно, проверьте громкость и устройство вывода.'
                  : 'DSP-линия не вернула тестовый тон. Проверьте VPN и повторите попытку.');
              }, 4000 + Number(el('delay').value) * 1000);
            }
            statusEl.textContent = test
              ? 'Тестовый тон DSP прозвучит после выбранной задержки'
              : mode === 'rvc'
                ? 'Phone Guy AI готов — говорите. Первый фрагмент появится после накопления и обработки.'
                : 'Простой телефонный эффект готов — говорите.';
            return;
          }
          if (message.type === 'metrics') {
            if (!Number.isFinite(message.processingMs) || message.processingMs < 0) throw protocolError();
            if (mode === 'dsp') {
              s.processingMs = message.processingMs;
              return;
            }
            if (!s.ready || !validateRvcMetrics(s, message)) throw protocolError();
            const acknowledged = s.capturePositions.find(position => position.endSample === message.consumedSamples);
            if (!acknowledged) throw protocolError();
            s.networkServerMs = performance.now() - acknowledged.sentAt;
            s.pendingMetrics = message;
            return;
          }
          throw protocolError();
        }

        if (mode === 'rvc') {
          const metrics = s.pendingMetrics;
          if (!s.ready || !metrics || !data || data.byteLength !== RVC_OUTPUT_BYTES ||
              data.byteLength !== metrics.outputSamples * 2) throw protocolError();
          const outputLevel = Math.min(1, level(new Int16Array(data)) * 4);
          if (session !== s) return;
          s.node.port.postMessage({type: 'play', pcm: data}, [data]);
          el('output').value = outputLevel;
          s.acknowledgedSamples = metrics.consumedSamples;
          s.expectedOutputStart += metrics.outputSamples;
          s.capturePositions = s.capturePositions.filter(position => position.endSample > s.acknowledgedSamples);
          s.processingMs = metrics.processingMs;
          s.pendingMetrics = null;
        } else {
          if (!data || data.byteLength !== CAPTURE_SAMPLES * 2 || !s.pending.length) throw protocolError();
          s.networkServerMs = performance.now() - s.pending.shift();
          const outputLevel = Math.min(1, level(new Int16Array(data)) * 4);
          if (session !== s) return;
          s.node.port.postMessage({type: 'play', pcm: data}, [data]);
          el('output').value = outputLevel;
        }
        s.lastProgress = performance.now();
        s.received++;
      } catch (error) {
        fail(s, error);
      }
    };
    s.socket.onerror = () => fail(s, new Error('Не удалось подключиться к серверу. Проверьте VPN.'));
    s.socket.onclose = () => {
      if (session === s) stop('Соединение закрыто. Проверьте VPN и нажмите «Начать» для повторного подключения.');
    };
  } catch (error) {
    fail(s, error);
  }
}

startButton.onclick = () => begin(false);
testButton.onclick = () => begin(true);
stopButton.onclick = () => stop();
profileEl.onchange = showProfile;
el('delay').oninput = () => {
  el('delay-value').textContent = el('delay').value;
  if (session?.node) {
    session.node.port.postMessage({type: 'delay', seconds: +el('delay').value});
    statusEl.textContent = session.mode === 'rvc'
      ? 'Дополнительная задержка изменена. Старый отложенный AI-звук очищен.'
      : 'Задержка изменена. Предыдущий отложенный звук очищен.';
  }
};
for (const id of ['pitch', 'effect', 'noise', 'gain']) {
  el(id).oninput = () => {
    el(id + '-value').textContent = el(id).value;
    if (session?.ready && session.mode === 'dsp' && session.socket.readyState === WebSocket.OPEN) {
      session.socket.send(JSON.stringify({type: 'settings', settings: settings()}));
    }
  };
}
showProfile();
setControls(false);
window.addEventListener('pagehide', () => stop());
