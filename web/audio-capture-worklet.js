class ShunyanPcmCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.chunk = new Int16Array(Math.max(128, Math.round(sampleRate / 10)));
    this.cursor = 0;
  }

  process(inputs, outputs) {
    const output = outputs[0]?.[0];
    if (output) output.fill(0);
    const input = inputs[0]?.[0];
    if (!input) return true;

    for (let i = 0; i < input.length; i += 1) {
      const sample = Math.max(-1, Math.min(1, input[i]));
      this.chunk[this.cursor++] = sample < 0 ? Math.round(sample * 32768) : Math.round(sample * 32767);
      if (this.cursor === this.chunk.length) {
        const completed = this.chunk;
        this.port.postMessage(completed.buffer, [completed.buffer]);
        this.chunk = new Int16Array(Math.max(128, Math.round(sampleRate / 10)));
        this.cursor = 0;
      }
    }
    return true;
  }
}

registerProcessor('shunyan-pcm-capture', ShunyanPcmCapture);
