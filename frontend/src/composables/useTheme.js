import { ref, watch } from "vue";
import { readPreference, writePreference } from "../lib/preferences";
export function useTheme() {
  const theme = ref(
    readPreference("theme", "light") === "dark" ? "dark" : "light",
  );
  watch(
    theme,
    (value) => {
      document.documentElement.dataset.theme = value;
      writePreference("theme", value);
    },
    { immediate: true },
  );
  const toggleTheme = () => {
    theme.value = theme.value === "dark" ? "light" : "dark";
  };
  return { theme, toggleTheme };
}
