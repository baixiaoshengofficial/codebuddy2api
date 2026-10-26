// Package oauth implements provider device login independently of HTTP handlers.
package oauth

import (
	"context"
	"fmt"
	"net/http"
	"net/url"
	"sync"

	"github.com/baixiaoshengofficial/codebuddy2api/internal/credential"
	"github.com/baixiaoshengofficial/codebuddy2api/internal/fault"
	"github.com/baixiaoshengofficial/codebuddy2api/internal/id"
	"github.com/baixiaoshengofficial/codebuddy2api/internal/upstream"
)

type Data = credential.Data

type Product struct {
	Name, Platform, Agent, Method string
	Expiry, Interval              int
	PollContentType               bool
}

var CodeBuddy = Product{"codebuddy", "CLI", "CLI/1.0.8 CodeBuddy/1.0.8", "codebuddy_real_auth", 1800, 5, false}
var WorkBuddy = Product{"workbuddy", "workbuddy", "WorkBuddy/5.2.5", "workbuddy_auth", 900, 3, true}

type Provider interface {
	Start(context.Context) (Data, error)
	Poll(context.Context, string) (Data, bool, error)
}
type Client struct {
	Product   Product
	HTTP      upstream.Doer
	Endpoint  func() string
	mu        sync.Mutex
	lastState string
}

func (c *Client) headers(poll bool) http.Header {
	base, _ := url.Parse(c.Endpoint())
	requestID := id.New()
	h := http.Header{}
	for k, v := range map[string]string{"Accept": "application/json, text/plain, */*", "Cache-Control": "no-cache", "Pragma": "no-cache", "Connection": "close", "X-Requested-With": "XMLHttpRequest", "X-Domain": base.Host, "X-No-Authorization": "true", "X-No-User-Id": "true", "X-No-Enterprise-Id": "true", "X-No-Department-Info": "true", "User-Agent": c.Product.Agent, "X-Product": "SaaS", "X-Request-ID": requestID} {
		h.Set(k, v)
	}
	if !poll || c.Product.PollContentType {
		h.Set("Content-Type", "application/json")
	}
	if poll {
		span := id.New()[:16]
		for k, v := range map[string]string{"b3": requestID + "-" + span + "-1-", "X-B3-TraceId": requestID, "X-B3-SpanId": span, "X-B3-Sampled": "1", "X-B3-ParentSpanId": ""} {
			h.Set(k, v)
		}
	}
	return h
}
func (c *Client) Start(ctx context.Context) (Data, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	for attempt := 0; attempt < 2; attempt++ {
		nonce := id.New()
		q := url.Values{"platform": {c.Product.Platform}, "nonce": {nonce}}
		base := c.Endpoint()
		p, status, err := upstream.JSON(ctx, c.HTTP, "POST", base+"/v2/plugin/auth/state?"+q.Encode(), c.headers(false), Data{"nonce": nonce})
		if err != nil || status != 200 {
			break
		}
		payload := Data(p)
		data := payload.Object("data")
		state, sOK := data["state"].(string)
		link, lOK := data["authUrl"].(string)
		if payload["code"] == nil || payload.Number("code") != 0 || !sOK || !lOK || state == "" || link == "" {
			break
		}
		if state == c.lastState {
			continue
		}
		c.lastState = state
		return Data{"success": true, "method": c.Product.Method, "auth_state": state, "verification_uri_complete": link, "verification_uri": base, "platform": c.Product.Platform, "expires_in": c.Product.Expiry, "interval": c.Product.Interval, "status": "awaiting_login"}, nil
	}
	return nil, fault.New(502, "auth_start_failed", "启动授权失败，请稍后重试")
}
func (c *Client) Poll(ctx context.Context, state string) (Data, bool, error) {
	if state == "" {
		return nil, false, fault.New(400, "missing_parameters", "缺少必要的参数：auth_state")
	}
	q := url.Values{"state": {state}}
	p, status, err := upstream.JSON(ctx, c.HTTP, "GET", c.Endpoint()+"/v2/plugin/auth/token?"+q.Encode(), c.headers(true), nil)
	if err != nil || status != 200 {
		return nil, false, fault.New(502, "auth_error", "授权查询失败，请稍后重试")
	}
	payload := Data(p)
	if payload.Number("code") == 11217 {
		return nil, true, nil
	}
	data := payload.Object("data")
	access, ok := data["accessToken"].(string)
	if payload["code"] == nil || payload.Number("code") != 0 || !ok || access == "" {
		return nil, false, fault.New(400, "auth_error", "授权失败或已过期，请重新开始")
	}
	token := Data{"access_token": access, "token_type": "Bearer"}
	for from, to := range map[string]string{"tokenType": "token_type", "expiresIn": "expires_in", "refreshToken": "refresh_token", "sessionState": "session_state", "scope": "scope", "domain": "domain"} {
		if data[from] != nil {
			token[to] = data[from]
		}
	}
	if c.Product.Name == "workbuddy" {
		for k, v := range c.account(ctx, state, access) {
			token[k] = v
		}
	}
	return token, false, nil
}
func (c *Client) account(ctx context.Context, state, access string) Data {
	base, _ := url.Parse(c.Endpoint())
	h := http.Header{}
	for k, v := range map[string]string{"Accept": "application/json", "Authorization": "Bearer " + access, "User-Agent": c.Product.Agent, "X-Product": "WorkBuddy", "X-Domain": base.Host} {
		h.Set(k, v)
	}
	p, status, err := upstream.JSON(ctx, c.HTTP, "GET", c.Endpoint()+"/v2/plugin/login/account?"+url.Values{"state": {state}}.Encode(), h, nil)
	if err != nil || status != 200 {
		return nil
	}
	data := Data(p).Object("data")
	out := Data{}
	for field, aliases := range map[string][]string{"account_uid": {"uid", "userId", "user_id", "id"}, "phone": {"phone", "mobile", "mobilePhone", "phoneNumber", "telephone"}, "account_name": {"name", "nickname"}, "account_email": {"email"}} {
		for _, source := range []Data{data, data.Object("account")} {
			for _, alias := range aliases {
				if source.Text(alias) != "" {
					out[field] = fmt.Sprint(source[alias])
					break
				}
			}
			if out[field] != nil {
				break
			}
		}
	}
	return out
}
