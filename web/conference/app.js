'use strict';

const ENDPOINT = 'wss://vm-voice-1.lan.awesomeio.ru/ws/conference';
const SAMPLE_RATE = 48000;
const FRAME_BYTES = 1920;
const el = id => document.getElementById(id);
const listenButton = el('listen');
const disconnectButton = el('disconnect');
const statusEl = el('status');
const roomStatusEl = el('room-status');
let session = null;

const errors = {
  invalid_control: 'Сервис отклонил запрос прослушивания.',
  listener_limit: 'В комнате уже максимальное число слушателей.',
  asterisk_unavailable: 'Телефонная комната недоступна.',
  upstream_unavailable: 'Источник конференции недоступен.',
  slow_listener: 'Воспроизведение не успевает за комнатой.',
  overloaded: 'Комната перегружена. Подключитесь снова.'
};

function pcmLevel(buffer) {
  const pcm = new Int16Array(buffer);
  if (!pcm.length) return 0;
  let sum = 0;
  for (const value of pcm) sum += (value / 32768) ** 2;
  return Math.min(1, Math.sqrt(sum / pcm.length) * 4);
}

function controls(listening) {
  listenButton.disabled = listening;
  disconnectButton.disabled = !listening;
}

function close(s, message = 'Отключено') {
  if (session !== s) return;
  session = null;
  try {
    if (s.socket?.readyState === WebSocket.OPEN) s.socket.send(JSON.stringify({type: 'stop'}));
  } catch {}
  try {s.socket?.close();} catch {}
  try {s.node?.port.postMessage({type: 'stop'});} catch {}
  try {s.node?.disconnect();} catch {}
  try {s.ctx?.close();} catch {}
  controls(false);
  el('output').value = 0;
  statusEl.textContent = message;
  roomStatusEl.textContent = 'Не подключена';
}

function validReady(message) {
  return message.version === 1 && message.sampleRate === SAMPLE_RATE &&
    message.channels === 1 && message.sampleFormat === 's16le' && message.frameBytes === FRAME_BYTES;
}

async function listen() {
  if (session) return;
  const s = {ready: false};
  session = s;
  controls(true);
  statusEl.textContent = 'Подключаю воспроизведение…';
  roomStatusEl.textContent = 'Подключение';
  try {
    if (!window.isSecureContext) throw new Error('Нужен HTTPS для воспроизведения.');
    s.ctx = new AudioContext({latencyHint: 'interactive'});
    await s.ctx.resume();
    if (session !== s) return;
    await s.ctx.audioWorklet.addModule('/static/conference/audio-worklet.js');
    if (session !== s) return;
    s.node = new AudioWorkletNode(s.ctx, 'conference-listener', {
      numberOfInputs: 0, numberOfOutputs: 1, outputChannelCount: [1]
    });
    s.node.port.onmessage = ({data}) => {
      if (session === s && data?.type === 'error') close(s, errors[data.code] || 'Ошибка воспроизведения.');
    };
    s.socket = new WebSocket(ENDPOINT);
    s.socket.binaryType = 'arraybuffer';
    s.socket.onopen = () => {
      if (session === s) s.socket.send(JSON.stringify({type: 'listen', version: 1}));
    };
    s.socket.onmessage = ({data}) => {
      if (session !== s) return;
      try {
        if (typeof data === 'string') {
          const message = JSON.parse(data);
          if (message.type === 'preparing') {
            statusEl.textContent = 'Комната в подготовке…';
            roomStatusEl.textContent = 'Подготовка';
            return;
          }
          if (message.type === 'ready' && !s.ready && validReady(message)) {
            s.ready = true;
            s.node.connect(s.ctx.destination);
            statusEl.textContent = 'Вы слушаете комнату.';
            roomStatusEl.textContent = 'Активна';
            return;
          }
          if (message.type === 'error') throw new Error(errors[message.code] || 'Ошибка комнаты.');
          if (message.type === 'stopped') return close(s, 'Комната остановлена.');
          throw new Error('Некорректный ответ комнаты.');
        }
        if (!s.ready || Object.prototype.toString.call(data) !== '[object ArrayBuffer]' ||
            data.byteLength !== FRAME_BYTES) {
          throw new Error('Некорректный аудиокадр комнаты.');
        }
        const outputLevel = pcmLevel(data);
        s.node.port.postMessage({type: 'play', pcm: data}, [data]);
        el('output').value = outputLevel;
      } catch (error) {
        close(s, error.message || 'Ошибка комнаты.');
      }
    };
    s.socket.onerror = () => close(s, 'Не удалось подключиться к комнате. Проверьте VPN.');
    s.socket.onclose = () => {
      if (session === s) close(s, 'Соединение с комнатой закрыто.');
    };
  } catch (error) {
    close(s, error.message || 'Не удалось начать прослушивание.');
  }
}

listenButton.onclick = () => listen();
disconnectButton.onclick = () => { if (session) close(session); };
controls(false);
window.addEventListener('pagehide', () => { if (session) close(session); });
