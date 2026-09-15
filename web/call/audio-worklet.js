'use strict';

const FRAME_SAMPLES = 960;

class PhoneGuyMicrophone extends AudioWorkletProcessor {
  constructor() {
    super();
    this.pending = new Int16Array(FRAME_SAMPLES);
    this.offset = 0;
  }

  process(inputs) {
    const channels = inputs[0];
    const input = channels && channels[0];
    if (!input) return true;
    let level = 0;
    for (let index = 0; index < input.length; index++) {
      const sample = Math.max(-1, Math.min(1, input[index]));
      level += sample * sample;
      this.pending[this.offset++] = sample < 0 ? sample * 32768 : sample * 32767;
      if (this.offset === FRAME_SAMPLES) {
        const frame = this.pending.buffer;
        this.port.postMessage({type: 'frame', pcm: frame}, [frame]);
        this.pending = new Int16Array(FRAME_SAMPLES);
        this.offset = 0;
      }
    }
    this.port.postMessage({type: 'level', value: Math.min(1, Math.sqrt(level / input.length) * 4)});
    return true;
  }
}

registerProcessor('phoneguy-microphone', PhoneGuyMicrophone);
