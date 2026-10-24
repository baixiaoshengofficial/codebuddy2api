import { computed, inject, onBeforeUnmount, onMounted, ref } from "vue";
import { consoleApi } from "../services/console";
import { setStoredPassword } from "../lib/api";
import { useAction } from "./useAction";
import { notify } from "../lib/notify";

export function useSettings() {
  const settings = ref({}),
    original = ref({}),
    labels = ref({}),
    readonly = ref([]),
    loading = ref(false),
    error = ref("");
  const { busy: saving, run } = useAction();
  const changes = computed(() =>
    Object.fromEntries(
      Object.entries(settings.value).filter(
        ([key, value]) =>
          !readonly.value.includes(key) && value !== original.value[key],
      ),
    ),
  );
  const dirty = computed(() => Object.keys(changes.value).length > 0);
  function canLeave() {
    return !dirty.value || window.confirm("设置尚未保存，确定离开并放弃修改？");
  }
  const registerGuard = inject("registerNavigationGuard", () => () => {});
  const unregister = registerGuard(canLeave);
  const preventUnload = (event) => {
    if (dirty.value) {
      event.preventDefault();
      event.returnValue = "";
    }
  };
  async function load() {
    loading.value = true;
    try {
      const result = await consoleApi.settings();
      settings.value = { ...result.settings };
      original.value = { ...result.settings };
      labels.value = result.labels;
      readonly.value = result.readonly_keys;
      error.value = "";
    } catch (cause) {
      error.value = cause.message;
    } finally {
      loading.value = false;
    }
  }
  async function save() {
    await run(async () => {
      const submitted = { ...changes.value };
      await consoleApi.saveSettings(submitted);
      if ("CODEBUDDY_PASSWORD" in submitted)
        setStoredPassword(submitted.CODEBUDDY_PASSWORD);
      original.value = { ...original.value, ...submitted };
      notify("设置已保存，立即生效", "success");
    });
  }
  function reset() {
    settings.value = { ...original.value };
  }
  onMounted(() => {
    load();
    window.addEventListener("beforeunload", preventUnload);
  });
  onBeforeUnmount(() => {
    unregister();
    window.removeEventListener("beforeunload", preventUnload);
  });
  return {
    settings,
    labels,
    readonly,
    loading,
    saving,
    error,
    dirty,
    save,
    reset,
    load,
  };
}
