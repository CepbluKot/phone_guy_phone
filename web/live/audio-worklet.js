'use strict';

const SAMPLE_RATE = 48000;
const CAPTURE_SAMPLES = 960;
const MAX_PACKET_SAMPLES = 96000;
const QUEUE_SAMPLES = SAMPLE_RATE * 12;
const DEFAULT_HOLD_SAMPLES = Math.round(SAMPLE_RATE * .125);
const MIN_HOLD_SAMPLES = Math.round(SAMPLE_RATE * .05);
const MAX_HOLD_SAMPLES = Math.round(SAMPLE_RATE * .6);
const HOLD_GROWTH_SAMPLES = Math.round(SAMPLE_RATE * .06);

class PhoneAudio extends AudioWorkletProcessor {
  constructor() {
    super();
    this.capture = new Int16Array(CAPTURE_SAMPLES);
    this.captureOffset = 0;
    this.mode = 'idle';
    this.packetSamples = MAX_PACKET_SAMPLES;
    this.queue = [];
    this.queuedSamples = 0;
    this.playOffset = 0;
    this.playing = false;
    this.holdSamples = 0;
    this.holdBase = DEFAULT_HOLD_SAMPLES;
    this.overloaded = false;
    this.underruns = 0;
    this.delayLine = new Float32Array(0);
    this.delayPosition = 0;
    this.port.onmessage = ({data}) => this.onMessage(data);
  }

  clearPlayback() {
    this.queue = [];
    this.queuedSamples = 0;
    this.playOffset = 0;
    this.playing = false;
    this.holdSamples = 0;
    this.overloaded = false;
  }

  onMessage(data) {
    if (data.type === 'configure') {
      if (!Number.isInteger(data.packetSamples) ||
          data.packetSamples < CAPTURE_SAMPLES || data.packetSamples > MAX_PACKET_SAMPLES) return;
      if (Number.isInteger(data.holdSamples) &&
          data.holdSamples >= MIN_HOLD_SAMPLES && data.holdSamples <= MAX_HOLD_SAMPLES) {
        this.holdBase = data.holdSamples;
      }
      this.packetSamples = data.packetSamples;
      this.mode = 'rvc';
      this.clearPlayback();
      return;
    }
    if (data.type === 'delay') {
      if (!Number.isFinite(data.seconds) || data.seconds < 0 || data.seconds > 10) return;
      this.delayLine = new Float32Array(Math.round(data.seconds * SAMPLE_RATE));
      this.delayPosition = 0;
      // A packet contains old speech; it must not leak into the new delay.
      this.clearPlayback();
      return;
    }
    if (data.type !== 'play' || !data.pcm) return;
    if (this.mode !== 'rvc' || data.pcm.byteLength !== this.packetSamples * 2) return;
    const packet = new Int16Array(data.pcm);
    if (this.overloaded) return;
    if (this.queuedSamples + packet.length > QUEUE_SAMPLES) {
      this.overloaded = true;
      this.port.postMessage({type: 'error', code: 'overloaded'});
      return;
    }
    if (!this.queue.length && !this.playing) this.holdSamples = this.holdBase;
    this.queue.push(packet);
    this.queuedSamples += packet.length;
  }

  queueMilliseconds() {
    const held = !this.playing ? this.holdSamples : 0;
    return (this.queuedSamples + held) * 1000 / SAMPLE_RATE;
  }

  process(inputs, outputs) {
    const input = inputs[0]?.[0];
    const output = outputs[0][0];

    for (let i = 0; i < output.length; i++) {
      if (input) {
        this.capture[this.captureOffset++] = Math.round(
          Math.max(-1, Math.min(32767 / 32768, input[i])) * 32768
        );
        if (this.captureOffset === CAPTURE_SAMPLES) {
          const pcm = this.capture.buffer;
          this.port.postMessage({
            type: 'capture', pcm, queueMs: this.queueMilliseconds(),
            underruns: this.underruns
          }, [pcm]);
          this.capture = new Int16Array(CAPTURE_SAMPLES);
          this.captureOffset = 0;
        }
      }

      output[i] = 0;
      if (!this.playing && this.queue.length) {
        if (this.holdSamples > 0) this.holdSamples--;
        else this.playing = true;
      }
      if (this.playing) {
        if (!this.queue.length) {
          this.playing = false;
          this.underruns++;
          // Widen the jitter margin for the restart after starvation.
          this.holdBase = Math.min(this.holdBase + HOLD_GROWTH_SAMPLES, MAX_HOLD_SAMPLES);
        } else {
          output[i] = this.queue[0][this.playOffset++] / 32768;
          this.queuedSamples--;
          if (this.playOffset === this.queue[0].length) {
            this.queue.shift();
            this.playOffset = 0;
          }
        }
      }
      if (this.delayLine.length) {
        const current = output[i];
        output[i] = this.delayLine[this.delayPosition];
        this.delayLine[this.delayPosition] = current;
        this.delayPosition = (this.delayPosition + 1) % this.delayLine.length;
      }
    }
    return true;
  }
}

registerProcessor('phone-audio', PhoneAudio);
