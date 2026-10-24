import { onBeforeUnmount, ref } from "vue";
import { authHeaders } from "../lib/api";
import { consumeChatStream } from "../lib/chatStream";

export function useChatTest() {
  const status = ref("idle"),
    content = ref(""),
    reasoning = ref(""),
    raw = ref(""),
    error = ref(""),
    duration = ref(null);
  let controller;
  function cancel() {
    controller?.abort();
  }
  async function send(body) {
    if (status.value === "running") return;
    controller = new AbortController();
    status.value = "running";
    content.value = "";
    reasoning.value = "";
    raw.value = "";
    error.value = "";
    duration.value = null;
    const started = performance.now();
    function collect(value) {
      raw.value += JSON.stringify(value, null, 2) + "\n";
      for (const choice of value.choices || []) {
        const message = choice.delta || choice.message || {};
        content.value += message.content || "";
        reasoning.value += message.reasoning_content || "";
      }
    }
    try {
      const response = await fetch("/codebuddy/v1/chat/completions", {
        method: "POST",
        headers: authHeaders({ "X-Client-Name": "Admin Console" }),
        body: JSON.stringify(body),
        signal: controller.signal,
      });
      if (!response.ok) {
        const value = await response.json().catch(() => ({}));
        throw new Error(
          value.detail || value.error?.message || `HTTP ${response.status}`,
        );
      }
      if (body.stream) await consumeChatStream(response.body, collect);
      else {
        const value = await response.json();
        if (value.error)
          throw new Error(value.error.message || String(value.error));
        collect(value);
      }
      status.value = "success";
    } catch (cause) {
      status.value = cause.name === "AbortError" ? "cancelled" : "error";
      error.value =
        cause.name === "AbortError"
          ? "已停止接收；上游可能已经消耗积分，不会自动重发。"
          : cause.message;
    } finally {
      duration.value = Math.round(performance.now() - started);
    }
  }
  onBeforeUnmount(cancel);
  return { status, content, reasoning, raw, error, duration, send, cancel };
}
