'use strict';

const INPUT_RATE = 48000;
const RESERVE_SAMPLES = INPUT_RATE / 4;
const MAX_QUEUE_SAMPLES = INPUT_RATE * 4;

class ConferenceListener extends AudioWorkletProcessor {
  constructor() {
    super();
    this.chunks = [];
    this.head = 0;
    this.offset = 0;
    this.queuedSamples = 0;
    this.started = false;
    this.phase = 0;
    this.port.onmessage = ({data}) => this.receive(data);
  }

  receive(data) {
    if (!data || typeof data !== 'object') return;
    if (data.type === 'stop') {
      this.clear();
      return;
    }
    if (data.type !== 'play' || Object.prototype.toString.call(data.pcm) !== '[object ArrayBuffer]' || data.pcm.byteLength % 2) return;
    const pcm = new Int16Array(data.pcm);
    if (!pcm.length) return;
    if (this.queuedSamples + pcm.length > MAX_QUEUE_SAMPLES) {
      this.port.postMessage({type: 'error', code: 'overloaded'});
      return;
    }
    const samples = new Float32Array(pcm.length);
    for (let i = 0; i < pcm.length; i++) samples[i] = pcm[i] / 32768;
    this.chunks.push(samples);
    this.queuedSamples += samples.length;
  }

  clear() {
    this.chunks = [];
    this.head = 0;
    this.offset = 0;
    this.queuedSamples = 0;
    this.started = false;
    this.phase = 0;
  }

  peek() {
    const chunk = this.chunks[this.head];
    return chunk ? chunk[this.offset] : 0;
  }

  consume() {
    if (!this.queuedSamples) return;
    this.offset++;
    this.queuedSamples--;
    if (this.offset === this.chunks[this.head].length) {
      this.head++;
      this.offset = 0;
      if (this.head > 32) {
        this.chunks = this.chunks.slice(this.head);
        this.head = 0;
      }
    }
  }

  process(_, outputs) {
    const output = outputs[0]?.[0];
    if (!output) return true;
    output.fill(0);
    if (!this.started) {
      if (this.queuedSamples < RESERVE_SAMPLES) return true;
      this.started = true;
    }
    const step = INPUT_RATE / sampleRate;
    for (let i = 0; i < output.length; i++) {
      if (!this.queuedSamples) {
        this.started = false;
        break;
      }
      output[i] = this.peek();
      this.phase += step;
      while (this.phase >= 1 && this.queuedSamples) {
        this.consume();
        this.phase -= 1;
      }
    }
    return true;
  }
}

registerProcessor('conference-listener', ConferenceListener);
