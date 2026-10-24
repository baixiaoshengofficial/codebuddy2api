import { ref } from "vue";
import { notify } from "../lib/notify";

export function useAction() {
  const busy = ref(false);
  async function run(action, message) {
    if (busy.value) return;
    busy.value = true;
    try {
      await action();
      if (message) notify(message, "success");
    } catch (error) {
      notify(error.message, "error");
    } finally {
      busy.value = false;
    }
  }
  return { busy, run };
}
