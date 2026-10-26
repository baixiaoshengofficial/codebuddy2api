// Package checkin handles daily WorkBuddy billing rewards and scheduling.
package checkin

import (
	"context"
	"fmt"
	"net/http"
	"net/url"
	"strings"

	"github.com/baixiaoshengofficial/codebuddy2api/internal/credential"
	"github.com/baixiaoshengofficial/codebuddy2api/internal/fault"
	"github.com/baixiaoshengofficial/codebuddy2api/internal/upstream"
)

type Data = credential.Data
type Billing interface {
	Status(context.Context, Data) (Data, error)
	Claim(context.Context, Data) (Data, error)
}
type Provider struct {
	Client   upstream.Doer
	Endpoint func() string
}

func (p *Provider) post(ctx context.Context, path string, d Data) (Data, error) {
	u, _ := url.Parse(p.Endpoint())
	domain := d.Text("domain")
	if domain == "" {
		domain = u.Host
	}
	headers := http.Header{}
	for k, v := range map[string]string{"Accept": "application/json", "Content-Type": "application/json", "Authorization": "Bearer " + d.Text("bearer_token"), "X-User-Id": d.Text("user_id"), "X-Domain": domain} {
		if v != "" {
			headers.Set(k, v)
		}
	}
	enterprise := d.Text("enterprise_id")
	if enterprise == "" {
		enterprise = d.Text("enterpriseId")
	}
	if enterprise != "" {
		headers.Set("X-Enterprise-Id", enterprise)
		headers.Set("X-Tenant-Id", enterprise)
	}
	payload, status, err := upstream.JSON(ctx, p.Client, "POST", p.Endpoint()+path, headers, Data{})
	if status == 401 {
		return nil, fault.New(401, "session_expired", "签到凭证已失效，请重新授权 WorkBuddy")
	}
	if status == 429 || status >= 500 || status == 0 {
		return nil, fault.New(503, "checkin_unavailable", "签到接口暂时不可用")
	}
	if err != nil {
		return nil, err
	}
	return Data(payload), nil
}
func (p *Provider) Status(ctx context.Context, d Data) (Data, error) {
	payload, err := p.post(ctx, "/v2/billing/meter/checkin-activity-status", d)
	if err != nil {
		return nil, err
	}
	if payload["code"] == nil || payload.Number("code") != 0 {
		return nil, fmt.Errorf("签到状态查询失败")
	}
	data, ok := payload["data"].(map[string]any)
	if !ok {
		return nil, fmt.Errorf("签到接口未返回状态数据")
	}
	return Data(data), nil
}
func (p *Provider) Claim(ctx context.Context, d Data) (Data, error) {
	payload, err := p.post(ctx, "/v2/billing/meter/daily-checkin", d)
	if err != nil {
		return nil, err
	}
	if payload["code"] == nil {
		return nil, fmt.Errorf("签到接口未返回业务状态")
	}
	if payload.Number("code") == 10001 || strings.Contains(payload.Text("msg"), "已签到") {
		return Data{"today_checked_in": true, "_already": true}, nil
	}
	if payload.Number("code") != 0 {
		return nil, fmt.Errorf("签到领取失败")
	}
	data, ok := payload["data"].(map[string]any)
	if !ok {
		return nil, fmt.Errorf("签到接口未返回奖励数据")
	}
	return Data(data), nil
}
