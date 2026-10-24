import { onBeforeUnmount, onMounted, ref, shallowRef } from "vue";

/** Serial, visibility-aware polling with stale-result protection. */
export function useResource(loader, initial, interval = 0) {
  const data = shallowRef(initial);
  const loading = ref(false);
  const error = ref("");
  const updatedAt = ref(null);
  let timer,
    disposed = false,
    sequence = 0;
  async function refresh() {
    const current = ++sequence;
    loading.value = true;
    try {
      const value = await loader();
      if (!disposed && current === sequence) {
        data.value = value;
        error.value = "";
        updatedAt.value = Date.now();
      }
    } catch (cause) {
      if (!disposed && current === sequence) error.value = cause.message;
    } finally {
      if (!disposed && current === sequence) loading.value = false;
    }
  }
  async function poll() {
    if (!document.hidden) await refresh();
    if (!disposed && interval) timer = setTimeout(poll, interval);
  }
  onMounted(poll);
  onBeforeUnmount(() => {
    disposed = true;
    sequence++;
    clearTimeout(timer);
  });
  return { data, loading, error, updatedAt, refresh };
}
