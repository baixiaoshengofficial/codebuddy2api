package app_test

import (
	"encoding/json"
	"net/http"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/baixiaoshengofficial/codebuddy2api/internal/credential"
)

func TestQuotaHTTPIsAuthenticatedIsolatedAndCached(t *testing.T) {
	var upstreamCalls atomic.Int32
	a, root := setup(t, doer(func(r *http.Request) (*http.Response, error) {
		upstreamCalls.Add(1)
		if r.URL.Path != "/v2/billing/meter/get-user-resource" {
			t.Errorf("quota triggered unrelated request: %s", r.URL.Path)
		}
		if r.Header.Get("X-Product") != "SaaS" {
			t.Error("missing official quota header")
		}
		return response(200, `{"code":0,"data":{"Response":{"Data":{"TotalCount":1,"Accounts":[{"AccountId":1234567890123456789,"CapacityType":1,"CapacityUnit":"credits","Status":0,"CycleCapacitySizePrecise":"100","CycleCapacityRemainPrecise":"0.00000018"}]}}}}`), nil
	}))
	d := data{"bearer_token": "quota-secret", "refresh_token": "refresh-secret", "user_id": "sample", "created_at": time.Now().Unix(), "expires_in": 86400}
	if err := a.Code.Put("account.json", d); err != nil {
		t.Fatal(err)
	}
	work := credential.JSONRepository{Directory: filepath.Join(root, ".workbuddy_creds")}
	if err := work.Put("account.json", d); err != nil {
		t.Fatal(err)
	}
	before := call(a, "GET", "/api/console/credentials", "", true).Body.String()
	for _, product := range []string{"codebuddy", "workbuddy"} {
		path := "/api/console/quotas/" + product
		if w := call(a, "GET", path, "", false); w.Code != 403 {
			t.Fatal(w.Code)
		}
		if w := call(a, "POST", path+"/account.json", "", false); w.Code != 403 {
			t.Fatal(w.Code)
		}
		list := call(a, "GET", path, "", true)
		if list.Code != 200 || list.Header().Get("Cache-Control") != "no-store" {
			t.Fatal(list.Code, list.Body.String())
		}
		payload := decode(t, list)
		rows := payload["accounts"].([]any)
		row := data(rows[0].(map[string]any))
		balance := row.Object("balance")
		if row.Text("status") != "ok" || balance.Text("remaining") != "0.00000018" {
			t.Fatal(row)
		}
		for _, secret := range []string{"quota-secret", "refresh-secret", "bearer_token", "AccountId"} {
			if strings.Contains(list.Body.String(), secret) {
				t.Fatal("quota response leaked private upstream fields")
			}
		}
		if w := call(a, "POST", path+"/account.json", "", true); w.Code != 200 {
			t.Fatal(w.Code, w.Body.String())
		}
		if w := call(a, "POST", path+"/missing.json", "", true); w.Code != 404 {
			t.Fatal(w.Code)
		}
	}
	if upstreamCalls.Load() != 2 {
		t.Fatal("read cache or products not isolated", upstreamCalls.Load())
	}
	if w := call(a, "GET", "/api/console/quotas/other", "", true); w.Code != 404 {
		t.Fatal(w.Code)
	}
	after := call(a, "GET", "/api/console/credentials", "", true).Body.String()
	var b, c data
	json.Unmarshal([]byte(before), &b)
	json.Unmarshal([]byte(after), &c)
	if b["mode"] != c["mode"] || b["current_filename"] != c["current_filename"] || b["usage_count"] != c["usage_count"] {
		t.Fatal("quota modified routing")
	}
	logs := decode(t, call(a, "GET", "/api/console/requests", "", true))
	if logs.Number("total") != 0 {
		t.Fatal("quota created model ledger requests")
	}
}
