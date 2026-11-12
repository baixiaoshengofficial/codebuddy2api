import { onBeforeUnmount, onMounted, provide, ref, watch } from "vue";
import { isView, resolveView } from "../lib/navigation";
import { readPreference, writePreference } from "../lib/preferences";
export function useConsoleNavigation() {
  const activeView = ref(
    resolveView(
      window.location.hash,
      readPreference("activeView", "credentials"),
    ),
  );
  let guard = () => true;
  provide("registerNavigationGuard", (next) => {
    guard = next;
    return () => {
      if (guard === next) guard = () => true;
    };
  });
  function selectView(id) {
    if (!isView(id) || (id !== activeView.value && !guard())) return false;
    activeView.value = id;
    if (window.location.hash !== `#${id}`) window.location.hash = id;
    return true;
  }
  function syncLocation() {
    const id = resolveView(window.location.hash, activeView.value);
    if (
      (id !== activeView.value && !guard()) ||
      !isView(window.location.hash.slice(1))
    ) {
      window.history.replaceState(null, "", `#${activeView.value}`);
      return;
    }
    activeView.value = id;
  }
  watch(activeView, (value) => writePreference("activeView", value));
  onMounted(() => {
    window.history.replaceState(null, "", `#${activeView.value}`);
    window.addEventListener("hashchange", syncLocation);
  });
  onBeforeUnmount(() => window.removeEventListener("hashchange", syncLocation));
  return { activeView, selectView, canLeave: () => guard() };
}
