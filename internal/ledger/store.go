// Package ledger persists request and routing diagnostics in the existing SQLite schema.
package ledger

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

type Data map[string]any
type Store struct{ db *sql.DB }

func Open(path string) (*Store, error) {
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		return nil, err
	}
	db, err := sql.Open("sqlite3", "file:"+filepath.ToSlash(path)+"?_busy_timeout=10000&_journal_mode=WAL&_foreign_keys=on")
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(1)
	s := &Store{db: db}
	if err = s.migrate(); err != nil {
		db.Close()
		return nil, err
	}
	if err := os.Chmod(path, 0600); err != nil {
		db.Close()
		return nil, err
	}
	return s, nil
}
func (s *Store) Close() error { return s.db.Close() }
func (s *Store) migrate() error {
	schema := `CREATE TABLE IF NOT EXISTS request_logs (
 id INTEGER PRIMARY KEY AUTOINCREMENT,request_id TEXT NOT NULL,conversation_id TEXT,created_at INTEGER NOT NULL,
 completed_at INTEGER,endpoint TEXT NOT NULL,model TEXT NOT NULL,client TEXT NOT NULL,client_detail TEXT,client_host TEXT,
 credential TEXT,credential_user_id TEXT,request_preview TEXT NOT NULL,response_preview TEXT,request_hash TEXT NOT NULL,
 is_streaming INTEGER NOT NULL DEFAULT 0,status_code INTEGER,status TEXT NOT NULL DEFAULT 'pending',latency_ms INTEGER,
 upstream_attempts INTEGER NOT NULL DEFAULT 1,input_tokens INTEGER,output_tokens INTEGER,total_tokens INTEGER,token_estimated INTEGER NOT NULL DEFAULT 0,error TEXT);
 CREATE INDEX IF NOT EXISTS idx_request_logs_created_at ON request_logs(created_at DESC);
 CREATE INDEX IF NOT EXISTS idx_request_logs_model ON request_logs(model);
 CREATE INDEX IF NOT EXISTS idx_request_logs_credential ON request_logs(credential);
 CREATE TABLE IF NOT EXISTS upstream_attempts(id INTEGER PRIMARY KEY,request_record_id INTEGER NOT NULL,number INTEGER NOT NULL,
 credential TEXT,started_at INTEGER NOT NULL,completed_at INTEGER,status_code INTEGER,error TEXT,UNIQUE(request_record_id,number));
 CREATE TABLE IF NOT EXISTS routing_events(id INTEGER PRIMARY KEY,created_at INTEGER NOT NULL,previous TEXT,current TEXT,reason TEXT NOT NULL,request_record_id INTEGER);`
	if _, err := s.db.Exec(schema); err != nil {
		return err
	}
	rows, err := s.query("PRAGMA table_info(request_logs)")
	if err != nil {
		return err
	}
	columns := map[string]bool{}
	for _, row := range rows {
		columns[fmt.Sprint(row["name"])] = true
	}
	for name, kind := range map[string]string{"token_estimated": "INTEGER NOT NULL DEFAULT 0", "upstream_attempts": "INTEGER NOT NULL DEFAULT 1", "client_detail": "TEXT", "response_preview": "TEXT"} {
		if !columns[name] {
			if _, err := s.db.Exec("ALTER TABLE request_logs ADD COLUMN " + name + " " + kind); err != nil {
				return err
			}
		}
	}
	return nil
}
func (s *Store) query(query string, args ...any) ([]Data, error) {
	rows, err := s.db.Query(query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	columns, err := rows.Columns()
	if err != nil {
		return nil, err
	}
	out := []Data{}
	for rows.Next() {
		values := make([]any, len(columns))
		pointers := make([]any, len(columns))
		for i := range values {
			pointers[i] = &values[i]
		}
		if err := rows.Scan(pointers...); err != nil {
			return nil, err
		}
		row := Data{}
		for i, k := range columns {
			if v, ok := values[i].([]byte); ok {
				row[k] = string(v)
			} else {
				row[k] = values[i]
			}
		}
		out = append(out, row)
	}
	return out, rows.Err()
}

type Start struct {
	RequestID, ConversationID, Endpoint, Model, Client, ClientDetail, ClientHost, Credential, UserID, Preview, Hash string
	Streaming                                                                                                       bool
}

func (s *Store) Start(in Start) (int64, error) {
	result, err := s.db.Exec(`INSERT INTO request_logs(request_id,conversation_id,created_at,endpoint,model,client,client_detail,client_host,credential,credential_user_id,request_preview,request_hash,is_streaming,upstream_attempts) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,0)`, in.RequestID, nullable(in.ConversationID), time.Now().UnixMilli(), in.Endpoint, in.Model, in.Client, nullable(in.ClientDetail), nullable(in.ClientHost), nullable(in.Credential), nullable(in.UserID), in.Preview, in.Hash, in.Streaming)
	if err != nil {
		return 0, err
	}
	id, err := result.LastInsertId()
	if id%100 == 0 {
		if _, e := s.db.Exec("DELETE FROM request_logs WHERE id NOT IN (SELECT id FROM request_logs ORDER BY id DESC LIMIT 50000)"); e != nil {
			return id, e
		}
		if _, e := s.db.Exec("DELETE FROM upstream_attempts WHERE request_record_id NOT IN (SELECT id FROM request_logs)"); e != nil {
			return id, e
		}
	}
	return id, err
}

type Finish struct {
	Status, Attempts                  int
	Latency                           int64
	Usage                             map[string]any
	InputEstimate, OutputEstimate     int64
	Output, Error, Credential, UserID string
}

func token(usage map[string]any, keys ...string) (int64, bool) {
	for _, key := range keys {
		switch v := usage[key].(type) {
		case json.Number:
			if n, err := v.Int64(); err == nil {
				return n, true
			} else if n, err := v.Float64(); err == nil {
				return int64(n), true
			}
		case float64:
			return int64(v), true
		case int64:
			return v, true
		case int:
			return int64(v), true
		}
	}
	return 0, false
}
func (s *Store) Finish(id int64, in Finish) error {
	input, hasInput := token(in.Usage, "prompt_tokens", "input_tokens")
	output, hasOutput := token(in.Usage, "completion_tokens", "output_tokens")
	estimated := false
	if !hasInput {
		input = in.InputEstimate
		estimated = true
	}
	if !hasOutput {
		output = in.OutputEstimate
		estimated = true
	}
	total, hasTotal := token(in.Usage, "total_tokens")
	if !hasTotal {
		total = input + output
	}
	state := "success"
	if in.Status >= 400 || in.Error != "" {
		state = "error"
	}
	_, err := s.db.Exec(`UPDATE request_logs SET completed_at=?,status_code=?,status=?,latency_ms=?,upstream_attempts=?,input_tokens=?,output_tokens=?,total_tokens=?,token_estimated=?,response_preview=?,error=?,credential=COALESCE(?,credential),credential_user_id=COALESCE(?,credential_user_id) WHERE id=?`, time.Now().UnixMilli(), in.Status, state, in.Latency, in.Attempts, input, output, total, estimated, nullable(Preview(in.Output)), nullable(Preview(in.Error)), nullable(in.Credential), nullable(in.UserID), id)
	return err
}
func nullable(s string) any {
	if s == "" {
		return nil
	}
	return s
}
func (s *Store) StartAttempt(record int64, number int, name string) (int64, error) {
	res, err := s.db.Exec("INSERT INTO upstream_attempts(request_record_id,number,credential,started_at) VALUES(?,?,?,?)", record, number, name, time.Now().UnixMilli())
	if err != nil {
		return 0, err
	}
	return res.LastInsertId()
}
func (s *Store) FinishAttempt(id int64, status int, message string) error {
	_, err := s.db.Exec("UPDATE upstream_attempts SET completed_at=?,status_code=?,error=? WHERE id=?", time.Now().UnixMilli(), status, nullable(Preview(message)), id)
	return err
}
func (s *Store) Switch(previous, current, reason string, record *int64) error {
	tx, err := s.db.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if _, err = tx.Exec("INSERT INTO routing_events(created_at,previous,current,reason,request_record_id) VALUES(?,?,?,?,?)", time.Now().UnixMilli(), nullable(previous), nullable(current), reason, record); err != nil {
		return err
	}
	if _, err = tx.Exec("DELETE FROM routing_events WHERE id NOT IN (SELECT id FROM routing_events ORDER BY id DESC LIMIT 1000)"); err != nil {
		return err
	}
	return tx.Commit()
}
func (s *Store) Events() ([]Data, error) {
	return s.query("SELECT * FROM routing_events ORDER BY id DESC LIMIT 30")
}
func (s *Store) Detail(id int64) (Data, error) {
	rows, err := s.query("SELECT * FROM request_logs WHERE id=?", id)
	if err != nil || len(rows) == 0 {
		return nil, err
	}
	attempts, err := s.query("SELECT * FROM upstream_attempts WHERE request_record_id=? ORDER BY number", id)
	if err != nil {
		return nil, err
	}
	rows[0]["attempts"] = attempts
	return rows[0], nil
}
