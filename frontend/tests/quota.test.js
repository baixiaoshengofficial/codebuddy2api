import test from "node:test";
import assert from "node:assert/strict";
import { creditAmount, quotaLabel } from "../src/lib/quota.js";

test("a failed or missing query never appears as zero credits", () => {
  for (const value of [null, undefined, "", " ", "NaN", -1, Infinity])
    assert.equal(creditAmount(value), "—");
  assert.equal(quotaLabel(null), "未知");
  assert.equal(quotaLabel({ status: "error", balance: null }), "未知");
  assert.equal(quotaLabel({ status: "unauthorized" }), "需授权");
  assert.equal(quotaLabel({ status: "unsupported" }), "暂不支持");
  assert.equal(quotaLabel(null, true), "查询中…");
  assert.equal(quotaLabel({ balance: { remaining: "0" } }), "0");
});
test("fractional credit balances remain distinguishable from exhaustion", () => {
  assert.equal(creditAmount("0.00000018"), "<0.01");
  assert.equal(creditAmount("20.28000018"), "20.28");
  assert.equal(creditAmount("1600.5"), "1,600.5");
  assert.equal(
    quotaLabel({ balance: { remaining: "10" }, stale: true, status: "error" }),
    "10",
  );
});
