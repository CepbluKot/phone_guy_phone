package conference

import "testing"

// The audio path copies one 20 ms frame per hop (model output split, per-listener fanout). These benchmarks pin down what
// those copies cost, so a future "optimization" can be judged against numbers instead of intuition.

func BenchmarkSourceFrame(b *testing.B) {
	pcm := make([]byte, phraseFrames*frameBytes)
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		_ = sourceFrame(pcm, "A", i%loopFrames)
	}
}

func BenchmarkFanoutCopy(b *testing.B) {
	frame := make([]byte, frameBytes)
	b.SetBytes(int64(frameBytes))
	b.ReportAllocs()
	for i := 0; i < b.N; i++ {
		_ = append([]byte(nil), frame...)
	}
}
