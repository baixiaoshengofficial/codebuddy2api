package ledger

import (
	"bytes"
	"encoding/csv"
	"fmt"
	"math"
	"strings"
	"time"
)

type Filter struct {
	Start, End                                *int64
	Search, Model, Client, Credential, Status string
}

func (f Filter) SQL() (string, []any) {
	clauses := []string{}
	args := []any{}
	if f.Start != nil {
		clauses = append(clauses, "created_at >= ?")
		args = append(args, *f.Start)
	}
	if f.End != nil {
		clauses = append(clauses, "created_at < ?")
		args = append(args, *f.End)
	}
	for _, field := range []struct{ name, value string }{{"model", f.Model}, {"client", f.Client}, {"credential", f.Credential}, {"status", f.Status}} {
		if field.value != "" {
			clauses = append(clauses, field.name+" = ?")
			args = append(args, field.value)
		}
	}
	if f.Search != "" {
		clauses = append(clauses, "(instr(request_id, ?) > 0 OR instr(request_preview, ?) > 0)")
		args = append(args, f.Search, f.Search)
	}
	if len(clauses) == 0 {
		return "", args
	}
	return " WHERE " + strings.Join(clauses, " AND "), args
}
func TimeRange(period string, start, end *int64, offset int, now time.Time) (*int64, *int64, error) {
	var from int64
	to := now.UnixMilli() + 1
	switch period {
	case "custom":
		if start == nil || end == nil || *start >= *end {
			return nil, nil, fmt.Errorf("请选择有效的开始和结束时间")
		}
		if *end-*start > 366*86400000 {
			return nil, nil, fmt.Errorf("自定义范围不能超过 366 天")
		}
		return start, end, nil
	case "today", "":
		local := now.In(time.FixedZone("local", offset*60))
		from = time.Date(local.Year(), local.Month(), local.Day(), 0, 0, 0, 0, local.Location()).UnixMilli()
	case "24h", "7d", "30d":
		days := map[string]int{"24h": 1, "7d": 7, "30d": 30}[period]
		from = now.Add(-time.Duration(days) * 24 * time.Hour).UnixMilli()
	default:
		return nil, nil, fmt.Errorf("不支持的时间范围")
	}
	return &from, &to, nil
}
func (s *Store) List(filter Filter, page, size int) (Data, error) {
	where, args := filter.SQL()
	count, err := s.query("SELECT COUNT(*) AS total FROM request_logs"+where, args...)
	if err != nil {
		return nil, err
	}
	total := count[0]["total"].(int64)
	rows, err := s.query("SELECT * FROM request_logs"+where+" ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?", append(args, size, (page-1)*size)...)
	if err != nil {
		return nil, err
	}
	filters := Data{}
	for column, label := range map[string]string{"model": "models", "client": "clients", "credential": "credentials"} {
		values, err := s.query("SELECT DISTINCT " + column + " AS value FROM request_logs WHERE " + column + " IS NOT NULL ORDER BY " + column)
		if err != nil {
			return nil, err
		}
		out := []any{}
		for _, v := range values {
			out = append(out, v["value"])
		}
		filters[label] = out
	}
	pages := (total + int64(size) - 1) / int64(size)
	if pages < 1 {
		pages = 1
	}
	return Data{"items": rows, "page": page, "page_size": size, "total": total, "pages": pages, "filters": filters}, nil
}
func (s *Store) Overview(filter Filter) (Data, error) {
	where, args := filter.SQL()
	rows, err := s.query(`SELECT COUNT(*) AS requests,COALESCE(SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END),0) AS pending,COALESCE(SUM(CASE WHEN status='success' THEN 1 ELSE 0 END),0) AS successes,AVG(CASE WHEN status!='pending' THEN latency_ms END) AS average_latency_ms,COALESCE(SUM(upstream_attempts),0) AS upstream_attempts,COALESCE(SUM(total_tokens),0) AS tokens FROM request_logs`+where, args...)
	if err != nil {
		return nil, err
	}
	result := rows[0]
	requests, pending, success := result["requests"].(int64), result["pending"].(int64), result["successes"].(int64)
	result["success_rate"] = nil
	if requests > pending {
		result["success_rate"] = math.Round(float64(success)/float64(requests-pending)*1000) / 10
	}
	delete(result, "successes")
	if average, ok := result["average_latency_ms"].(float64); ok {
		result["average_latency_ms"] = math.Round(average)
	}
	for field, label := range map[string]string{"model": "models", "client": "clients"} {
		values, err := s.query("SELECT "+field+" AS name,COUNT(*) AS count FROM request_logs"+where+" GROUP BY "+field, args...)
		if err != nil {
			return nil, err
		}
		group := Data{}
		for _, v := range values {
			group[fmt.Sprint(v["name"])] = v["count"]
		}
		result[label] = group
	}
	return result, nil
}
func (s *Store) Stats() (Data, error) {
	result := Data{}
	for field, label := range map[string]string{"model": "model_usage", "client": "client_usage", "credential": "credential_usage"} {
		rows, err := s.query("SELECT " + field + " AS name,COUNT(*) AS count FROM request_logs WHERE " + field + " IS NOT NULL GROUP BY " + field)
		if err != nil {
			return nil, err
		}
		group := Data{}
		for _, row := range rows {
			group[fmt.Sprint(row["name"])] = row["count"]
		}
		result[label] = group
	}
	return result, nil
}
func (s *Store) Export(filter Filter) ([]byte, error) {
	where, args := filter.SQL()
	rows, err := s.query("SELECT * FROM request_logs"+where+" ORDER BY created_at DESC LIMIT 50000", args...)
	if err != nil {
		return nil, err
	}
	var buffer bytes.Buffer
	buffer.WriteString("\ufeff")
	writer := csv.NewWriter(&buffer)
	columns := strings.Split("created_at,completed_at,status,status_code,model,client,client_detail,client_host,credential,credential_user_id,endpoint,request_id,conversation_id,request_hash,request_preview,response_preview,is_streaming,latency_ms,upstream_attempts,input_tokens,output_tokens,total_tokens,token_estimated,error", ",")
	writer.Write(columns)
	for _, row := range rows {
		values := make([]string, len(columns))
		for i, key := range columns {
			if row[key] != nil {
				values[i] = fmt.Sprint(row[key])
			}
		}
		writer.Write(values)
	}
	writer.Flush()
	return buffer.Bytes(), writer.Error()
}
