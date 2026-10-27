package credential

import (
	"os"
	"sync"
	"time"

	"github.com/baixiaoshengofficial/codebuddy2api/internal/fault"
	"github.com/baixiaoshengofficial/codebuddy2api/internal/jsonfile"
)

type RoutingState struct {
	Mode         string             `json:"mode"`
	Current      string             `json:"current_filename"`
	Manual       string             `json:"manual_selected_filename"`
	CurrentIndex int                `json:"current_index"`
	ManualIndex  *int               `json:"manual_selected_index"`
	Cooldowns    map[string]float64 `json:"cooldowns"`
}
type StateStore interface {
	Load() (RoutingState, error)
	Save(RoutingState) error
}
type JSONState struct{ Path string }

func (s JSONState) Load() (RoutingState, error) {
	var state RoutingState
	err := jsonfile.Read(s.Path, &state)
	if os.IsNotExist(err) {
		err = nil
	}
	return state, err
}
func (s JSONState) Save(state RoutingState) error { return jsonfile.Write(s.Path, state) }

type Selection struct {
	Record
	Index int
	Auto  bool
}
type Router struct {
	mu          sync.Mutex
	repo        RoutingRepository
	persistence StateStore
	state       RoutingState
	usageCount  int64
	now         func() time.Time
	Cooldown    time.Duration
}

