import test from "node:test";
import assert from "node:assert/strict";
import {
  activeRequestFilterCount,
  beijingInputTime,
  commitRequestFilters,
  createAppliedRequestFilters,
  createRequestFilters,
  parseRequestRange,
  requestQueryParams,
} from "../src/lib/requestFilters.js";
import { workbuddyAuthorization } from "../src/lib/credentialState.js";

const now = Date.parse("2026-10-26T14:35:00Z");

test("custom time inputs use Beijing time regardless of the browser timezone", () => {
  const draft = createRequestFilters(now);
  assert.equal(draft.end, "2026-10-26T22:35");
  assert.equal(draft.start, "2026-10-25T22:35");
  assert.equal(beijingInputTime(now), draft.end);
  const applied = commitRequestFilters(
    createAppliedRequestFilters(now),
    draft,
    "range",
  );
  assert.equal(applied.end, now);
  assert.equal(applied.start, now - 86400000);
  assert.equal(requestQueryParams(applied, 1, 20).get("offset"), "480");
});

test("typing search cannot change polling or export until submitted", () => {
  const applied = createAppliedRequestFilters(now);
  const draft = {
    ...createRequestFilters(now),
    search: " unfinished query ",
    model: "model-a",
  };
  const dimensions = commitRequestFilters(applied, draft, "dimensions");
  const polling = requestQueryParams(dimensions, 1, 20);
  assert.equal(polling.get("model"), "model-a");
  assert.equal(polling.has("search"), false);
  const searched = commitRequestFilters(dimensions, draft, "search");
  assert.equal(
    requestQueryParams(searched, 1, 20).get("search"),
    "unfinished query",
  );
  assert.equal(requestQueryParams(dimensions, 1, 20).has("search"), false);
});

test("editing custom dates cannot leak into search, filter changes or export", () => {
  let applied = { ...createAppliedRequestFilters(now), period: "custom" };
  const before = requestQueryParams(applied, 1, 20).toString();
  const draft = {
    ...createRequestFilters(now),
    period: "custom",
    start: "invalid",
    end: "",
    status: "error",
    search: "hello",
  };
  applied = commitRequestFilters(applied, draft, "dimensions");
  applied = commitRequestFilters(applied, draft, "search");
  const query = requestQueryParams(applied, 1, 20);
  assert.equal(query.get("start"), new URLSearchParams(before).get("start"));
  assert.equal(query.get("end"), new URLSearchParams(before).get("end"));
  assert.equal(query.get("search"), "hello");
  assert.equal(query.get("status"), "error");
  assert.throws(() => commitRequestFilters(applied, draft, "range"), /有效/);
});

test("applying a custom range preserves the active query and ignores draft search", () => {
  const applied = {
    ...createAppliedRequestFilters(now),
    model: "model-a",
    search: "applied",
  };
  const draft = {
    ...createRequestFilters(now),
    start: "2026-10-20T00:00",
    search: "not applied",
  };
  const value = commitRequestFilters(applied, draft, "range");
  assert.equal(value.period, "custom");
  assert.equal(value.model, "model-a");
  assert.equal(value.search, "applied");
  assert.equal(value.start, Date.parse("2026-10-19T16:00Z"));
});

test("invalid ranges fail locally and cannot mutate the applied query", () => {
  for (const range of [
    ["", ""],
    ["2026-10-26T22:00", "2026-10-25T22:00"],
    ["2024-01-01T00:00", "2026-01-01T00:00"],
  ])
    assert.throws(() => parseRequestRange(...range));
  const applied = createAppliedRequestFilters(now);
  const before = { ...applied };
  assert.throws(() =>
    commitRequestFilters(applied, { start: "", end: "" }, "range"),
  );
  assert.deepEqual(applied, before);
});

test("reset defaults clear active selections and query reflects page size", () => {
  const dirty = {
    ...createAppliedRequestFilters(now),
    period: "7d",
    model: "m",
    status: "error",
    search: "q",
  };
  assert.equal(activeRequestFilterCount(dirty), 4);
  const reset = createAppliedRequestFilters(now);
  assert.equal(activeRequestFilterCount(reset), 0);
  const query = requestQueryParams(reset, 3, 50);
  assert.equal(query.get("page"), "3");
  assert.equal(query.get("page_size"), "50");
  assert.equal(query.has("start"), false);
  assert.equal(query.has("end"), false);
});

test("successful past check-in does not hide an expired or renewable login", () => {
  assert.deepEqual(
    workbuddyAuthorization({ is_expired: true }, { status: "claimed" }),
    { state: "expired", label: "已过期" },
  );
  assert.deepEqual(
    workbuddyAuthorization(
      { is_expired: true, has_refresh_token: true },
      { status: "claimed" },
    ),
    { state: "refreshable", label: "待自动续期" },
  );
});

test("a rejected WorkBuddy session requires reauthorization even before token expiry", () => {
  assert.deepEqual(
    workbuddyAuthorization(
      { is_expired: false, has_refresh_token: true },
      { status: "expired" },
    ),
    { state: "reauthorize", label: "需要重新授权" },
  );
  assert.deepEqual(
    workbuddyAuthorization({ is_expired: false }, { status: "error" }),
    { state: "available", label: "已授权" },
  );
});
