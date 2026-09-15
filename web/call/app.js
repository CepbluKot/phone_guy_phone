'use strict';

const ENDPOINT = 'wss://vm-voice-1.lan.awesomeio.ru/ws/call';
const READY = {type: 'start', version: 1, sampleRate: 48000, channels: 1, sampleFormat: 's16le'};
const el = id => document.getElementById(id);
let call = null;

function controls(active) {
  el('call').disabled = active;
  el('hangup').disabled = !active;
}

function finish(current, message = 'Звонок завершён') {
  if (call !== current) return;
  call = null;
  try { if (current.socket?.readyState === WebSocket.OPEN) current.socket.send(JSON.stringify({type: 'stop'})); } catch {}
  try { current.socket?.close(); } catch {}
  try { current.node?.disconnect(); } catch {}
  try { current.source?.disconnect(); } catch {}
  for (const track of current.stream?.getTracks?.() || []) track.stop();
  try { current.context?.close(); } catch {}
  el('input').value = 0;
  el('status').textContent = message;
  controls(false);
}

async function start() {
  if (call) return;
  const current = {ready: false};
  call = current;
  controls(true);
  el('status').textContent = 'Запрашиваю микрофон…';
  try {
    if (!window.isSecureContext) throw new Error('Нужен HTTPS и VPN.');
    current.stream = await navigator.mediaDevices.getUserMedia({audio: {channelCount: 1, sampleRate: 48000}, video: false});
    if (call !== current) return;
    current.context = new AudioContext({latencyHint: 'interactive', sampleRate: 48000});
    await current.context.resume();
    if (call !== current) return;
    await current.context.audioWorklet.addModule('audio-worklet.js');
    current.source = current.context.createMediaStreamSource(current.stream);
    current.node = new AudioWorkletNode(current.context, 'phoneguy-microphone', {numberOfInputs: 1, numberOfOutputs: 0, channelCount: 1});
    current.node.port.onmessage = ({data}) => {
      if (call !== current || !data) return;
      if (data.type === 'level') el('input').value = data.value;
      if (data.type === 'frame' && current.ready && current.socket.readyState === WebSocket.OPEN) current.socket.send(data.pcm);
    };
    current.source.connect(current.node);
    current.socket = new WebSocket(ENDPOINT);
    current.socket.binaryType = 'arraybuffer';
    current.socket.onopen = () => {
      if (call === current) current.socket.send(JSON.stringify(READY));
    };
    current.socket.onmessage = ({data}) => {
      if (call !== current || typeof data !== 'string') return;
      try {
        const message = JSON.parse(data);
        if (message.type === 'preparing') el('status').textContent = 'Подготавливаю Phone Guy…';
        else if (message.type === 'ringing') { current.ready = true; el('status').textContent = 'Телефон звонит — сними трубку и говори.'; }
        else if (message.type === 'error') finish(current, message.code === 'busy' ? 'Линия сейчас занята.' : 'Не удалось начать звонок.');
        else if (message.type === 'stopped') finish(current);
        else throw new Error('invalid reply');
      } catch { finish(current, 'Некорректный ответ голосового сервиса.'); }
    };
    current.socket.onerror = () => finish(current, 'Нет соединения с голосовым сервисом. Проверь VPN.');
    current.socket.onclose = () => { if (call === current) finish(current); };
  } catch (error) {
    finish(current, error?.message || 'Не удалось открыть микрофон.');
  }
}

el('call').onclick = start;
el('hangup').onclick = () => { if (call) finish(call); };
window.addEventListener('pagehide', () => { if (call) finish(call); });
controls(false);