func NewRouter(repo RoutingRepository, stateStore StateStore, clock func() time.Time) (*Router, error) {
	if clock == nil {
		clock = time.Now
	}
	state, err := stateStore.Load()
	if err != nil {
		return nil, err
	}
	if state.Mode != "manual" {
		state.Mode = "auto"
	}
	if state.Cooldowns == nil {
		state.Cooldowns = map[string]float64{}
	}
	records, err := repo.List()
	if err != nil {
		return nil, err
	}
	if state.Current == "" && state.CurrentIndex >= 0 && state.CurrentIndex < len(records) {
		state.Current = records[state.CurrentIndex].Filename
	}
	if state.Mode == "manual" {
		found := false
		for _, row := range records {
			if row.Filename == state.Manual && state.Manual != "" {
				found = true
				state.Current = state.Manual
			}
		}
		if !found {
			state.Mode = "auto"
			state.Manual = ""
		}
	}
	return &Router{repo: repo, persistence: stateStore, state: state, now: clock, Cooldown: 5 * time.Minute}, nil
}
func (r *Router) Mode() string { r.mu.Lock(); defer r.mu.Unlock(); return r.state.Mode }
func (r *Router) cooled(name string) bool {
	return r.state.Cooldowns[name] > float64(r.now().UnixMilli())/1000
}
func indexOf(records []Record, name string) int {
	for i, row := range records {
		if row.Filename == name {
			return i
		}
	}
	return -1
}
func (r *Router) save(next RoutingState, records []Record) error {
	next.CurrentIndex = indexOf(records, next.Current)
	next.ManualIndex = nil
	if i := indexOf(records, next.Manual); i >= 0 {
		next.ManualIndex = &i
	}
	if err := r.persistence.Save(next); err != nil {
		return err
	}
	if r.state.Current != next.Current {
		r.usageCount = 0
	}
	r.state = next
	return nil
}
func cloneState(s RoutingState) RoutingState {
	out := s
	out.Cooldowns = map[string]float64{}
	for k, v := range s.Cooldowns {
		out.Cooldowns[k] = v
	}
	return out
}
func (r *Router) Select(filename string, index *int) (Selection, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	records, err := r.repo.List()
	if err != nil {
		return Selection{}, err
	}
	if index != nil && filename != "" {
		return Selection{}, fault.New(422, "invalid_selector", "Specify only one credential selector")
	}
	explicit := filename != "" || index != nil
	if index != nil {
		if *index < 0 || *index >= len(records) {
			return Selection{}, fault.New(401, "invalid_credential", "没有可用的 CodeBuddy 凭证")
		}
		filename = records[*index].Filename
	}
	if explicit {
		i := indexOf(records, filename)
		if i < 0 {
			return Selection{}, fault.New(409, "credential_changed", "凭证列表已变更，请刷新后重试")
		}
		if !Usable(records[i].Data, r.now()) {
			return Selection{}, fault.New(401, "expired_credential", "CodeBuddy 凭证已失效，请重新授权")
		}
		return Selection{records[i], i, false}, nil
	}
	state := cloneState(r.state)
	if state.Mode == "manual" {
		i := indexOf(records, state.Manual)
		if i >= 0 && Usable(records[i].Data, r.now()) {
			return Selection{records[i], i, false}, nil
		}
		state.Mode = "auto"
		state.Manual = ""
	}
	current := indexOf(records, state.Current)
	usable := 0
	for offset := 0; offset < len(records); offset++ {
		i := 0
		if current >= 0 {
			i = (current + offset) % len(records)
		} else {
			i = offset
		}
		row := records[i]
		if !Usable(row.Data, r.now()) {
			continue
		}
		usable++
		if r.cooled(row.Filename) {
			continue
		}
		if state.Current != row.Filename || state.Mode != r.state.Mode {
			state.Current = row.Filename
			if err := r.save(state, records); err != nil {
				return Selection{}, err
			}
		}
		r.usageCount++
		return Selection{row, i, true}, nil
	}
	if usable > 0 {
		return Selection{}, fault.New(402, "insufficient_credit", "所有可用凭证积分不足")
	}
	return Selection{}, fault.New(401, "no_credentials", "没有可用的 CodeBuddy 凭证")
}
func (r *Router) Exhausted(name string) (string, string, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	records, err := r.repo.List()
	if err != nil {
		return "", "", err
	}
	state := cloneState(r.state)
	before := state.Current
	state.Cooldowns[name] = float64(r.now().Add(r.Cooldown).UnixMilli()) / 1000
	if state.Mode == "auto" && state.Current == name {
		start := indexOf(records, name)
		for offset := 1; offset < len(records); offset++ {
			row := records[(start+offset)%len(records)]
			if Usable(row.Data, r.now()) && state.Cooldowns[row.Filename] <= float64(r.now().UnixMilli())/1000 {
				state.Current = row.Filename
				break
			}
		}
	}
	return before, state.Current, r.save(state, records)
}
func (r *Router) ClearCooldown(name string) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	records, err := r.repo.List()
	if err != nil {
		return err
	}
	state := cloneState(r.state)
	delete(state.Cooldowns, name)
	return r.save(state, records)
}
func (r *Router) SetMode(mode, filename string) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	if mode != "auto" && mode != "manual" {
		return fault.New(422, "invalid_mode", "请选择有效的模式或凭证")
	}
	records, err := r.repo.List()
	if err != nil {
		return err
	}
	state := cloneState(r.state)
	state.Mode = mode
	state.Manual = ""
	if mode == "manual" {
		if indexOf(records, filename) < 0 {
			return fault.New(409, "credential_changed", "凭证列表已变更，请刷新后重试")
		}
		state.Manual = filename
		state.Current = filename
		delete(state.Cooldowns, filename)
	}
	return r.save(state, records)
}
func (r *Router) Delete(filename string) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	if err := r.repo.Delete(filename); err != nil {
		return err
	}
	records, err := r.repo.List()
	if err != nil {
		return err
	}
	state := cloneState(r.state)
	delete(state.Cooldowns, filename)
	if state.Manual == filename {
		state.Mode = "auto"
		state.Manual = ""
	}
	if state.Current == filename {
		state.Current = ""
	}
	return r.save(state, records)
}
func (r *Router) Snapshot() (Data, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	records, err := r.repo.List()
	if err != nil {
		return nil, err
	}
	rows := []Data{}
	eligible := []int{}
	for i, row := range records {
		info := Info(row, i, r.now(), "codebuddy")
		state := "available"
		switch {
		case row.Data.Number("session_dead_at") > 0:
			state = "reauthorize"
		case !Usable(row.Data, r.now()):
			state = "expired"
		case r.cooled(row.Filename) && !(r.state.Mode == "manual" && r.state.Manual == row.Filename):
			state = "cooldown"
		default:
			if Expired(row.Data, r.now()) {
				state = "refreshable"
			}
			eligible = append(eligible, i)
		}
		info["state"] = state
		info["cooldown_until"] = nil
		if r.cooled(row.Filename) {
			info["cooldown_until"] = int64(r.state.Cooldowns[row.Filename] * 1000)
		}
		delete(info, "scope")
		delete(info, "token_type")
		rows = append(rows, info)
	}
	selected := indexOf(records, r.state.Current)
	if r.state.Mode == "manual" {
		selected = indexOf(records, r.state.Manual)
	}
	active := -1
	for _, i := range eligible {
		if i == selected {
			active = i
		}
	}
	if active < 0 && r.state.Mode == "auto" && len(eligible) > 0 {
		active = eligible[0]
		for _, i := range eligible {
			if i > selected {
				active = i
				break
			}
		}
	}
	var current, next any
	if active >= 0 {
		current = active
		if r.state.Mode == "auto" {
			for _, i := range eligible {
				if i > active {
					next = i
					break
				}
			}
			if next == nil && eligible[0] != active {
				next = eligible[0]
			}
		}
	}
	for i := range rows {
		rows[i]["is_current"] = i == active
	}
	return Data{"mode": r.state.Mode, "credentials": rows, "current_index": current, "next_index": next, "available": len(eligible), "cooldown_seconds": int(r.Cooldown.Seconds()), "usage_count": r.usageCount, "server_time": r.now().UnixMilli()}, nil
}
