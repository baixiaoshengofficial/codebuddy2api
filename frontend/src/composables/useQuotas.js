import { ref } from "vue";
import { useResource } from "./useResource";
import { consoleApi } from "../services/console";
import { notify } from "../lib/notify";

export function useQuotas(product) {
  const resource = useResource(
    () => consoleApi.quotas(product),
    { accounts: [] },
    60000,
  );
  const refreshing = ref(new Set());
  async function refreshOne(filename) {
    if (refreshing.value.has(filename)) return;
    refreshing.value = new Set([...refreshing.value, filename]);
    try {
      const result = await consoleApi.refreshQuota(product, filename);
      resource.data.value = {
        ...resource.data.value,
        accounts: [
          ...resource.data.value.accounts.filter(
            (row) => row.filename !== filename,
          ),
          result,
        ],
      };
      // Supersede any older list response already in flight.
      await resource.refresh();
    } catch (error) {
      notify(error.message, "error");
    } finally {
      const next = new Set(refreshing.value);
      next.delete(filename);
      refreshing.value = next;
    }
  }
  return { ...resource, refreshing, refreshOne };
}
