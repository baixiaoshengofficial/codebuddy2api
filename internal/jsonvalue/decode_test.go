package jsonvalue

import (
	"encoding/json"
	"testing"
)

func TestExactNumbersAndSingleValue(t *testing.T) {
	var data map[string]any
	if err := Unmarshal([]byte(`{"uid":1234567890123456789,"value":1.25}`), &data); err != nil {
		t.Fatal(err)
	}
	if data["uid"].(json.Number).String() != "1234567890123456789" || data["value"].(json.Number).String() != "1.25" {
		t.Fatal(data)
	}
	for _, input := range []string{`{} {}`, `{"key":}`, `{} trailing`} {
		if err := Unmarshal([]byte(input), &data); err == nil {
			t.Fatal("invalid JSON accepted", input)
		}
	}
}
