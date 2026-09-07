'use strict';

const SAMPLE_RATE = 48000;
const CAPTURE_SAMPLES = 960; // 20ms
const BLOCK_SAMPLES = 14400; // 0.3s @ 48kHz, must match rvc_service/rt_server.py BLOCK_S
const QUEUE_SAMPLES = SAMPLE_RATE * 6;
const INITIAL_HOLD_SAMPLES = Math.round(SAMPLE_RATE * 0.1);

class RtAudio extends AudioWorkletProcessor {
  constructor() {
    super();
    this.capture = new Int16Array(CAPTURE_SAMPLES);
    this.captureOffset = 0;
    this.queue = [];
    this.queuedSamples = 0;
    this.playOffset = 0;
    this.playing = false;
    this.holdSamples = 0;
    this.overloaded = false;
    this.underruns = 0;
    this.port.onmessage = ({data}) => this.onMessage(data);
  }

  onMessage(data) {
    if (data.type !== 'play' || !data.pcm) return;
    if (data.pcm.byteLength % 2 !== 0) return;
    const packet = new Int16Array(data.pcm);
    if (this.overloaded) return;
    if (this.queuedSamples + packet.length > QUEUE_SAMPLES) {
      this.overloaded = true;
      this.port.postMessage({type: 'error', code: 'overloaded'});
      return;
    }
    if (!this.queue.length && !this.playing) this.holdSamples = INITIAL_HOLD_SAMPLES;
    this.queue.push(packet);
    this.queuedSamples += packet.length;
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
            type: 'capture', pcm, queueMs: this.queuedSamples * 1000 / SAMPLE_RATE
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
        } else {
          output[i] = this.queue[0][this.playOffset++] / 32768;
          this.queuedSamples--;
          if (this.playOffset === this.queue[0].length) {
            this.queue.shift();
            this.playOffset = 0;
          }
        }
      }
    }
    return true;
  }
}

registerProcessor('rt-audio', RtAudio);
void BLOCK_SAMPLES; // documented client/server contract, not enforced client-side
