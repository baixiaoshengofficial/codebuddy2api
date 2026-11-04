package quota

import (
	"context"
	"fmt"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"

	"github.com/baixiaoshengofficial/codebuddy2api/internal/credential"
	"github.com/baixiaoshengofficial/codebuddy2api/internal/fault"
	"github.com/baixiaoshengofficial/codebuddy2api/internal/upstream"
)

type Provider struct {
	Client   upstream.Doer
	Endpoint func() string
	Agent    string
	Now      func() time.Time
}

func (p *Provider) Query(ctx context.Context, d credential.Data) (Balance, error) {
	base := strings.TrimRight(p.Endpoint(), "/")
	u, err := url.Parse(base)
	if err != nil || u.Host == "" {
		return Balance{}, fault.New(503, "quota_unavailable", "额度查询地址不可用")
	}
	domain := d.Text("domain")
	if domain == "" {
		domain = u.Host
	}
	headers := http.Header{"Accept": {"application/json"}, "Content-Type": {"application/json"}, "Authorization": {"Bearer " + d.Text("bearer_token")}, "X-User-Id": {d.Text("user_id")}, "X-Domain": {domain}, "X-Product": {"SaaS"}, "X-Requested-With": {"XMLHttpRequest"}, "User-Agent": {p.Agent}}
	origin := base
	if login, err := url.Parse("https://" + domain); err == nil && login.Host == domain && login.User == nil {
		origin = login.Scheme + "://" + login.Host
	}
	headers.Set("Origin", origin)
	headers.Set("Referer", origin+"/")
	for _, key := range []string{"enterprise_id", "enterpriseId"} {
		if d.Text(key) != "" {
			headers.Set("X-Enterprise-Id", d.Text(key))
			headers.Set("X-Tenant-Id", d.Text(key))
			break
		}
	}
	now := time.Now()
	if p.Now != nil {
		now = p.Now()
	}
	now = now.In(china)
	rows := []credential.Data{}
	seen := map[string]bool{}
	const pageSize = 100
	for page := 1; page <= 100; page++ {
		body := credential.Data{"PageNumber": page, "PageSize": pageSize, "ProductCode": "p_tcaca", "Status": []int{0, 3}, "OnlyValidPeriod": true, "IsDisplayTotalInfo": true, "SlicePeriodStartTime": now.Format("2006-01-02") + " 00:00:00", "SlicePeriodEndTime": now.Format("2006-01-02") + " 23:59:59"}
		payload, status, err := upstream.JSON(ctx, p.Client, "POST", base+"/v2/billing/meter/get-user-resource", headers, body)
		if status == 401 {
			return Balance{}, fault.New(401, "session_expired", "登录已失效，请重新授权后查询额度")
		}
		if status == 403 {
			return Balance{}, fault.New(403, "quota_forbidden", "该凭证暂时无法查询额度")
		}
		if status == 404 {
			return Balance{}, fault.New(404, "quota_unsupported", "当前站点暂不支持额度查询")
		}
		if err != nil || status != 200 {
			return Balance{}, fault.New(503, "quota_unavailable", "额度查询暂时失败，请稍后刷新")
		}
		root := credential.Data(payload)
		if root.Text("code") != "0" {
			return Balance{}, fault.New(502, "quota_rejected", "上游未返回有效额度")
		}
		response := root.Object("data").Object("Response")
		if len(response.Object("Error")) > 0 {
			return Balance{}, fault.New(502, "quota_rejected", "上游未返回有效额度")
		}
		data := response.Object("Data")
		accounts, ok := data["Accounts"].([]any)
		if !ok || data["TotalCount"] == nil {
			return Balance{}, fault.New(502, "quota_invalid", "上游额度数据不完整")
		}
		totalCount, countErr := strconv.ParseInt(data.Text("TotalCount"), 10, 32)
		if countErr != nil || totalCount < 0 || totalCount > 100*pageSize {
			return Balance{}, fault.New(502, "quota_invalid", "上游额度分页数据异常")
		}
		for _, raw := range accounts {
			object, ok := raw.(map[string]any)
			if !ok {
				return Balance{}, fault.New(502, "quota_invalid", "上游额度数据不完整")
			}
			row := credential.Data(object)
			// The account ID is only an internal pagination key; it is never returned.
			id := row.Text("AccountId")
			if id != "" {
				if seen[id] {
					return Balance{}, fault.New(502, "quota_invalid", "额度数据在分页期间发生变化，请刷新")
				}
				seen[id] = true
			}
			rows = append(rows, row)
		}
		if int64(len(rows)) == totalCount {
			balance, err := normalize(rows, now)
			if err != nil {
				return Balance{}, fmt.Errorf("normalize quota: %w", err)
			}
			return balance, nil
		}
		if len(accounts) == 0 || int64(len(rows)) > totalCount {
			return Balance{}, fault.New(502, "quota_invalid", "上游额度分页数据不完整")
		}
	}
	return Balance{}, fmt.Errorf("quota pagination exceeded")
}
