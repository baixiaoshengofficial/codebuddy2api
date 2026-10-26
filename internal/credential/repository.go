package credential

import (
	"errors"
	"fmt"
	"io/fs"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
	"unicode"

	"github.com/baixiaoshengofficial/codebuddy2api/internal/jsonfile"
)

var ErrNotFound = errors.New("credential not found")
var ErrConflict = errors.New("credential changed")

// Repository keeps storage details out of account and routing services.
type Lister interface{ List() ([]Record, error) }
type Reader interface{ Get(string) (Data, error) }
type Updater interface {
	Reader
	CompareAndSwap(string, string, Data) error
}
type AccountRepository interface {
	Lister
	Updater
	Put(string, Data) error
}
type RoutingRepository interface {
	Lister
	Delete(string) error
}
type Repository interface {
	AccountRepository
	Delete(string) error
}
type JSONRepository struct {
	mu        sync.Mutex
	Directory string
}

func (r *JSONRepository) path(name string) (string, error) {
	if name == "" || filepath.Base(name) != name || strings.ContainsAny(name, `/\`) || !strings.HasSuffix(name, ".json") || name == "manager_state.json" {
		return "", ErrNotFound
	}
	return filepath.Join(r.Directory, name), nil
}
func (r *JSONRepository) get(name string) (Data, error) {
	p, err := r.path(name)
	if err != nil {
		return nil, err
	}
	d := Data{}
	if err = jsonfile.Read(p, &d); err != nil {
		if os.IsNotExist(err) {
			return nil, ErrNotFound
		}
		return nil, err
	}
	if d == nil || d.Text("bearer_token") == "" {
		return nil, ErrNotFound
	}
	return d, nil
}
func (r *JSONRepository) Get(name string) (Data, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	return r.get(name)
}
func (r *JSONRepository) List() ([]Record, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	files, err := os.ReadDir(r.Directory)
	if errors.Is(err, fs.ErrNotExist) {
		return []Record{}, nil
	}
	if err != nil {
		return nil, err
	}
	out := []Record{}
	for _, f := range files {
		if f.IsDir() || f.Name() == "manager_state.json" || !strings.HasSuffix(f.Name(), ".json") {
			continue
		}
		data, err := r.get(f.Name())
		if err != nil {
			slog.Warn("Skipping unreadable credential", "filename", f.Name())
			continue
		}
		out = append(out, Record{f.Name(), data})
	}
	return out, nil
}
func (r *JSONRepository) Put(name string, d Data) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	p, err := r.path(name)
	if err != nil {
		return err
	}
	if d.Text("bearer_token") == "" {
		return fmt.Errorf("empty credential token")
	}
	return jsonfile.Write(p, d)
}
func (r *JSONRepository) CompareAndSwap(name, oldToken string, d Data) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	old, err := r.get(name)
	if err != nil {
		return err
	}
	if old.Text("bearer_token") != oldToken {
		return ErrConflict
	}
	p, _ := r.path(name)
	return jsonfile.Write(p, d)
}
func (r *JSONRepository) Delete(name string) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	if _, err := r.get(name); err != nil {
		return err
	}
	p, _ := r.path(name)
	return os.Remove(p)
}

// Accounts coordinates login deduplication while leaving routing policy separate.
type Accounts struct {
	mu      sync.Mutex
	Repo    AccountRepository
	Product string
}

func (a *Accounts) Save(d Data) (string, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	records, err := a.Repo.List()
	if err != nil {
		return "", err
	}
	for _, r := range records {
		sameUser := d.Text("user_id") != "" && d.Text("user_id") != "unknown" && d.Text("user_id") == r.Data.Text("user_id")
		samePhone := a.Product == "workbuddy" && Phone(d) != "" && Phone(d) == Phone(r.Data)
		if d.Text("domain") == r.Data.Text("domain") && (sameUser || samePhone) {
			return r.Filename, a.Repo.CompareAndSwap(r.Filename, r.Data.Text("bearer_token"), LoginReplacement(r.Data, d))
		}
	}
	id := []rune{}
	for _, c := range d.Text("user_id") {
		if unicode.IsLetter(c) || unicode.IsDigit(c) || strings.ContainsRune("._-", c) {
			id = append(id, c)
			if len(id) == 20 {
				break
			}
		}
	}
	if len(id) == 0 {
		id = []rune("unknown")
	}
	stamp := d.Number("created_at")
	if stamp == 0 {
		stamp = time.Now().Unix()
	}
	name := fmt.Sprintf("%s_%s_%d.json", a.Product, string(id), stamp)
	if _, err := a.Repo.Get(name); err == nil {
		name = fmt.Sprintf("%s_%s_%d_%d.json", a.Product, string(id), stamp, time.Now().UnixNano())
	}
	return name, a.Repo.Put(name, d)
}
func Info(r Record, index int, now time.Time, product string) Data {
	d := r.Data
	u := d.Object("user_info")
	var expires, remaining any
	if d.Number("created_at") > 0 && d.Number("expires_in") > 0 {
		n := d.Number("created_at") + d.Number("expires_in")
		expires = n
		remaining = n - now.Unix()
	}
	email := first(u, "email")
	if email == "" && product == "workbuddy" {
		email = Phone(d)
	}
	if email == "" {
		email = d.Text("user_id")
	}
	out := Data{"index": index, "filename": r.Filename, "user_id": d.Text("user_id"), "email": email, "name": first(u, "name", "nickname"), "created_at": d["created_at"], "expires_in": d["expires_in"], "expires_at": expires, "time_remaining": remaining, "is_expired": Expired(d, now), "domain": d["domain"], "has_refresh_token": d.Text("refresh_token") != "", "session_state": d["session_state"], "token_type": d["token_type"], "scope": d["scope"]}
	if product == "workbuddy" {
		out["phone"] = Phone(d)
		out["product"] = "WorkBuddy"
	}
	return out
}
