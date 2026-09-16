'use strict';

const FRAME_SAMPLES = 960;
const LEVEL_INTERVAL_SAMPLES = 4800;

class PhoneGuyMicrophone extends AudioWorkletProcessor {
  constructor() {
    super();
    this.pending = new Int16Array(FRAME_SAMPLES);
    this.offset = 0;
    this.levelSquares = 0;
    this.levelSamples = 0;
  }

  process(inputs) {
    const channels = inputs[0];
    const input = channels && channels[0];
    if (!input) return true;
    for (let index = 0; index < input.length; index++) {
      const sample = Math.max(-1, Math.min(1, input[index]));
      this.levelSquares += sample * sample;
      this.levelSamples++;
      this.pending[this.offset++] = sample < 0 ? sample * 32768 : sample * 32767;
      if (this.offset === FRAME_SAMPLES) {
        const frame = this.pending.buffer;
        this.port.postMessage({type: 'frame', pcm: frame}, [frame]);
        this.pending = new Int16Array(FRAME_SAMPLES);
        this.offset = 0;
      }
    }
    if (this.levelSamples >= LEVEL_INTERVAL_SAMPLES) {
      this.port.postMessage({
        type: 'level',
        value: Math.min(1, Math.sqrt(this.levelSquares / this.levelSamples) * 4),
      });
      this.levelSquares = 0;
      this.levelSamples = 0;
    }
    return true;
  }
}

registerProcessor('phoneguy-microphone', PhoneGuyMicrophone);
