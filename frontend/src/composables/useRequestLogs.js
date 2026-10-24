import { computed, reactive, ref, watch } from "vue";
import { useResource } from "./useResource";
import { consoleApi, exportRequests } from "../services/console";
import { notify } from "../lib/notify";
import { useAction } from "./useAction";

export function useRequestLogs() {
  const inputTime = (time) =>
    new Date(time + 480 * 60000).toISOString().slice(0, 16);
  const appliedRange = ref([Date.now() - 86400000, Date.now()]);
  const filters = reactive({
    period: "today",
    model: "",
    client: "",
    credential: "",
    status: "",
    search: "",
    start: inputTime(appliedRange.value[0]),
    end: inputTime(appliedRange.value[1]),
  });
  const page = ref(1);
  const params = computed(() => {
    const value = new URLSearchParams({
      page: page.value,
      page_size: 20,
      offset: 480,
    });
    for (const [key, entry] of Object.entries(filters)) {
      if (key === "start" || key === "end") {
        if (filters.period === "custom")
          value.set(key, appliedRange.value[key === "start" ? 0 : 1]);
      } else if (entry) value.set(key, entry);
    }
    return value;
  });
  const resource = useResource(
    () => consoleApi.requests(params.value),
    { items: [], filters: {}, pages: 1, total: 0 },
    5000,
  );
  function apply() {
    if (filters.period === "custom") {
      const range = [filters.start, filters.end].map((value) =>
        Date.parse(`${value}+08:00`),
      );
      if (!range.every(Number.isFinite) || range[0] >= range[1]) {
        notify("请选择有效的开始和结束时间", "error");
        return;
      }
      appliedRange.value = range;
    }
    page.value = 1;
    resource.refresh();
  }
  watch(
    () => [
      filters.period,
      filters.model,
      filters.client,
      filters.credential,
      filters.status,
    ],
    apply,
  );
  function changePage(delta) {
    page.value = Math.min(
      resource.data.value.pages,
      Math.max(1, page.value + delta),
    );
    resource.refresh();
  }
  const { run, busy: exporting } = useAction();
  return {
    ...resource,
    filters,
    page,
    apply,
    changePage,
    exporting,
    exportCsv: () => run(() => exportRequests(params.value)),
  };
}
