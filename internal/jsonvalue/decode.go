// Package jsonvalue decodes JSON without rounding integer identifiers or schemas.
package jsonvalue

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
)

func Unmarshal(data []byte, target any) error {
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	if err := decoder.Decode(target); err != nil {
		return err
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		return fmt.Errorf("expected one JSON value")
	}
	return nil
}
