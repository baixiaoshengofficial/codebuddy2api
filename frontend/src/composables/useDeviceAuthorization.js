import { onBeforeUnmount, ref } from "vue";
import { consoleApi } from "../services/console";
import { isOAuthPendingError } from "../lib/api";
import { notify } from "../lib/notify";

export function useDeviceAuthorization(product, onSaved) {
  const pending = ref(false);
  const url = ref("");
  const error = ref("");
  let controller,
    timer,
    generation = 0;
  function cancel() {
    generation++;
    clearTimeout(timer);
    controller?.abort();
    pending.value = false;
    url.value = "";
  }
  async function start(filename = null) {
    cancel();
    const current = generation;
    controller = new AbortController();
    const signal = controller.signal;
    pending.value = true;
    error.value = "";
    try {
      const result = await consoleApi.startAuthorization(
        product,
        signal,
        filename,
      );
      if (current !== generation) return;
      if (!result.auth_state || !result.verification_uri_complete)
        throw new Error("未返回完整的授权链接");
      url.value = result.verification_uri_complete;
      let delay = Math.max(3000, Number(result.interval || 3) * 1000);
      const deadline =
        Date.now() + Math.min(Number(result.expires_in || 900), 900) * 1000;
      async function poll() {
        try {
          if (Date.now() >= deadline)
            throw new Error("授权等待已过期，请重新开始");
          const value = await consoleApi.pollAuthorization(
            product,
            result.auth_state,
            signal,
            filename,
          );
          if (current !== generation) return;
          if (value.access_token || value.saved) {
            if (!value.saved) throw new Error("授权成功，但服务器保存凭证失败");
            cancel();
            notify(
              filename ? "重新授权成功，已更新原凭证" : "授权成功，凭证已保存",
              "success",
            );
            await onSaved();
            return;
          }
        } catch (cause) {
          if (current !== generation) return;
          if (!isOAuthPendingError(cause)) {
            error.value = cause.message;
            cancel();
            return;
          }
          if (cause.error === "slow_down") delay += 5000;
        }
        if (current === generation) timer = setTimeout(poll, delay);
      }
      timer = setTimeout(poll, delay);
    } catch (cause) {
      if (current === generation) {
        error.value = cause.message;
        cancel();
      }
    }
  }
  onBeforeUnmount(cancel);
  return { pending, url, error, start, cancel };
}
