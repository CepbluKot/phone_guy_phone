'use strict';
const el = id => document.getElementById(id);
const startButton = el('start'), stopButton = el('stop'), testButton = el('test');
const statusEl = el('status'), latencyEl = el('latency');
let session = null;
const settings = () => ({
  pitchSemitones: +el('pitch').value, effectMix: +el('effect').value,
  noiseMix: +el('noise').value, outputGainDb: +el('gain').value
});
const errors = {
  NotAllowedError: 'Разрешите доступ к микрофону в настройках сайта.',
  NotFoundError: 'Микрофон не найден. Подключите устройство.',
  NotReadableError: 'Не удалось открыть микрофон: проверьте, не занят ли он.',
  busy: 'Линия занята другой вкладкой. Остановите её и попробуйте снова.',
  invalid_frame: 'Сервер отклонил аудиопакет. Обновите страницу.'
};
function level(pcm) {
  let sum = 0;
  for (const value of pcm) sum += (value / 32768) ** 2;
  return Math.sqrt(sum / pcm.length);
}
function stop(message = 'Остановлено') {
  const s = session;
  session = null;
  if (s) {
    clearInterval(s.timer); clearTimeout(s.testTimer);
    s.stream?.getTracks().forEach(track => track.stop());
    if (s.source?.stop) {try {s.source.stop();} catch {}}
    s.source?.disconnect(); s.node?.disconnect();
    s.socket?.close(); s.ctx?.close();
  }
  startButton.disabled = false; testButton.disabled = false; stopButton.disabled = true;
  el('input').value = 0; el('output').value = 0;
  statusEl.textContent = message;
}
function fail(s, error) {
  if (session === s) stop(errors[error.name] || errors[error.message] || ('Ошибка: ' + error.message));
}
async function begin(test = false) {
  if (session) return;
  const s = {sent: 0, received: 0, pending: [], lastReply: performance.now(), ready: false,
    queueMs: 0, rtt: 0, processingMs: 0, underruns: 0, dropped: 0};
  session = s;
  startButton.disabled = testButton.disabled = true; stopButton.disabled = false;
  statusEl.textContent = test ? 'Проверка линии…' : 'Запрашиваю микрофон…';
  try {
    if (!window.isSecureContext) throw new Error('Нужен HTTPS.');
    s.ctx = new AudioContext({sampleRate: 48000, latencyHint: 'interactive'});
    await s.ctx.resume();
    if (s.ctx.sampleRate !== 48000) throw new Error('Браузер не поддерживает аудио 48 кГц.');
    await s.ctx.audioWorklet.addModule('/static/audio-worklet.js?v=2');
    if (session !== s) return;
    s.node = new AudioWorkletNode(s.ctx, 'phone-audio', {numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1], channelCount: 1, channelCountMode: 'explicit'});
    if (test) {
      const oscillator = s.ctx.createOscillator();
      oscillator.frequency.value = 440;
      const gain = s.ctx.createGain(); gain.gain.value = 0.12;
      oscillator.connect(gain); gain.connect(s.node);
      s.source = oscillator;
    } else {
      s.stream = await navigator.mediaDevices.getUserMedia({audio: {channelCount: 1, echoCancellation: false, noiseSuppression: false, autoGainControl: false}});
      if (session !== s) {s.stream.getTracks().forEach(t => t.stop()); return;}
      s.source = s.ctx.createMediaStreamSource(s.stream);
      s.source.connect(s.node);
      s.stream.getTracks().forEach(t => {t.onended = () => fail(s, new Error('Микрофон отключён.'));});
    }
    s.socket = new WebSocket('wss://vm-voice-1.lan.awesomeio.ru/ws/audio');
    s.socket.binaryType = 'arraybuffer';
    s.node.port.onmessage = ({data}) => {
      if (session !== s || !s.ready || data.type !== 'capture') return;
      try {
        if (s.socket.readyState !== WebSocket.OPEN || s.pending.length > 25 || s.socket.bufferedAmount > 48000) throw new Error('Сеть не успевает передавать звук. Подключитесь заново.');
        el('input').value = Math.min(1, level(new Int16Array(data.pcm)) * 4);
        s.queueMs = data.queueMs; s.underruns = data.underruns; s.dropped = data.dropped;
        s.pending.push(performance.now()); s.sent++;
        s.socket.send(data.pcm);
      } catch (error) {fail(s, error);}
    };
    s.socket.onopen = () => {
      if (session === s) s.socket.send(JSON.stringify({type: 'start', sampleRate: 48000, channels: 1, sampleFormat: 's16le', settings: settings()}));
    };
    s.socket.onmessage = ({data}) => {
      if (session !== s) return;
      try {
        if (typeof data === 'string') {
          const message = JSON.parse(data);
          if (message.type === 'error') throw new Error(message.code);
          if (message.type === 'metrics') {s.processingMs = message.processingMs; return;}
          if (message.type === 'ready') {
            s.ready = true; s.lastReply = performance.now();
            s.node.connect(s.ctx.destination);
            if (test) {
              s.source.start();
              s.testTimer = setTimeout(() => {
                if (session === s) stop(s.received ? 'Проверка завершена: звук вернулся с сервера. Если тона не было слышно, проверьте громкость и устройство вывода.' : 'Нет ответа со звуком.');
              }, 4000);
            }
            statusEl.textContent = test ? 'Должен быть слышен тестовый тон' : 'Линия подключена — говорите в микрофон';
          }
          return;
        }
        if (data.byteLength !== 1920 || !s.pending.length) throw new Error('Неверный ответ аудиосервера.');
        s.rtt = performance.now() - s.pending.shift();
        s.lastReply = performance.now(); s.received++;
        el('output').value = Math.min(1, level(new Int16Array(data)) * 4);
        s.node.port.postMessage({type: 'play', pcm: data}, [data]);
      } catch (error) {fail(s, error);}
    };
    s.socket.onerror = () => fail(s, new Error('Не удалось подключиться к серверу. Проверьте VPN.'));
    s.socket.onclose = () => {if (session === s) stop('Соединение закрыто. Нажмите «Начать» для подключения.');};
    s.timer = setInterval(() => {
      if (session !== s) return;
      if (performance.now() - s.lastReply > 6000) return fail(s, new Error('Нет аудиоответа более 6 секунд.'));
      const deviceMs = ((s.ctx.baseLatency || 0) + (s.ctx.outputLatency || 0)) * 1000;
      const estimate = s.rtt + s.queueMs + deviceMs + 20 + (Math.abs(settings().pitchSemitones) > .001 ? 40 : 0);
      latencyEl.textContent = 'Задержка ≈ ' + Math.round(estimate) + ' мс · сеть + сервер: ' + Math.round(s.rtt) + ' мс';
      el('diagnostics').textContent = 'Отправлено / получено: ' + s.sent + ' / ' + s.received + ' · обработка: ' + s.processingMs.toFixed(1) + ' мс · прерывания: ' + s.underruns + ' · сброшено: ' + s.dropped;
    }, 250);
  } catch (error) {fail(s, error);}
}
startButton.onclick = () => begin(false);
testButton.onclick = () => begin(true);
stopButton.onclick = () => stop();
for (const id of ['pitch', 'effect', 'noise', 'gain']) {
  el(id).oninput = () => {
    el(id + '-value').textContent = el(id).value;
    if (session?.ready && session.socket.readyState === WebSocket.OPEN)
      session.socket.send(JSON.stringify({type: 'settings', settings: settings()}));
  };
}
window.addEventListener('pagehide', () => stop());
