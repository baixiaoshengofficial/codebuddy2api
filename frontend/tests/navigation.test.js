import test from "node:test";
import assert from "node:assert/strict";
import { resolveView } from "../src/lib/navigation.js";
import { readPreference, writePreference } from "../src/lib/preferences.js";

test("a valid deep link takes precedence over the previous page", () => {
  assert.equal(resolveView("#requests", "settings"), "requests");
  assert.equal(resolveView("", "settings"), "settings");
});

test("unknown routes and obsolete preferences resolve to an existing page", () => {
  assert.equal(resolveView("#unknown", "dashboard"), "dashboard");
  assert.equal(resolveView("#unknown", "removed-page"), "credentials");
  assert.equal(resolveView(null, null), "credentials");
});

test("blocked browser storage does not prevent navigation or theme changes", () => {
  const original = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    get() {
      throw new Error("Storage access denied");
    },
  });
  try {
    assert.equal(readPreference("theme", "light"), "light");
    assert.doesNotThrow(() => writePreference("activeView", "requests"));
  } finally {
    if (original) Object.defineProperty(globalThis, "localStorage", original);
    else delete globalThis.localStorage;
  }
});
