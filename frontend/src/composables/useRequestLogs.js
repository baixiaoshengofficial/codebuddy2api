import { computed, reactive, ref, watch } from "vue";
import { useResource } from "./useResource";
import { consoleApi, exportRequests } from "../services/console";
import { useAction } from "./useAction";
import {
  activeRequestFilterCount,
  beijingInputTime,
  commitRequestFilters,
  createAppliedRequestFilters,
  createRequestFilters,
  requestQueryParams,
} from "../lib/requestFilters";

export function useRequestLogs() {
  const now = Date.now();
  const filters = reactive(createRequestFilters(now));
  const applied = ref(createAppliedRequestFilters(now));
  const page = ref(1);
  const pageSize = ref(20);
  const rangeError = ref("");
  const params = computed(() =>
    requestQueryParams(applied.value, page.value, pageSize.value),
  );
  const resource = useResource(
    () => consoleApi.requests(params.value),
    { items: [], filters: {}, pages: 1, total: 0 },
    5000,
  );
  function updateFilters(patch) {
    Object.assign(filters, patch);
  }
  function commit(action) {
    applied.value = commitRequestFilters(applied.value, filters, action);
    page.value = 1;
  }
  function applySearch() {
    commit("search");
  }
  function applyRange() {
    try {
      commit("range");
      rangeError.value = "";
    } catch (error) {
      rangeError.value = error.message;
    }
  }
  function resetFilters() {
    const now = Date.now();
    Object.assign(filters, createRequestFilters(now));
    applied.value = createAppliedRequestFilters(now);
    rangeError.value = "";
    page.value = 1;
  }
  watch(
    () => [
      filters.period,
      filters.model,
      filters.client,
      filters.credential,
      filters.status,
    ],
    () => commit("dimensions"),
  );
  watch(
    () => [filters.start, filters.end],
    () => {
      rangeError.value = "";
    },
  );
  watch(pageSize, () => {
    page.value = 1;
  });
  watch(() => params.value.toString(), resource.refresh);
  watch(
    () => resource.data.value.pages,
    (pages) => {
      if (page.value > pages) page.value = Math.max(1, pages);
    },
  );
  function changePage(delta) {
    page.value = Math.min(
      resource.data.value.pages,
      Math.max(1, page.value + delta),
    );
  }
  const activeCount = computed(() => activeRequestFilterCount(applied.value));
  const pendingSearch = computed(
    () => filters.search.trim() !== applied.value.search,
  );
  const pendingRange = computed(
    () =>
      filters.period === "custom" &&
      (filters.start !== beijingInputTime(applied.value.start) ||
        filters.end !== beijingInputTime(applied.value.end)),
  );
  const { run, busy: exporting } = useAction();
  return {
    ...resource,
    filters,
    updateFilters,
    page,
    pageSize,
    activeCount,
    pendingSearch,
    pendingRange,
    rangeError,
    applySearch,
    applyRange,
    resetFilters,
    changePage,
    exporting,
    exportCsv: () => run(() => exportRequests(params.value)),
  };
}
