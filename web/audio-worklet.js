class PhoneAudio extends AudioWorkletProcessor {
  constructor() {
    super();
    this.capture = new Int16Array(960);
    this.captureOffset = 0;
    this.queue = [];
    this.playOffset = 0;
    this.playing = false;
    this.underruns = 0;
    this.dropped = 0;
    this.delayLine = new Float32Array(0);
    this.delayPosition = 0;
    this.port.onmessage = ({data}) => {
      if (data.type === 'delay') {
        if (!Number.isFinite(data.seconds) || data.seconds < 0 || data.seconds > 10) return;
        // Reset queued delayed audio when changing delay; never replay old speech.
        this.delayLine = new Float32Array(Math.round(data.seconds * 48000));
        this.delayPosition = 0;
        return;
      }
      if (data.type !== 'play' || data.pcm.byteLength !== 1920) return;
      if (this.queue.length >= 20) {
        this.queue.shift();
        this.playOffset = 0;
        this.dropped++;
      }
      this.queue.push(new Int16Array(data.pcm));
    };
  }

  process(inputs, outputs) {
    const input = inputs[0]?.[0];
    const output = outputs[0][0];
    if (!this.playing && this.queue.length >= 8) this.playing = true;
    for (let i = 0; i < output.length; i++) {
      if (input) {
        this.capture[this.captureOffset++] = Math.round(Math.max(-1, Math.min(32767 / 32768, input[i])) * 32768);
        if (this.captureOffset === 960) {
          const pcm = this.capture.buffer;
          this.port.postMessage({type: 'capture', pcm, queueMs: (this.queue.length * 960 - this.playOffset) / 48,
            underruns: this.underruns, dropped: this.dropped}, [pcm]);
          this.capture = new Int16Array(960);
          this.captureOffset = 0;
        }
      }
      output[i] = 0;
      if (this.playing) {
        if (!this.queue.length) {
          this.playing = false;
          this.underruns++;
        } else {
          output[i] = this.queue[0][this.playOffset++] / 32768;
          if (this.playOffset === 960) {this.queue.shift(); this.playOffset = 0;}
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
