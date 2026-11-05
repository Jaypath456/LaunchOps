// Package backoff computes retry delays. It is pure: randomness is injected.
package backoff

import (
	"math/rand/v2"
	"time"
)

// Rand is the randomness FullJitter needs. *rand.Rand from math/rand/v2 satisfies it.
type Rand interface {
	Int64N(n int64) int64
}

// SystemRand draws from the goroutine-safe top-level math/rand/v2 generator.
type SystemRand struct{}

func (SystemRand) Int64N(n int64) int64 { return rand.Int64N(n) }

// Ceiling returns min(cap, base * 2^(attempts-1)); attempts below 1 count as 1.
// It never overflows: doubling stops as soon as the next value would reach cap.
func Ceiling(attempts int, base, cap time.Duration) time.Duration {
	if base <= 0 || cap <= 0 {
		return 0
	}
	if base >= cap {
		return cap
	}
	d := base
	for i := 1; i < attempts; i++ {
		if d > cap/2 {
			return cap
		}
		d *= 2
	}
	return d
}

// FullJitter returns a uniformly random delay in [0, Ceiling(attempts, base, cap)].
func FullJitter(attempts int, base, cap time.Duration, rng Rand) time.Duration {
	ceiling := Ceiling(attempts, base, cap)
	if ceiling <= 0 {
		return 0
	}
	return time.Duration(rng.Int64N(int64(ceiling) + 1))
}
