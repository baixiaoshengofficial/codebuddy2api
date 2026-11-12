import { nextTick, onBeforeUnmount, onMounted } from "vue";
// Native dialogs make the background inert; Tab wrapping is shared by all modal surfaces.
export function useDialog(panel, close) {
  let overflow, previous;
  onMounted(() => {
    previous = document.activeElement;
    overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    panel.value.showModal();
  });
  onBeforeUnmount(() => {
    panel.value?.close();
    if (typeof overflow === "string") document.body.style.overflow = overflow;
    nextTick(() => {
      if (previous?.isConnected) previous.focus({ preventScroll: true });
    });
  });
  function cancel(event) {
    event.preventDefault();
    close();
  }
  function keydown(event) {
    if (event.key !== "Tab") return;
    const items = [
      ...panel.value.querySelectorAll(
        "button, a[href], input, select, textarea, [tabindex]",
      ),
    ].filter(
      (element) =>
        element.tabIndex >= 0 &&
        !element.matches(":disabled") &&
        element.getClientRects().length &&
        getComputedStyle(element).visibility !== "hidden",
    );
    const first = items[0],
      last = items.at(-1);
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }
  function backdrop(event) {
    if (event.target !== panel.value) return;
    const box = panel.value.getBoundingClientRect();
    if (
      event.clientX < box.left ||
      event.clientX > box.right ||
      event.clientY < box.top ||
      event.clientY > box.bottom
    )
      close();
  }
  return { cancel, backdrop, keydown };
}
