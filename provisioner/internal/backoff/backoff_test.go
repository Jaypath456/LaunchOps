package backoff

import (
	"math"
	"math/rand/v2"
	"testing"
	"time"
)

// fixedRand returns lo (0) or hi (n-1) so tests can hit both ends of the range.
type fixedRand struct{ max bool }

func (f fixedRand) Int64N(n int64) int64 {
	if f.max {
		return n - 1
	}
	return 0
}

func TestCeilingDoublesThenCaps(t *testing.T) {
	base, cap := 100*time.Millisecond, time.Second
	for _, tc := range []struct {
		attempts int
		want     time.Duration
	}{
		{-3, 100 * time.Millisecond}, {0, 100 * time.Millisecond}, {1, 100 * time.Millisecond},
		{2, 200 * time.Millisecond}, {3, 400 * time.Millisecond}, {4, 800 * time.Millisecond},
		{5, time.Second}, {6, time.Second}, {100, time.Second}, {math.MaxInt, time.Second},
	} {
		if got := Ceiling(tc.attempts, base, cap); got != tc.want {
			t.Errorf("Ceiling(%d) = %v, want %v", tc.attempts, got, tc.want)
		}
	}
}

func TestCeilingNoOverflowAtExtremes(t *testing.T) {
	cap := time.Duration(math.MaxInt64)
	if got := Ceiling(1000, time.Nanosecond, cap); got != cap {
		t.Fatalf("Ceiling with max cap = %v, want %v", got, cap)
	}
	if got := Ceiling(3, 2*time.Second, time.Second); got != time.Second {
		t.Fatalf("base above cap = %v, want cap", got)
	}
	if got := Ceiling(3, 0, time.Second); got != 0 {
		t.Fatalf("zero base = %v, want 0", got)
	}
}

func TestFullJitterBoundsAreInclusive(t *testing.T) {
	base, cap := 100*time.Millisecond, time.Second
	for attempts := 1; attempts <= 8; attempts++ {
		ceiling := Ceiling(attempts, base, cap)
		if got := FullJitter(attempts, base, cap, fixedRand{max: false}); got != 0 {
			t.Errorf("attempt %d low end = %v, want 0", attempts, got)
		}
		if got := FullJitter(attempts, base, cap, fixedRand{max: true}); got != ceiling {
			t.Errorf("attempt %d high end = %v, want %v", attempts, got, ceiling)
		}
	}
	if got := FullJitter(50, base, cap, fixedRand{max: true}); got != cap {
		t.Fatalf("capped high end = %v, want %v", got, cap)
	}
}

func TestFullJitterRandomDrawsStayInBounds(t *testing.T) {
	base, cap := 10*time.Millisecond, 500*time.Millisecond
	sources := map[string]Rand{"seeded": rand.New(rand.NewPCG(1, 2)), "system": SystemRand{}}
	for name, rng := range sources {
		for attempts := 1; attempts <= 10; attempts++ {
			ceiling := Ceiling(attempts, base, cap)
			for range 1000 {
				if got := FullJitter(attempts, base, cap, rng); got < 0 || got > ceiling {
					t.Fatalf("%s attempt %d: %v outside [0, %v]", name, attempts, got, ceiling)
				}
			}
		}
	}
}

func TestFullJitterZeroCeiling(t *testing.T) {
	if got := FullJitter(3, 0, 0, fixedRand{max: true}); got != 0 {
		t.Fatalf("got %v, want 0", got)
	}
}
