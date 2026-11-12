export function useTabs(active, values) {
  return function onTabKeydown(event) {
    if (event.target.getAttribute("role") !== "tab") return;
    const index = values.indexOf(active.value);
    let next;
    if (event.key === "ArrowRight") next = (index + 1) % values.length;
    else if (event.key === "ArrowLeft")
      next = (index - 1 + values.length) % values.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = values.length - 1;
    else return;
    event.preventDefault();
    active.value = values[next];
    event.currentTarget.querySelectorAll('[role="tab"]')[next]?.focus();
  };
}
