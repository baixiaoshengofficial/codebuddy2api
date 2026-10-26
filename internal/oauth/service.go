package oauth

import (
	"context"
	"errors"
	"sync"
	"time"

	"github.com/baixiaoshengofficial/codebuddy2api/internal/credential"
	"github.com/baixiaoshengofficial/codebuddy2api/internal/fault"
)

type AccountSaver interface {
	Save(credential.Data) (string, error)
}
type IdentityStore interface {
	Get(string) (credential.Data, error)
	CompareAndSwap(string, string, credential.Data) error
}
type session struct {
	savedFilename string
	notified      bool
	mu            sync.Mutex
	filename      string
	original      Data
	deadline      time.Time
	result        Data
}
type Service struct {
	Provider Provider
	Store    IdentityStore
	Accounts AccountSaver
	Product  string
	OnSaved  func(string) error
	Now      func() time.Time
	mu       sync.Mutex
	sessions map[string]*session
}

func (s *Service) now() time.Time {
	if s.Now != nil {
		return s.Now()
	}
	return time.Now()
}
func (s *Service) Start(ctx context.Context, filename string) (Data, error) {
	var original Data
	if filename != "" {
		var err error
		original, err = s.Store.Get(filename)
		if err != nil {
			return nil, fault.New(404, "reauthorization_failed", "凭证不存在或已被删除，请刷新列表")
		}
	}
	result, err := s.Provider.Start(ctx)
	if err != nil {
		return nil, err
	}
	state := result.Text("auth_state")
	if state == "" || result.Text("verification_uri_complete") == "" {
		return nil, fault.New(502, "invalid_auth_response", "上游未返回完整授权链接")
	}
	ttl := result.Number("expires_in")
	if ttl <= 0 || ttl > 1800 {
		ttl = 900
	}
	if filename != "" && ttl > 900 {
		ttl = 900
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.sessions == nil {
		s.sessions = map[string]*session{}
	}
	for k, v := range s.sessions {
		if !v.deadline.After(s.now()) {
			delete(s.sessions, k)
		}
	}
	if _, ok := s.sessions[state]; ok {
		return nil, fault.New(409, "reauthorization_failed", "授权会话重复，请重新开始")
	}
	s.sessions[state] = &session{filename: filename, original: original, deadline: s.now().Add(time.Duration(ttl) * time.Second)}
	if filename != "" {
		result["filename"] = filename
	}
	return result, nil
}
func (s *Service) Poll(ctx context.Context, state string, reauthorize bool) (Data, error) {
	if state == "" {
		return nil, fault.New(400, "missing_parameters", "缺少必要的参数：auth_state")
	}
	s.mu.Lock()
	entry := s.sessions[state]
	s.mu.Unlock()
	if entry == nil || !entry.deadline.After(s.now()) || (entry.filename != "") != reauthorize {
		return nil, fault.New(410, "reauthorization_failed", "授权会话已过期，请重新开始")
	}
	entry.mu.Lock()
	defer entry.mu.Unlock()
	if entry.result != nil {
		return s.complete(entry)
	}
	if entry.filename != "" {
		current, err := s.Store.Get(entry.filename)
		if err != nil {
			return nil, fault.New(404, "reauthorization_failed", "凭证不存在或已被删除，请刷新列表")
		}
		if current.Text("bearer_token") != entry.original.Text("bearer_token") {
			return nil, fault.New(409, "reauthorization_failed", "该凭证已更新，请刷新列表后重新授权")
		}
	}
	token, pending, err := s.Provider.Poll(ctx, state)
	if err != nil {
		return nil, err
	}
	if pending {
		return nil, fault.New(400, "authorization_pending", "等待用户登录授权")
	}
	if token.Text("access_token") == "" && token.Text("bearer_token") == "" {
		return nil, fault.New(502, "invalid_token_response", "上游未返回有效凭证")
	}
	fresh := credential.Build(s.Product, token, s.now())
	filename := entry.filename
	if filename != "" {
		if !credential.SameIdentity(entry.original, fresh) {
			return nil, fault.New(409, "reauthorization_failed", "登录账号与所选凭证不一致，请使用原账号重新授权；原凭证未修改")
		}
		err = s.Store.CompareAndSwap(filename, entry.original.Text("bearer_token"), credential.LoginReplacement(entry.original, fresh))
	} else {
		filename, err = s.Accounts.Save(fresh)
	}
	if errors.Is(err, credential.ErrConflict) {
		return nil, fault.New(409, "reauthorization_failed", "该凭证已更新，请刷新列表后重试")
	}
	if err != nil {
		return nil, err
	}
	entry.result = Data{"saved": true, "message": "授权成功，凭证已保存"}
	if entry.filename != "" {
		entry.result["filename"] = filename
		entry.result["message"] = "重新授权成功，已更新原凭证"
	}
	entry.savedFilename = filename
	return s.complete(entry)
}

func (s *Service) complete(entry *session) (Data, error) {
	if !entry.notified && s.OnSaved != nil {
		if err := s.OnSaved(entry.savedFilename); err != nil {
			return nil, err
		}
	}
	entry.notified = true
	return credential.Clone(entry.result), nil
}
