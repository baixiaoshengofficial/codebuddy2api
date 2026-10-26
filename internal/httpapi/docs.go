package httpapi

import "net/http"

var documentedPaths = map[string][]string{
	"/health": {"get"}, "/codebuddy/v1/models": {"get"}, "/codebuddy/v1/chat/completions": {"post"}, "/codebuddy/v1/responses": {"post"},
	"/api/settings": {"get", "post"}, "/api/checkin": {"get"}, "/api/checkin/run": {"post"}, "/api/stats": {"get"},
	"/api/console/status": {"get"}, "/api/console/credentials": {"get"}, "/api/console/credentials/mode": {"post"}, "/api/console/credentials/{filename}": {"delete"},
	"/api/console/requests": {"get"}, "/api/console/requests/export": {"get"}, "/api/console/requests/{id}": {"get"}, "/api/console/overview": {"get"},
	"/workbuddy/credentials": {"get"}, "/workbuddy/credentials/{filename}": {"delete"},
	"/codebuddy/auth/start": {"get"}, "/codebuddy/auth/poll": {"post"}, "/codebuddy/auth/reauthorize": {"post"}, "/codebuddy/auth/reauthorize/poll": {"post"},
	"/workbuddy/auth/start": {"get"}, "/workbuddy/auth/poll": {"post"}, "/workbuddy/auth/reauthorize": {"post"}, "/workbuddy/auth/reauthorize/poll": {"post"},
}

func (s *Server) openapi(w http.ResponseWriter, r *http.Request) {
	paths := map[string]any{}
	for path, methods := range documentedPaths {
		operations := map[string]any{}
		for _, method := range methods {
			operation := map[string]any{"responses": map[string]any{"200": map[string]any{"description": "Success"}}, "security": []any{map[string]any{"BearerAuth": []any{}}}}
			if path == "/health" || path == "/codebuddy/v1/models" {
				delete(operation, "security")
			}
			operations[method] = operation
		}
		paths[path] = operations
	}
	writeJSON(w, 200, map[string]any{"openapi": "3.0.3", "info": map[string]any{"title": "CodeBuddy2API", "version": s.d.Version}, "components": map[string]any{"securitySchemes": map[string]any{"BearerAuth": map[string]any{"type": "http", "scheme": "bearer"}}}, "paths": paths})
}
func (s *Server) docs(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Write([]byte(`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>CodeBuddy2API API</title><style>body{max-width:1000px;margin:3rem auto;font:16px system-ui;padding:1rem}code,pre{background:#f4f5f4;padding:.5rem}pre{overflow:auto}</style><h1>CodeBuddy2API API</h1><p>鉴权：Authorization: Bearer &lt;访问密码&gt;</p><p>聊天：POST /codebuddy/v1/chat/completions，支持 stream 和 tools。Responses 为基础适配，流式事件在收齐回复后发送。</p><pre>{"model":"模型名称","messages":[{"role":"user","content":"你好"}],"stream":true}</pre><p>请求筛选：period=today|24h|7d|30d|custom，offset 为时区分钟偏移，start/end 为毫秒时间戳。可叠加 model、client、credential、status、search。分页 page=1、page_size=20。</p><p>授权：GET /{product}/auth/start；POST /{product}/auth/poll，正文 {"auth_state":"…"}。重新授权：POST /{product}/auth/reauthorize，正文 {"filename":"…"}，随后轮询 /auth/reauthorize/poll。</p><p><a href="/openapi.json">接口清单（OpenAPI）</a></p><pre id="paths"></pre><script>fetch('/openapi.json').then(r=>r.json()).then(s=>{document.getElementById('paths').textContent=Object.entries(s.paths).flatMap(([p,m])=>Object.keys(m).map(x=>x.toUpperCase()+' '+p)).join('\n')})</script></html>`))
}
