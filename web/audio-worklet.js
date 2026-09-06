'use strict';

const SAMPLE_RATE = 48000;
const CAPTURE_SAMPLES = 960;
const DSP_PACKET_SAMPLES = 960;
const DSP_QUEUE_PACKETS = 20;
const DSP_PREBUFFER_PACKETS = 8;
const RVC_PACKET_SAMPLES = 96000;
const RVC_QUEUE_SAMPLES = SAMPLE_RATE * 12;
const RVC_INITIAL_HOLD_SAMPLES = Math.round(SAMPLE_RATE * .25);

class PhoneAudio extends AudioWorkletProcessor {
  constructor() {
    super();
    this.capture = new Int16Array(CAPTURE_SAMPLES);
    this.captureOffset = 0;
    this.mode = 'dsp';
    this.queue = [];
    this.queuedSamples = 0;
    this.playOffset = 0;
    this.playing = false;
    this.holdSamples = 0;
    this.overloaded = false;
    this.underruns = 0;
    this.dropped = 0;
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
      if (data.mode !== 'rvc' && data.mode !== 'dsp') return;
      this.clearPlayback();
      this.mode = data.mode;
      return;
    }
    if (data.type === 'delay') {
      if (!Number.isFinite(data.seconds) || data.seconds < 0 || data.seconds > 10) return;
      this.delayLine = new Float32Array(Math.round(data.seconds * SAMPLE_RATE));
      this.delayPosition = 0;
      // An RVC packet contains old speech spanning two seconds. A delay change
      // must not let any part of that packet leak into the new delay setting.
      if (this.mode === 'rvc') this.clearPlayback();
      return;
    }
    if (data.type !== 'play' || !data.pcm) return;
    const expectedBytes = (this.mode === 'rvc' ? RVC_PACKET_SAMPLES : DSP_PACKET_SAMPLES) * 2;
    if (data.pcm.byteLength !== expectedBytes) return;
    const packet = new Int16Array(data.pcm);

    if (this.mode === 'rvc') {
      if (this.overloaded) return;
      if (this.queuedSamples + packet.length > RVC_QUEUE_SAMPLES) {
        this.overloaded = true;
        this.port.postMessage({
          type: 'error', code: 'overloaded',
          message: 'Воспроизведение не успевает за обработкой. Остановите сеанс и повторите запуск.'
        });
        return;
      }
      if (!this.queue.length && !this.playing) this.holdSamples = RVC_INITIAL_HOLD_SAMPLES;
      this.queue.push(packet);
      this.queuedSamples += packet.length;
      return;
    }

    if (this.queue.length >= DSP_QUEUE_PACKETS) {
      const discarded = this.queue[0].length - this.playOffset;
      this.queue.shift();
      this.queuedSamples -= discarded;
      this.playOffset = 0;
      this.dropped++;
    }
    this.queue.push(packet);
    this.queuedSamples += packet.length;
  }

  queueMilliseconds() {
    const held = this.mode === 'rvc' && !this.playing ? this.holdSamples : 0;
    return (this.queuedSamples + held) * 1000 / SAMPLE_RATE;
  }

  process(inputs, outputs) {
    const input = inputs[0]?.[0];
    const output = outputs[0][0];
    if (this.mode === 'dsp' && !this.playing && this.queue.length >= DSP_PREBUFFER_PACKETS) {
      this.playing = true;
    }

    for (let i = 0; i < output.length; i++) {
      if (input) {
        this.capture[this.captureOffset++] = Math.round(
          Math.max(-1, Math.min(32767 / 32768, input[i])) * 32768
        );
        if (this.captureOffset === CAPTURE_SAMPLES) {
          const pcm = this.capture.buffer;
          this.port.postMessage({
            type: 'capture', pcm, queueMs: this.queueMilliseconds(),
            underruns: this.underruns, dropped: this.dropped
          }, [pcm]);
          this.capture = new Int16Array(CAPTURE_SAMPLES);
          this.captureOffset = 0;
        }
      }

      output[i] = 0;
      if (this.mode === 'rvc' && !this.playing && this.queue.length) {
        if (this.holdSamples > 0) this.holdSamples--;
        else this.playing = true;
      }
      if (this.playing) {
        if (!this.queue.length) {
          this.playing = false;
          this.underruns++;
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
