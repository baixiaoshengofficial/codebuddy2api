// Package jsonfile provides atomic, private JSON persistence.
package jsonfile

import (
	"encoding/json"
	"os"
	"path/filepath"

	"github.com/baixiaoshengofficial/codebuddy2api/internal/jsonvalue"
)

func Read(path string, target any) error {
	data, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	return jsonvalue.Unmarshal(data, target)
}

// Write leaves the previous file intact on serialization or write failure.
func Write(path string, value any) error {
	data, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	if err = os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		return err
	}
	f, err := os.CreateTemp(filepath.Dir(path), ".write-*")
	if err != nil {
		return err
	}
	defer os.Remove(f.Name())
	if _, err = f.Write(data); err != nil {
		f.Close()
		return err
	}
	if err = f.Sync(); err != nil {
		f.Close()
		return err
	}
	if err = f.Close(); err != nil {
		return err
	}
	return os.Rename(f.Name(), path)
}
