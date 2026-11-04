// Package quota queries account credits independently of chat routing and check-in.
package quota

import (
	"fmt"
	"math/big"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/baixiaoshengofficial/codebuddy2api/internal/credential"
)

type Balance struct {
	refreshAt       time.Time
	Remaining       string `json:"remaining"`
	Total           string `json:"total"`
	Used            string `json:"used"`
	Packages        int    `json:"packages"`
	NextExpiryAt    *int64 `json:"next_expiry_at"`
	ExpiringCredits string `json:"expiring_credits"`
}

var china = time.FixedZone("UTC+8", 8*3600)

// Amounts stay decimal strings across the API; upstream Precise fields take priority.
func amount(d credential.Data, keys ...string) (*big.Rat, error) {
	for _, key := range keys {
		if d[key] == nil {
			continue
		}
		raw := d.Text(key)
		if len(raw) > 64 || strings.ContainsAny(raw, "/eE") {
			return nil, fmt.Errorf("invalid credit amount")
		}
		n, ok := new(big.Rat).SetString(raw)
		if !ok || n.Sign() < 0 {
			return nil, fmt.Errorf("invalid credit amount")
		}
		// Official amounts have up to eight fractional places. Accept up to eighteen,
		// without rounding an upstream value into an apparently valid balance.
		if parts := strings.Split(raw, "."); len(parts) > 2 || (len(parts) == 2 && len(parts[1]) > 18) {
			return nil, fmt.Errorf("unsupported credit precision")
		}
		return n, nil
	}
	return nil, fmt.Errorf("missing credit amount")
}
func decimal(n *big.Rat) string {
	s := strings.TrimRight(strings.TrimRight(n.FloatString(18), "0"), ".")
	if s == "" {
		return "0"
	}
	return s
}
func timestamp(v any) (time.Time, error) {
	if v == nil || fmt.Sprint(v) == "" || fmt.Sprint(v) == "0" {
		return time.Time{}, nil
	}
	raw := fmt.Sprint(v)
	if n, err := strconv.ParseInt(raw, 10, 64); err == nil {
		if n > 1e12 {
			return time.UnixMilli(n), nil
		}
		return time.Unix(n, 0), nil
	}
	for _, layout := range []string{time.RFC3339Nano, "2006-01-02 15:04:05"} {
		if t, err := time.ParseInLocation(layout, raw, china); err == nil {
			return t, nil
		}
	}
	return time.Time{}, fmt.Errorf("invalid credit validity period")
}
func earlier(a, b time.Time) time.Time {
	if a.IsZero() || (!b.IsZero() && b.Before(a)) {
		return b
	}
	return a
}
func later(a, b time.Time) time.Time {
	if b.After(a) {
		return b
	}
	return a
}

// normalize counts only current valid credit resources, with the current slice
// limiting time-sliced packages. Future cycles and expired resources are excluded.
func normalize(rows []credential.Data, now time.Time) (Balance, error) {
	total, remaining := new(big.Rat), new(big.Rat)
	type expiry struct {
		at     time.Time
		credit *big.Rat
	}
	expiries := []expiry{}
	count := 0
	var refreshAt time.Time
	for _, row := range rows {
		if row.Text("CapacityUnit") == "" {
			return Balance{}, fmt.Errorf("missing resource unit")
		}
		if row.Text("CapacityUnit") != "credits" {
			continue
		}
		if row["Status"] == nil {
			return Balance{}, fmt.Errorf("missing resource status")
		}
		status, err := strconv.Atoi(row.Text("Status"))
		if err != nil {
			return Balance{}, fmt.Errorf("invalid resource status")
		}
		if status != 0 && status != 3 {
			continue
		}
		start, err := timestamp(row["DeductionStartTime"])
		if err != nil {
			return Balance{}, err
		}
		end, err := timestamp(row["DeductionEndTime"])
		if err != nil {
			return Balance{}, err
		}
		cycleStart, err := timestamp(row["CycleStartTime"])
		if err != nil {
			return Balance{}, err
		}
		cycleEnd, err := timestamp(row["CycleEndTime"])
		if err != nil {
			return Balance{}, err
		}
		start, end = later(start, cycleStart), earlier(end, cycleEnd)
		if start.After(now) {
			refreshAt = earlier(refreshAt, start)
			continue
		}
		if !end.IsZero() && !end.After(now) {
			continue
		}
		if row.Number("CapacityType") != 1 && row.Number("CapacityType") != 2 && row.Number("CapacityType") != 4 {
			return Balance{}, fmt.Errorf("unsupported credit capacity type")
		}
		size, err := amount(row, "CycleCapacitySizePrecise", "CycleCapacitySize")
		if err != nil {
			return Balance{}, err
		}
		left, err := amount(row, "CycleCapacityRemainPrecise", "CycleCapacityRemain")
		if err != nil {
			return Balance{}, err
		}
		if row.Number("CapacityType") == 4 {
			slices, ok := row["SlicePeriodUsageDetails"].([]any)
			if !ok {
				return Balance{}, fmt.Errorf("missing sliced credit usage")
			}
			found := false
			for _, raw := range slices {
				slice, ok := raw.(map[string]any)
				if !ok {
					return Balance{}, fmt.Errorf("invalid sliced credit usage")
				}
				d := credential.Data(slice)
				a, err := timestamp(d["SlicePeriodStartTime"])
				if err != nil {
					return Balance{}, err
				}
				b, err := timestamp(d["SlicePeriodEndTime"])
				if err != nil {
					return Balance{}, err
				}
				if a.IsZero() || b.IsZero() || a.After(now) || !b.After(now) {
					continue
				}
				// Ambiguous overlapping slices must not double-count the same resource.
				if found {
					return Balance{}, fmt.Errorf("overlapping credit slices")
				}
				found = true
				sliceSize, err := amount(d, "SlicePeriodCapacitySizePrecise", "SlicePeriodCapacitySize")
				if err != nil {
					return Balance{}, err
				}
				sliceLeft, err := amount(d, "SlicePeriodCapacityRemainPrecise", "SlicePeriodCapacityRemain")
				if err != nil {
					return Balance{}, err
				}
				if sliceLeft.Cmp(sliceSize) > 0 {
					return Balance{}, fmt.Errorf("invalid sliced credit balance")
				}
				if sliceSize.Cmp(size) < 0 {
					size = sliceSize
				}
				if sliceLeft.Cmp(left) < 0 {
					left = sliceLeft
				}
				end = earlier(end, b)
			}
			if !found {
				return Balance{}, fmt.Errorf("current credit slice unavailable")
			}
		}
		if left.Cmp(size) > 0 {
			return Balance{}, fmt.Errorf("invalid credit balance")
		}
		refreshAt = earlier(refreshAt, end)
		count++
		total.Add(total, size)
		remaining.Add(remaining, left)
		if left.Sign() > 0 && !end.IsZero() {
			expiries = append(expiries, expiry{end, left})
		}
	}
	balance := Balance{refreshAt: refreshAt, Remaining: decimal(remaining), Total: decimal(total), Used: decimal(new(big.Rat).Sub(total, remaining)), Packages: count, ExpiringCredits: "0"}
	sort.Slice(expiries, func(i, j int) bool { return expiries[i].at.Before(expiries[j].at) })
	if len(expiries) > 0 {
		at := expiries[0].at.Unix()
		balance.NextExpiryAt = &at
		expiring := new(big.Rat)
		for _, e := range expiries {
			if e.at.Unix() == at {
				expiring.Add(expiring, e.credit)
			}
		}
		balance.ExpiringCredits = decimal(expiring)
	}
	return balance, nil
}
